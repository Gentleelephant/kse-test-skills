from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ks_admin.py"


class FakeKubeSphereHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str, str]] = {}
    bodies: list[str] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        type(self).bodies.append(body)
        status, content_type, payload = type(self).routes.get(
            self.path,
            (404, "application/json", json.dumps({"message": "not found"})),
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, format: str, *args: object) -> None:
        return


class LoginTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeKubeSphereHandler.routes = {}
        FakeKubeSphereHandler.bodies = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeKubeSphereHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_login(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "login",
                "--server",
                self.base_url,
                "--username",
                "admin",
                "--password",
                "secret-password",
                "--client-secret",
                "secret-client",
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_oauth_token_success(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            200,
            "application/json",
            json.dumps({"access_token": "oauth-token"}),
        )

        result = self.run_login()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "oauth-token")
        self.assertIn("grant_type=password", FakeKubeSphereHandler.bodies[0])

    def test_fallback_to_legacy_iam_login_after_oauth_404(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            404,
            "application/json",
            json.dumps({"message": "not found"}),
        )
        FakeKubeSphereHandler.routes["/kapis/iam.kubesphere.io/v1alpha2/login"] = (
            200,
            "application/json",
            json.dumps({"token": "legacy-token"}),
        )

        result = self.run_login()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "legacy-token")
        self.assertEqual(len(FakeKubeSphereHandler.bodies), 2)

    def test_nested_token_response(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            200,
            "application/json",
            json.dumps({"data": {"token": "nested-token"}}),
        )

        result = self.run_login()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nested-token")

    def test_html_response_mentions_console_in_error(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            200,
            "text/html",
            "<!doctype html><html><body>console login</body></html>",
        )
        FakeKubeSphereHandler.routes["/kapis/iam.kubesphere.io/v1alpha2/login"] = (
            404,
            "application/json",
            json.dumps({"message": "not found"}),
        )

        result = self.run_login()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Console", result.stderr)
        self.assertIn("ks-apiserver", result.stderr)
        self.assertNotIn("secret-password", result.stderr)

    def test_diagnose_redacts_password_secret_and_token(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            200,
            "application/json",
            json.dumps({"access_token": "sensitive-token"}),
        )

        result = self.run_login("--diagnose")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("secret-password", result.stdout)
        self.assertNotIn("secret-client", result.stdout)
        self.assertNotIn("sensitive-token", result.stdout)
        diagnostic = json.loads(result.stdout)
        self.assertTrue(diagnostic["success"])
        self.assertTrue(diagnostic["attempts"][0]["token_found"])


if __name__ == "__main__":
    unittest.main()
