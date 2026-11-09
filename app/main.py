from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
RETENTION_DAYS = 20

app = FastAPI(title="JobPilot AI", version="1.0.0",
              description="Personal job discovery and application command center")

EXCLUDED_TERMS = ("marketing", "sales", "business development", "telecalling",
                  "cold calling", "field sales", "door to door sales")
SKILL_CATALOG = (
    "python", "java", "javascript", "typescript", "c++", "c#", "html", "css",
    "react", "react native", "node.js", "express", "fastapi", "django", "flask",
    "sql", "mysql", "postgresql", "mongodb", "mongoose", "firebase", "git",
    "github", "jenkins", "docker", "kubernetes", "linux", "aws", "azure", "gcp",
    "devops", "cloud computing", "ci/cd", "flutter", "dart", "figma", "ui/ux",
    "machine learning", "deep learning", "artificial intelligence", "pytorch",
    "tensorflow", "scikit-learn", "data science", "data analysis", "pandas",
    "numpy", "opencv", "computer vision", "cybersecurity", "networking",
    "rest api", "api development", "tailwind css"
)
DEFAULT_TARGET_ROLES = [
    "python developer", "full stack developer", "react developer",
    "frontend developer", "backend developer", "ai/ml intern", "devops engineer",
    "cloud engineer", "software developer", "data science intern",
    "flutter developer", "cybersecurity intern"
]
PLATFORMS = ["LinkedIn", "Internshala", "Naukri", "Indeed", "Foundit",
             "Glassdoor", "Freshersworld", "Other"]
APPLICATION_STATUSES = ["Applied", "Interview", "Selected", "Rejected", "Saved", "In Progress"]


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    headline: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None
    skills: list[str] | None = None
    target_roles: list[str] | None = None
    preferred_location: str | None = None
    paid_internships_only: bool | None = None
    exclude_terms: list[str] | None = None


class ApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=180)
    platform: str = "Other"
    status: str = "Applied"
    url: str = ""
    location: str = ""
    applied_on: str = ""
    notes: str = ""


class ApplicationUpdate(BaseModel):
    company: str | None = None
    role: str | None = None
    platform: str | None = None
    status: str | None = None
    url: str | None = None
    location: str | None = None
    applied_on: str | None = None
    notes: str | None = None


class MemorySettings(BaseModel):
    enabled: bool


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(" + table + ")")}


