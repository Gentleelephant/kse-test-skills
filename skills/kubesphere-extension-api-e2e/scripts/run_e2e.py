#!/usr/bin/env python3
"""Plan and run RoleTemplate-driven KubeSphere extension API E2E tests."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised by CLI smoke tests
    yaml = None


CONFIG_SCHEMA_VERSION = 1
CONFIG_NAME = "kse-e2e.yaml"
RUN_LABEL = "testing.kubesphere.io/run-id"
METHODS = ("get", "post", "put", "patch", "delete")
VERBS = {"post": "create", "put": "update", "patch": "patch", "delete": "delete"}
DEFAULT_ROLES = [
    "authenticated",
    "cluster-admin",
    "cluster-viewer",
    "workspace-admin",
    "workspace-viewer",
    "namespace-admin",
    "namespace-operator",
    "namespace-viewer",
    "no-permission",
    "no-token",
]
SENSITIVE_KEYS = {
    "adminpassword",
    "admintoken",
    "oauthclientsecret",
    "password",
    "token",
    "access_token",
    "authorization",
    "client_secret",
}
LANGUAGE_MARKERS = {
    "go": ("go.mod",),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
    "python": ("pyproject.toml", "requirements.txt", "setup.py"),
    "node": ("package.json",),
}
SOURCE_SUFFIXES = {
    "go": {".go"},
    "java": {".java", ".kt"},
    "python": {".py"},
    "node": {".js", ".jsx", ".ts", ".tsx"},
}


class E2EError(RuntimeError):
    pass


@dataclass
class HTTPResponse:
    status: int
    body: str
    headers: dict[str, str]


class Client:
    def __init__(self, server: str, token: str = "", insecure: bool = False):
        self.server = server.rstrip("/")
        self.token = token
        self.context = ssl._create_unverified_context() if insecure else None

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        content_type: str = "application/json",
        timeout: float = 30,
    ) -> HTTPResponse:
        data = None
        headers = {"Accept": "application/json", "User-Agent": "kse-extension-api-e2e/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            if content_type == "application/x-www-form-urlencoded":
                data = urllib.parse.urlencode(body).encode()
            else:
                data = json.dumps(body).encode()
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.server + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=timeout) as response:
                return HTTPResponse(response.status, response.read().decode(errors="replace"), dict(response.headers))
        except urllib.error.HTTPError as exc:
            return HTTPResponse(exc.code, exc.read().decode(errors="replace"), dict(exc.headers))
        except urllib.error.URLError as exc:
            raise E2EError(f"{method} {path} failed: {exc.reason}") from exc

    def json(self, method: str, path: str, body: Any = None) -> Any:
        response = self.request(method, path, body)
        if response.status >= 400:
            raise E2EError(f"{method} {path} failed: HTTP {response.status}: {summarize(response.body)}")
        if not response.body:
            return {}
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise E2EError(f"{method} {path} returned non-JSON content") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_yaml() -> None:
    if yaml is None:
        raise E2EError(
            "ENVIRONMENT_ERROR: PyYAML is required; run "
            "uv run --with-requirements <skill-dir>/requirements.txt python "
            "<skill-dir>/scripts/run_e2e.py <command>"
        )


def load_yaml(path: Path) -> Any:
    require_yaml()
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except OSError as exc:
        raise E2EError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise E2EError(f"invalid YAML in {path}: {exc}") from exc


def dump_yaml(value: Any) -> str:
    require_yaml()
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)


def nested(value: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def sanitize_name(value: str, fallback: str = "extension-e2e") -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    return (cleaned[:40].rstrip("-") or fallback)


def resolve(config_path: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def duration_seconds(value: str, field: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*", str(value))
    if not match:
        raise E2EError(f"{field} must be a duration such as 30s or 10m")
    amount = float(match.group(1))
    return amount * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match.group(2)]


def git_tracked(path: Path) -> bool:
    root = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if root.returncode != 0:
        return False
    repo = Path(root.stdout.strip())
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", str(path.resolve().relative_to(repo.resolve()))],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def require_private_config(path: Path) -> None:
    if git_tracked(path):
        raise E2EError(f"{path} is tracked by Git; remove it from the index before storing test credentials")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise E2EError(f"{path} permissions are {mode:04o}; run chmod 600 {path}")


def secret_values(config: dict[str, Any]) -> list[str]:
    values: list[str] = []
    public_values: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = key.lower()
                if (
                    isinstance(item, str)
                    and item
                    and (
                        sensitive_item(key, item)
                        or "password" in normalized
                        or "token" in normalized
                        or "secret" in normalized
                    )
                ):
                    values.append(item)
                else:
                    if isinstance(item, str) and item:
                        public_values.add(item)
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(config)
    return list(dict.fromkeys(item for item in values if item not in public_values))


def sensitive_item(key: str, value: Any) -> bool:
    normalized = key.lower()
    if normalized == "authorization":
        return isinstance(value, str) and value.upper() not in ("ALLOW", "DENY")
    return normalized in SENSITIVE_KEYS


def redact(value: Any, secrets_to_hide: list[str] | None = None) -> Any:
    secrets_to_hide = [item for item in (secrets_to_hide or []) if item]
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if sensitive_item(key, item) and item else redact(item, secrets_to_hide))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets_to_hide) for item in value]
    if isinstance(value, str):
        result = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", value)
        for secret in sorted(secrets_to_hide, key=len, reverse=True):
            result = result.replace(secret, "<redacted>")
        return result
    return value


def summarize(body: str, limit: int = 800) -> str:
    body = re.sub(r"\s+", " ", body.strip())
    return body if len(body) <= limit else body[:limit] + "…"


def find_candidates(root: Path, names: set[str], max_depth: int = 5) -> list[Path]:
    ignored = {".git", ".idea", "node_modules", "vendor", "dist", "test-results", "__pycache__"}
    found: list[Path] = []
    for current, dirs, files in os.walk(root):
        relative = Path(current).relative_to(root)
        if len(relative.parts) >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [item for item in dirs if item not in ignored and not item.startswith(".")]
        for filename in files:
            if filename.lower() in names:
                found.append(Path(current) / filename)
    return sorted(found, key=lambda item: (len(item.relative_to(root).parts), str(item)))


def relpath(path: Path, root: Path) -> str:
    try:
        value = path.resolve().relative_to(root.resolve())
        return "./" + str(value)
    except ValueError:
        return str(path.resolve())


def detect_language(root: Path) -> str:
    for language, markers in LANGUAGE_MARKERS.items():
        if any((root / marker).is_file() for marker in markers):
            return language
    return "unknown"


def discover_backend_code(project: Path) -> list[dict[str, Any]]:
    language = detect_language(project)
    if language == "unknown":
        return []
    include = [
        f"./{name}"
        for name in ("cmd", "pkg", "internal", "src", "app")
        if (project / name).is_dir()
    ]
    return [
        {
            "name": sanitize_name(project.name, "backend"),
            "path": ".",
            "language": language,
            "include": include,
            "exclude": ["./vendor", "./node_modules", "./test-results", "./dist", "./build"],
        }
    ]


def backend_sources(config_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = nested(config, "sources", "backendCode", default=[]) or []
    if not isinstance(configured, list):
        raise E2EError("sources.backendCode must be a list")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            raise E2EError(f"sources.backendCode[{index}] must be an object")
        raw_path = str(item.get("path", "")).strip()
        if not raw_path:
            raise E2EError(f"sources.backendCode[{index}].path is required")
        root = resolve(config_path, raw_path)
        language = str(item.get("language", "auto"))
        if language == "auto":
            language = detect_language(root)
        result.append(
            {
                "name": str(item.get("name") or sanitize_name(root.name, f"backend-{index + 1}")),
                "path": root,
                "configuredPath": raw_path,
                "language": language,
                "include": item.get("include", []) or [],
                "exclude": item.get("exclude", []) or [],
            }
        )
    return result


def source_inventory(config_path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for source in backend_sources(config_path, config):
        root = source["path"]
        suffixes = SOURCE_SUFFIXES.get(source["language"], set())
        includes = source["include"] or ["."]
        excluded = [
            (root / str(raw)).resolve()
            for raw in source["exclude"]
        ]
        files: list[str] = []
        for raw in includes:
            candidate = (root / str(raw)).resolve()
            if candidate.is_file():
                candidates = [candidate]
            elif candidate.is_dir():
                candidates = (item for item in candidate.rglob("*") if item.is_file())
            else:
                continue
            for item in candidates:
                resolved = item.resolve()
                if any(resolved == path or path in resolved.parents for path in excluded):
                    continue
                if suffixes and resolved.suffix.lower() not in suffixes:
                    continue
                files.append(relpath(resolved, config_path.parent))
        inventory.append(
            {
                "name": source["name"],
                "path": relpath(root, config_path.parent),
                "language": source["language"],
                "files": sorted(set(files)),
            }
        )
    return inventory


def prompt(label: str, default: str = "", secret: bool = False, non_interactive: bool = False) -> str:
    if non_interactive:
        return default
    suffix = f" [{default}]" if default else ""
    value = getpass.getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ")
    return value.strip() or default


def add_gitignore(project: Path) -> None:
    path = project / ".gitignore"
    line = f"/{CONFIG_NAME}"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if line in existing:
        return
    with path.open("a", encoding="utf-8") as stream:
        if existing and path.stat().st_size:
            stream.write("\n")
        stream.write(line + "\n")


def init_config(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    if not project.is_dir():
        raise E2EError(f"project directory does not exist: {project}")
    output = project / CONFIG_NAME
    if output.exists() and not args.force:
        raise E2EError(f"{output} already exists; use --force to replace it")

    api_candidates = find_candidates(
        project,
        {"apidocs.json", "openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml"},
    )
    extension_candidates = find_candidates(project, {"extension.yaml"})
    chart_candidates = find_candidates(project, {"chart.yaml"})
    api_doc = args.api_doc or (relpath(api_candidates[0], project) if api_candidates else "./apidocs.json")
    extension = args.extension or (
        relpath(extension_candidates[0].parent, project)
        if extension_candidates
        else relpath(chart_candidates[0].parent, project)
        if chart_candidates
        else "."
    )
    extension_name = ""
    extension_file = resolve(output, extension) / "extension.yaml"
    extension_root = resolve(output, extension)
    values_files = [
        relpath(candidate, project)
        for candidate in (
            extension_root / "values.yaml",
            project / "values.yaml",
        )
        if candidate.is_file()
    ]
    values_files = list(dict.fromkeys(values_files))
    if extension_file.exists():
        extension_data = load_yaml(extension_file) or {}
        extension_name = str(extension_data.get("name", ""))
    name = sanitize_name(extension_name or project.name) + "-api-e2e"
    server = args.server or prompt("ks-apiserver address", "http://127.0.0.1:30880", non_interactive=args.non_interactive)
    cluster = args.cluster or prompt("KubeSphere cluster", "host", non_interactive=args.non_interactive)
    auth_mode = args.auth_mode or prompt("Authentication mode (auto/token/password)", "auto", non_interactive=args.non_interactive)
    username = args.username or prompt("Admin username", "admin", non_interactive=args.non_interactive)
    token = args.token or ""
    password = args.password or ""
    if not args.non_interactive:
        if auth_mode == "token":
            token = prompt("Admin token", secret=True)
        else:
            password = prompt("Admin password", secret=True)
    client_secret = args.client_secret or prompt(
        "OAuth client secret", "kubesphere", secret=True, non_interactive=args.non_interactive
    )

    config = {
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "name": name,
        "target": {
            "ksAPIServer": server,
            "cluster": cluster,
            "tls": {"insecureSkipVerify": False},
        },
        "auth": {
            "mode": auth_mode,
            "adminUsername": username,
            "adminPassword": password,
            "adminToken": token,
            "loginPath": "/oauth/token",
            "oauthClientID": "kubesphere",
            "oauthClientSecret": client_secret,
        },
        "sources": {
            "apiDocs": [{"path": api_doc, "format": "auto"}],
            "extension": {
                "path": extension,
                "renderer": "auto",
                "valuesFiles": values_files,
                "releaseName": sanitize_name(extension_name or project.name),
                "namespace": (extension_data.get("namespace") if extension_file.exists() else None)
                or f"{sanitize_name(extension_name or project.name)}-system",
                "kubeVersion": minimum_kube_version(str(extension_data.get("kubeVersion", "")))
                if extension_file.exists()
                else "",
            },
        },
        "discovery": {
            "extensionName": extension_name,
            "roleTemplateSelector": (
                {"kubesphere.io/extension-ref": extension_name} if extension_name else {}
            ),
        },
        "apiMappings": {},
        "roles": {"mode": "auto", "include": DEFAULT_ROLES.copy()},
        "execution": {
            "resourcePrefix": sanitize_name(extension_name or project.name) + "-e2e",
            "timeout": "10m",
            "roleAggregationWait": "30s",
            "membershipWait": "30s",
            "cleanup": "always",
        },
        "report": {
            "directory": "./test-results",
            "plan": "kse-e2e-plan.json",
            "json": "kse-e2e-report.json",
            "markdown": "kse-e2e-report.md",
        },
    }
    output.write_text(dump_yaml(config), encoding="utf-8")
    os.chmod(output, 0o600)
    add_gitignore(project)
    print(f"Created {output}")
    print(dump_yaml(redact({"target": config["target"], "auth": config["auth"]})).strip())


def load_config(path_value: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_value).expanduser().resolve()
    if not path.exists():
        raise E2EError(
            f"未找到 {path}。\n请执行：\n"
            f"python3 {Path(__file__).resolve()} init --project {path.parent}\n"
            "或者提示 Agent：使用 $kubesphere-extension-api-e2e 为当前项目创建本地测试配置。"
        )
    require_private_config(path)
    config = load_yaml(path)
    if not isinstance(config, dict):
        raise E2EError("configuration root must be a YAML object")
    if any(key in config for key in ("apiVersion", "kind", "metadata", "spec")):
        raise E2EError("configuration must use the plain YAML schema; Kubernetes-style fields are not supported")
    if config.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        raise E2EError(f"configuration schemaVersion must be {CONFIG_SCHEMA_VERSION}")
    return path, config


def validate_config_shape(path: Path, config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    server = nested(config, "target", "ksAPIServer", default="")
    parsed = urllib.parse.urlparse(str(server))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append("target.ksAPIServer must be an http or https URL")
    if not nested(config, "target", "cluster", default=""):
        errors.append("target.cluster is required")

    auth = nested(config, "auth", default={}) or {}
    mode = auth.get("mode", "auto")
    if mode not in ("auto", "token", "password"):
        errors.append("auth.mode must be auto, token, or password")
    if mode == "token" and not auth.get("adminToken"):
        errors.append("auth.adminToken is required in token mode")
    if mode == "password" and (not auth.get("adminUsername") or not auth.get("adminPassword")):
        errors.append("auth.adminUsername and adminPassword are required in password mode")
    if mode == "auto" and not auth.get("adminToken") and (
        not auth.get("adminUsername") or not auth.get("adminPassword")
    ):
        errors.append("auto authentication requires adminToken or adminUsername/adminPassword")

    docs = nested(config, "sources", "apiDocs", default=[])
    if not isinstance(docs, list) or not docs:
        errors.append("sources.apiDocs must contain at least one document")
    else:
        for item in docs:
            raw = item.get("path", "") if isinstance(item, dict) else ""
            if not raw or not resolve(path, raw).is_file():
                errors.append(f"API document does not exist: {raw or '<empty>'}")

    extension = nested(config, "sources", "extension", default={}) or {}
    extension_path = resolve(path, str(extension.get("path", "")))
    if not extension_path.is_dir():
        errors.append(f"extension path does not exist: {extension_path}")
    elif not (extension_path / "extension.yaml").is_file() and not (extension_path / "Chart.yaml").is_file():
        errors.append(f"{extension_path} contains neither extension.yaml nor Chart.yaml")
    for raw in extension.get("valuesFiles", []) or []:
        if not resolve(path, str(raw)).is_file():
            errors.append(f"values file does not exist: {raw}")

    try:
        sources = backend_sources(path, config)
    except E2EError as exc:
        errors.append(str(exc))
        sources = []
    for index, source in enumerate(sources):
        root = source["path"]
        if not root.is_dir():
            errors.append(f"backend code path does not exist: {source['configuredPath']}")
            continue
        if source["language"] not in (*LANGUAGE_MARKERS, "unknown"):
            errors.append(
                f"sources.backendCode[{index}].language must be auto, go, java, python, or node"
            )
        for field in ("include", "exclude"):
            if not isinstance(source[field], list):
                errors.append(f"sources.backendCode[{index}].{field} must be a list")

    execution = nested(config, "execution", default={}) or {}
    try:
        duration_seconds(execution.get("timeout", "10m"), "execution.timeout")
        duration_seconds(execution.get("roleAggregationWait", "30s"), "execution.roleAggregationWait")
        duration_seconds(execution.get("membershipWait", "30s"), "execution.membershipWait")
    except E2EError as exc:
        errors.append(str(exc))
    if execution.get("cleanup", "always") not in ("always", "never"):
        errors.append("execution.cleanup must be always or never")
    mappings = nested(config, "apiMappings", default={}) or {}
    if not isinstance(mappings, dict):
        errors.append("apiMappings must be an object keyed by operationId or METHOD path")
    else:
        required = {"apiGroup", "version", "resource", "verb", "scope"}
        for key, mapping in mappings.items():
            if not isinstance(mapping, dict):
                errors.append(f"apiMappings.{key} must be an object")
                continue
            missing = sorted(required - set(mapping))
            if missing:
                errors.append(f"apiMappings.{key} is missing: {', '.join(missing)}")
    scenarios = nested(config, "scenarios", default={}) or {}
    if scenarios:
        if not isinstance(scenarios, dict):
            errors.append("scenarios must be an object")
        else:
            errors.extend(validate_scenarios(path, config, scenarios))
    return errors


def validate_scenarios(path: Path, config: dict[str, Any], scenarios: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("overrides", "additional"):
        if not isinstance(scenarios.get(field, []), list):
            errors.append(f"scenarios.{field} must be a list")
    if not isinstance(scenarios.get("variables", {}), dict):
        errors.append("scenarios.variables must be an object")
    overrides = scenarios.get("overrides", []) if isinstance(scenarios.get("overrides", []), list) else []
    for index, override in enumerate(overrides):
        prefix = f"scenarios.overrides[{index}]"
        if not isinstance(override, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not override.get("operationId") and not (override.get("method") and override.get("path")):
            errors.append(f"{prefix} requires operationId or method and path")
        errors.extend(validate_evidence(prefix, override.get("evidence", []), path))
    additional = scenarios.get("additional", []) if isinstance(scenarios.get("additional", []), list) else []
    identifiers: list[str] = []
    roles = set(nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES)
    for index, scenario in enumerate(additional):
        prefix = f"scenarios.additional[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = str(scenario.get("id", "")).strip()
        if not identifier:
            errors.append(f"{prefix}.id is required")
        else:
            identifiers.append(identifier)
        status = str(scenario.get("status", "")).upper()
        if status and status != "NEEDS_CONFIGURATION":
            errors.append(f"{prefix}.status may only be NEEDS_CONFIGURATION")
        actor = str(scenario.get("actor", ""))
        if not status and actor not in roles:
            errors.append(f"{prefix}.actor must be one of the configured roles")
        request = scenario.get("request")
        if not status and (
            not isinstance(request, dict) or not request.get("method") or not request.get("path")
        ):
            errors.append(f"{prefix}.request requires method and path")
        setup = scenario.get("setup", []) or []
        if not isinstance(setup, list):
            errors.append(f"{prefix}.setup must be a list")
            setup = []
        for step_index, step in enumerate(setup):
            if not isinstance(step, dict) or not step.get("method") or not step.get("path"):
                errors.append(f"{prefix}.setup[{step_index}] requires method and path")
        dependencies = scenario.get("dependsOn", []) or []
        if not isinstance(dependencies, list):
            errors.append(f"{prefix}.dependsOn must be a list")
        if not status and not isinstance(scenario.get("authorization"), dict):
            errors.append(f"{prefix}.authorization is required")
        if not status and not isinstance(scenario.get("expect"), dict):
            errors.append(f"{prefix}.expect is required")
        errors.extend(validate_evidence(prefix, scenario.get("evidence", []), path))
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        errors.append("scenario IDs must be unique: " + ", ".join(duplicates))
    known = set(identifiers)
    for scenario in additional:
        if not isinstance(scenario, dict):
            continue
        for dependency in scenario.get("dependsOn", []) or []:
            if dependency not in known:
                errors.append(f"scenario {scenario.get('id', '<unknown>')} depends on unknown scenario {dependency}")
    try:
        order_scenarios(additional)
    except E2EError as exc:
        errors.append(str(exc))
    return errors


def validate_evidence(prefix: str, evidence: Any, config_path: Path) -> list[str]:
    if not isinstance(evidence, list) or not evidence:
        return [f"{prefix}.evidence must contain at least one design source"]
    errors: list[str] = []
    for index, item in enumerate(evidence):
        field = f"{prefix}.evidence[{index}]"
        if not isinstance(item, dict) or not item.get("type") or not item.get("reference"):
            errors.append(f"{field} requires type and reference")
            continue
        certainty = str(item.get("certainty", ""))
        if certainty not in ("confirmed", "inferred", "runtime"):
            errors.append(f"{field}.certainty must be confirmed, inferred, or runtime")
        if item.get("type") == "source":
            reference = str(item["reference"])
            raw_path, separator, line = reference.rpartition(":")
            if not separator or not line.isdigit():
                errors.append(f"{field}.reference must use path:line for source evidence")
            elif not resolve(config_path, raw_path).is_file():
                errors.append(f"{field}.reference source does not exist: {raw_path}")
    return errors


def order_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("id")): item
        for item in scenarios
        if isinstance(item, dict) and item.get("id")
    }
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        if identifier in visiting:
            raise E2EError(f"scenario dependency cycle contains {identifier}")
        visiting.add(identifier)
        for dependency in by_id[identifier].get("dependsOn", []) or []:
            if dependency in by_id:
                visit(str(dependency))
        visiting.remove(identifier)
        visited.add(identifier)
        ordered.append(by_id[identifier])

    for identifier in by_id:
        visit(identifier)
    return ordered


def choose_auth(config: dict[str, Any]) -> str:
    auth = nested(config, "auth", default={}) or {}
    mode = auth.get("mode", "auto")
    if mode == "token" or (mode == "auto" and auth.get("adminToken")):
        return "token"
    return "password"


def login(config: dict[str, Any]) -> str:
    auth = nested(config, "auth", default={}) or {}
    if choose_auth(config) == "token":
        return str(auth.get("adminToken", ""))
    target = nested(config, "target", default={}) or {}
    client = Client(
        str(target["ksAPIServer"]),
        insecure=bool(nested(config, "target", "tls", "insecureSkipVerify", default=False)),
    )
    body = {
        "grant_type": "password",
        "username": auth.get("adminUsername", "admin"),
        "password": auth.get("adminPassword", ""),
        "client_id": auth.get("oauthClientID", "kubesphere"),
        "client_secret": auth.get("oauthClientSecret", "kubesphere"),
    }
    path = str(auth.get("loginPath", "/oauth/token"))
    response = client.request("POST", path, body, "application/x-www-form-urlencoded")
    if response.status >= 400:
        raise E2EError(f"admin login failed: HTTP {response.status} at {path}")
    try:
        payload = json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise E2EError(f"admin login at {path} returned non-JSON content") from exc
    token = extract_token(payload)
    if not token:
        raise E2EError(f"admin login at {path} returned no bearer token")
    return token


def extract_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("access_token", "token", "accessToken"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        for item in value.values():
            token = extract_token(item)
            if token:
                return token
    elif isinstance(value, list):
        for item in value:
            token = extract_token(item)
            if token:
                return token
    return ""


def renderer_command(path: Path, config: dict[str, Any]) -> tuple[str, list[str]]:
    extension = nested(config, "sources", "extension", default={}) or {}
    root = resolve(path, str(extension.get("path", "")))
    renderer = extension.get("renderer", "auto")
    if renderer == "auto":
        renderer = "ksbuilder" if (root / "extension.yaml").is_file() else "helm"
    if renderer not in ("ksbuilder", "helm"):
        raise E2EError("extension renderer must be auto, ksbuilder, or helm")
    executable = shutil.which(renderer)
    if not executable:
        raise E2EError(f"{renderer} is required to render {root}")
    values: list[str] = []
    for raw in extension.get("valuesFiles", []) or []:
        values.extend(["--values", str(resolve(path, str(raw)))])
    kube_version = str(extension.get("kubeVersion", "")).strip()
    if not kube_version and (root / "extension.yaml").is_file():
        metadata = load_yaml(root / "extension.yaml") or {}
        kube_version = minimum_kube_version(str(metadata.get("kubeVersion", "")))
    version_args = ["--kube-version", kube_version] if kube_version else []
    if renderer == "ksbuilder":
        return renderer, [executable, "template", str(root), "--dry-run=client", *version_args, *values]
    release = sanitize_name(str(extension.get("releaseName", "extension-e2e")))
    namespace = str(extension.get("namespace", "default"))
    return renderer, [executable, "template", release, str(root), "--namespace", namespace, *version_args, *values]


def minimum_kube_version(constraint: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", constraint)
    return match.group(1) if match else ""


def render_roletemplates(path: Path, config: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    renderer, command = renderer_command(path, config)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if (
        result.returncode != 0
        and renderer == "ksbuilder"
        and "incompatible with Kubernetes v1.20.0" in (result.stderr + result.stdout)
    ):
        result = render_extension_children(path, config)
        renderer = "helm-fallback"
    if result.returncode != 0:
        raise E2EError(f"{renderer} render failed: {summarize(result.stderr or result.stdout)}")
    templates: list[dict[str, Any]] = []
    try:
        documents = yaml.safe_load_all(result.stdout)
        for document in documents:
            if not isinstance(document, dict):
                continue
            if document.get("kind") == "RoleTemplate":
                templates.append(document)
            if document.get("kind") == "List":
                templates.extend(
                    item for item in document.get("items", []) if isinstance(item, dict) and item.get("kind") == "RoleTemplate"
                )
    except yaml.YAMLError as exc:
        raise E2EError(f"{renderer} emitted invalid YAML: {exc}") from exc
    if not templates:
        raise E2EError("rendered extension contains no RoleTemplate resources")
    return renderer, templates


def merge_dict(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_dict(target[key], value)
        else:
            target[key] = value
    return target


def render_extension_children(path: Path, config: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    extension = nested(config, "sources", "extension", default={}) or {}
    root = resolve(path, str(extension.get("path", "")))
    metadata = load_yaml(root / "extension.yaml") or {}
    dependency_names = [
        item.get("name")
        for item in metadata.get("dependencies", [])
        if isinstance(item, dict) and item.get("name")
    ]
    helm = shutil.which("helm")
    if not helm:
        return subprocess.CompletedProcess([], 1, "", "helm is required for the ksbuilder compatibility fallback")
    root_values: dict[str, Any] = {}
    default_values = root / "values.yaml"
    if default_values.is_file():
        root_values = load_yaml(default_values) or {}
    for raw in extension.get("valuesFiles", []) or []:
        value = load_yaml(resolve(path, str(raw))) or {}
        if isinstance(value, dict):
            merge_dict(root_values, value)
    kube_version = str(extension.get("kubeVersion", "")).strip() or minimum_kube_version(
        str(metadata.get("kubeVersion", ""))
    )
    namespace = str(extension.get("namespace", metadata.get("namespace", "default")))
    release = sanitize_name(str(extension.get("releaseName", metadata.get("name", "extension"))))
    outputs: list[str] = []
    for name in dependency_names:
        chart = root / "charts" / str(name)
        if not (chart / "Chart.yaml").is_file():
            continue
        override: dict[str, Any] = {}
        if isinstance(root_values.get("global"), dict):
            override["global"] = root_values["global"]
        if isinstance(root_values.get(name), dict):
            merge_dict(override, root_values[name])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as values_file:
            yaml.safe_dump(override, values_file, allow_unicode=True, sort_keys=False)
            values_file.flush()
            command = [
                helm,
                "template",
                f"{release}-{sanitize_name(str(name))}",
                str(chart),
                "--namespace",
                namespace,
                "--values",
                values_file.name,
            ]
            if kube_version:
                command.extend(["--kube-version", kube_version])
            rendered = subprocess.run(command, capture_output=True, text=True, check=False)
        if rendered.returncode != 0:
            return rendered
        outputs.append(rendered.stdout)
    if not outputs:
        return subprocess.CompletedProcess([], 1, "", f"no renderable child charts found under {root / 'charts'}")
    return subprocess.CompletedProcess([], 0, "\n---\n".join(outputs), "")


def load_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        value = load_yaml(path)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise E2EError(f"cannot parse API document {path}: {exc}") from exc


def infer_api(path: str, operation: dict[str, Any], method: str) -> dict[str, Any]:
    clean = path.split("?", 1)[0]
    parts = [part for part in clean.split("/") if part]
    group = version = ""
    issues: list[str] = []
    for marker in ("kapis", "apis"):
        if marker in parts:
            index = parts.index(marker)
            if len(parts) > index + 2:
                group, version = parts[index + 1], parts[index + 2]
            break
    scope = "cluster"
    resource = ""
    for marker, value in (("workspaces", "workspace"), ("namespaces", "namespace")):
        if marker in parts:
            scope = value
            index = parts.index(marker)
            if len(parts) > index + 2:
                resource = parts[index + 2]
            break
    tags = operation.get("tags") or []
    if resource.startswith("{") or not resource:
        tail = parts[-1] if parts else ""
        resource = tail if not tail.startswith("{") else (parts[-2] if len(parts) > 1 else "")
    if resource.startswith("{") or resource in ("workspace", "namespace", "cluster"):
        resource = str(tags[0]) if tags else ""
    verb = VERBS.get(method, "")
    if method == "get":
        placeholders = re.findall(r"{([^}]+)}", clean)
        operation_id = str(operation.get("operationId", "")).lower()
        verb = (
            "get"
            if (placeholders and placeholders[-1] not in ("workspace", "namespace", "cluster", "resources"))
            or operation_id.startswith("get")
            else "list"
        )
    for field, value in (("apiGroup", group), ("version", version), ("resource", resource), ("verb", verb)):
        if not value:
            issues.append(f"cannot infer {field}")
    return {
        "group": group,
        "version": version,
        "resource": resource,
        "verb": verb,
        "scope": scope,
        "mappingSource": "api-document",
        "mappingIssues": issues,
    }


def load_operations(path: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = nested(config, "routing", "pathOverrides", default={}) or {}
    mappings = nested(config, "apiMappings", default={}) or {}
    operations: list[dict[str, Any]] = []
    docs = nested(config, "sources", "apiDocs", default=[]) or []
    for source in docs:
        doc_path = resolve(path, str(source["path"]))
        document = load_document(doc_path)
        if not isinstance(document.get("paths"), dict):
            raise E2EError(f"{doc_path} is not Swagger 2 or OpenAPI 3")
        for raw_path, path_item in document["paths"].items():
            if not isinstance(path_item, dict):
                continue
            resolved_path = str(overrides.get(raw_path, raw_path))
            for method in METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                identity = infer_api(resolved_path, operation, method)
                operation_id = operation.get("operationId") or f"{method.upper()} {resolved_path}"
                operation_key = f"{method.upper()} {resolved_path}"
                mapping = mappings.get(operation_id) or mappings.get(operation_key)
                if mapping:
                    identity.update(
                        {
                            "group": str(mapping["apiGroup"]),
                            "version": str(mapping["version"]),
                            "resource": str(mapping["resource"]),
                            "verb": str(mapping["verb"]),
                            "scope": str(mapping["scope"]),
                            "mappingSource": "config",
                            "mappingIssues": [],
                        }
                    )
                operations.append(
                    {
                        "operationId": operation_id,
                        "operationKey": operation_key,
                        "summary": operation.get("summary", ""),
                        "method": method.upper(),
                        "path": resolved_path,
                        **identity,
                        "document": relpath(doc_path, path.parent),
                    }
                )
    return operations


SCOPE_LABEL = "iam.kubesphere.io/scope"
MANAGED_LABEL = "kubesphere.io/managed"


def _rule(
    api_groups: list[str] | None = None,
    resources: list[str] | None = None,
    verbs: list[str] | None = None,
    non_resource_urls: list[str] | None = None,
    resource_names: list[str] | None = None,
) -> dict[str, Any]:
    rule: dict[str, Any] = {}
    if non_resource_urls is not None:
        rule["nonResourceURLs"] = list(non_resource_urls)
    else:
        rule["apiGroups"] = list(api_groups or [])
        rule["resources"] = list(resources or [])
        if resource_names:
            rule["resourceNames"] = list(resource_names)
    rule["verbs"] = list(verbs or [])
    return rule


def _allow_all_rules() -> list[dict[str, Any]]:
    return [
        _rule(["*"], ["*"], ["*"]),
        _rule(verbs=["*"], non_resource_urls=["*"]),
    ]


# Base rules and aggregation selectors of the KSE builtin roles.
#
# Source: kse/config/ks-core/templates/{clusterroles,globalroles,builtinroles}.yaml.
# `selector` holds the labels from aggregationRoleTemplates.roleSelector. The
# RoleTemplate controller merges iam.kubesphere.io/scope into that selector
# (pkg/componenthelper/auth/rbac/helper.go), so a role only aggregates
# RoleTemplates whose scope equals the role scope. `selector: None` means the
# role has no aggregation at all.
#
# `rules` are the role's own rules. Even when no extension RoleTemplate is
# aggregated, these rules authorize requests, so an offline expectation must
# include them (cluster-admin and platform-admin allow everything).
BUILTIN_ROLES: dict[str, dict[str, Any]] = {
    "anonymous": {
        "scope": "global",
        "selector": None,
        "rules": [
            _rule(["config.kubesphere.io"], ["configs"], ["get", "list"]),
            _rule(verbs=["get"], non_resource_urls=["/static/images/*"]),
        ],
    },
    "authenticated": {
        "scope": "global",
        "selector": {"iam.kubesphere.io/aggregate-to-authenticated": ""},
        "rules": [
            _rule(["config.kubesphere.io"], ["configs"], ["get", "list"]),
            _rule(["iam.kubesphere.io"], ["users", "roletemplates", "categories"], ["list"]),
            _rule(["resources.kubesphere.io"], ["registry", "git"], ["get", "create"]),
            _rule(["resources.kubesphere.io"], ["clusters"], ["get", "list"]),
            _rule(["*"], ["storageclasses", "storageclasscapabilities", "nodes"], ["get", "list"]),
            _rule(["resources.kubesphere.io"], ["namespaces", "services"], ["list"]),
            _rule(["tenant.kubesphere.io"], ["workspacetemplates"], ["patch"]),
            _rule(["extensions.kubesphere.io"], ["jsbundles"], ["get", "list"]),
            _rule(["kubesphere.io"], ["extensions"], ["get", "list"]),
            _rule(
                ["application.kubesphere.io"],
                ["apps", "categories", "apps/versions", "applications"],
                ["get", "list"],
            ),
            _rule(["cluster.kubesphere.io"], ["labels"], ["get", "list"]),
            _rule(verbs=["get"], non_resource_urls=["/static/images/*"]),
        ],
    },
    "platform-admin": {
        "scope": "global",
        "selector": {},
        "rules": _allow_all_rules(),
    },
    "platform-regular": {"scope": "global", "selector": None, "rules": []},
    "platform-self-provisioner": {
        "scope": "global",
        "selector": None,
        "rules": [_rule(["tenant.kubesphere.io"], ["workspaces", "workspacetemplates"], ["create", "watch"])],
    },
    "cluster-admin": {
        "scope": "cluster",
        "selector": {},
        "rules": _allow_all_rules(),
    },
    "cluster-viewer": {
        "scope": "cluster",
        "selector": {"iam.kubesphere.io/aggregate-to-cluster-viewer": ""},
        "rules": [
            _rule(["*"], ["*"], ["get", "list", "watch"]),
            _rule(verbs=["get"], non_resource_urls=["*"]),
        ],
    },
    "workspace-admin": {
        "scope": "workspace",
        "selector": {},
        "rules": [_rule(["*"], ["*"], ["*"])],
    },
    "workspace-viewer": {
        "scope": "workspace",
        "selector": {"iam.kubesphere.io/aggregate-to-viewer": ""},
        "rules": [_rule(["*"], ["*"], ["get", "list", "watch"])],
    },
    "workspace-regular": {
        "scope": "workspace",
        "selector": {"iam.kubesphere.io/aggregate-to-regular": ""},
        "rules": [_rule(["*"], ["workspaces", "workspacemembers"], ["get", "list", "watch"])],
    },
    "workspace-self-provisioner": {
        "scope": "workspace",
        "selector": {"iam.kubesphere.io/aggregate-to-self-provisioner": ""},
        "rules": [],
    },
    "namespace-admin": {
        "scope": "namespace",
        "selector": {MANAGED_LABEL: "true"},
        "rules": [_rule(["*"], ["*"], ["*"])],
    },
    "namespace-operator": {
        "scope": "namespace",
        "selector": {"iam.kubesphere.io/aggregate-to-operator": "", MANAGED_LABEL: "true"},
        "rules": [
            _rule(["*"], ["*"], ["get", "list", "watch"]),
            _rule(
                [
                    "",
                    "apps",
                    "extensions",
                    "batch",
                    "autoscaling",
                    "app.k8s.io",
                    "operations.kubesphere.io",
                    "resources.kubesphere.io",
                    "config.istio.io",
                    "events.k8s.io",
                    "events.kubesphere.io",
                    "snapshot.storage.k8s.io",
                    "networking.k8s.io",
                ],
                ["*"],
                ["*"],
            ),
        ],
    },
    "namespace-viewer": {
        "scope": "namespace",
        "selector": {"iam.kubesphere.io/aggregate-to-viewer": "", MANAGED_LABEL: "true"},
        "rules": [_rule(["*"], ["*"], ["get", "list", "watch"])],
    },
}

# Request scopes reachable from a binding at the given scope. A ClusterRole's
# rules apply cluster-wide, including namespaced resources; a WorkspaceRole
# only reaches its Workspace and its Namespaces; a Role only its Namespace.
SCOPE_REACH = {
    "global": {"global", "cluster", "workspace", "namespace"},
    "cluster": {"cluster", "workspace", "namespace"},
    "workspace": {"workspace", "namespace"},
    "namespace": {"namespace"},
}

# Template scopes reachable from a request scope. A cluster-scope template
# authorizes namespaced requests because its rules apply cluster-wide.
TEMPLATE_SCOPE_CHILDREN = {
    "cluster": {"cluster", "workspace", "namespace"},
    "workspace": {"workspace", "namespace"},
    "namespace": {"namespace"},
}


def role_scope(role: str) -> str:
    model = BUILTIN_ROLES.get(role)
    if model:
        return str(model["scope"])
    if role.startswith("cluster-"):
        return "cluster"
    if role.startswith("workspace-"):
        return "workspace"
    if role.startswith("namespace-"):
        return "namespace"
    if role.startswith("platform-"):
        return "global"
    return ""


def identity_roles(role: str) -> list[str]:
    """Builtin roles that apply to the identity behind `role`.

    Every logged-in user also carries the `authenticated` GlobalRole, and an
    unauthenticated request is authenticated as `system:anonymous`, which
    carries the `anonymous` GlobalRole.
    """
    if role == "no-token":
        return ["anonymous"]
    if role in ("authenticated", "no-permission"):
        return ["authenticated"]
    return ["authenticated", role]


def template_applies(template: dict[str, Any], role: str) -> bool:
    """Whether a RoleTemplate is aggregated into the builtin `role`.

    Both the roleSelector labels and the scope label of the RoleTemplate must
    match. There is no `aggregate-to-cluster-admin` or
    `aggregate-to-platform-admin` label: those roles aggregate every template
    whose scope matches.
    """
    model = BUILTIN_ROLES.get(role)
    if not model:
        return False
    selector = model.get("selector")
    if selector is None:
        return False
    labels = nested(template, "metadata", "labels", default={}) or {}
    if labels.get(SCOPE_LABEL, "") != model["scope"]:
        return False
    return all(labels.get(key) == value for key, value in selector.items())


def rule_matches(rule: dict[str, Any], operation: dict[str, Any]) -> bool:
    if not isinstance(rule, dict):
        return False

    def matches(values: Any, expected: str) -> bool:
        return isinstance(values, list) and ("*" in values or expected in values)

    verbs = [str(verb).lower() for verb in rule.get("verbs", []) or []]
    verb = str(operation.get("verb", "")).lower()
    if "*" not in verbs and verb not in verbs:
        return False

    non_resource_urls = rule.get("nonResourceURLs")
    if non_resource_urls is not None:
        path = str(operation.get("nonResourcePath", ""))
        if not path:
            return False
        return any(
            url == "*" or path == url or (str(url).endswith("*") and path.startswith(str(url)[:-1]))
            for url in non_resource_urls
        )

    group = str(operation.get("group", ""))
    resource = str(operation.get("resource", ""))
    if not (matches(rule.get("apiGroups"), group) and matches(rule.get("resources"), resource)):
        return False

    resource_names = rule.get("resourceNames")
    if resource_names:
        # resourceNames cannot authorize a request that does not name a resource
        # (for example list or create), so an unnamed operation never matches.
        name = str(operation.get("resourceName", ""))
        if not name or name not in resource_names:
            return False
    return True


def rego_decision(identity: str, operation: dict[str, Any]) -> str:
    """Evaluate the builtin `iam.kubesphere.io/rego-override` for known cases.

    `allow` means the Rego policy grants the request, `deny` means it does not,
    and `uncertain` means the decision depends on request attributes that are
    not available offline (for example whether a user reads their own record).
    """
    if identity == "platform-admin":
        # The platform-admin Rego override allows every scope it lists and adds
        # nothing to the role's own `*` rules.
        return "allow"
    if identity != "authenticated":
        return "deny"
    group = str(operation.get("group", ""))
    resource = str(operation.get("resource", ""))
    verb = str(operation.get("verb", "")).lower()
    path = str(operation.get("path", ""))
    if resource == "users":
        # The authenticated Rego override allows every verb on the caller's own
        # user record; it does not restrict self-service to get/list/watch.
        return "uncertain"
    if resource in ("clustermembers", "workspacemembers", "namespacemembers") and verb in (
        "get",
        "list",
        "watch",
    ):
        # Member self-service rules compare input.User.Name with the requested
        # subject and are limited to read verbs.
        return "uncertain"
    if group == "tenant.kubesphere.io" and verb in ("get", "list", "watch") and path.startswith("/kapis/"):
        return "allow"
    if path in ("/api", "/api/v1") and verb == "get":
        return "allow"
    return "deny"


def relation_allowed(role: str, operation_scope: str, target: str) -> bool:
    scope = role_scope(role)
    if role in ("authenticated", "no-permission"):
        return True
    if scope in ("global", "cluster") or target == "own":
        return True
    if scope == "workspace" and operation_scope == "namespace" and target == "sibling-namespace":
        return True
    return False


def operation_targets(operation: dict[str, Any]) -> list[str]:
    path = str(operation.get("path", ""))
    if "{namespace}" in path:
        return ["own", "sibling-namespace", "other-workspace"]
    if "{workspace}" in path:
        return ["own", "other-workspace"]
    return ["cluster"]


def expected_decision(
    templates: list[dict[str, Any]],
    role: str,
    operation: dict[str, Any],
    target: str = "own",
) -> tuple[str, list[str], str]:
    """Return (ALLOW|DENY|UNVERIFIED, sources, reason) for one matrix row."""
    if target != "cluster" and not relation_allowed(role, str(operation.get("scope", "")), target):
        return "DENY", [], "binding scope does not reach the target"
    request_scope = str(operation.get("scope", ""))
    sources: list[str] = []
    uncertain = False
    for identity in identity_roles(role):
        model = BUILTIN_ROLES.get(identity)
        if model is None:
            continue
        if request_scope and request_scope not in SCOPE_REACH.get(str(model["scope"]), {request_scope}):
            continue
        if any(rule_matches(rule, operation) for rule in model.get("rules", [])):
            sources.append(f"builtin:{identity}")
        if model.get("selector") is not None:
            for template in templates:
                if not template_applies(template, identity):
                    continue
                template_scope = str(nested(template, "metadata", "labels", SCOPE_LABEL, default=""))
                if (
                    request_scope
                    and template_scope not in ("", "global")
                    and request_scope not in TEMPLATE_SCOPE_CHILDREN.get(template_scope, {template_scope})
                ):
                    continue
                rules = nested(template, "spec", "rules", default=[]) or []
                if any(rule_matches(rule, operation) for rule in rules if isinstance(rule, dict)):
                    sources.append(str(nested(template, "metadata", "name", default="<unnamed>")))
        rego = rego_decision(identity, operation)
        if rego == "allow":
            sources.append(f"rego:{identity}")
        elif rego == "uncertain":
            uncertain = True
    sources = list(dict.fromkeys(sources))
    if sources:
        return "ALLOW", sources, ""
    if uncertain:
        return (
            "UNVERIFIED",
            [],
            "Rego override depends on the calling user or subject and cannot be evaluated offline",
        )
    return "DENY", [], ""


def expected_allow(
    templates: list[dict[str, Any]],
    role: str,
    operation: dict[str, Any],
    target: str = "own",
) -> tuple[bool, list[str]]:
    decision, sources, _ = expected_decision(templates, role, operation, target)
    return decision == "ALLOW", sources


def build_plan(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    renderer, templates = render_roletemplates(path, config)
    operations = load_operations(path, config)
    scenarios = load_scenarios(path, config)
    for operation in operations:
        fixture = fixture_for(scenarios, operation)
        sar = fixture.get("sar", {}) if fixture else {}
        for key in ("group", "version", "resource", "verb", "scope", "nonResourcePath", "resourceName"):
            if key in sar:
                operation[key] = str(sar[key])
        if fixture and fixture.get("requestPath"):
            operation["requestPath"] = str(fixture["requestPath"])
        if fixture and fixture.get("evidence"):
            operation["evidence"] = fixture["evidence"]
    roles = nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES
    matrix = []
    for operation in operations:
        for role in roles:
            for target in operation_targets(operation):
                decision, sources, reason = expected_decision(templates, role, operation, target)
                matrix.append(
                    {
                        "role": role,
                        "target": target,
                        "operationId": operation["operationId"],
                        "operationKey": operation["operationKey"],
                        "method": operation["method"],
                        "path": operation["path"],
                        "sar": {
                            key: operation[key]
                            for key in (
                                "group",
                                "version",
                                "resource",
                                "verb",
                                "scope",
                                "nonResourcePath",
                                "resourceName",
                            )
                            if key in operation
                        },
                        "expected": decision,
                        "expectedReason": reason,
                        "roleTemplates": sources,
                        "evidence": operation.get("evidence", []),
                    }
                )
    needs_configuration = [
        {
            "operationId": operation["operationId"],
            "reason": "write operation needs a valid request body in scenarios.overrides",
        }
        for operation in operations
        if operation["method"] != "GET" and fixture_for(scenarios, operation) is None
    ]
    needs_configuration.extend(
        {
            "scenario": scenario.get("id", ""),
            "reason": scenario.get("reason", "scenario requires configuration"),
        }
        for scenario in scenarios["additional"]
        if str(scenario.get("status", "")).upper() == "NEEDS_CONFIGURATION"
    )
    needs_configuration.extend(
        {
            "operationId": operation["operationId"],
            "reason": "; ".join(operation.get("mappingIssues", [])),
            "required": f"apiMappings.{operation['operationId']}",
        }
        for operation in operations
        if operation.get("mappingIssues")
    )
    return {
        "generatedAt": utc_now(),
        "renderer": renderer,
        "backendSources": source_inventory(path, config),
        "roleTemplates": [roletemplate_summary(item) for item in templates],
        "operations": operations,
        "matrix": matrix,
        "scenarios": order_scenarios(scenarios["additional"]),
        "needsConfiguration": needs_configuration,
        "testEnvironment": {
            "workspaces": 2,
            "primaryWorkspaceNamespaces": 2,
            "secondaryWorkspaceNamespaces": 1,
            "namespaceResource": "core/v1 Namespace",
            "namespaceCreatePath": "/api/v1/namespaces",
            "workspaceOwnershipLabel": "kubesphere.io/workspace",
            "membershipAPI": "iam.kubesphere.io/v1beta1",
        },
    }


def roletemplate_summary(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": nested(template, "metadata", "name", default=""),
        "labels": nested(template, "metadata", "labels", default={}) or {},
        "rules": nested(template, "spec", "rules", default=[]) or [],
    }


def sanitized_summary(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "ksAPIServer": nested(config, "target", "ksAPIServer", default=""),
        "cluster": nested(config, "target", "cluster", default=""),
        "authenticationMode": choose_auth(config),
        "adminUsername": nested(config, "auth", "adminUsername", default=""),
        "roles": nested(config, "roles", "include", default=DEFAULT_ROLES),
        "testEnvironment": {
            "workspaces": 2,
            "primaryWorkspaceNamespaces": 2,
            "secondaryWorkspaceNamespaces": 1,
            "namespaceResource": "core/v1 Namespace",
            "namespaceCreatePath": "/api/v1/namespaces",
            "workspaceOwnershipLabel": "kubesphere.io/workspace",
            "workspaceMembers": [
                role
                for role in (nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES)
                if str(role).startswith("workspace-")
            ],
            "namespaceMembers": [
                role
                for role in (nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES)
                if str(role).startswith("namespace-")
            ],
            "memberAPI": "iam.kubesphere.io/v1beta1",
        },
        "cleanup": nested(config, "execution", "cleanup", default="always"),
    }


def validate_command(args: argparse.Namespace) -> None:
    path, config = load_config(args.config)
    errors = validate_config_shape(path, config)
    if errors:
        raise E2EError("CONFIGURATION_ERROR:\n- " + "\n- ".join(errors))
    renderer, templates = render_roletemplates(path, config)
    operations = load_operations(path, config)
    token = login(config)
    target = nested(config, "target", default={}) or {}
    client = Client(
        str(target["ksAPIServer"]),
        token,
        bool(nested(config, "target", "tls", "insecureSkipVerify", default=False)),
    )
    response = client.request("GET", "/apis/iam.kubesphere.io/v1beta1/roletemplates?limit=1")
    if response.status >= 400:
        raise E2EError(
            f"ENVIRONMENT_ERROR: credentials cannot read RoleTemplates: HTTP {response.status}"
        )
    api_service_checks = check_apiservices(client, operations)
    failed_services = [item["name"] for item in api_service_checks if item["result"] != "PASS"]
    if failed_services:
        raise E2EError("CLUSTER_ERROR: APIService validation failed: " + ", ".join(failed_services))
    membership_checks = check_membership_apis(client)
    failed_membership = [item["name"] for item in membership_checks if item["result"] != "PASS"]
    if failed_membership:
        raise E2EError(
            "CLUSTER_ERROR: KubeSphere member API validation failed: "
            + ", ".join(failed_membership)
        )
    cluster_checks = check_cluster_readiness(client)
    failed_clusters = [item["name"] for item in cluster_checks if item["result"] != "PASS"]
    if failed_clusters:
        raise E2EError(
            "CLUSTER_ERROR: cluster readiness validation failed; Workspace cleanup may be blocked: "
            + ", ".join(failed_clusters)
        )
    print(
        json.dumps(
            {
                "result": "PASS",
                "config": sanitized_summary(config),
                "renderer": renderer,
                "roleTemplates": len(templates),
                "apiOperations": len(operations),
                "apiServices": api_service_checks,
                "membershipAPIs": membership_checks,
                "clusters": cluster_checks,
                "backendSources": source_inventory(path, config),
                "credentials": "valid",
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def plan_command(args: argparse.Namespace) -> None:
    path, config = load_config(args.config)
    errors = validate_config_shape(path, config)
    if errors:
        raise E2EError("configuration validation failed:\n- " + "\n- ".join(errors))
    plan = build_plan(path, config)
    report_directory = resolve(path, nested(config, "report", "directory", default="./test-results"))
    default_plan = report_directory / str(nested(config, "report", "plan", default="kse-e2e-plan.json"))
    output = Path(args.output).resolve() if args.output else default_plan
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(redact(plan, secret_values(config)), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"result": "PASS", "config": sanitized_summary(config), "plan": str(output), "rows": len(plan["matrix"])}, indent=2, ensure_ascii=False))


def random_password() -> str:
    return "Kse!" + secrets.token_urlsafe(18)


def object_body(api_version: str, kind: str, name: str, run_id: str, spec: Any = None, **metadata: Any) -> dict[str, Any]:
    meta = {"name": name, "labels": {RUN_LABEL: run_id}}
    meta.update(metadata)
    value: dict[str, Any] = {"apiVersion": api_version, "kind": kind, "metadata": meta}
    if spec is not None:
        value["spec"] = spec
    return value


def create_resource(
    client: Client,
    method: str,
    path: str,
    body: Any,
    state: dict[str, Any],
    secrets_to_hide: list[str] | None = None,
) -> Any:
    response = client.request(method, path, body)
    if response.status not in (200, 201):
        detail = redact(summarize(response.body), secrets_to_hide)
        raise E2EError(f"{method} {path} failed: HTTP {response.status}: {detail}")
    name = nested(body, "metadata", "name", default="")
    if method == "POST" and name:
        state["resources"].append({"path": path.rstrip("/") + "/" + urllib.parse.quote(str(name), safe="")})
    try:
        return json.loads(response.body) if response.body else {}
    except json.JSONDecodeError:
        return {}


def wait_ready(client: Client, paths: list[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    pending = set(paths)
    last_status: dict[str, int] = {}
    while pending and time.monotonic() < deadline:
        for path in list(pending):
            response = client.request("GET", path)
            last_status[path] = response.status
            if 200 <= response.status < 300:
                pending.remove(path)
        if pending:
            time.sleep(0.5)
    if pending:
        details = ", ".join(f"{path}=HTTP {last_status.get(path, 0)}" for path in sorted(pending))
        raise E2EError(f"derived roles were not ready within {timeout:g}s: {details}")


def wait_namespaces(client: Client, expected_workspaces: dict[str, str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    pending = dict(expected_workspaces)
    last_reason: dict[str, str] = {}
    while pending and time.monotonic() < deadline:
        for namespace, workspace in list(pending.items()):
            path = f"/api/v1/namespaces/{urllib.parse.quote(namespace, safe='')}"
            response = client.request("GET", path)
            if response.status != 200:
                last_reason[namespace] = f"HTTP {response.status}"
                continue
            try:
                value = json.loads(response.body)
            except json.JSONDecodeError:
                last_reason[namespace] = "invalid JSON"
                continue
            actual_workspace = nested(
                value,
                "metadata",
                "labels",
                "kubesphere.io/workspace",
                default="",
            )
            if actual_workspace != workspace:
                last_reason[namespace] = (
                    f"kubesphere.io/workspace={actual_workspace!r}, expected {workspace!r}"
                )
                continue
            pending.pop(namespace)
        if pending:
            time.sleep(0.5)
    if pending:
        details = ", ".join(
            f"{namespace}: {last_reason.get(namespace, 'not observed')}"
            for namespace in sorted(pending)
        )
        raise E2EError(
            "SETUP_BLOCKED: Kubernetes Namespaces were not ready with the expected "
            f"Workspace ownership within {timeout:g}s: {details}"
        )


def wait_membership(
    client: Client,
    binding_path: str,
    username: str,
    role_name: str,
    workspace: str,
    namespace: str,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    last_status = 0
    while time.monotonic() < deadline:
        response = client.request("GET", binding_path)
        last_status = response.status
        if response.status == 200:
            try:
                binding = json.loads(response.body)
            except json.JSONDecodeError:
                binding = {}
            labels = nested(binding, "metadata", "labels", default={}) or {}
            subjects = binding.get("subjects", []) if isinstance(binding, dict) else []
            role_ref = binding.get("roleRef", {}) if isinstance(binding, dict) else {}
            subject_matches = any(
                isinstance(subject, dict)
                and subject.get("kind") == "User"
                and subject.get("name") == username
                for subject in subjects
            )
            scope_matches = (
                (not workspace or labels.get("kubesphere.io/workspace") == workspace)
                and (not namespace or nested(binding, "metadata", "namespace", default="") == namespace)
            )
            if (
                labels.get("iam.kubesphere.io/user-ref") == username
                and labels.get("iam.kubesphere.io/role-ref") == role_name
                and role_ref.get("name") == role_name
                and subject_matches
                and scope_matches
            ):
                return
        time.sleep(0.5)
    raise E2EError(
        f"SETUP_BLOCKED: membership for {username} as {role_name} "
        f"was not ready within {timeout:g}s (last HTTP {last_status})"
    )


def invite_test_role(
    admin: Client,
    state: dict[str, Any],
    role: str,
    username: str,
    workspace: str,
    namespace: str,
    run_id: str,
) -> tuple[str, str, str] | None:
    if role in ("authenticated", "no-permission"):
        return None
    if role.startswith("cluster-"):
        role_name = role
        collection = "/kapis/iam.kubesphere.io/v1beta1/clustermembers"
        delete_path = collection + "/" + urllib.parse.quote(username, safe="")
        binding_path = (
            "/apis/iam.kubesphere.io/v1beta1/clusterrolebindings/"
            + urllib.parse.quote(f"{username}-{role_name}", safe="")
        )
        scope_workspace = scope_namespace = ""
    elif role.startswith("workspace-"):
        role_name = f"{workspace}-{role.removeprefix('workspace-')}"
        collection = (
            "/kapis/iam.kubesphere.io/v1beta1/workspaces/"
            + urllib.parse.quote(workspace, safe="")
            + "/workspacemembers"
        )
        delete_path = collection + "/" + urllib.parse.quote(username, safe="")
        binding_path = (
            "/apis/iam.kubesphere.io/v1beta1/workspacerolebindings/"
            + urllib.parse.quote(f"{username}-{role_name}", safe="")
        )
        scope_workspace, scope_namespace = workspace, ""
    elif role.startswith("namespace-"):
        role_name = role.removeprefix("namespace-")
        collection = (
            "/kapis/iam.kubesphere.io/v1beta1/namespaces/"
            + urllib.parse.quote(namespace, safe="")
            + "/namespacemembers"
        )
        delete_path = collection + "/" + urllib.parse.quote(username, safe="")
        binding_path = (
            "/apis/iam.kubesphere.io/v1beta1/namespaces/"
            + urllib.parse.quote(namespace, safe="")
            + "/rolebindings/"
            + urllib.parse.quote(f"{username}-{role_name}", safe="")
        )
        scope_workspace, scope_namespace = "", namespace
    elif role.startswith("platform-"):
        bind_test_role(admin, state, role, username, workspace, namespace, run_id)
        return None
    else:
        return None

    response = admin.request("POST", collection, [{"username": username, "roleRef": role_name}])
    if response.status not in (200, 201):
        raise E2EError(
            f"SETUP_BLOCKED: inviting {username} as {role_name} failed: "
            f"HTTP {response.status}: {summarize(response.body)}"
        )
    state["resources"].append({"path": delete_path, "verifyPath": binding_path})
    return binding_path, scope_workspace, scope_namespace


def prepare_environment(config_path: Path, config: dict[str, Any], admin: Client, run_id: str) -> tuple[dict[str, Any], dict[str, Client]]:
    prefix = sanitize_name(nested(config, "execution", "resourcePrefix", default="extension-e2e"))
    suffix = run_id.rsplit("-", 1)[-1]
    workspace = f"{prefix}-{suffix}-a"
    other_workspace = f"{prefix}-{suffix}-b"
    namespace = f"{prefix}-{suffix}-a1"
    sibling_namespace = f"{prefix}-{suffix}-a2"
    other_namespace = f"{prefix}-{suffix}-b1"
    cluster = str(nested(config, "target", "cluster", default="host"))
    manager = str(nested(config, "auth", "adminUsername", default="admin"))
    roles = nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES
    state: dict[str, Any] = {
        "runID": run_id,
        "createdAt": utc_now(),
        "resources": [],
        "users": {},
        "variables": {
            "runID": run_id,
            "cluster": cluster,
            "workspace": workspace,
            "otherWorkspace": other_workspace,
            "namespace": namespace,
            "siblingNamespace": sibling_namespace,
            "otherNamespace": other_namespace,
        },
    }
    state_path = state_file(config_path, config, run_id)
    save_state(state_path, state)

    for name in (workspace, other_workspace):
        body = object_body(
            "tenant.kubesphere.io/v1beta1",
            "WorkspaceTemplate",
            name,
            run_id,
            {
                "placement": {"clusters": [{"name": cluster}]},
                "template": {
                    "metadata": {"labels": {RUN_LABEL: run_id}, "annotations": {"kubesphere.io/creator": manager}},
                    "spec": {"manager": manager},
                },
            },
            annotations={"kubesphere.io/creator": manager},
        )
        create_resource(admin, "POST", "/apis/tenant.kubesphere.io/v1beta1/workspacetemplates", body, state)
        save_state(state_path, state)

    wait_timeout = duration_seconds(
        nested(config, "execution", "roleAggregationWait", default="30s"),
        "execution.roleAggregationWait",
    )
    membership_timeout = duration_seconds(
        nested(config, "execution", "membershipWait", default="30s"),
        "execution.membershipWait",
    )
    if any(role.startswith("workspace-") for role in roles):
        wait_ready(
            admin,
            [
                f"/apis/iam.kubesphere.io/v1beta1/workspaceroles/"
                f"{urllib.parse.quote(workspace + '-' + name)}"
                for name in ("admin", "viewer")
            ],
            wait_timeout,
        )

    for name, ws in (
        (namespace, workspace),
        (sibling_namespace, workspace),
        (other_namespace, other_workspace),
    ):
        body = object_body(
            "v1",
            "Namespace",
            name,
            run_id,
            None,
            annotations={"kubesphere.io/creator": manager},
        )
        body["metadata"]["labels"].update({"kubesphere.io/workspace": ws, "kubesphere.io/managed": "true"})
        create_resource(admin, "POST", "/api/v1/namespaces", body, state)
        save_state(state_path, state)

    wait_namespaces(
        admin,
        {
            namespace: workspace,
            sibling_namespace: workspace,
            other_namespace: other_workspace,
        },
        wait_timeout,
    )
    if any(role.startswith("namespace-") for role in roles):
        wait_ready(
            admin,
            [
                f"/apis/iam.kubesphere.io/v1beta1/namespaces/"
                f"{urllib.parse.quote(namespace)}/roles/{name}"
                for name in ("admin", "operator", "viewer")
            ],
            wait_timeout,
        )

    clients: dict[str, Client] = {"no-token": Client(admin.server, insecure=admin.context is not None)}
    for index, role in enumerate(role for role in roles if role != "no-token"):
        username = sanitize_name(f"{prefix}-{suffix}-{index}-{role}", "e2e-user")
        password = random_password()
        body = object_body(
            "iam.kubesphere.io/v1beta1",
            "User",
            username,
            run_id,
            {"email": f"{username}@example.invalid", "displayName": username, "password": password},
        )
        create_resource(
            admin,
            "POST",
            "/apis/iam.kubesphere.io/v1beta1/users",
            body,
            state,
            [password],
        )
        state["users"][role] = username
        save_state(state_path, state)
        membership = invite_test_role(admin, state, role, username, workspace, namespace, run_id)
        save_state(state_path, state)
        if membership:
            binding_path, scope_workspace, scope_namespace = membership
            role_name = (
                role
                if role.startswith("cluster-")
                else f"{workspace}-{role.removeprefix('workspace-')}"
                if role.startswith("workspace-")
                else role.removeprefix("namespace-")
            )
            wait_membership(
                admin,
                binding_path,
                username,
                role_name,
                scope_workspace,
                scope_namespace,
                membership_timeout,
            )
        token = login_user(config, username, password, wait_timeout)
        clients[role] = Client(admin.server, token, admin.context is not None)

    return state, clients


def bind_test_role(
    admin: Client,
    state: dict[str, Any],
    role: str,
    username: str,
    workspace: str,
    namespace: str,
    run_id: str,
) -> None:
    if role in ("authenticated", "no-permission"):
        return
    role_name = role
    kind = path = ""
    metadata: dict[str, Any] = {
        "name": sanitize_name(f"{run_id[-16:]}-{role}-binding"),
        "labels": {RUN_LABEL: run_id},
    }
    if role.startswith("cluster-"):
        kind, path = "ClusterRoleBinding", "/apis/iam.kubesphere.io/v1beta1/clusterrolebindings"
        role_kind = "ClusterRole"
    elif role.startswith("workspace-"):
        kind, path = "WorkspaceRoleBinding", "/apis/iam.kubesphere.io/v1beta1/workspacerolebindings"
        role_kind = "WorkspaceRole"
        role_name = f"{workspace}-{role.removeprefix('workspace-')}"
        metadata["labels"]["kubesphere.io/workspace"] = workspace
    elif role.startswith("namespace-"):
        kind = "RoleBinding"
        path = f"/apis/iam.kubesphere.io/v1beta1/namespaces/{urllib.parse.quote(namespace)}/rolebindings"
        role_kind = "Role"
        role_name = role.removeprefix("namespace-")
        metadata["namespace"] = namespace
    elif role.startswith("platform-"):
        kind, path = "GlobalRoleBinding", "/apis/iam.kubesphere.io/v1beta1/globalrolebindings"
        role_kind = "GlobalRole"
    else:
        return
    metadata["labels"].update({"iam.kubesphere.io/role-ref": role_name, "iam.kubesphere.io/user-ref": username})
    body = {
        "apiVersion": "iam.kubesphere.io/v1beta1",
        "kind": kind,
        "metadata": metadata,
        "subjects": [{"apiGroup": "iam.kubesphere.io", "kind": "User", "name": username}],
        "roleRef": {"apiGroup": "iam.kubesphere.io", "kind": role_kind, "name": role_name},
    }
    create_resource(admin, "POST", path, body, state)


def login_user(config: dict[str, Any], username: str, password: str, timeout: float = 30) -> str:
    copy = json.loads(json.dumps(config))
    copy["auth"]["mode"] = "password"
    copy["auth"]["adminUsername"] = username
    copy["auth"]["adminPassword"] = password
    copy["auth"]["adminToken"] = ""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            return login(copy)
        except E2EError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise E2EError(f"temporary user {username} could not log in within {timeout:g}s: {last_error}")


def state_file(config_path: Path, config: dict[str, Any], run_id: str) -> Path:
    directory = resolve(config_path, nested(config, "report", "directory", default="./test-results"))
    return directory / f".kse-e2e-state-{run_id}.json"


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def substitute(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, variables) for item in value]
    if isinstance(value, str):
        result = value
        for key, item in variables.items():
            text = str(item)
            result = result.replace("${" + key + "}", text).replace(
                "{" + key + "}", urllib.parse.quote(text, safe="")
            )
        return result
    return value


def load_scenarios(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    configured = nested(config, "scenarios", default={}) or {}
    if not isinstance(configured, dict):
        raise E2EError("scenarios must be an object")
    result = {
        "variables": dict(configured.get("variables", {}) or {}),
        "operations": list(configured.get("overrides", []) or []),
        "additional": list(configured.get("additional", []) or []),
    }
    # Backward-compatible import for configurations created by the first release.
    raw = nested(config, "fixtures", "file", default="")
    if raw:
        value = load_yaml(resolve(config_path, str(raw)))
        if not isinstance(value, dict):
            raise E2EError("fixture file must contain an object")
        result["variables"] = {**(value.get("variables", {}) or {}), **result["variables"]}
        result["operations"] = [*(value.get("operations", []) or []), *result["operations"]]
    return result


def fixture_for(fixtures: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    for item in fixtures.get("operations", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("operationId") == operation["operationId"]:
            return item
        if str(item.get("method", "")).upper() == operation["method"] and item.get("path") == operation["path"]:
            return item
    return None


def executable_case(operation: dict[str, Any], fixture: dict[str, Any] | None, variables: dict[str, str]) -> tuple[bool, str]:
    path = substitute(operation["path"], variables)
    unresolved = re.findall(r"{([^}]+)}", path)
    if unresolved:
        return False, "missing fixture variables: " + ", ".join(unresolved)
    if operation["method"] != "GET" and fixture is None:
        return False, "write operation has no fixture"
    return True, ""


def diagnostic_quality(role: str, operation: dict[str, Any], response: HTTPResponse) -> bool:
    if response.status < 400:
        return True
    body = response.body.lower()
    if role == "no-token":
        if response.status == 401:
            return any(word in body for word in ("unauthorized", "authorization", "token", "认证"))
        return (
            any(word in body for word in ("system:anonymous", "anonymous"))
            and operation["resource"].lower() in body
            and operation["verb"].lower() in body
        )
    if response.status == 403:
        scope_terms = {
            "cluster": ("cluster",),
            "workspace": ("workspace",),
            "namespace": ("namespace", "project"),
            "global": ("global", "platform"),
        }.get(operation["scope"], (operation["scope"],))
        return (
            operation["resource"].lower() in body
            and operation["verb"].lower() in body
            and any(term in body for term in scope_terms)
        )
    return bool(body.strip())


def diagnose(expected: str, response: HTTPResponse | None, error: str, diagnostic_ok: bool) -> tuple[str, str]:
    if error:
        return "network/client", "check ks-apiserver URL, TLS, network, and APIService availability"
    assert response is not None
    if not diagnostic_ok:
        return "error response", "include the denied user, verb, resource, scope, field, or object in the error"
    if expected == "DENY" and 200 <= response.status < 300:
        return "authorization bypass", "inspect route authorization, SAR attributes, RoleTemplates, and aggregation immediately"
    return {
        401: ("authentication", "check user login, token validity, and Authorization forwarding"),
        403: ("RBAC/SAR", "check RoleTemplate labels/rules, aggregated roles, bindings, and SAR attributes"),
        404: ("route/deployment", "check API documentation path, APIService, deployed version, and fixture objects"),
        400: ("fixture/validation", "check request fixture, business validation, and admission response"),
    }.get(response.status, ("APIService/upstream", "check request ID in ks-apiserver and extension backend logs") if response.status >= 500 else ("HTTP contract", "compare the API documentation and response contract"))


def compare_live_templates(admin: Client, config: dict[str, Any], source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selector = nested(config, "discovery", "roleTemplateSelector", default={}) or {}
    query = ""
    if selector:
        query = "?" + urllib.parse.urlencode({"labelSelector": ",".join(f"{key}={value}" for key, value in selector.items())})
    response = admin.request("GET", "/apis/iam.kubesphere.io/v1beta1/roletemplates" + query)
    if response.status != 200:
        return [{"result": "BLOCKED", "actualStatus": response.status, "message": "cannot list live RoleTemplates"}]
    try:
        items = json.loads(response.body).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        items = []
    live = {nested(item, "metadata", "name", default=""): roletemplate_summary(item) for item in items}
    checks = []
    for template in source:
        summary = roletemplate_summary(template)
        actual = live.get(summary["name"])
        checks.append(
            {
                "name": summary["name"],
                "result": "PASS" if roletemplate_equivalent(summary, actual) else "FAIL",
                "source": summary,
                "live": actual,
            }
        )
    return checks


def check_apiservices(admin: Client, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identities = sorted(
        {
            (str(item.get("group", "")), str(item.get("version", "")))
            for item in operations
            if str(item.get("path", "")).startswith("/kapis/")
            and item.get("group")
            and item.get("version")
        }
    )
    checks: list[dict[str, Any]] = []
    for group, version in identities:
        name = f"{version}.{group}"
        path = f"/apis/extensions.kubesphere.io/v1alpha1/apiservices/{urllib.parse.quote(name, safe='')}"
        response = admin.request("GET", path)
        checks.append(
            {
                "name": name,
                "path": path,
                "actualStatus": response.status,
                "result": "PASS" if response.status == 200 else "FAIL",
                "message": "" if response.status == 200 else summarize(response.body),
            }
        )
    return checks


def check_membership_apis(admin: Client) -> list[dict[str, Any]]:
    paths = {
        "cluster-members": "/kapis/iam.kubesphere.io/v1beta1/clustermembers?limit=1",
        "workspace-members": "/kapis/iam.kubesphere.io/v1beta1/workspaces/kse-e2e-api-probe/workspacemembers?limit=1",
        "namespace-members": "/kapis/iam.kubesphere.io/v1beta1/namespaces/default/namespacemembers?limit=1",
    }
    checks: list[dict[str, Any]] = []
    for name, path in paths.items():
        response = admin.request("GET", path)
        checks.append(
            {
                "name": name,
                "path": path,
                "actualStatus": response.status,
                "result": "PASS" if response.status == 200 else "FAIL",
                "message": "" if response.status == 200 else summarize(response.body),
            }
        )
    return checks


def check_cluster_readiness(admin: Client) -> list[dict[str, Any]]:
    path = "/apis/cluster.kubesphere.io/v1alpha1/clusters?limit=500"
    response = admin.request("GET", path)
    if response.status != 200:
        return [
            {
                "name": "cluster-list",
                "path": path,
                "actualStatus": response.status,
                "result": "FAIL",
                "reason": "ClusterListFailed",
                "message": summarize(response.body),
            }
        ]
    try:
        items = json.loads(response.body).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        items = []
    checks: list[dict[str, Any]] = []
    for item in items:
        name = str(nested(item, "metadata", "name", default=""))
        conditions = nested(item, "status", "conditions", default=[]) or []
        ready = next(
            (
                condition
                for condition in conditions
                if isinstance(condition, dict) and condition.get("type") == "Ready"
            ),
            {},
        )
        is_ready = ready.get("status") == "True"
        checks.append(
            {
                "name": name,
                "path": f"/apis/cluster.kubesphere.io/v1alpha1/clusters/{urllib.parse.quote(name, safe='')}",
                "actualStatus": response.status,
                "result": "PASS" if is_ready else "FAIL",
                "reason": str(ready.get("reason", "ReadyConditionMissing")),
                "message": "" if is_ready else str(ready.get("message", "cluster Ready condition is missing")),
            }
        )
    return checks


def roletemplate_equivalent(source: dict[str, Any], live: dict[str, Any] | None) -> bool:
    if not live or source.get("rules") != live.get("rules"):
        return False
    live_labels = live.get("labels", {})
    return all(live_labels.get(key) == value for key, value in source.get("labels", {}).items())


def compare_aggregations(
    admin: Client,
    templates: list[dict[str, Any]],
    state: dict[str, Any],
    roles: list[str],
) -> list[dict[str, Any]]:
    workspace = state["variables"]["workspace"]
    namespace = state["variables"]["namespace"]
    paths = {
        "cluster-admin": "/apis/iam.kubesphere.io/v1beta1/clusterroles/cluster-admin",
        "cluster-viewer": "/apis/iam.kubesphere.io/v1beta1/clusterroles/cluster-viewer",
        "workspace-admin": f"/apis/iam.kubesphere.io/v1beta1/workspaceroles/{urllib.parse.quote(workspace + '-admin')}",
        "workspace-viewer": f"/apis/iam.kubesphere.io/v1beta1/workspaceroles/{urllib.parse.quote(workspace + '-viewer')}",
        "namespace-admin": f"/apis/iam.kubesphere.io/v1beta1/namespaces/{urllib.parse.quote(namespace)}/roles/admin",
        "namespace-operator": f"/apis/iam.kubesphere.io/v1beta1/namespaces/{urllib.parse.quote(namespace)}/roles/operator",
        "namespace-viewer": f"/apis/iam.kubesphere.io/v1beta1/namespaces/{urllib.parse.quote(namespace)}/roles/viewer",
        "platform-admin": "/apis/iam.kubesphere.io/v1beta1/globalroles/platform-admin",
        "authenticated": "/apis/iam.kubesphere.io/v1beta1/globalroles/authenticated",
    }
    checks: list[dict[str, Any]] = []
    for role in roles:
        path = paths.get(role)
        if not path:
            continue
        expected = sorted(
            str(nested(template, "metadata", "name", default=""))
            for template in templates
            if template_applies(template, role)
        )
        response = admin.request("GET", path)
        actual: list[str] = []
        if response.status == 200:
            try:
                payload = json.loads(response.body)
                actual = sorted(nested(payload, "aggregationRoleTemplates", "templateNames", default=[]) or [])
            except json.JSONDecodeError:
                pass
        missing = sorted(set(expected) - set(actual))
        checks.append(
            {
                "role": role,
                "path": path,
                "expectedRoleTemplates": expected,
                "actualRoleTemplates": actual,
                "missing": missing,
                "actualStatus": response.status,
                "result": "PASS" if response.status == 200 and not missing else "FAIL",
            }
        )
    return checks


def value_at_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            raise E2EError(f"response assertion path does not exist: {path}")
    return current


def check_assertions(body: str, fixture: dict[str, Any] | None) -> str:
    assertions = fixture.get("assertions", []) if fixture else []
    if not assertions:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "response assertions require a JSON response"
    for assertion in assertions:
        if not isinstance(assertion, dict) or not assertion.get("path"):
            return "each response assertion requires path"
        try:
            actual = value_at_path(payload, str(assertion["path"]))
        except E2EError as exc:
            if assertion.get("absent") is True:
                continue
            return str(exc)
        if assertion.get("absent") is True:
            return f"response assertion {assertion['path']} expected the field to be absent"
        if assertion.get("exists") is True:
            continue
        if "equals" in assertion and actual != assertion["equals"]:
            return f"response assertion {assertion['path']} expected {assertion['equals']!r}, got {actual!r}"
        if "contains" in assertion:
            expected = assertion["contains"]
            contains = expected in actual if isinstance(actual, (list, str, dict)) else False
            if isinstance(actual, list) and not contains:
                contains = any(
                    isinstance(item, dict)
                    and expected in (item.get("name"), nested(item, "metadata", "name", default=None))
                    for item in actual
                )
            if not contains:
                return f"response assertion {assertion['path']} expected to contain {expected!r}"
        if "omits" in assertion:
            unexpected = assertion["omits"]
            contains = unexpected in actual if isinstance(actual, (list, str, dict)) else False
            if isinstance(actual, list) and not contains:
                contains = any(
                    isinstance(item, dict)
                    and unexpected in (item.get("name"), nested(item, "metadata", "name", default=None))
                    for item in actual
                )
            if contains:
                return f"response assertion {assertion['path']} expected to omit {unexpected!r}"
    return ""


def capture_values(body: str, capture: dict[str, Any], variables: dict[str, Any]) -> str:
    if not capture:
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "response capture requires a JSON response"
    for name, raw_path in capture.items():
        try:
            variables[str(name)] = str(value_at_path(payload, str(raw_path)))
        except E2EError as exc:
            return str(exc)
    return ""


def unresolved_variables(value: Any) -> list[str]:
    missing: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            missing.update(unresolved_variables(item))
    elif isinstance(value, list):
        for item in value:
            missing.update(unresolved_variables(item))
    elif isinstance(value, str):
        for first, second in re.findall(r"\$\{([^}]+)}|{([^}]+)}", value):
            missing.add(first or second)
    return sorted(missing)


def register_cleanup(state: dict[str, Any], cleanup: Any, variables: dict[str, Any]) -> None:
    items = cleanup if isinstance(cleanup, list) else [cleanup]
    for item in items:
        if isinstance(item, str):
            item = {"method": "DELETE", "path": item}
        if not isinstance(item, dict) or not item.get("path"):
            continue
        resolved = substitute(item, variables)
        state["resources"].append(
            {
                "method": str(resolved.get("method", "DELETE")).upper(),
                "path": str(resolved["path"]),
            }
        )


def scenario_operation(scenario: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    authorization = scenario.get("authorization", {}) or {}
    operation = {
        "group": str(authorization.get("group", "")),
        "version": str(authorization.get("version", "")),
        "resource": str(authorization.get("resource", "")),
        "verb": str(authorization.get("verb", VERBS.get(str(request.get("method", "")).lower(), ""))),
        "scope": str(authorization.get("scope", "cluster")),
    }
    # Non-resource and named-resource requests need attributes that the generic
    # group/resource/verb triple cannot express.
    for key in ("nonResourcePath", "resourceName"):
        if key in authorization:
            operation[key] = str(authorization[key])
    return operation


def execute_scenarios(
    plan: dict[str, Any],
    config_path: Path,
    config: dict[str, Any],
    state: dict[str, Any],
    admin: Client,
    clients: dict[str, Client],
) -> list[dict[str, Any]]:
    configured = load_scenarios(config_path, config)
    variables: dict[str, Any] = {**state["variables"], **configured["variables"]}
    outcomes: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for raw in plan.get("scenarios", []):
        scenario = substitute(raw, {str(key): str(value) for key, value in variables.items()})
        identifier = str(scenario.get("id", ""))
        result: dict[str, Any] = {
            "scenario": identifier,
            "role": scenario.get("actor", ""),
            "evidence": scenario.get("evidence", []),
            "functionalPassed": False,
            "diagnosticPassed": True,
            "passed": False,
        }
        if str(scenario.get("status", "")).upper() == "NEEDS_CONFIGURATION":
            result.update(
                {
                    "result": "NEEDS_CONFIGURATION",
                    "skipped": True,
                    "skipReason": scenario.get("reason", "scenario requires configuration"),
                }
            )
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        failed_dependencies = [
            dependency
            for dependency in scenario.get("dependsOn", []) or []
            if outcomes.get(str(dependency)) != "PASS"
        ]
        if failed_dependencies:
            result.update(
                {
                    "result": "BLOCKED",
                    "skipped": True,
                    "skipReason": "dependency did not pass: " + ", ".join(failed_dependencies),
                }
            )
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        error = ""
        needs_configuration = False
        for raw_step in raw.get("setup", []) or []:
            step = substitute(raw_step, {str(key): str(value) for key, value in variables.items()})
            missing = unresolved_variables(
                {
                    key: step[key]
                    for key in ("actor", "method", "path", "body", "contentType", "expectedStatuses")
                    if key in step
                }
            )
            if missing:
                error = "missing scenario variables: " + ", ".join(missing)
                needs_configuration = True
                break
            step_actor = str(step.get("actor", "admin"))
            client = admin if step_actor == "admin" else clients.get(step_actor)
            if client is None:
                error = f"setup actor is unavailable: {step_actor}"
                break
            try:
                response = client.request(
                    str(step["method"]).upper(),
                    str(step["path"]),
                    step.get("body"),
                    str(step.get("contentType", "application/json")),
                )
            except E2EError as exc:
                error = str(exc)
                break
            expected_statuses = step.get("expectedStatuses", [200, 201, 202, 204])
            if response.status not in expected_statuses:
                error = f"setup {step['method']} {step['path']} returned HTTP {response.status}: {summarize(response.body)}"
                break
            error = capture_values(response.body, step.get("capture", {}) or {}, variables)
            if error:
                break
            if step.get("cleanup"):
                register_cleanup(state, step["cleanup"], variables)
            elif step.get("cleanupPath"):
                register_cleanup(state, str(step["cleanupPath"]), variables)
            save_state(state_file(config_path, config, state["runID"]), state)
        if error:
            result.update(
                {
                    "result": "NEEDS_CONFIGURATION" if needs_configuration else "FAIL",
                    "error": error,
                    "likelyFailureLayer": "configuration" if needs_configuration else "scenario setup",
                    "suggestion": (
                        "add the named values to scenarios.variables"
                        if needs_configuration
                        else "check setup dependencies, request bodies, and source-derived assumptions"
                    ),
                }
            )
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        scenario = substitute(raw, {str(key): str(value) for key, value in variables.items()})
        request = scenario["request"]
        missing = unresolved_variables(request)
        if missing:
            result.update(
                {
                    "result": "NEEDS_CONFIGURATION",
                    "skipped": True,
                    "skipReason": "missing scenario variables: " + ", ".join(missing),
                }
            )
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        actor = str(scenario["actor"])
        client = clients.get(actor)
        if client is None:
            result.update({"result": "BLOCKED", "error": f"test user client is unavailable: {actor}"})
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        started = time.monotonic()
        try:
            response = client.request(
                str(request["method"]).upper(),
                str(request["path"]),
                request.get("body"),
                str(request.get("contentType", "application/json")),
            )
        except E2EError as exc:
            result.update(
                {
                    "method": str(request["method"]).upper(),
                    "path": request["path"],
                    "result": "FAIL",
                    "error": str(exc),
                    "likelyFailureLayer": "network/client",
                    "suggestion": "check ks-apiserver URL, TLS, network, and APIService availability",
                }
            )
            outcomes[identifier] = result["result"]
            results.append(result)
            continue
        operation = scenario_operation(scenario, request)
        expected = scenario.get("expect", {}) or {}
        authorization = str(expected.get("authorization", "ALLOW")).upper()
        statuses = expected.get("statuses")
        if statuses:
            functional = response.status in statuses
        elif authorization == "DENY":
            functional = response.status == 403
        else:
            functional = 200 <= response.status < 300
        # An explicit `statuses` list describes business responses for an
        # authorized caller; it must never turn a DENY bypass into PASS.
        if authorization == "DENY" and response.status != 403:
            functional = False
        assertion_error = check_assertions(response.body, scenario) if functional else ""
        capture_error = capture_values(response.body, scenario.get("capture", {}) or {}, variables) if functional else ""
        error = assertion_error or capture_error
        functional = functional and not error
        required_error_terms = [str(item).lower() for item in expected.get("errorContains", []) or []]
        diagnostic = diagnostic_quality(actor, operation, response)
        if required_error_terms:
            diagnostic = all(term in response.body.lower() for term in required_error_terms)
        layer, suggestion = diagnose(authorization, response, error, diagnostic)
        if actor == "no-token" and response.status == 401:
            layer = "authentication"
            suggestion = (
                "ks-apiserver returned 401 to a request without Authorization; a missing token is "
                "authenticated as system:anonymous and must be denied by RBAC with 403. Verify the URL "
                "targets ks-apiserver (not Console), anonymous authentication is enabled, and the route "
                "is not excluded from RBAC"
            )
        if scenario.get("cleanup"):
            register_cleanup(state, scenario["cleanup"], variables)
            save_state(state_file(config_path, config, state["runID"]), state)
        result.update(
            {
                "method": str(request["method"]).upper(),
                "path": request["path"],
                "sar": operation,
                "expected": authorization,
                "expectedStatuses": statuses or [],
                "actualStatus": response.status,
                "durationMs": round((time.monotonic() - started) * 1000),
                "requestID": response.headers.get("Audit-Id")
                or response.headers.get("X-Request-ID")
                or response.headers.get("X-Trace-ID")
                or "",
                "response": summarize(response.body),
                "functionalPassed": functional,
                "diagnosticPassed": diagnostic,
                "passed": functional and diagnostic,
                "result": "PASS" if functional and diagnostic else "FAIL",
                "error": error,
                "likelyFailureLayer": "" if functional and diagnostic else layer,
                "suggestion": "" if functional and diagnostic else suggestion,
            }
        )
        outcomes[identifier] = result["result"]
        results.append(result)
    return results


def execute_matrix(
    plan: dict[str, Any],
    config_path: Path,
    config: dict[str, Any],
    state: dict[str, Any],
    clients: dict[str, Client],
) -> list[dict[str, Any]]:
    fixtures = load_scenarios(config_path, config)
    variables = {**state["variables"], **{str(k): str(v) for k, v in (fixtures.get("variables", {}) or {}).items()}}
    operation_by_key = {item["operationKey"]: item for item in plan["operations"]}
    results: list[dict[str, Any]] = []
    for row in plan["matrix"]:
        operation = operation_by_key[row["operationKey"]]
        fixture = fixture_for(fixtures, operation)
        target = row.get("target", "own")
        workspace = state["variables"]["workspace"]
        namespace = state["variables"]["namespace"]
        if target == "sibling-namespace":
            namespace = state["variables"]["siblingNamespace"]
        elif target == "other-workspace":
            workspace = state["variables"]["otherWorkspace"]
            namespace = state["variables"]["otherNamespace"]
        row_variables = {
            **variables,
            "workspace": workspace,
            "namespace": namespace,
            "role": sanitize_name(row["role"]),
            "username": state.get("users", {}).get(row["role"], ""),
            "resources": operation["resource"],
        }
        resolved_fixture = substitute(fixture, row_variables) if fixture else None
        request_operation = {**operation, "path": operation.get("requestPath", operation["path"])}
        can_run, reason = executable_case(request_operation, resolved_fixture, row_variables)
        result = {**row, "functionalPassed": False, "diagnosticPassed": True, "passed": False}
        if row.get("expected") == "UNVERIFIED":
            result.update(
                {
                    "result": "NOT_TESTED",
                    "skipped": True,
                    "skipReason": row.get("expectedReason", "expectation cannot be derived offline"),
                }
            )
            results.append(result)
            continue
        if not can_run:
            result.update({"result": "NOT_TESTED", "skipped": True, "skipReason": reason})
            results.append(result)
            continue
        request_path = substitute(request_operation["path"], row_variables)
        body = resolved_fixture.get("body") if resolved_fixture and "body" in resolved_fixture else None
        client = clients.get(row["role"])
        if not client:
            result.update({"result": "BLOCKED", "error": "test user client is unavailable"})
            results.append(result)
            continue
        started = time.monotonic()
        response = None
        error = ""
        try:
            response = client.request(operation["method"], request_path, body)
        except E2EError as exc:
            error = str(exc)
        result["durationMs"] = round((time.monotonic() - started) * 1000)
        result["actualPath"] = request_path
        if response:
            result["actualStatus"] = response.status
            result["requestID"] = response.headers.get("Audit-Id") or response.headers.get("X-Request-ID") or response.headers.get("X-Trace-ID") or ""
            result["response"] = summarize(response.body)
            if row["expected"] == "ALLOW":
                successes = resolved_fixture.get("expectedSuccess", []) if resolved_fixture else []
                functional = response.status in successes if successes else 200 <= response.status < 300
            else:
                functional = response.status == 403
            if functional and row["expected"] == "ALLOW":
                assertion_error = check_assertions(response.body, resolved_fixture)
                if assertion_error:
                    functional = False
                    error = assertion_error
                cleanup_path = resolved_fixture.get("cleanupPath") if resolved_fixture else ""
                if 200 <= response.status < 300 and cleanup_path:
                    state["resources"].append({"path": str(cleanup_path)})
            diagnostic = diagnostic_quality(row["role"], operation, response)
        else:
            functional = diagnostic = False
        layer, suggestion = diagnose(row["expected"], response, error, diagnostic)
        if row["role"] == "no-token" and response is not None and response.status == 401:
            layer = "authentication"
            suggestion = (
                "ks-apiserver returned 401 to a request without Authorization; a missing token is "
                "authenticated as system:anonymous and must be denied by RBAC with 403. Verify the URL "
                "targets ks-apiserver (not Console), anonymous authentication is enabled, and the route "
                "is not excluded from RBAC"
            )
        result.update(
            {
                "functionalPassed": functional,
                "diagnosticPassed": diagnostic,
                "passed": functional and diagnostic,
                "result": "PASS" if functional and diagnostic else "FAIL",
                "error": error,
                "likelyFailureLayer": "" if functional and diagnostic else layer,
                "suggestion": "" if functional and diagnostic else suggestion,
            }
        )
        results.append(result)
    return results


def cleanup_state(admin: Client, state: dict[str, Any], timeout: float = 30) -> list[str]:
    errors: list[str] = []
    pending: dict[str, str] = {}
    for item in reversed(state.get("resources", [])):
        path = item.get("path", "")
        if not path:
            continue
        try:
            method = str(item.get("method", "DELETE")).upper()
            response = admin.request(method, path)
            if response.status not in (200, 202, 204, 404):
                errors.append(f"{method} {path}: HTTP {response.status}: {summarize(response.body)}")
            elif method == "DELETE" and response.status != 404:
                pending[str(item.get("verifyPath") or path)] = path
        except E2EError as exc:
            errors.append(str(exc))
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for path in sorted(pending):
            try:
                if admin.request("GET", path).status == 404:
                    del pending[path]
            except E2EError:
                pass
        if pending:
            time.sleep(0.5)
    errors.extend(
        f"DELETE {delete_path}: object still exists at {verify_path} after {timeout:g}s"
        for verify_path, delete_path in sorted(pending.items())
    )
    return errors


def report_paths(config_path: Path, config: dict[str, Any]) -> tuple[Path, Path]:
    report = nested(config, "report", default={}) or {}
    directory = resolve(config_path, str(report.get("directory", "./test-results")))
    return directory / str(report.get("json", "kse-e2e-report.json")), directory / str(report.get("markdown", "kse-e2e-report.md"))


def conclusion(report: dict[str, Any]) -> str:
    if report.get("setupErrors"):
        return "BLOCKED"
    if report.get("cleanupErrors"):
        return "FAIL"
    results = report.get("results", [])
    evidence = (
        report.get("apiServiceChecks", [])
        + report.get("membershipAPIChecks", [])
        + report.get("clusterReadinessChecks", [])
        + report.get("roleTemplateChecks", [])
        + report.get("aggregationChecks", [])
    )
    if any(item.get("result") == "FAIL" for item in results) or any(item.get("result") == "FAIL" for item in evidence):
        return "FAIL"
    if any(
        item.get("result")
        in ("NOT_TESTED", "NEEDS_CONFIGURATION", "BLOCKED", "SETUP_BLOCKED")
        for item in results
    ):
        return "PARTIAL"
    return "PASS"


def write_report(config_path: Path, config: dict[str, Any], report: dict[str, Any]) -> None:
    secrets_to_hide = secret_values(config)
    safe = redact(report, secrets_to_hide)
    safe["conclusion"] = conclusion(safe)
    safe["setupStatus"] = "SETUP_BLOCKED" if safe.get("setupErrors") else "READY"
    cleanup_policy = nested(config, "execution", "cleanup", default="always")
    if cleanup_policy != "always":
        safe["cleanupStatus"] = "NOT_RUN"
    elif safe.get("cleanupErrors"):
        safe["cleanupStatus"] = "FAIL"
    elif safe.get("results") and not safe.get("setupErrors"):
        safe["cleanupStatus"] = "VERIFIED"
    else:
        safe["cleanupStatus"] = "NOT_RUN"
    safe["evidenceStatus"] = (
        "VERIFIED"
        if safe.get("results")
        and not safe.get("setupErrors")
        and safe["cleanupStatus"] == "VERIFIED"
        else "INFERRED"
    )
    json_path, markdown_path = report_paths(config_path, config)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(safe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for item in safe.get("results", []):
        counts[item.get("result", "UNKNOWN")] = counts.get(item.get("result", "UNKNOWN"), 0) + 1
    lines = [
        "# KubeSphere Extension API E2E",
        "",
        f"- Conclusion: **{safe['conclusion']}**",
        f"- Evidence status: **{safe['evidenceStatus']}**",
        f"- Run ID: `{safe.get('runID', '')}`",
        f"- Target: `{safe.get('target', {}).get('ksAPIServer', '')}`",
        f"- Cluster: `{safe.get('target', {}).get('cluster', '')}`",
        f"- Authentication: `{safe.get('authenticationMode', '')}`",
        f"- Results: `{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`",
        f"- Setup: `{safe['setupStatus']}`",
        f"- Cleanup: `{safe['cleanupStatus']}`",
        "",
        "## Result Summary",
        "",
        "| Result | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {result} | {count} |" for result, count in sorted(counts.items()))
    lines.extend(
        [
        "",
        "## Mismatches",
        "",
        "| Role | Method | Path | Expected | Actual | Layer | Suggestion |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in safe.get("results", []):
        if item.get("result") == "FAIL":
            lines.append(
                "| {role} | {method} | `{path}` | {expected} | {actual} | {layer} | {suggestion} |".format(
                    role=item.get("role", ""),
                    method=item.get("method", ""),
                    path=item.get("actualPath", item.get("path", "")).replace("|", "\\|"),
                    expected=item.get("expected", ""),
                    actual=item.get("actualStatus", item.get("error", "")),
                    layer=item.get("likelyFailureLayer", ""),
                    suggestion=item.get("suggestion", "").replace("|", "\\|"),
                )
            )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"conclusion": safe["conclusion"], "runID": safe.get("runID"), "jsonReport": str(json_path), "markdownReport": str(markdown_path)}, indent=2, ensure_ascii=False))


def run_command(args: argparse.Namespace) -> None:
    if not args.execute:
        raise E2EError("live execution requires --execute")
    config_path, config = load_config(args.config)
    errors = validate_config_shape(config_path, config)
    if errors:
        raise E2EError("configuration validation failed:\n- " + "\n- ".join(errors))
    print(json.dumps({"preflight": sanitized_summary(config)}, indent=2, ensure_ascii=False))
    started = time.monotonic()
    run_id = sanitize_name(
        f"{nested(config, 'execution', 'resourcePrefix', default='extension-e2e')}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    )
    report: dict[str, Any] = {
        "runID": run_id,
        "startedAt": utc_now(),
        "target": nested(config, "target", default={}),
        "authenticationMode": choose_auth(config),
        "setupErrors": [],
        "cleanupErrors": [],
        "results": [],
    }
    state = None
    admin = None
    try:
        token = login(config)
        target = nested(config, "target", default={}) or {}
        admin = Client(
            str(target["ksAPIServer"]),
            token,
            bool(nested(config, "target", "tls", "insecureSkipVerify", default=False)),
        )
        plan = build_plan(config_path, config)
        unresolved_mappings = [
            operation for operation in plan["operations"] if operation.get("mappingIssues")
        ]
        if unresolved_mappings:
            names = ", ".join(operation["operationId"] for operation in unresolved_mappings)
            raise E2EError(f"SETUP_BLOCKED: add apiMappings for unresolved operations: {names}")
        report["renderer"] = plan["renderer"]
        report["backendSources"] = plan["backendSources"]
        report["operations"] = plan["operations"]
        report["permissionMatrix"] = plan["matrix"]
        report["scenarios"] = plan["scenarios"]
        report["needsConfiguration"] = plan["needsConfiguration"]
        report["apiServiceChecks"] = check_apiservices(admin, plan["operations"])
        report["membershipAPIChecks"] = check_membership_apis(admin)
        failed_membership = [
            item["name"] for item in report["membershipAPIChecks"] if item["result"] != "PASS"
        ]
        if failed_membership:
            raise E2EError(
                "SETUP_BLOCKED: KubeSphere member APIs unavailable: "
                + ", ".join(failed_membership)
            )
        report["clusterReadinessChecks"] = check_cluster_readiness(admin)
        failed_clusters = [
            item["name"]
            for item in report["clusterReadinessChecks"]
            if item["result"] != "PASS"
        ]
        if failed_clusters:
            raise E2EError(
                "SETUP_BLOCKED: registered clusters are not ready; "
                "Workspace cleanup may be blocked: "
                + ", ".join(failed_clusters)
            )
        _, source_templates = render_roletemplates(config_path, config)
        report["roleTemplateChecks"] = compare_live_templates(admin, config, source_templates)
        state, clients = prepare_environment(config_path, config, admin, run_id)
        report["aggregationChecks"] = compare_aggregations(
            admin,
            source_templates,
            state,
            nested(config, "roles", "include", default=DEFAULT_ROLES) or DEFAULT_ROLES,
        )
        report["results"] = execute_matrix(plan, config_path, config, state, clients)
        report["results"].extend(execute_scenarios(plan, config_path, config, state, admin, clients))
    except (E2EError, OSError, ValueError) as exc:
        report["setupErrors"].append(str(redact(str(exc), secret_values(config))))
    finally:
        state_path = state_file(config_path, config, run_id)
        if state is None and state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        if state is not None and admin is not None and nested(config, "execution", "cleanup", default="always") == "always":
            report["cleanupErrors"] = cleanup_state(admin, state)
            if not report["cleanupErrors"] and state_path.exists():
                state_path.unlink()
        report["finishedAt"] = utc_now()
        report["durationMs"] = round((time.monotonic() - started) * 1000)
        write_report(config_path, config, report)
    if conclusion(redact(report, secret_values(config))) != "PASS":
        raise SystemExit(1)


def cleanup_command(args: argparse.Namespace) -> None:
    if not args.execute:
        raise E2EError("cleanup requires --execute")
    config_path, config = load_config(args.config)
    path = state_file(config_path, config, sanitize_name(args.run_id))
    if not path.exists():
        print(
            json.dumps(
                {
                    "result": "PASS",
                    "runID": sanitize_name(args.run_id),
                    "alreadyClean": True,
                    "state": str(path),
                },
                indent=2,
            )
        )
        return
    state = json.loads(path.read_text(encoding="utf-8"))
    token = login(config)
    target = nested(config, "target", default={}) or {}
    admin = Client(
        str(target["ksAPIServer"]),
        token,
        bool(nested(config, "target", "tls", "insecureSkipVerify", default=False)),
    )
    errors = cleanup_state(admin, state)
    if errors:
        raise E2EError("cleanup failed:\n- " + "\n- ".join(errors))
    path.unlink()
    print(
        json.dumps(
            {
                "result": "PASS",
                "runID": state.get("runID"),
                "alreadyClean": False,
                "verified": True,
                "removedState": str(path),
            },
            indent=2,
        )
    )


def doctor_result(checks: list[dict[str, str]]) -> str:
    return "FAIL" if any(item["status"] == "FAIL" for item in checks) else "PASS"


def doctor_command(args: argparse.Namespace) -> None:
    checks: list[dict[str, str]] = [
        {
            "name": "python",
            "status": "PASS" if sys.version_info >= (3, 11) else "FAIL",
            "detail": sys.version.split()[0],
            "next": "Install Python 3.11 or newer" if sys.version_info < (3, 11) else "",
        },
        {
            "name": "pyyaml",
            "status": "PASS" if yaml is not None else "FAIL",
            "detail": getattr(yaml, "__version__", "not installed"),
            "next": (
                "Run with uv --with-requirements <skill-dir>/requirements.txt"
                if yaml is None
                else ""
            ),
        },
    ]
    for command in ("ksbuilder", "kubectl"):
        path = shutil.which(command)
        checks.append(
            {
                "name": command,
                "status": "PASS" if path else "WARN",
                "detail": path or "not found on PATH",
                "next": f"Install {command} before cluster validation" if not path else "",
            }
        )

    config = None
    config_path = Path(args.config).expanduser().resolve()
    if yaml is not None:
        try:
            config_path, config = load_config(args.config)
            errors = validate_config_shape(config_path, config)
            checks.append(
                {
                    "name": "configuration",
                    "status": "FAIL" if errors else "PASS",
                    "detail": "; ".join(errors) if errors else f"private config: {config_path}",
                    "next": "Fix the listed configuration fields and rerun doctor" if errors else "",
                }
            )
        except E2EError as exc:
            checks.append(
                {
                    "name": "configuration",
                    "status": "FAIL",
                    "detail": str(exc),
                    "next": f"Run init --project {config_path.parent}",
                }
            )

    if config is not None and not any(item["status"] == "FAIL" for item in checks):
        try:
            token = login(config)
            target = nested(config, "target", default={}) or {}
            client = Client(
                str(target["ksAPIServer"]),
                token,
                bool(nested(config, "target", "tls", "insecureSkipVerify", default=False)),
            )
            response = client.request(
                "GET", "/apis/iam.kubesphere.io/v1beta1/roletemplates?limit=1"
            )
            server_status = "PASS" if response.status == 200 else "FAIL"
            checks.append(
                {
                    "name": "ks-apiserver",
                    "status": server_status,
                    "detail": f"RoleTemplate API HTTP {response.status}",
                    "next": (
                        ""
                        if server_status == "PASS"
                        else "Verify target.ksAPIServer and administrator credentials"
                    ),
                }
            )
            operations = load_operations(config_path, config)
            live_checks = (
                check_apiservices(client, operations)
                + check_membership_apis(client)
                + check_cluster_readiness(client)
            )
            for item in live_checks:
                status = str(item["result"])
                checks.append(
                    {
                        "name": str(item["name"]),
                        "status": status,
                        "detail": f"HTTP {item.get('actualStatus', '')} {item.get('message', '')}".strip(),
                        "next": (
                            ""
                            if status == "PASS"
                            else "Restore the API or cluster readiness before run --execute"
                        ),
                    }
                )
        except (E2EError, OSError, ValueError) as exc:
            checks.append(
                {
                    "name": "cluster-preflight",
                    "status": "FAIL",
                    "detail": str(redact(str(exc), secret_values(config))),
                    "next": "Verify server reachability, TLS settings, and credentials",
                }
            )

    result = doctor_result(checks)
    print(json.dumps({"result": result, "checks": checks}, indent=2, ensure_ascii=False))
    if result != "PASS":
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="Check dependencies, private configuration, connectivity, and cluster APIs"
    )
    doctor.add_argument("--config", default=CONFIG_NAME)
    doctor.set_defaults(func=doctor_command)

    init = commands.add_parser("init", help="Create a local secret-bearing kse-e2e.yaml")
    init.add_argument("--project", default=".")
    init.add_argument("--force", action="store_true")
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--server")
    init.add_argument("--cluster")
    init.add_argument("--auth-mode", choices=["auto", "token", "password"])
    init.add_argument("--username")
    init.add_argument("--password")
    init.add_argument("--token")
    init.add_argument("--client-secret")
    init.add_argument("--api-doc")
    init.add_argument("--extension")
    init.set_defaults(func=init_config)

    validate = commands.add_parser("validate", help="Validate configuration, sources, rendering, and credentials")
    validate.add_argument("--config", default=CONFIG_NAME)
    validate.set_defaults(func=validate_command)

    plan = commands.add_parser("plan", help="Render RoleTemplates and generate the expected permission matrix")
    plan.add_argument("--config", default=CONFIG_NAME)
    plan.add_argument("--output")
    plan.set_defaults(func=plan_command)

    run = commands.add_parser("run", help="Create temporary users and execute the API matrix")
    run.add_argument("--config", default=CONFIG_NAME)
    run.add_argument("--execute", action="store_true")
    run.set_defaults(func=run_command)

    cleanup = commands.add_parser("cleanup", help="Clean resources from an interrupted run")
    cleanup.add_argument("--config", default=CONFIG_NAME)
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--execute", action="store_true")
    cleanup.set_defaults(func=cleanup_command)
    return parser


def main() -> None:
    try:
        args = build_parser().parse_args()
        args.func(args)
    except E2EError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
