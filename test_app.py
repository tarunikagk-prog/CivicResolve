import unittest
from app import app, fallback_triage, validate_image_data


class CivicResolveTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_home_and_health_endpoints(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"CivicResolve", res.data)
        self.assertEqual(res.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(res.headers.get("X-Content-Type-Options"), "nosniff")

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "healthy")

    def test_get_issues_returns_list(self):
        res = self.client.get("/api/issues")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 3)

    def test_create_sanitize_and_update_issue_lifecycle(self):
        res = self.client.post("/api/issues", json={
            "title": "<script>alert(1)</script>Blocked storm drain",
            "description": "Heavy water accumulation blocking pedestrians near school.",
            "location": "Neelambur Main Road",
            "category": "Auto-Detect (AI)"
        })
        self.assertEqual(res.status_code, 201)
        issue = res.get_json()
        self.assertNotIn("<script>", issue["title"])
        self.assertIn(issue["priority"], {"Low", "Medium", "High", "Critical"})

        patch_res = self.client.patch(f"/api/issues/{issue['id']}/status", json={"status": "Resolved"})
        self.assertEqual(patch_res.status_code, 200)
        self.assertEqual(patch_res.get_json()["status"], "Resolved")

    def test_validation_and_error_handling(self):
        bad_post = self.client.post("/api/issues", json={"title": "", "description": "", "location": ""})
        self.assertEqual(bad_post.status_code, 400)

        bad_status = self.client.patch("/api/issues/CR-101/status", json={"status": "UnknownStatus"})
        self.assertEqual(bad_status.status_code, 400)

        missing_issue = self.client.patch("/api/issues/CR-DOES-NOT-EXIST/status", json={"status": "Resolved"})
        self.assertEqual(missing_issue.status_code, 404)

    def test_triage_and_image_security_helpers(self):
        triage = fallback_triage("Water pipe burst", "Flooding street near hospital", "Auto-Detect (AI)")
        self.assertEqual(triage["category"], "Water Supply & Drainage")
        self.assertEqual(triage["priority"], "Critical")
        self.assertEqual(validate_image_data("javascript:alert(1)"), "")


if __name__ == "__main__":
    unittest.main()
