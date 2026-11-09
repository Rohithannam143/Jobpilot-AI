from __future__ import annotations

import csv
import io
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("JOBPILOT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobpilot.sqlite3"

app = FastAPI(title="JobPilot AI", version="0.1.0", description="Job and internship discovery assistant")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app"), name="static")

EXCLUDED_TERMS = ("marketing", "sales", "business development", "telecalling", "cold calling")
DEFAULT_SKILLS = {
    "python", "javascript", "react", "html", "css", "sql", "fastapi",
    "git", "github", "jenkins", "tomcat", "devops", "cloud", "websocket",
    "socket.io", "flutter", "machine learning", "artificial intelligence",
}
ROLE_TERMS = (
    "python developer", "full stack", "frontend", "backend", "react",
    "ai", "machine learning", "data science", "devops", "cloud",
    "cybersecurity", "flutter", "ui/ux", "software engineer",
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            location TEXT DEFAULT '',
            role_type TEXT DEFAULT 'job',
            stipend REAL,
            salary TEXT DEFAULT '',
            url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            posted_date TEXT DEFAULT '',
            status TEXT DEFAULT 'saved',
            match_score INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, company, platform, url)
        );
        """)
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def score_listing(row: dict[str, Any]) -> int:
    text = " ".join(str(row.get(k) or "") for k in ("title", "description")).lower()
    score = sum(5 for skill in DEFAULT_SKILLS if skill in text)
    score += sum(7 for term in ROLE_TERMS if term in text)
    if "hyderabad" in str(row.get("location", "")).lower():
        score += 10
    if str(row.get("role_type", "")).lower() in {"internship", "intern"}:
        score += 4
    return min(score, 100)


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    if not title:
        raise ValueError("Each listing must have a title.")
    role_type = str(row.get("role_type") or "job").strip().lower()
    stipend_raw = str(row.get("stipend") or "").strip()
    try:
        stipend = float(stipend_raw) if stipend_raw else None
    except ValueError:
        stipend = None
    result = {
        "title": title,
        "company": str(row.get("company") or "").strip(),
        "platform": str(row.get("platform") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "role_type": role_type,
        "stipend": stipend,
        "salary": str(row.get("salary") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "posted_date": str(row.get("posted_date") or "").strip(),
        "status": str(row.get("status") or "saved").strip().lower(),
    }
    result["match_score"] = score_listing(result)
    return result


def save_listing(item: dict[str, Any]) -> bool:
    with closing(connect()) as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO listings
            (title, company, platform, location, role_type, stipend, salary, url,
             description, posted_date, status, match_score)
            VALUES (:title, :company, :platform, :location, :role_type, :stipend,
                    :salary, :url, :description, :posted_date, :status, :match_score)
        """, item)
        conn.commit()
        return cur.rowcount > 0


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (BASE_DIR / "app" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "JobPilot AI"}


@app.get("/api/listings")
def get_listings(
    q: str = "",
    location: str = "",
    paid_only: bool = False,
    include_internships: bool = True,
    min_score: int = 0,
) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM listings ORDER BY match_score DESC, created_at DESC").fetchall()
    results = []
    for raw in rows:
        item = dict(raw)
        searchable = f"{item['title']} {item['company']} {item['description']}".lower()
        if any(term in searchable for term in EXCLUDED_TERMS):
            continue
        if q and q.lower() not in searchable:
            continue
        if location and location.lower() not in item["location"].lower():
            continue
        if paid_only and (item["stipend"] is None or item["stipend"] <= 0):
            continue
        if not include_internships and item["role_type"] in {"internship", "intern"}:
            continue
        if item["match_score"] < min_score:
            continue
        results.append(item)
    return results


@app.post("/api/import")
async def import_csv(file: UploadFile = File(...)) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(status_code=413, detail="CSV must be smaller than 5 MB.")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "title" not in {h.strip().lower() for h in reader.fieldnames}:
            raise ValueError("CSV must contain a title column.")
        inserted = 0
        skipped = 0
        for raw_row in reader:
            row = {str(k).strip().lower(): v for k, v in raw_row.items() if k}
            try:
                item = normalize(row)
                if save_listing(item):
                    inserted += 1
                else:
                    skipped += 1
            except ValueError:
                skipped += 1
        return JSONResponse({"inserted": inserted, "skipped": skipped})
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.")


@app.patch("/api/listings/{listing_id}/status")
def update_status(listing_id: int, status: str = Form(...)) -> dict[str, Any]:
    allowed = {"saved", "preparing", "applied", "interview", "rejected", "offer"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"Status must be one of: {', '.join(sorted(allowed))}")
    with closing(connect()) as conn:
        cur = conn.execute("UPDATE listings SET status=? WHERE id=?", (status, listing_id))
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Listing not found.")
    return {"id": listing_id, "status": status}


@app.get("/api/stats")
def stats() -> dict[str, int]:
    with closing(connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        paid = conn.execute("SELECT COUNT(*) FROM listings WHERE stipend > 0").fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM listings WHERE status='applied'").fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM listings WHERE status='interview'").fetchone()[0]
    return {"total": total, "paid_internships": paid, "applied": applied, "interviews": interviews}
