from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from docx import Document
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("JOBPILOT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobpilot.sqlite3"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

app = FastAPI(
    title="JobPilot AI",
    version="0.2.0",
    description="Resume-aware job and internship discovery and application tracking assistant",
)

EXCLUDED_TERMS = (
    "marketing", "sales", "business development", "telecalling",
    "cold calling", "field sales", "door to door sales",
)
SKILL_CATALOG = (
    "python", "java", "javascript", "typescript", "c++", "c#", "html", "css",
    "react", "react native", "node.js", "express", "fastapi", "django", "flask",
    "sql", "mysql", "postgresql", "mongodb", "mongoose", "firebase",
    "git", "github", "jenkins", "docker", "kubernetes", "linux", "aws",
    "azure", "gcp", "devops", "cloud computing", "ci/cd", "tomcat",
    "websocket", "socket.io", "flutter", "dart", "figma", "ui/ux",
    "machine learning", "deep learning", "artificial intelligence", "pytorch",
    "tensorflow", "scikit-learn", "data science", "data analysis",
    "pandas", "numpy", "opencv", "computer vision", "cybersecurity",
    "networking", "rest api", "api development", "fastapi", "tailwind css",
)
DEFAULT_TARGET_ROLES = [
    "python developer", "full stack developer", "react developer",
    "frontend developer", "backend developer", "ai/ml intern",
    "devops engineer", "cloud engineer", "software developer",
    "data science intern", "flutter developer", "cybersecurity intern",
]
DEFAULT_EXCLUDED = list(EXCLUDED_TERMS)


class ProfileUpdate(BaseModel):
    skills: list[str] | None = None
    target_roles: list[str] | None = None
    preferred_location: str | None = None
    paid_internships_only: bool | None = None
    exclude_terms: list[str] | None = None


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
        CREATE TABLE IF NOT EXISTS candidate_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            skills_json TEXT NOT NULL DEFAULT '[]',
            target_roles_json TEXT NOT NULL DEFAULT '[]',
            preferred_location TEXT NOT NULL DEFAULT 'Hyderabad',
            paid_internships_only INTEGER NOT NULL DEFAULT 0,
            exclude_terms_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute(
            """INSERT OR IGNORE INTO candidate_profile
            (id, skills_json, target_roles_json, preferred_location,
             paid_internships_only, exclude_terms_json)
            VALUES (1, ?, ?, ?, 0, ?)""",
            (
                json.dumps([]),
                json.dumps(DEFAULT_TARGET_ROLES),
                "Hyderabad",
                json.dumps(DEFAULT_EXCLUDED),
            ),
        )
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


def get_profile() -> dict[str, Any]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone()
    if not row:
        return {
            "skills": [], "target_roles": DEFAULT_TARGET_ROLES,
            "preferred_location": "Hyderabad", "paid_internships_only": False,
            "exclude_terms": DEFAULT_EXCLUDED,
        }
    return {
        "skills": json.loads(row["skills_json"]),
        "target_roles": json.loads(row["target_roles_json"]),
        "preferred_location": row["preferred_location"],
        "paid_internships_only": bool(row["paid_internships_only"]),
        "exclude_terms": json.loads(row["exclude_terms_json"]),
        "updated_at": row["updated_at"],
    }


def update_profile(values: dict[str, Any]) -> dict[str, Any]:
    current = get_profile()
    current.update({k: v for k, v in values.items() if v is not None})
    for key in ("skills", "target_roles", "exclude_terms"):
        current[key] = list(dict.fromkeys(
            str(x).strip().lower() for x in current.get(key, []) if str(x).strip()
        ))
    with closing(connect()) as conn:
        conn.execute(
            """UPDATE candidate_profile SET skills_json=?, target_roles_json=?,
            preferred_location=?, paid_internships_only=?, exclude_terms_json=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=1""",
            (
                json.dumps(current["skills"]),
                json.dumps(current["target_roles"]),
                str(current.get("preferred_location") or "").strip(),
                int(bool(current.get("paid_internships_only"))),
                json.dumps(current["exclude_terms"]),
            ),
        )
        conn.commit()
    return get_profile()


def extract_skills(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    found = []
    for skill in SKILL_CATALOG:
        pattern = r"(?<![a-z0-9])" + re.escape(skill.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, normalized) and skill not in found:
            found.append(skill)
    return found


def extract_resume_text(filename: str, raw: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if suffix == ".docx":
            doc = Document(io.BytesIO(raw))
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    parts.append(" ".join(cell.text for cell in row.cells))
            return "\n".join(parts)
        if suffix == ".txt":
            return raw.decode("utf-8-sig")
    except Exception as exc:
        raise ValueError("Could not read this resume file. Try exporting it as PDF or DOCX.") from exc
    raise ValueError("Supported resume formats are PDF, DOCX and TXT.")


def score_listing(row: dict[str, Any], profile: dict[str, Any]) -> tuple[int, list[str]]:
    searchable = " ".join(
        str(row.get(k) or "") for k in ("title", "description", "company")
    ).lower()
    skills = profile.get("skills") or []
    targets = profile.get("target_roles") or []
    reasons: list[str] = []
    matched_skills = [s for s in skills if s.lower() in searchable]
    if matched_skills:
        reasons.append("Skills: " + ", ".join(matched_skills[:6]))
    skill_score = min(70, round(70 * len(matched_skills) / max(1, min(5, len(skills))))) if skills else 0

    matched_roles = [r for r in targets if r.lower() in searchable]
    if matched_roles:
        reasons.append("Role match: " + ", ".join(matched_roles[:2]))
    role_score = min(20, 10 * len(matched_roles))

    preferred = str(profile.get("preferred_location") or "").strip().lower()
    listing_location = str(row.get("location") or "").lower()
    location_score = 10 if preferred and preferred in listing_location else 0
    if location_score:
        reasons.append("Preferred location")

    score = min(100, skill_score + role_score + location_score)
    if not skills and not targets:
        score = 0
        reasons.append("Upload a resume or add skills to personalize matching")
    return score, reasons


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    if not title:
        raise ValueError("Each listing must have a title.")
    stipend_raw = str(row.get("stipend") or "").strip()
    stipend = None
    if stipend_raw:
        cleaned = re.sub(r"[₹,\s]", "", stipend_raw)
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        stipend = float(match.group()) if match else None
    return {
        "title": title,
        "company": str(row.get("company") or "").strip(),
        "platform": str(row.get("platform") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "role_type": str(row.get("role_type") or "job").strip().lower(),
        "stipend": stipend,
        "salary": str(row.get("salary") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "posted_date": str(row.get("posted_date") or "").strip(),
        "status": str(row.get("status") or "saved").strip().lower(),
    }


def save_listing(item: dict[str, Any]) -> bool:
    with closing(connect()) as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO listings
            (title, company, platform, location, role_type, stipend, salary, url,
             description, posted_date, status)
            VALUES (:title, :company, :platform, :location, :role_type, :stipend,
                    :salary, :url, :description, :posted_date, :status)
        """, item)
        conn.commit()
        return cur.rowcount > 0


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (BASE_DIR / "app" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "JobPilot AI", "version": "0.2.0"}


@app.get("/api/profile")
def read_profile() -> dict[str, Any]:
    return get_profile()


@app.put("/api/profile")
def write_profile(payload: ProfileUpdate) -> dict[str, Any]:
    return update_profile(payload.model_dump(exclude_unset=True))


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Choose a resume file.")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Resume must be smaller than 8 MB.")
    try:
        text = extract_resume_text(file.filename, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(text.strip()) < 30:
        raise HTTPException(
            status_code=422,
            detail="No readable text found. If this is a scanned PDF, export a text-based PDF or DOCX.",
        )
    skills = extract_skills(text)
    profile = update_profile({"skills": skills})
    return {
        "message": "Resume parsed. Review the detected skills and edit them if needed.",
        "detected_skills": skills,
        "characters_read": len(text),
        "profile": profile,
        "note": "The uploaded file is processed in memory; the original resume is not saved.",
    }


@app.get("/api/listings")
def get_listings(
    q: str = "",
    location: str = "",
    paid_only: bool = False,
    include_internships: bool = True,
    min_score: int = 0,
) -> list[dict[str, Any]]:
    profile = get_profile()
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM listings ORDER BY created_at DESC").fetchall()
    results = []
    excluded = [x.lower() for x in profile.get("exclude_terms", DEFAULT_EXCLUDED)]
    for raw in rows:
        item = dict(raw)
        searchable = f"{item['title']} {item['company']} {item['description']}".lower()
        if any(term and term in searchable for term in excluded):
            continue
        if q and q.lower() not in searchable:
            continue
        if location and location.lower() not in item["location"].lower():
            continue
        is_internship = item["role_type"] in {"internship", "intern"}
        if paid_only and (item["stipend"] is None or item["stipend"] <= 0):
            continue
        if is_internship and profile.get("paid_internships_only"):
            if item["stipend"] is None or item["stipend"] <= 0:
                continue
        if not include_internships and is_internship:
            continue
        score, reasons = score_listing(item, profile)
        item["match_score"] = score
        item["match_reasons"] = reasons
        if score < min_score:
            continue
        results.append(item)
    results.sort(key=lambda x: (x["match_score"], x["created_at"]), reverse=True)
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
        inserted = skipped = 0
        for raw_row in reader:
            row = {str(k).strip().lower(): v for k, v in raw_row.items() if k}
            try:
                if save_listing(normalize(row)):
                    inserted += 1
                else:
                    skipped += 1
            except ValueError:
                skipped += 1
        return JSONResponse({"inserted": inserted, "skipped": skipped})
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.") from exc


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
        paid = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE role_type IN ('internship','intern') AND stipend > 0"
        ).fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM listings WHERE status='applied'").fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM listings WHERE status='interview'").fetchone()[0]
    return {"total": total, "paid_internships": paid, "applied": applied, "interviews": interviews}
