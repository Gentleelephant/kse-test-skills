#!/usr/bin/env python3
"""KubeSphere authentication, user, and RBAC helper."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any


@dataclass
class Client:
    server: str
    token: str | None = None
    insecure: bool = False

    def __post_init__(self) -> None:
        self.server = self.server.rstrip("/")
        self.context = None
        if self.insecure:
            import ssl

            self.context = ssl._create_unverified_context()

    def request(self, method: str, path: str, body: Any = None, content_type: str = "application/json") -> Any:
        status, raw = self.raw_request(method, path, body, content_type)
        if status >= 400:
            raise SystemExit(f"{method} {path} failed: HTTP {status}: {raw}")
        return parse_response(raw)

    def raw_request(self, method: str, path: str, body: Any = None, content_type: str = "application/json") -> tuple[int, str]:
        curl = find_curl()
        if curl:
            return self.curl_raw_request(curl, method, path, body, content_type)

        import urllib.error
        import urllib.request

        data = None
        headers = {"Accept": "application/json", "User-Agent": "curl/8.0.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            if content_type == "application/x-www-form-urlencoded":
                data = urlencode(body).encode()
            else:
                data = json.dumps(body).encode()
            headers["Content-Type"] = content_type

        req = urllib.request.Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self.context, timeout=30) as res:
                raw = res.read().decode()
                return res.status, raw
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            return exc.code, detail
        except urllib.error.URLError as exc:
            raise SystemExit(f"{method} {path} failed: {exc.reason}") from exc

    def curl_raw_request(self, curl: str, method: str, path: str, body: Any = None, content_type: str = "application/json") -> tuple[int, str]:
        headers = ["Accept: application/json"]
        if self.token:
            headers.append(f"Authorization: Bearer {self.token}")
        data = None
        if body is not None:
            if content_type == "application/x-www-form-urlencoded":
                data = urlencode(body).encode()
            else:
                data = json.dumps(body).encode()
            headers.append(f"Content-Type: {content_type}")

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            output_path = tmp.name
        try:
            cmd = [curl, "-sS", "-o", output_path, "-w", "%{http_code}", "-X", method]
            if self.insecure:
                cmd.append("-k")
            for header in headers:
                cmd.extend(["-H", header])
            if data is not None:
                cmd.extend(["--data-binary", "@-"])
            cmd.append(f"{self.server}{path}")
            proc = subprocess.run(cmd, input=data, capture_output=True)
            status_text = proc.stdout.decode(errors="replace").strip()
            raw = read_text(output_path)
            if proc.returncode != 0:
                raise SystemExit(f"{method} {path} failed: {proc.stderr.decode(errors='replace').strip()}")
            status = int(status_text[-3:]) if status_text[-3:].isdigit() else 0
            return status, raw
        finally:
            try:
                os.unlink(output_path)
            except OSError:
                pass


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def find_curl() -> str | None:
    if os.path.exists("/usr/bin/curl"):
        return "/usr/bin/curl"
    if os.path.exists("/opt/homebrew/bin/curl"):
        return "/opt/homebrew/bin/curl"
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, "curl")
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def urlencode(value: Any) -> str:
    import urllib.parse

    return urllib.parse.urlencode(value)


def is_token_like(value: str) -> bool:
    return bool(value) and value.count(".") == 2


def extract_token(payload: Any) -> str | None:
    if isinstance(payload, str) and is_token_like(payload):
        return payload
    if not isinstance(payload, dict):
        if isinstance(payload, list):
            for item in payload:
                token = extract_token(item)
                if token:
                    return token
        return None
    for key in ("access_token", "token", "accessToken"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for value in payload.values():
        if isinstance(value, (dict, list)):
            token = extract_token(value)
            if token:
                return token
    return None


def parse_response(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def response_shape(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"kind": "json", "keys": sorted(str(key) for key in payload.keys())}
    if isinstance(payload, list):
        return {"kind": "json-list", "items": len(payload)}
    if isinstance(payload, str):
        text = payload.strip()
        shape = {"kind": "text", "length": len(payload)}
        if text.lower().startswith("<!doctype html") or text.lower().startswith("<html"):
            shape["hint"] = "response looks like HTML; --server may point to KubeSphere Console instead of ks-apiserver"
        return shape
    return {"kind": type(payload).__name__}


def login_problem(status: int, payload: Any) -> str:
    if status in (401, 403):
        return "credentials, client_id/client_secret, or identity provider rejected the login"
    if status == 404:
        return "login path was not found; check --server and --login-path"
    if status >= 500:
        return "server-side error from KubeSphere or its gateway"
    if isinstance(payload, str):
        text = payload.strip().lower()
        if text.startswith("<!doctype html") or text.startswith("<html"):
            return "non-JSON HTML response; --server may point to KubeSphere Console instead of ks-apiserver"
        return "non-JSON response; expected a JSON token response from ks-apiserver"
    return "no bearer token found in response"


def make_login_body(kind: str, args: argparse.Namespace, password: str) -> tuple[dict[str, str], str]:
    if kind == "oauth":
        return (
            {
                "grant_type": "password",
                "username": args.username,
                "password": password,
                "client_id": args.client_id,
                "client_secret": args.client_secret,
            },
            "application/x-www-form-urlencoded",
        )
    return ({"username": args.username, "password": password}, "application/json")


def infer_login_kind(path: str, auth_mode: str) -> str:
    if auth_mode in ("oauth", "iam-json"):
        return "oauth" if auth_mode == "oauth" else "iam-json"
    return "oauth" if "oauth" in path else "iam-json"


def login_attempts(args: argparse.Namespace, password: str) -> list[tuple[str, dict[str, str], str, str]]:
    paths: list[tuple[str, str]] = []
    if args.login_path:
        paths.append((args.login_path, infer_login_kind(args.login_path, args.auth_mode)))
    if args.auth_mode in ("auto", "oauth"):
        paths.append(("/oauth/token", "oauth"))
    if args.auth_mode in ("auto", "iam-json"):
        paths.append(("/kapis/iam.kubesphere.io/v1alpha2/login", "iam-json"))

    attempts: list[tuple[str, dict[str, str], str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, kind in paths:
        normalized_path = path if path.startswith("/") else f"/{path}"
        key = (normalized_path, kind)
        if key in seen:
            continue
        seen.add(key)
        body, content_type = make_login_body(kind, args, password)
        attempts.append((normalized_path, body, content_type, kind))
    return attempts


def login_diagnostic(path: str, kind: str, status: int | None, payload: Any = None, error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path, "auth_mode": kind}
    if status is not None:
        result["status"] = status
    if error:
        result["error"] = error
        return result
    result["response"] = response_shape(payload)
    result["token_found"] = bool(extract_token(payload))
    if status is not None and (status >= 400 or not result["token_found"]):
        result["hint"] = login_problem(status, payload)
    return result


def login(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass("KubeSphere password: ")
    client = Client(args.server, insecure=args.insecure)
    attempts = login_attempts(args, password)
    errors: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    for path, body, content_type, kind in attempts:
        try:
            status, raw = client.raw_request("POST", path, body, content_type)
            payload = parse_response(raw)
            diagnostics.append(login_diagnostic(path, kind, status, payload))
            if status >= 400:
                errors.append(f"{path}: HTTP {status}: {login_problem(status, payload)}")
                continue
            token = extract_token(payload)
            if token:
                if args.diagnose:
                    print(json.dumps({"success": True, "attempts": diagnostics}, indent=2, sort_keys=True))
                    return
                if args.output == "json":
                    print(json.dumps({"token": token}, indent=2, sort_keys=True))
                else:
                    print(token)
                return
            errors.append(f"{path}: {login_problem(status, payload)}; response {response_shape(payload)}")
        except SystemExit as exc:
            message = str(exc)
            diagnostics.append(login_diagnostic(path, kind, None, error=message))
            errors.append(message)
    if args.diagnose:
        print(json.dumps({"success": False, "attempts": diagnostics}, indent=2, sort_keys=True))
        return
    raise SystemExit("Login failed:\n" + "\n".join(errors))


def create_user(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass("New user password: ")
    client = Client(args.server, args.token, args.insecure)
    body = {
        "apiVersion": f"iam.kubesphere.io/{args.api_version}",
        "kind": "User",
        "metadata": {"name": args.username},
        "spec": {"email": args.email, "password": password},
    }
    if args.display_name:
        body["spec"]["displayName"] = args.display_name
    collection = f"/apis/iam.kubesphere.io/{args.api_version}/users"
    if args.dry_run:
        print_request("POST", collection, body)
        return
    client.request("POST", collection, body)
    result = client.request("GET", f"{collection}/{args.username}")
    print(json.dumps({"result": "PASS", "verified": redact(result)}, indent=2, sort_keys=True))


def delete_user(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    path = f"/apis/iam.kubesphere.io/{args.api_version}/users/{args.username}"
    delete_and_verify(client, path, args.dry_run)


def list_roles(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    paths = {
        "platform": "/apis/iam.kubesphere.io/v1beta1/globalroles",
        "cluster": "/apis/iam.kubesphere.io/v1beta1/clusterroles",
        "workspace": "/apis/iam.kubesphere.io/v1beta1/workspaceroles",
        "namespace": "/apis/iam.kubesphere.io/v1beta1/namespaces/{namespace}/roles",
    }
    path = paths[args.scope].format(workspace=args.workspace or "", namespace=args.namespace or "")
    result = client.request("GET", path)
    print(json.dumps(result, indent=2, sort_keys=True))


def list_roletemplates(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    labels: list[str] = []
    if args.extension:
        labels.append(f"kubesphere.io/extension-ref={args.extension}")
    labels.extend(args.label or [])
    path = "/apis/iam.kubesphere.io/v1beta1/roletemplates"
    if labels:
        path = f"{path}?{urlencode({'labelSelector': ','.join(labels)})}"
    result = client.request("GET", path)
    print(json.dumps(result, indent=2, sort_keys=True))


def api_call(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    body = None
    if args.body_file:
        raw_body = read_text(args.body_file)
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--body-file is not valid JSON: {exc}") from exc
    elif args.body:
        try:
            body = json.loads(args.body)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--body is not valid JSON: {exc}") from exc
    status, raw = client.raw_request(args.method, args.path, body, args.content_type)
    payload: Any = raw
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        pass
    print(json.dumps({"status": status, "body": payload}, indent=2, sort_keys=True))
    if args.expect_status is not None and status != args.expect_status:
        raise SystemExit(f"expected HTTP {args.expect_status}, got HTTP {status}")
    if args.expect_status is None and status >= 400:
        raise SystemExit(f"{args.method} {args.path} failed: HTTP {status}")


def binding_name(subject: str, role: str, scope: str) -> str:
    return f"{subject}-{role}-{scope}".lower().replace("_", "-").replace(".", "-")


def validate_scope(args: argparse.Namespace) -> None:
    if args.scope == "cluster":
        require(args.cluster, "--cluster is required for cluster role bindings")
    elif args.scope == "workspace":
        require(args.workspace, "--workspace is required for workspace role bindings")
    elif args.scope == "namespace":
        require(args.cluster, "--cluster is required for namespace role bindings")
        require(args.workspace, "--workspace is required for namespace role bindings")
        require(args.namespace, "--namespace is required for namespace role bindings")


def binding_collection(args: argparse.Namespace) -> str:
    validate_scope(args)
    if args.scope == "platform":
        return "/apis/iam.kubesphere.io/v1beta1/globalrolebindings"
    if args.scope == "cluster":
        return "/apis/iam.kubesphere.io/v1beta1/clusterrolebindings"
    if args.scope == "workspace":
        return "/apis/iam.kubesphere.io/v1beta1/workspacerolebindings"
    return f"/apis/iam.kubesphere.io/v1beta1/namespaces/{args.namespace}/rolebindings"


def bind_role(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    name = args.name or binding_name(args.subject, args.role, args.scope)
    path = binding_collection(args)
    if args.scope == "cluster":
        body = {
            "apiVersion": "iam.kubesphere.io/v1beta1",
            "kind": "ClusterRoleBinding",
            "metadata": {
                "name": name,
                "labels": {"iam.kubesphere.io/role-ref": args.role, "iam.kubesphere.io/user-ref": args.subject},
            },
            "subjects": [{"kind": "User", "apiGroup": "iam.kubesphere.io", "name": args.subject}],
            "roleRef": {"kind": "ClusterRole", "apiGroup": "iam.kubesphere.io", "name": args.role},
        }
    elif args.scope == "namespace":
        body = {
            "apiVersion": "iam.kubesphere.io/v1beta1",
            "kind": "RoleBinding",
            "metadata": {
                "name": name,
                "namespace": args.namespace,
                "labels": {"iam.kubesphere.io/role-ref": args.role, "iam.kubesphere.io/user-ref": args.subject},
            },
            "subjects": [{"kind": "User", "apiGroup": "iam.kubesphere.io", "name": args.subject}],
            "roleRef": {"kind": "Role", "apiGroup": "iam.kubesphere.io", "name": args.role},
        }
    elif args.scope == "platform":
        body = {
            "apiVersion": "iam.kubesphere.io/v1beta1",
            "kind": "GlobalRoleBinding",
            "metadata": {
                "name": name,
                "labels": {"iam.kubesphere.io/role-ref": args.role, "iam.kubesphere.io/user-ref": args.subject},
            },
            "roleRef": {"apiGroup": "iam.kubesphere.io", "kind": "GlobalRole", "name": args.role},
            "subjects": [{"apiGroup": "iam.kubesphere.io", "kind": "User", "name": args.subject}],
        }
    else:
        body = {
            "apiVersion": "iam.kubesphere.io/v1beta1",
            "kind": "WorkspaceRoleBinding",
            "metadata": {
                "name": name,
                "labels": {
                    "iam.kubesphere.io/role-ref": args.role,
                    "iam.kubesphere.io/user-ref": args.subject,
                    "kubesphere.io/workspace": args.workspace,
                },
            },
            "roleRef": {"apiGroup": "iam.kubesphere.io", "kind": "WorkspaceRole", "name": args.role},
            "subjects": [{"apiGroup": "iam.kubesphere.io", "kind": "User", "name": args.subject}],
        }
    if args.dry_run:
        print_request("POST", path, body)
        return
    client.request("POST", path, body)
    result = client.request("GET", f"{path}/{name}")
    print(json.dumps({"result": "PASS", "verified": redact(result)}, indent=2, sort_keys=True))


def unbind_role(args: argparse.Namespace) -> None:
    client = Client(args.server, args.token, args.insecure)
    name = args.name or binding_name(args.subject, args.role, args.scope)
    delete_and_verify(client, f"{binding_collection(args)}/{name}", args.dry_run)


def delete_and_verify(client: Client, path: str, dry_run: bool) -> None:
    if dry_run:
        print_request("DELETE", path, None)
        return
    status, raw = client.raw_request("DELETE", path)
    if status not in (200, 202, 204, 404):
        raise SystemExit(f"DELETE {path} failed: HTTP {status}: {raw}")
    verify_status, verify_raw = client.raw_request("GET", path)
    if verify_status != 404:
        raise SystemExit(
            f"DELETE {path} could not be verified: GET returned HTTP {verify_status}: {verify_raw}"
        )
    print(
        json.dumps(
            {"result": "PASS", "path": path, "alreadyAbsent": status == 404, "verifiedStatus": 404},
            indent=2,
            sort_keys=True,
        )
    )


def print_request(method: str, path: str, body: Any) -> None:
    print(json.dumps({"method": method, "path": path, "body": redact(body)}, indent=2, sort_keys=True))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        sensitive = {"password", "token", "accesstoken", "clientsecret", "authorization"}
        return {
            k: (
                "<redacted>"
                if k.lower().replace("_", "") in sensitive
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def require(value: Any, message: str) -> None:
    if not value:
        raise SystemExit(message)


def add_common(
    parser: argparse.ArgumentParser, token: bool = True, output_default: str = "json"
) -> None:
    parser.add_argument("--server", required=True, help="KubeSphere base URL, for example https://ks.example.com")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    if token:
        parser.add_argument("--token", required=True, help="Bearer token")
    parser.add_argument(
        "--output",
        choices=["text", "json"] if output_default == "text" else ["json"],
        default=output_default,
        help="Output format (commands other than login currently emit JSON for compatibility)",
    )


def add_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--subject", required=True, help="Username to grant or revoke")
    parser.add_argument("--role", required=True)
    parser.add_argument("--scope", choices=["platform", "cluster", "workspace", "namespace"], required=True)
    parser.add_argument("--cluster")
    parser.add_argument("--workspace")
    parser.add_argument("--namespace")
    parser.add_argument("--name", help="Binding object name")
    parser.add_argument("--dry-run", action="store_true", help="Print the request without sending it")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="Print a bearer token")
    add_common(p, token=False, output_default="text")
    p.add_argument("--username", required=True)
    p.add_argument("--password")
    p.add_argument("--client-id", default="kubesphere")
    p.add_argument("--client-secret", default="kubesphere")
    p.add_argument("--auth-mode", choices=["auto", "oauth", "iam-json"], default="auto")
    p.add_argument("--login-path", help="Custom login path on ks-apiserver, for example /oauth/token")
    p.add_argument("--diagnose", action="store_true", help="Print sanitized login diagnostics instead of a token")
    p.set_defaults(func=login)

    p = sub.add_parser("create-user", help="Create a KubeSphere user")
    add_common(p)
    p.add_argument("--username", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--password")
    p.add_argument("--display-name")
    p.add_argument("--api-version", choices=["v1alpha2", "v1beta1"], default="v1alpha2")
    p.add_argument("--dry-run", action="store_true", help="Print the request without sending it")
    p.set_defaults(func=create_user)

    p = sub.add_parser("delete-user", help="Delete a KubeSphere user and verify removal")
    add_common(p)
    p.add_argument("--username", required=True)
    p.add_argument("--api-version", choices=["v1alpha2", "v1beta1"], default="v1alpha2")
    p.add_argument("--dry-run", action="store_true", help="Print the request without sending it")
    p.set_defaults(func=delete_user)

    p = sub.add_parser("list-roles", help="List roles for a scope")
    add_common(p)
    p.add_argument("--scope", choices=["platform", "cluster", "workspace", "namespace"], required=True)
    p.add_argument("--workspace")
    p.add_argument("--namespace")
    p.set_defaults(func=list_roles)

    p = sub.add_parser("list-roletemplates", help="List KubeSphere RoleTemplates")
    add_common(p)
    p.add_argument("--extension", help="Filter by kubesphere.io/extension-ref")
    p.add_argument("--label", action="append", help="Additional label selector expression, for example iam.kubesphere.io/scope=namespace")
    p.set_defaults(func=list_roletemplates)

    p = sub.add_parser("api-call", help="Call an API through ks-apiserver and optionally assert the HTTP status")
    add_common(p)
    p.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("--path", required=True, help="Absolute API path on ks-apiserver, for example /apis/batch.volcano.sh/v1alpha1/namespaces/demo/jobs")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--content-type", default="application/json")
    p.add_argument("--expect-status", type=int)
    p.set_defaults(func=api_call)

    p = sub.add_parser("bind-role", help="Bind a role to a user")
    add_common(p)
    add_binding_arguments(p)
    p.add_argument("--role-kind", default="ClusterRole", choices=["Role", "ClusterRole"], help="Deprecated; IAM bindings infer the role kind from scope")
    p.set_defaults(func=bind_role)

    p = sub.add_parser("unbind-role", help="Delete an IAM role binding and verify removal")
    add_common(p)
    add_binding_arguments(p)
    p.set_defaults(func=unbind_role)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