def init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            company TEXT DEFAULT '', platform TEXT DEFAULT '', location TEXT DEFAULT '',
            role_type TEXT DEFAULT 'job', stipend REAL, salary TEXT DEFAULT '',
            url TEXT DEFAULT '', description TEXT DEFAULT '', posted_date TEXT DEFAULT '',
            status TEXT DEFAULT 'saved', match_score INTEGER DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL, role TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'Other',
            status TEXT NOT NULL DEFAULT 'Applied', url TEXT DEFAULT '', location TEXT DEFAULT '',
            applied_on TEXT DEFAULT '', notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            event_type TEXT NOT NULL, details TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        """)
        profile_cols = columns(conn, "candidate_profile")
        for name, definition in {
            "full_name": "TEXT NOT NULL DEFAULT ''",
            "email": "TEXT NOT NULL DEFAULT ''",
            "headline": "TEXT NOT NULL DEFAULT ''",
            "location": "TEXT NOT NULL DEFAULT ''",
            "linkedin_url": "TEXT NOT NULL DEFAULT ''",
            "portfolio_url": "TEXT NOT NULL DEFAULT ''"
        }.items():
            if name not in profile_cols:
                conn.execute("ALTER TABLE candidate_profile ADD COLUMN " + name + " " + definition)
        conn.execute(
            """INSERT OR IGNORE INTO candidate_profile
            (id, skills_json, target_roles_json, preferred_location,
             paid_internships_only, exclude_terms_json)
            VALUES (1, ?, ?, 'Hyderabad', 0, ?)""",
            (json.dumps([]), json.dumps(DEFAULT_TARGET_ROLES), json.dumps(list(EXCLUDED_TERMS)))
        )
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('memory_enabled','1')")
        conn.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('last_cleanup','')")
        conn.commit()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def run_retention_cleanup(force: bool = False) -> dict[str, Any]:
    with closing(connect()) as conn:
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM app_settings")}
        enabled = settings.get("memory_enabled", "1") == "1"
        last = settings.get("last_cleanup", "")
        today = utc_now().date().isoformat()
        if not enabled:
            return {"enabled": False, "deleted_events": 0, "last_cleanup": last,
                    "retention_days": RETENTION_DAYS}
        if not force and last[:10] == today:
            return {"enabled": True, "deleted_events": 0, "last_cleanup": last,
                    "retention_days": RETENTION_DAYS}
        cutoff = (utc_now() - timedelta(days=RETENTION_DAYS)).isoformat()
        cur = conn.execute("DELETE FROM application_events WHERE created_at < ?", (cutoff,))
        conn.execute("UPDATE app_settings SET value=? WHERE key='last_cleanup'", (iso_now(),))
        conn.commit()
        return {"enabled": True, "deleted_events": cur.rowcount, "last_cleanup": iso_now(),
                "retention_days": RETENTION_DAYS}


@app.on_event("startup")
def startup() -> None:
    init_db()
    run_retention_cleanup()


def get_profile() -> dict[str, Any]:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM candidate_profile WHERE id=1").fetchone()
    if not row:
        return {}
    return {
        "full_name": row["full_name"], "email": row["email"], "headline": row["headline"],
        "location": row["location"], "linkedin_url": row["linkedin_url"],
        "portfolio_url": row["portfolio_url"], "skills": json.loads(row["skills_json"]),
        "target_roles": json.loads(row["target_roles_json"]),
        "preferred_location": row["preferred_location"],
        "paid_internships_only": bool(row["paid_internships_only"]),
        "exclude_terms": json.loads(row["exclude_terms_json"]),
        "updated_at": row["updated_at"]
    }


def valid_optional_url(value: str | None, label: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail=label + " must be a valid http(s) URL.")
    return value


def update_profile(values: dict[str, Any]) -> dict[str, Any]:
    current = get_profile()
    current.update({k: v for k, v in values.items() if v is not None})
    current["linkedin_url"] = valid_optional_url(current.get("linkedin_url"), "LinkedIn URL")
    current["portfolio_url"] = valid_optional_url(current.get("portfolio_url"), "Portfolio URL")
    for key in ("skills", "target_roles", "exclude_terms"):
        current[key] = list(dict.fromkeys(str(x).strip().lower()
                              for x in current.get(key, []) if str(x).strip()))
    with closing(connect()) as conn:
        conn.execute(
            """UPDATE candidate_profile SET full_name=?,email=?,headline=?,location=?,
            linkedin_url=?,portfolio_url=?,skills_json=?,target_roles_json=?,
            preferred_location=?,paid_internships_only=?,exclude_terms_json=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=1""",
            (current.get("full_name", ""), current.get("email", ""),
             current.get("headline", ""), current.get("location", ""),
             current["linkedin_url"], current["portfolio_url"],
             json.dumps(current["skills"]), json.dumps(current["target_roles"]),
             current.get("preferred_location", ""), int(bool(current.get("paid_internships_only"))),
             json.dumps(current["exclude_terms"]))
        )
        conn.commit()
    return get_profile()


def extract_skills(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower())
    return [skill for skill in SKILL_CATALOG
            if re.search(r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])", normalized)]


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
        raise ValueError("Could not read this resume. Try PDF or DOCX.") from exc
    raise ValueError("Supported resume formats are PDF, DOCX and TXT.")


def score_listing(row: dict[str, Any], profile: dict[str, Any]) -> tuple[int, list[str]]:
    searchable = " ".join(str(row.get(k) or "") for k in ("title", "description", "company")).lower()
    skills = profile.get("skills") or []
    targets = profile.get("target_roles") or []
    matched_skills = [s for s in skills if s.lower() in searchable]
    matched_roles = [r for r in targets if r.lower() in searchable]
    reasons = []
    if matched_skills:
        reasons.append("Skills: " + ", ".join(matched_skills[:6]))
    if matched_roles:
        reasons.append("Role match: " + ", ".join(matched_roles[:2]))
    score = min(70, round(70 * len(matched_skills) / max(1, min(5, len(skills))))) if skills else 0
    score += min(20, 10 * len(matched_roles))
    preferred = str(profile.get("preferred_location") or "").strip().lower()
    if preferred and preferred in str(row.get("location") or "").lower():
        score += 10
        reasons.append("Preferred location")
    if not skills and not targets:
        reasons.append("Add skills to personalize matching")
    return min(100, score), reasons


def normalize_listing(row: dict[str, Any]) -> dict[str, Any]:
    title = str(row.get("title") or "").strip()
    if not title:
        raise ValueError("Each listing must have a title.")
    stipend_raw = str(row.get("stipend") or "").strip()
    match = re.search(r"\d+(?:\.\d+)?", re.sub(r"[₹,\s]", "", stipend_raw))
    return {
        "title": title, "company": str(row.get("company") or "").strip(),
        "platform": str(row.get("platform") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "role_type": str(row.get("role_type") or "job").strip().lower(),
        "stipend": float(match.group()) if match else None,
        "salary": str(row.get("salary") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "posted_date": str(row.get("posted_date") or "").strip(),
        "status": str(row.get("status") or "saved").strip().lower()
    }


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible alias for CSV listing normalization."""
    return normalize_listing(row)


