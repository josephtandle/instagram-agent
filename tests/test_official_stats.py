import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError


MAIN_PATH = Path(__file__).resolve().parents[1] / "src" / "main.py"


class OfficialStatsTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("official_stats_test", MAIN_PATH)
        self.main = importlib.util.module_from_spec(spec)
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("dotenv.load_dotenv"):
            spec.loader.exec_module(self.main)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.main.HEALTH_PATH = base / "health.json"
        self.main.STATUS_PATH = base / "status.json"
        self.main.LATEST_OFFICIAL_STATS_PATH = base / "latest.json"
        self.main.META_IG_ACCESS_TOKEN = ""
        self.main.META_IG_ACCOUNT_ID = ""
        self.main.COMPOSIO_API_KEY = "ck_test_secret"
        self.main.COMPOSIO_INSTAGRAM_ACCOUNT = ""
        self.args = argparse.Namespace(username="test_account", include_media=True, source="auto")
        self.requests = []
        self.graph_error = None
        self.graph_requests = []
        self.now = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
        windows = self.main._get_official_windows(self.now)
        window_patch = mock.patch.object(self.main, "_get_official_windows", side_effect=lambda: json.loads(json.dumps(windows)))
        window_patch.start()
        self.addCleanup(window_patch.stop)
        self.http = mock.patch.object(self.main, "urlopen", side_effect=self.respond)
        self.http.start()
        self.addCleanup(self.http.stop)

    def respond(self, request, timeout):
        if request.data is None:
            self.assertEqual(timeout, 30)
            self.graph_requests.append(request.full_url)
            if self.graph_error:
                raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(json.dumps({"error": {"message": self.graph_error}}).encode()))
            response = mock.MagicMock()
            response.__enter__.return_value = response
            data = {"data": []} if "/media?" in request.full_url else {"id": "graph-id", "username": "test_account", "followers_count": 34, "media_count": 0}
            response.read.return_value = json.dumps(data).encode()
            return response
        self.assertEqual(timeout, 120)
        self.assertEqual(request.full_url, "https://connect.composio.dev/mcp")
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data)
        self.requests.append((request, payload))
        method = payload["method"]
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["x-consumer-api-key"], "ck_test_secret")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json, text/event-stream")
        self.assertEqual(headers["mcp-protocol-version"], "2025-06-18")
        if method == "initialize":
            self.assertEqual(payload["id"], 1)
            data = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}
        else:
            self.assertEqual(headers["mcp-session-id"], "test-session")
            if method == "notifications/initialized":
                self.assertNotIn("id", payload)
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = b""
                return response
            self.assertEqual(payload["params"]["name"], "COMPOSIO_MULTI_EXECUTE_TOOL")
            arguments = payload["params"]["arguments"]
            self.assertFalse(arguments["sync_response_to_workbench"])
            self.assertEqual(arguments["current_step"], "OFFICIAL_STATS")
            results = []
            self.assertLessEqual(len(arguments["tools"]), 50)
            for index, tool in enumerate(arguments["tools"]):
                if self.main.COMPOSIO_INSTAGRAM_ACCOUNT:
                    self.assertEqual(tool["account"], self.main.COMPOSIO_INSTAGRAM_ACCOUNT)
                else:
                    self.assertNotIn("account", tool)
                value = self.tool_response(tool)
                results.append({"index": index, "tool_slug": tool["tool_slug"], "response": value})
            data = {"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"text": json.dumps({"successful": True, "data": {"results": results}})}]}}
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Mcp-Session-Id": "test-session"}
        response.read.return_value = ("event: message\ndata: " + json.dumps(data) + "\n\n").encode()
        return response

    def tool_response(self, tool):
        if tool["tool_slug"] == "INSTAGRAM_GET_USER_INFO":
            data = {"id": "connection-id", "user_id": "graph-id", "username": "test_account", "followers_count": 12, "media_count": 0}
        else:
            data = {"data": []}
        return {"successful": True, "data": data}

    def run_stats(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.main.cmd_official_stats(self.args)
        return json.loads(output.getvalue())

    def test_missing_graph_token_uses_composio(self):
        result = self.run_stats()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "composio")
        self.assertEqual(result["account"]["id"], "graph-id")
        self.assertEqual(result["account"]["followers"], 12)
        self.assertEqual([p["method"] for _, p in self.requests][:2], ["initialize", "notifications/initialized"])
        snapshot = json.loads(self.main.LATEST_OFFICIAL_STATS_PATH.read_text())
        self.assertEqual(snapshot, result)
        self.assertEqual(self.graph_requests, [])
        health = json.loads(self.main.HEALTH_PATH.read_text())["accounts"]["test_account"]
        self.assertEqual(health["last_official_stats_source"], "composio")

    def test_sse_parsing_takes_last_data_message(self):
        result = self.main._parse_composio_response(': keepalive\nevent: message\ndata: {"result": "first"}\n\ndata: {"result": "last"}\n\n')
        self.assertEqual(result, {"result": "last"})

    def test_graph_auth_errors_fall_back(self):
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        for error in ("(#10) denied", "(#190) expired", "OAuthException", "Invalid access token", "Permission denied", "Object does not exist"):
            with self.subTest(error=error):
                self.graph_error = error
                result = self.run_stats()
                self.assertEqual(result["source"], "composio")
                self.assertEqual(result["graphError"], "Meta Graph API error: " + error)

    def test_working_graph_path_keeps_graph_source(self):
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        result = self.run_stats()
        self.assertEqual(result["source"], "meta_graph_api")
        self.assertEqual(result["account"]["followers"], 34)
        self.assertNotIn("graphError", result)
        self.assertEqual(len(self.graph_requests), 2)
        self.assertEqual(self.requests, [])

    def test_non_auth_graph_error_also_falls_back(self):
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        self.graph_error = "Service temporarily unavailable"
        with contextlib.redirect_stdout(io.StringIO()):
            self.main.cmd_official_stats(self.args)
        result = json.loads(self.main.LATEST_OFFICIAL_STATS_PATH.read_text())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "composio")
        self.assertIn("Service temporarily unavailable", result["graphError"])
        self.assertNotEqual(self.requests, [])

    def test_forced_graph_does_not_fall_back(self):
        self.args.source = "graph"
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        self.graph_error = "(#10) permission denied"
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            self.main.cmd_official_stats(self.args)
        self.assertEqual(self.requests, [])

    def test_forced_composio_ignores_graph_credentials(self):
        self.args.source = "composio"
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        self.main.COMPOSIO_INSTAGRAM_ACCOUNT = "test-connection"
        self.assertEqual(self.run_stats()["source"], "composio")
        self.assertEqual(self.graph_requests, [])

    def test_unavailable_when_nothing_configured(self):
        self.main.COMPOSIO_API_KEY = ""
        result = self.run_stats()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("COMPOSIO_API_KEY", result["error"])
        self.assertEqual(result["configured"], {"META_IG_ACCESS_TOKEN": False, "META_IG_ACCOUNT_ID": False, "COMPOSIO_API_KEY": False})
        self.assertEqual(self.requests, [])

    def media_row(self, media_id, timestamp=None):
        return {"id": str(media_id), "timestamp": (timestamp or self.now - timedelta(days=1)).isoformat(), "media_type": "VIDEO", "like_count": 2, "comments_count": 3, "permalink": "https://example.com/media"}

    def test_per_item_insight_failure_is_tolerated(self):
        original = self.tool_response
        def respond(tool):
            slug = tool["tool_slug"]
            if slug == "INSTAGRAM_GET_IG_USER_MEDIA":
                return {"successful": True, "data": {"data": [self.media_row(i) for i in range(3)]}}
            if slug == "INSTAGRAM_GET_IG_MEDIA_INSIGHTS":
                item = tool["arguments"]["ig_media_id"]
                metric = tool["arguments"]["metric"][0]
                if item == "0" and metric == "views":
                    return {"successful": True, "data": {"data": [{"name": metric, "values": [{"value": 7}]}]}}
                if item == "1" and metric == "plays":
                    return {"successful": True, "data": {"data": [{"name": metric, "total_value": {"value": 9}}]}}
                return {"successful": False, "error": "insights unavailable"}
            return original(tool)
        self.tool_response = respond
        result = self.run_stats()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([m["views"] for m in result["media"]], [7, 9, None])
        self.assertEqual(result["windows"]["yesterday"]["views"], 16)
        self.assertEqual(result["windows"]["yesterday"]["comments"], 9)
        plays = [t["arguments"]["ig_media_id"] for _, p in self.requests if p["method"] == "tools/call" for t in p["params"]["arguments"]["tools"] if t["arguments"].get("metric") == ["plays"]]
        self.assertEqual(plays, ["1", "2"])

    def test_insights_are_batched_and_media_output_is_capped(self):
        original = self.tool_response
        def respond(tool):
            if tool["tool_slug"] == "INSTAGRAM_GET_IG_USER_MEDIA":
                return {"successful": True, "data": {"data": [self.media_row(i) for i in range(100)]}}
            if tool["tool_slug"] == "INSTAGRAM_GET_IG_MEDIA_INSIGHTS":
                return {"successful": True, "data": {"data": [{"name": "views", "values": [{"value": 1}]}]}}
            return original(tool)
        self.tool_response = respond
        result = self.run_stats()
        self.assertEqual(result["mediaFetched"], 100)
        self.assertEqual(len(result["media"]), 25)
        self.assertEqual(result["windows"]["yesterday"]["views"], 100)
        batches = [p["params"]["arguments"]["tools"] for _, p in self.requests if p["method"] == "tools/call"]
        self.assertEqual([len(batch) for batch in batches], [1, 1, 50, 50])

    def test_pagination_uses_cursor_and_stops_after_four_pages(self):
        original = self.tool_response
        pages = []
        def respond(tool):
            if tool["tool_slug"] == "INSTAGRAM_GET_IG_USER_MEDIA":
                pages.append(tool["arguments"])
                return {"successful": True, "data": {"data": [], "paging": {"cursors": {"after": "cursor-" + str(len(pages))}, "next": "https://example.com/?access_token=paging-secret"}}}
            return original(tool)
        self.tool_response = respond
        self.args.include_media = False
        result = self.run_stats()
        self.assertEqual(len(pages), 4)
        self.assertNotIn("after", pages[0])
        self.assertEqual(pages[1]["after"], "cursor-1")
        self.assertEqual(result["media"], [])
        self.assertNotIn("paging-secret", self.main.LATEST_OFFICIAL_STATS_PATH.read_text())

    def test_pagination_stops_at_oldest_needed_cutoff(self):
        original = self.tool_response
        pages = []
        def respond(tool):
            if tool["tool_slug"] == "INSTAGRAM_GET_IG_USER_MEDIA":
                pages.append(tool)
                return {"successful": True, "data": {"data": [self.media_row("old", self.now - timedelta(days=60))], "paging": {"cursors": {"after": "unused"}}}}
            return original(tool)
        self.tool_response = respond
        result = self.run_stats()
        self.assertEqual(len(pages), 1)
        self.assertEqual(result["mediaFetched"], 0)

    def test_graph_error_credentials_are_redacted(self):
        self.main.META_IG_ACCESS_TOKEN = "graph-token-secret"
        self.main.META_IG_ACCOUNT_ID = "graph-id"
        self.graph_error = "(#10) permission denied graph-token-secret ck_test_secret https://example.com/?access_token=other-secret"
        result = self.run_stats()
        for secret in ("graph-token-secret", "ck_test_secret", "other-secret"):
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(secret, self.main.LATEST_OFFICIAL_STATS_PATH.read_text())

    def test_composio_tool_failure_is_redacted_and_persisted(self):
        self.tool_response = lambda tool: {"successful": False, "error": "failed ck_test_secret"}
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            self.main.cmd_official_stats(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["source"], "composio")
        self.assertNotIn("ck_test_secret", output.getvalue())
        self.assertEqual(json.loads(self.main.LATEST_OFFICIAL_STATS_PATH.read_text()), result)

    def test_cli_source_flag_drives_mocked_flow(self):
        with mock.patch("sys.argv", ["instagram", "--username", "test_account", "official-stats", "--source", "composio"]), contextlib.redirect_stdout(io.StringIO()) as output:
            self.main.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["source"], "composio")
        self.assertEqual(result["media"], [])
        self.assertEqual(json.loads(self.main.LATEST_OFFICIAL_STATS_PATH.read_text()), result)


if __name__ == "__main__":
    unittest.main()
