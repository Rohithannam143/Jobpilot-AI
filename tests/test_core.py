import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app import main


class JobPilotCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = main.DB_PATH
        main.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        main.init_db()

    def tearDown(self):
        main.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    def test_resume_skill_extraction(self):
        skills = main.extract_skills(
            "Built projects using Python, React, FastAPI, SQL, GitHub and Docker."
        )
        self.assertIn("python", skills)
        self.assertIn("react", skills)
        self.assertIn("fastapi", skills)

    def test_profile_is_saved_and_used_for_matching(self):
        profile = main.update_profile({
            "full_name": "Test Candidate",
            "linkedin_url": "https://www.linkedin.com/in/test-candidate",
            "skills": ["python", "react"],
            "target_roles": ["python developer"],
            "preferred_location": "Hyderabad",
            "paid_internships_only": True,
        })
        self.assertEqual(profile["skills"], ["python", "react"])
        self.assertEqual(profile["full_name"], "Test Candidate")
        self.assertTrue(profile["linkedin_url"].startswith("https://"))
        score, reasons = main.score_listing({
            "title": "Python Developer Intern",
            "description": "Python and React development",
            "location": "Hyderabad",
            "role_type": "internship",
        }, profile)
        self.assertGreaterEqual(score, 80)
        self.assertTrue(any("Skills:" in reason for reason in reasons))

    def test_csv_stipend_normalization(self):
        item = main.normalize({
            "title": "Python Intern",
            "role_type": "internship",
            "stipend": "₹5,000",
            "location": "Hyderabad",
        })
        self.assertEqual(item["stipend"], 5000.0)

    def test_duplicate_listing_is_ignored(self):
        item = main.normalize({
            "title": "Backend Intern",
            "company": "Example",
            "platform": "CSV",
            "url": "https://example.com/job/1",
        })
        self.assertTrue(main.save_listing(item))
        self.assertFalse(main.save_listing(item))

    def test_application_create_update_and_delete(self):
        item = main.create_application(main.ApplicationCreate(
            company="Example Ltd", role="Python Developer",
            platform="LinkedIn", status="Applied",
            url="https://example.com/jobs/1"
        ))
        self.assertEqual(item["status"], "Applied")
        updated = main.edit_application(item["id"], main.ApplicationUpdate(status="Interview"))
        self.assertEqual(updated["status"], "Interview")
        self.assertEqual(len(main.application_activity(item["id"])), 2)
        self.assertTrue(main.delete_application(item["id"])["deleted"])
        self.assertEqual(main.list_applications(), [])

    def test_memory_cleanup_only_removes_old_events(self):
        item = main.create_application(main.ApplicationCreate(
            company="Example Ltd", role="QA Intern", platform="Other"
        ))
        old_date = (main.utc_now() - timedelta(days=21)).isoformat()
        with main.connect() as conn:
            conn.execute("UPDATE application_events SET created_at=?", (old_date,))
            conn.commit()
        result = main.run_retention_cleanup(force=True)
        self.assertEqual(result["deleted_events"], 1)
        self.assertEqual(len(main.list_applications()), 1)
        self.assertEqual(main.application_activity(item["id"]), [])

    def test_memory_can_be_disabled(self):
        result = main.update_memory(main.MemorySettings(enabled=False))
        self.assertFalse(result["enabled"])


if __name__ == "__main__":
    unittest.main()
