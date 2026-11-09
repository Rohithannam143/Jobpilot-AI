import tempfile
import unittest
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
            "skills": ["python", "react"],
            "target_roles": ["python developer"],
            "preferred_location": "Hyderabad",
            "paid_internships_only": True,
        })
        self.assertEqual(profile["skills"], ["python", "react"])
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


if __name__ == "__main__":
    unittest.main()
