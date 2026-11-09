# JobPilot AI

A responsive career command center for organizing job discovery, candidate details and application progress. Built with FastAPI, SQLite and a lightweight browser dashboard.

## Product modules

- **Overview:** application totals, status pipeline and recent tracked roles.
- **Application tracker:** add, edit, filter and delete applications by company, role, platform, date, URL, status and notes.
- **Candidate profile:** personal details, professional headline, LinkedIn and portfolio links, skills, target roles and location preferences.
- **Resume parsing:** PDF, DOCX and TXT text extraction with curated technology-skill detection.
- **Job discovery:** CSV listing import, deduplication, filters and explainable skill/role/location matching.
- **Job memory:** optional 20-day cleanup of expired internal activity-history events.
- **Privacy:** original resume files are processed in memory and are not saved.

## Application statuses and platforms

Statuses: Applied, Interview, Selected, Rejected, Saved and In Progress.

Platforms: LinkedIn, Internshala, Naukri, Indeed, Foundit, Glassdoor, Freshersworld and Other.

## 20-day activity retention

When enabled, JobPilot checks the retention policy during startup and normal API requests. It deletes only internal activity-history events older than 20 days. Application tracker entries are preserved until the user explicitly deletes them. The cleanup does not log in to or modify any external job portal and cannot withdraw external applications.

## Run locally

\`\`\`bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
\`\`\`

Open http://127.0.0.1:8000. API documentation is available at http://127.0.0.1:8000/docs.

## Import job listings

Upload a UTF-8 CSV with columns:

\`title, company, platform, location, role_type, stipend, salary, url, description, posted_date\`

For paid internships, stipend must be a positive numeric amount. Missing or unknown stipend is not considered paid.

## API overview

- \`GET/PUT /api/profile\`
- \`POST /api/resume\`
- \`GET /api/listings\`
- \`POST /api/import\`
- \`GET/POST /api/applications\`
- \`PATCH/DELETE /api/applications/{id}\`
- \`GET /api/applications/{id}/activity\`
- \`GET/PUT /api/memory\`
- \`POST /api/memory/cleanup\`
- \`GET /api/stats\`
- \`GET /health\`

## Important limitations and production checklist

- Live job-board search and automatic external application submission are not implemented. Use permitted official APIs/feeds or user-authorized workflows and follow each platform's terms, authentication and rate limits.
- CAPTCHA or OTP protections must never be bypassed. Applicants should review applications and answers before submission.
- Skill detection uses a curated vocabulary and should be reviewed by the candidate.
- Scanned/image-only PDFs require OCR before text can be extracted.
- This starter does not include user authentication or multi-user isolation. Add authentication, authorization, HTTPS and CSRF protections before exposing it publicly.
- Configure a persistent writable directory with \`JOBPILOT_DATA_DIR\`. For multi-instance production deployments, use a managed database instead of SQLite.
- Do not commit API keys, credentials, session cookies or resumes.
