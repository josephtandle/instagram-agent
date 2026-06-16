import importlib.util
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAIN_PATH = "/Users/myos/.myos/workspace/agents/instagram/src/main.py"
TRANSCRIBER_PATH = "/Users/myos/.myos/workspace/agents/instagram/transcriber/src/main.py"


class InstagramGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.main = load_module(MAIN_PATH, "instagram_main_test")
        self.transcriber = load_module(TRANSCRIBER_PATH, "instagram_transcriber_test")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

        self.main.USAGE_PATH = self.base / "usage.json"
        self.main.HEALTH_PATH = self.base / "health.json"

        self.transcriber.HEALTH_PATH = self.base / "transcriber-health.json"
        self.transcriber.SESSIONS_DIR = self.base / "sessions"
        self.transcriber.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cooldown_blocks_actions(self):
        self.main.set_account_cooldown("joe", 3600, "rate_limited", "read_dms", "429")
        with self.assertRaises(SystemExit):
            self.main.enforce_action_guard("joe", "dm_thread_reads")

    def test_budget_check_uses_caps(self):
        today = self.main._utc_now().strftime("%Y-%m-%d")
        self.main.USAGE_PATH.write_text(json.dumps({
            today: {"joe": {"sent_dms": 20}}
        }))
        with self.assertRaises(SystemExit):
            self.main.check_daily_cap("joe", "sent_dms")

    def test_transcriber_refuses_anonymous_downloads(self):
        with mock.patch.dict(self.transcriber.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                self.transcriber.build_loader()
        self.assertIn("Anonymous Instagram downloads are disabled", str(ctx.exception))

    def test_transcriber_cooldown_activates_and_expires(self):
        self.transcriber.set_download_cooldown(3600, "forbidden_response", "forbidden")
        active = self.transcriber.get_active_download_cooldown()
        self.assertIsNotNone(active)

        expired = {
            "blocked_until": (self.transcriber._utc_now() - timedelta(minutes=1)).isoformat(),
            "reason": "expired",
            "trigger": "test",
        }
        self.transcriber.HEALTH_PATH.write_text(json.dumps(expired))
        self.assertIsNone(self.transcriber.get_active_download_cooldown())


if __name__ == "__main__":
    unittest.main()
