import unittest

from scripts.relogin_web_profiles import is_logged_in


class ReloginProfileVerificationTests(unittest.TestCase):
    def test_chatgpt_home_without_authenticated_session_is_not_success(self):
        snapshot = {"url": "https://chatgpt.com/", "title": "ChatGPT", "body": ""}
        self.assertFalse(is_logged_in(snapshot, {"status": 401, "authenticated": False}))

    def test_authenticated_session_is_required_for_success(self):
        snapshot = {"url": "https://chatgpt.com/", "title": "ChatGPT", "body": ""}
        self.assertTrue(
            is_logged_in(
                snapshot,
                {"status": 200, "authenticated": True, "email": "owner@example.com"},
            )
        )


if __name__ == "__main__":
    unittest.main()
