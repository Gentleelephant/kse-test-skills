from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ks_admin.py"
SPEC = importlib.util.spec_from_file_location("ks_admin", SCRIPT)
assert SPEC and SPEC.loader
iam = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = iam
SPEC.loader.exec_module(iam)


class FakeKubeSphereHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str, str]] = {}
    bodies: list[str] = []

    def do_POST(self) -> None:
        self.respond()

    def do_GET(self) -> None:
        self.respond()

    def do_DELETE(self) -> None:
        self.respond()

    def respond(self) -> None:
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

    def test_login_supports_json_output(self) -> None:
        FakeKubeSphereHandler.routes["/oauth/token"] = (
            200,
            "application/json",
            json.dumps({"access_token": "oauth-token"}),
        )
        result = self.run_login("--output", "json")
        self.assertEqual({"token": "oauth-token"}, json.loads(result.stdout))

    def test_invalid_api_body_fails_before_request(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "api-call",
                "--server",
                self.base_url,
                "--token",
                "expired",
                "--path",
                "/apis/example.io/v1/widgets",
                "--body",
                "{broken",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--body is not valid JSON", result.stderr)

    def test_expired_token_is_reported_as_401(self) -> None:
        FakeKubeSphereHandler.routes["/apis/example.io/v1/widgets"] = (
            401,
            "application/json",
            json.dumps({"message": "token expired"}),
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "api-call",
                "--server",
                self.base_url,
                "--token",
                "expired",
                "--path",
                "/apis/example.io/v1/widgets",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn('"status": 401', result.stdout)

    def test_namespace_binding_requires_full_scope_context(self) -> None:
        args = argparse.Namespace(
            scope="namespace", cluster="host", workspace=None, namespace="demo"
        )
        with self.assertRaisesRegex(SystemExit, "--workspace is required"):
            iam.validate_scope(args)

    def test_delete_is_idempotent_and_verified(self) -> None:
        client = mock.Mock()
        client.raw_request.side_effect = [(404, ""), (404, "")]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            iam.delete_and_verify(client, "/apis/example/users/alice", False)
        self.assertTrue(json.loads(output.getvalue())["alreadyAbsent"])
        self.assertEqual(
            [call.args[:2] for call in client.raw_request.call_args_list],
            [("DELETE", "/apis/example/users/alice"), ("GET", "/apis/example/users/alice")],
        )

    def test_network_timeout_or_tls_error_is_actionable(self) -> None:
        client = iam.Client("https://ks.example.com")
        with mock.patch.object(iam, "find_curl", return_value=None), mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("TLS handshake timed out"),
        ):
            with self.assertRaisesRegex(SystemExit, "TLS handshake timed out"):
                client.raw_request("GET", "/version")

    def test_duplicate_user_returns_conflict_without_password(self) -> None:
        path = "/apis/iam.kubesphere.io/v1alpha2/users"
        FakeKubeSphereHandler.routes[path] = (
            409,
            "application/json",
            json.dumps({"message": "user already exists"}),
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "create-user",
                "--server",
                self.base_url,
                "--token",
                "admin-token",
                "--username",
                "alice",
                "--email",
                "alice@example.com",
                "--password",
                "new-user-secret",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("HTTP 409", result.stderr)
        self.assertNotIn("new-user-secret", result.stderr)

    def test_duplicate_binding_returns_conflict(self) -> None:
        path = "/apis/iam.kubesphere.io/v1beta1/clusterrolebindings"
        FakeKubeSphereHandler.routes[path] = (
            409,
            "application/json",
            json.dumps({"message": "binding already exists"}),
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "bind-role",
                "--server",
                self.base_url,
                "--token",
                "admin-token",
                "--subject",
                "alice",
                "--role",
                "cluster-viewer",
                "--scope",
                "cluster",
                "--cluster",
                "host",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("HTTP 409", result.stderr)


if __name__ == "__main__":
    unittest.main()
