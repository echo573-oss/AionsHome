import unittest
from pathlib import Path


class DeviceContextUiContractTest(unittest.TestCase):
    def test_activity_page_contains_context_panels_and_status_endpoint(self):
        html = Path("static/activity-logs.html").read_text(encoding="utf-8")
        for marker in (
            "contextPc",
            "contextPhone",
            "contextNotifications",
            "contextPrompt",
        ):
            self.assertIn(marker, html)
        self.assertIn("/api/device-context/status", html)
        self.assertIn("AionDeviceContext", html)


if __name__ == "__main__":
    unittest.main()