def save_listing(item: dict[str, Any]) -> bool:
    with closing(connect()) as conn:
        cur = conn.execute("""INSERT OR IGNORE INTO listings
        (title,company,platform,location,role_type,stipend,salary,url,description,posted_date,status)
        VALUES (:title,:company,:platform,:location,:role_type,:stipend,:salary,:url,:description,:posted_date,:status)""", item)
        conn.commit()
        return cur.rowcount > 0


def add_event(conn: sqlite3.Connection, application_id: int, event_type: str, details: str = "") -> None:
    conn.execute("INSERT INTO application_events(application_id,event_type,details,created_at) VALUES(?,?,?,?)",
                 (application_id, event_type, details, iso_now()))


def application_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (BASE_DIR / "app" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "JobPilot AI", "version": "1.0.0"}


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
        raise HTTPException(status_code=422, detail="No readable text found. Scanned PDFs need OCR first.")
    skills = extract_skills(text)
    profile = update_profile({"skills": skills})
    return {"message": "Resume parsed. Review the detected skills before saving.",
            "detected_skills": skills, "characters_read": len(text),
            "profile": profile, "note": "The original resume is processed in memory and is not saved."}


@app.get("/api/listings")
def get_listings(q: str = "", location: str = "", paid_only: bool = False,
                 include_internships: bool = True, min_score: int = 0) -> list[dict[str, Any]]:
    profile = get_profile()
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM listings ORDER BY created_at DESC").fetchall()
    results = []
    excluded = [x.lower() for x in profile.get("exclude_terms", list(EXCLUDED_TERMS))]
    for raw in rows:
        item = dict(raw)
        searchable = f"{item['title']} {item['company']} {item['description']}".lower()
        if any(term and term in searchable for term in excluded):
            continue
        if q and q.lower() not in searchable:
            continue
        if location and location.lower() not in item["location"].lower():
            continue
        internship = item["role_type"] in {"internship", "intern"}
        if paid_only and (item["stipend"] is None or item["stipend"] <= 0):
            continue
        if internship and profile.get("paid_internships_only") and (item["stipend"] is None or item["stipend"] <= 0):
            continue
        if not include_internships and internship:
            continue
        score, reasons = score_listing(item, profile)
        item["match_score"], item["match_reasons"] = score, reasons
        if score >= min_score:
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
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if not reader.fieldnames or "title" not in {h.strip().lower() for h in reader.fieldnames}:
            raise ValueError("CSV must contain a title column.")
        inserted = skipped = 0
        for raw_row in reader:
            row = {str(k).strip().lower(): v for k, v in raw_row.items() if k}
            try:
                was_inserted = save_listing(normalize_listing(row))
                if was_inserted:
                    inserted += 1
                else:
                    skipped += 1
            except ValueError:
                skipped += 1
        return JSONResponse({"inserted": inserted, "skipped": skipped})
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/applications")
def list_applications(q: str = "", status: str = "", platform: str = "") -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM applications ORDER BY updated_at DESC,id DESC").fetchall()
    items = [application_dict(r) for r in rows]
    if q:
        ql = q.lower()
        items = [x for x in items if ql in (x["company"] + " " + x["role"] + " " + x["notes"]).lower()]
    if status:
        items = [x for x in items if x["status"].lower() == status.lower()]
    if platform:
        items = [x for x in items if x["platform"].lower() == platform.lower()]
    return items


