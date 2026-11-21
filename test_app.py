import unittest
from app import app


class CivicResolveTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_page_loads(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CivicResolve", res.data)

    def test_get_issues(self):
        res = self.client.get("/api/issues")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.get_json(), list)

    def test_create_and_update_issue(self):
        res = self.client.post("/api/issues", json={
            "title": "Blocked storm drain near bus stand",
            "description": "Heavy water accumulation blocking pedestrians.",
            "location": "Neelambur Main Road",
            "category": "Auto-Detect (AI)"
        })
        self.assertEqual(res.status_code, 201)
        issue = res.get_json()
        self.assertIn("priority", issue)

        patch_res = self.client.patch(f"/api/issues/{issue['id']}/status", json={"status": "Resolved"})
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.get_json()["status"], "Resolved")

    def test_validation_rejects_empty_fields(self):
        res = self.client.post("/api/issues", json={"title": "", "description": "", "location": ""})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
