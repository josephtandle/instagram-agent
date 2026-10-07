"""Joe's HARD RULE (2026-10-07): never DM his friends / close circle to sell.

send-dm must ask the shared close-circle guard first and refuse a close friend
without ever logging in or calling direct_send. Uses a fixture root built by
agents/shared/__tests__/fixtures/close-circle-fixture.js, never live data.
"""
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = REPO_ROOT / "src" / "main.py"
WORKSPACE = Path(os.environ.get("MYOS_WORKSPACE", "/Users/myos/.myos/workspace"))
GUARD = Path(os.environ.get("MYOS_CLOSE_CIRCLE_GUARD", WORKSPACE / "agents" / "shared" / "close-circle-guard.js"))
FIXTURE = GUARD.parent / "__tests__" / "fixtures" / "close-circle-fixture.js"


def load_main():
    spec = importlib.util.spec_from_file_location("instagram_main_cc_test", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(GUARD.exists() and FIXTURE.exists(), "shared close-circle guard not installed")
class CloseCircleSendDmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            ["node", "-e", f"process.stdout.write(require({json.dumps(str(FIXTURE))}).buildFixture())"],
            capture_output=True, text=True, check=True)
        cls.root = out.stdout.strip()

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"MYOS_CLOSE_CIRCLE_ROOT": self.root})
        self.env.start()
        self.main = load_main()
        self.main.CLOSE_CIRCLE_GUARD = str(GUARD)
        self.tmp = tempfile.TemporaryDirectory()
        self.main.USAGE_PATH = Path(self.tmp.name) / "usage.json"
        self.main.HEALTH_PATH = Path(self.tmp.name) / "health.json"
        self.client = mock.MagicMock()
        self.client.user_id_from_username.return_value = "123"
        self.client.direct_send.return_value = SimpleNamespace(id="t1")
        for name in ("human_pause", "typing_delay", "enforce_action_guard", "record_action",
                     "mark_account_success", "write_status"):
            p = mock.patch.object(self.main, name, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(self.main, "get_client", return_value=self.client)
        self.get_client = p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_send(self, handle, name=None):
        args = SimpleNamespace(handle=handle, text="hey, want in on the cohort?", name=name, username="joe")
        buf = io.StringIO()
        code = 0
        with redirect_stdout(buf):
            try:
                self.main.cmd_send_dm(args)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_close_friend_is_refused_before_login(self):
        code, out = self.run_send("@nora.friend")
        self.assertEqual(code, 3)
        self.assertIn("close-circle guard", out)
        self.get_client.assert_not_called()
        self.client.direct_send.assert_not_called()

    def test_name_only_match_is_held(self):
        code, _ = self.run_send("unknown.acct.zz", name="Gil Amir")
        self.assertEqual(code, 3)
        self.client.direct_send.assert_not_called()

    def test_normal_lead_is_sent(self):
        code, out = self.run_send("lena.lead", name="Lena Lead")
        self.assertEqual(code, 0, out)
        self.client.direct_send.assert_called_once()

    def test_guard_unavailable_fails_closed(self):
        self.main.CLOSE_CIRCLE_GUARD = "/nonexistent/close-circle-guard.js"
        code, _ = self.run_send("lena.lead")
        self.assertEqual(code, 3)
        self.client.direct_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