@app.post("/api/applications", status_code=201)
def create_application(payload: ApplicationCreate) -> dict[str, Any]:
    status = payload.status.strip().title()
    if status not in APPLICATION_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported application status.")
    platform = payload.platform.strip()
    if platform not in PLATFORMS:
        platform = "Other"
    url = valid_optional_url(payload.url, "Application URL")
    applied_on = payload.applied_on.strip() or utc_now().date().isoformat()
    with closing(connect()) as conn:
        cur = conn.execute("""INSERT INTO applications
        (company,role,platform,status,url,location,applied_on,notes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (payload.company.strip(), payload.role.strip(), platform, status, url,
         payload.location.strip(), applied_on, payload.notes.strip(), iso_now(), iso_now()))
        app_id = cur.lastrowid
        add_event(conn, app_id, "Created", "Application added as " + status)
        conn.commit()
        row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    return application_dict(row)


@app.patch("/api/applications/{application_id}")
def edit_application(application_id: int, payload: ApplicationUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    with closing(connect()) as conn:
        old = conn.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="Application not found.")
        updates = dict(old)
        updates.update({k: v for k, v in values.items() if v is not None})
        if "status" in values:
            updates["status"] = str(updates["status"]).strip().title()
            if updates["status"] not in APPLICATION_STATUSES:
                raise HTTPException(status_code=422, detail="Unsupported application status.")
        if "platform" in values and updates["platform"] not in PLATFORMS:
            updates["platform"] = "Other"
        updates["url"] = valid_optional_url(updates.get("url"), "Application URL")
        conn.execute("""UPDATE applications SET company=?,role=?,platform=?,status=?,url=?,
        location=?,applied_on=?,notes=?,updated_at=? WHERE id=?""",
        (updates["company"], updates["role"], updates["platform"], updates["status"],
         updates["url"], updates["location"], updates["applied_on"], updates["notes"],
         iso_now(), application_id))
        if updates["status"] != old["status"]:
            add_event(conn, application_id, "Status changed",
                      str(old["status"]) + " → " + str(updates["status"]))
        conn.commit()
        row = conn.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
    return application_dict(row)


@app.delete("/api/applications/{application_id}")
def delete_application(application_id: int) -> dict[str, Any]:
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM applications WHERE id=?", (application_id,))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Application not found.")
        conn.execute("DELETE FROM application_events WHERE application_id=?", (application_id,))
        conn.commit()
    return {"deleted": True, "id": application_id}


@app.get("/api/applications/{application_id}/activity")
def application_activity(application_id: int) -> list[dict[str, Any]]:
    run_retention_cleanup()
    with closing(connect()) as conn:
        exists = conn.execute("SELECT id FROM applications WHERE id=?", (application_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Application not found.")
        rows = conn.execute("SELECT event_type,details,created_at FROM application_events WHERE application_id=? ORDER BY id DESC",
                            (application_id,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/memory")
def memory_status() -> dict[str, Any]:
    run_retention_cleanup()
    with closing(connect()) as conn:
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM app_settings")}
        count = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
    last = settings.get("last_cleanup", "")
    next_cleanup = ""
    if settings.get("memory_enabled", "1") == "1":
        try:
            next_cleanup = (datetime.fromisoformat(last) + timedelta(days=1)).isoformat() if last else iso_now()
        except ValueError:
            next_cleanup = iso_now()
    return {"enabled": settings.get("memory_enabled", "1") == "1",
            "retention_days": RETENTION_DAYS, "stored_events": count,
            "last_cleanup": last, "next_cleanup": next_cleanup,
            "scope": "Only JobPilot internal activity history. Application records and external job-portal accounts are not deleted."}


@app.put("/api/memory")
def update_memory(payload: MemorySettings) -> dict[str, Any]:
    with closing(connect()) as conn:
        conn.execute("UPDATE app_settings SET value=? WHERE key='memory_enabled'",
                     ("1" if payload.enabled else "0",))
        conn.commit()
    if payload.enabled:
        run_retention_cleanup(force=True)
    return memory_status()


@app.post("/api/memory/cleanup")
def cleanup_memory() -> dict[str, Any]:
    result = run_retention_cleanup(force=True)
    return {**result, "message": "Expired internal activity history cleaned. Application records were preserved."}


@app.patch("/api/listings/{listing_id}/status")
def update_listing_status(listing_id: int, status: str = Form(...)) -> dict[str, Any]:
    allowed = {"saved", "preparing", "applied", "interview", "rejected", "offer"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported listing status.")
    with closing(connect()) as conn:
        cur = conn.execute("UPDATE listings SET status=? WHERE id=?", (status, listing_id))
        conn.commit()
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Listing not found.")
    return {"id": listing_id, "status": status}


@app.get("/api/stats")
def stats() -> dict[str, int]:
    with closing(connect()) as conn:
        listing_total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        app_total = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM applications WHERE status='Applied'").fetchone()[0]
        interviews = conn.execute("SELECT COUNT(*) FROM applications WHERE status='Interview'").fetchone()[0]
        selected = conn.execute("SELECT COUNT(*) FROM applications WHERE status='Selected'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM applications WHERE status='Rejected'").fetchone()[0]
    return {"listings": listing_total, "applications": app_total, "applied": applied,
            "interviews": interviews, "selected": selected, "rejected": rejected}
