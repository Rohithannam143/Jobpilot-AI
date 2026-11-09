# JobPilot AI — Multi-Platform Job & Internship Assistant

JobPilot AI helps you collect job/internship listings, filter out irrelevant roles, rank matches against your skills, and track applications.

## Current MVP
- FastAPI backend with a lightweight SQLite database.
- Dashboard for viewing, filtering, and tracking listings.
- CSV import for listings collected from job boards.
- Match scoring based on skills and preferred role keywords.
- Filters for location, paid internships, salary/stipend, and excluded job categories.
- Application tracker (saved, preparing, applied, interview, rejected, offer).

## Important integration boundary
This repository does **not** currently scrape or submit applications to job portals automatically. Portal integrations must use permitted APIs, feeds, or user-authorized workflows and respect each site's terms, rate limits, CAPTCHA and authentication requirements. Never bypass CAPTCHA/OTP or submit applications without the applicant reviewing the answers.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Import listings
Upload a CSV with these optional columns:
`title, company, platform, location, role_type, stipend, salary, url, description, posted_date`

A stipend should be a positive numeric monthly amount to qualify as paid. Blank/unknown stipend is not treated as paid.

## Environment
No API key is required for the MVP. Do not commit passwords, API keys, session cookies, or personal identity documents.
