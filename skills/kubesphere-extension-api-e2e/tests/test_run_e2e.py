from __future__ import annotations

import argparse
import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_e2e.py"
SPEC = importlib.util.spec_from_file_location("run_e2e", SCRIPT)
assert SPEC and SPEC.loader
e2e = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = e2e
SPEC.loader.exec_module(e2e)


def roletemplate(name: str, scope: str, labels: list[str], verbs: list[str]) -> dict:
    all_labels = {"iam.kubesphere.io/scope": scope}
    all_labels.update({label: "" for label in labels})
    return {
        "kind": "RoleTemplate",
        "metadata": {"name": name, "labels": all_labels},
        "spec": {
            "rules": [
                {
                    "apiGroups": ["example.io"],
                    "resources": ["widgets"],
                    "verbs": verbs,
                }
            ]
        },
    }


class RunnerTests(unittest.TestCase):
    def test_infer_workspace_list_operation(self) -> None:
        actual = e2e.infer_api(
            "/kapis/example.io/v1/workspaces/{workspace}/widgets",
            {"tags": ["widgets"]},
            "get",
        )
        self.assertEqual(
            actual,
            {
                "group": "example.io",
                "version": "v1",
                "resource": "widgets",
                "verb": "list",
                "scope": "workspace",
            },
        )

    def test_minimum_kube_version_from_extension_constraint(self) -> None:
        self.assertEqual(e2e.minimum_kube_version(">=1.21.0-0"), "1.21.0")

    def test_infer_item_get_operation(self) -> None:
        actual = e2e.infer_api(
            "/kapis/example.io/v1/namespaces/{namespace}/widgets/{name}",
            {"tags": ["widgets"]},
            "get",
        )
        self.assertEqual(actual["verb"], "get")
        self.assertEqual(actual["scope"], "namespace")

    def test_roletemplate_permissions_follow_scope_and_aggregation(self) -> None:
        templates = [
            roletemplate(
                "workspace-view-widgets",
                "workspace",
                ["iam.kubesphere.io/aggregate-to-viewer"],
                ["get", "list"],
            ),
            roletemplate("workspace-manage-widgets", "workspace", [], ["create"]),
        ]
        list_operation = {
            "group": "example.io",
            "resource": "widgets",
            "verb": "list",
        }
        create_operation = {
            "group": "example.io",
            "resource": "widgets",
            "verb": "create",
        }
        self.assertTrue(e2e.expected_allow(templates, "workspace-viewer", list_operation)[0])
        self.assertFalse(e2e.expected_allow(templates, "workspace-viewer", create_operation)[0])
        self.assertTrue(e2e.expected_allow(templates, "workspace-admin", create_operation)[0])
        self.assertFalse(e2e.expected_allow(templates, "cluster-admin", create_operation)[0])

    def test_authenticated_template_applies_to_unprivileged_user(self) -> None:
        template = roletemplate(
            "global-view-policy",
            "global",
            ["iam.kubesphere.io/aggregate-to-authenticated"],
            ["get"],
        )
        operation = {"group": "example.io", "resource": "widgets", "verb": "get"}
        self.assertTrue(e2e.expected_allow([template], "no-permission", operation)[0])
        self.assertFalse(e2e.expected_allow([template], "no-token", operation)[0])

    def test_redact_hides_configured_and_header_secrets(self) -> None:
        value = {
            "adminPassword": "password-value",
            "note": "Authorization: Bearer abc.def.ghi and password-value",
        }
        text = json.dumps(e2e.redact(value, ["password-value"]))
        self.assertNotIn("password-value", text)
        self.assertNotIn("abc.def.ghi", text)
        self.assertIn("<redacted>", text)

    def test_user_creation_error_redacts_generated_password(self) -> None:
        client = mock.Mock()
        client.request.return_value = e2e.HTTPResponse(
            400,
            '{"message":"rejected generated-password"}',
            {},
        )
        with self.assertRaises(e2e.E2EError) as raised:
            e2e.create_resource(
                client,
                "POST",
                "/apis/iam.kubesphere.io/v1beta1/users",
                {},
                {"resources": []},
                ["generated-password"],
            )
        self.assertNotIn("generated-password", str(raised.exception))

    def test_token_has_priority_in_auto_mode(self) -> None:
        config = {
            "spec": {
                "auth": {
                    "mode": "auto",
                    "adminUsername": "admin",
                    "adminPassword": "password",
                    "adminToken": "token",
                }
            }
        }
        self.assertEqual(e2e.choose_auth(config), "token")

    def test_password_login_extracts_token_without_logging_credentials(self) -> None:
        config = {
            "spec": {
                "target": {
                    "ksAPIServer": "http://127.0.0.1:1",
                    "tls": {"insecureSkipVerify": False},
                },
                "auth": {
                    "mode": "password",
                    "adminUsername": "admin",
                    "adminPassword": "secret-password",
                    "loginPath": "/oauth/token",
                    "oauthClientID": "kubesphere",
                    "oauthClientSecret": "secret-client",
                },
            }
        }
        response = e2e.HTTPResponse(200, '{"access_token":"issued-token"}', {})
        with mock.patch.object(e2e.Client, "request", return_value=response):
            self.assertEqual(e2e.login(config), "issued-token")

    def test_noninteractive_init_creates_private_ignored_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "extension.yaml").write_text("name: demo\nnamespace: demo-system\n", encoding="utf-8")
            (project / "apidocs.json").write_text('{"swagger":"2.0","paths":{}}', encoding="utf-8")
            args = argparse.Namespace(
                project=str(project),
                force=False,
                non_interactive=True,
                server="http://127.0.0.1:30880",
                cluster="host",
                auth_mode="password",
                username="admin",
                password="test-password",
                token=None,
                client_secret="kubesphere",
                api_doc=None,
                extension=None,
            )

            with mock.patch("builtins.print"):
                e2e.init_config(args)

            config = project / "kse-e2e.yaml"
            self.assertTrue(config.exists())
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertIn("/kse-e2e.yaml", (project / ".gitignore").read_text(encoding="utf-8"))
            loaded = e2e.load_yaml(config)
            self.assertEqual(loaded["spec"]["auth"]["adminPassword"], "test-password")
            self.assertEqual(loaded["spec"]["sources"]["apiDocs"][0]["path"], "./apidocs.json")

    def test_write_without_fixture_is_not_executable(self) -> None:
        operation = {"method": "POST", "path": "/kapis/example.io/v1/widgets"}
        self.assertEqual(
            e2e.executable_case(operation, None, {}),
            (False, "write operation has no fixture"),
        )

    def test_unresolved_item_name_is_not_executable(self) -> None:
        operation = {"method": "GET", "path": "/kapis/example.io/v1/widgets/{name}"}
        runnable, reason = e2e.executable_case(operation, None, {})
        self.assertFalse(runnable)
        self.assertIn("name", reason)

    def test_openapi_operations_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "openapi.json"
            document.write_text(
                json.dumps(
                    {
                        "openapi": "3.0.0",
                        "paths": {
                            "/kapis/example.io/v1/widgets": {
                                "get": {"operationId": "listWidgets", "tags": ["widgets"]}
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "kse-e2e.yaml"
            config = {
                "spec": {
                    "sources": {"apiDocs": [{"path": "./openapi.json"}]},
                    "routing": {"pathOverrides": {}},
                }
            }
            operations = e2e.load_operations(config_path, config)
            self.assertEqual(len(operations), 1)
            self.assertEqual(operations[0]["operationId"], "listWidgets")
            self.assertEqual(operations[0]["verb"], "list")

    def test_assertions_report_mismatch(self) -> None:
        error = e2e.check_assertions(
            '{"metadata":{"name":"actual"}}',
            {"assertions": [{"path": "metadata.name", "equals": "expected"}]},
        )
        self.assertIn("expected", error)

    def test_absent_assertion_passes_only_when_field_is_missing(self) -> None:
        fixture = {"assertions": [{"path": "spec.weight", "absent": True}]}
        self.assertEqual(e2e.check_assertions('{"spec":{}}', fixture), "")
        self.assertIn("absent", e2e.check_assertions('{"spec":{"weight":1}}', fixture))

    def test_tracked_secret_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            config = root / "kse-e2e.yaml"
            config.write_text("kind: ExtensionAPIE2E\n", encoding="utf-8")
            config.chmod(0o600)
            subprocess.run(["git", "-C", str(root), "add", "kse-e2e.yaml"], check=True)
            with self.assertRaises(e2e.E2EError):
                e2e.require_private_config(config)

    def test_conclusion_distinguishes_partial_and_fail(self) -> None:
        self.assertEqual(
            e2e.conclusion({"results": [{"result": "NOT_TESTED"}]}),
            "PARTIAL",
        )
        self.assertEqual(
            e2e.conclusion({"results": [{"result": "FAIL"}]}),
            "FAIL",
        )
        self.assertEqual(
            e2e.conclusion(
                {
                    "results": [{"result": "PASS"}],
                    "aggregationChecks": [{"result": "FAIL"}],
                }
            ),
            "FAIL",
        )

    def test_live_roletemplate_may_have_additional_installer_label(self) -> None:
        source = {
            "name": "view-widgets",
            "labels": {"iam.kubesphere.io/scope": "workspace"},
            "rules": [{"apiGroups": ["example.io"], "resources": ["widgets"], "verbs": ["get"]}],
        }
        live = {
            **source,
            "labels": {
                **source["labels"],
                "kubesphere.io/extension-ref": "example",
            },
        }
        self.assertTrue(e2e.roletemplate_equivalent(source, live))


if __name__ == "__main__":
    unittest.main()
