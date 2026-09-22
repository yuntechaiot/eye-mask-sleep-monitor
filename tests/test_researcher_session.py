"""Regression tests for the researcher's fixed 30-minute login window."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from researcher_session import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    ResearcherSessionStore,
    cookie_script,
)


class ResearcherSessionTests(unittest.TestCase):
    def test_refresh_does_not_extend_expiration(self):
        now = [1_000.0]
        sessions = ResearcherSessionStore(clock=lambda: now[0])
        session_id, session = sessions.create("test-token", "researcher")

        now[0] += SESSION_TTL_SECONDS - 1
        self.assertEqual(sessions.get(session_id), session)
        now[0] += 1
        self.assertIsNone(sessions.get(session_id))

    def test_logout_revokes_session(self):
        sessions = ResearcherSessionStore()
        session_id, _ = sessions.create("test-token", "researcher")
        sessions.revoke(session_id)
        self.assertIsNone(sessions.get(session_id))

    def test_browser_cookie_contains_only_short_lived_session_id(self):
        sessions = ResearcherSessionStore()
        session_id, _ = sessions.create("test-token", "researcher")
        script = cookie_script(session_id)
        self.assertIn(f"{COOKIE_NAME}={session_id}", script)
        self.assertNotIn("test-token", script)
        self.assertNotIn("Max-Age", script)
        self.assertIn("Max-Age=0", cookie_script(None))

    def test_login_fields_use_browser_password_manager_hints(self):
        app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
        app = AppTest.from_file(str(app_path)).run(timeout=10)
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [field.proto.autocomplete for field in app.text_input],
            ["username", "current-password"],
        )
        self.assertEqual(len(app.checkbox), 0)


if __name__ == "__main__":
    unittest.main()
