# JobPilot AI

A resume-aware job and internship discovery workspace built with FastAPI, SQLite and a lightweight browser dashboard.

## Features implemented
- Resume upload and text extraction for PDF, DOCX and TXT.
- Skill detection from a curated technology vocabulary.
- Editable candidate skills, target roles, preferred location and paid-internship preference.
- Per-listing match scores with explanations based on skill, role and location overlap.
- CSV job-listing import, deduplication, filters and application status tracking.
- Excludes marketing, sales, business development and telecalling by default.
- Health endpoint at \`/health\`; JSON APIs under \`/api\`.

## Run locally
\`\`\`bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
\`\`\`
Open http://127.0.0.1:8000.

## Import listings
Upload a UTF-8 CSV with columns:
\`title, company, platform, location, role_type, stipend, salary, url, description, posted_date\`.
For paid internships, stipend must be a positive numeric amount. Missing or unknown stipend is not considered paid.

## Resume privacy and limitations
The uploaded resume is processed in memory; the original file and extracted full text are not persisted. Only detected skills are saved in the local candidate profile. Scanned/image-only PDFs need OCR before text can be extracted. Skill detection uses a curated vocabulary and should be reviewed by the candidate.

## Live portals and applications
This version does not claim live search or automatic application submission on external job boards. Connectors must use permitted official APIs/feeds or user-authorized workflows and follow each platform's terms, rate limits and authentication requirements. CAPTCHA/OTP must never be bypassed. The applicant should review each application and its answers before submission.

## Deployment
Set a persistent writable data directory using \`JOBPILOT_DATA_DIR\` so SQLite data survives restarts. Use a managed database instead of SQLite for multi-instance production deployments. Configure HTTPS and authentication before exposing a deployment publicly. Do not commit API keys, credentials, session cookies or resumes.
