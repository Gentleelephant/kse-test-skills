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
    def test_example_config_contains_no_credentials(self) -> None:
        example = e2e.load_yaml(SCRIPT.parents[1] / "assets" / "kse-e2e.example.yaml")
        auth = example["spec"]["auth"]
        self.assertEqual(auth["adminPassword"], "")
        self.assertEqual(auth["adminToken"], "")

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

    def test_infer_static_get_operation_from_operation_id(self) -> None:
        actual = e2e.infer_api(
            "/kapis/example.io/v1/scheduling-policy",
            {"operationId": "GetSchedulingPolicy", "tags": ["scheduling-policy"]},
            "get",
        )
        self.assertEqual(actual["verb"], "get")

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

    def test_roletemplate_scope_must_match_request_scope(self) -> None:
        template = roletemplate(
            "workspace-view-widgets",
            "workspace",
            ["iam.kubesphere.io/aggregate-to-viewer"],
            ["list"],
        )
        operation = {
            "group": "example.io",
            "resource": "widgets",
            "verb": "list",
            "scope": "cluster",
        }
        self.assertFalse(e2e.expected_allow([template], "workspace-viewer", operation)[0])

    def test_parent_role_scope_applies_to_descendant_request_scope(self) -> None:
        cluster_template = roletemplate(
            "cluster-view-widgets",
            "cluster",
            ["iam.kubesphere.io/aggregate-to-cluster-viewer"],
            ["list"],
        )
        workspace_template = roletemplate(
            "workspace-view-widgets",
            "workspace",
            ["iam.kubesphere.io/aggregate-to-viewer"],
            ["list"],
        )
        namespace_operation = {
            "group": "example.io",
            "resource": "widgets",
            "verb": "list",
            "scope": "namespace",
        }
        self.assertTrue(e2e.expected_allow([cluster_template], "cluster-viewer", namespace_operation)[0])
        self.assertTrue(e2e.expected_allow([workspace_template], "workspace-viewer", namespace_operation)[0])

    def test_operation_key_is_unique_when_openapi_ids_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "openapi.json"
            document.write_text(
                json.dumps(
                    {
                        "openapi": "3.0.0",
                        "paths": {
                            "/kapis/example.io/v1/widgets": {
                                "get": {"operationId": "func1", "tags": ["widgets"]}
                            },
                            "/kapis/example.io/v1/gadgets": {
                                "get": {"operationId": "func1", "tags": ["gadgets"]}
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = {
                "spec": {
                    "sources": {"apiDocs": [{"path": "./openapi.json"}]},
                    "routing": {"pathOverrides": {}},
                }
            }
            operations = e2e.load_operations(root / "kse-e2e.yaml", config)
            self.assertEqual(len({item["operationKey"] for item in operations}), 2)

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

    def test_secret_values_include_scenario_secrets(self) -> None:
        config = {
            "spec": {
                "auth": {"adminPassword": "admin-secret"},
                "scenarios": {"variables": {"providerSecret": "provider-secret"}},
            }
        }
        self.assertEqual(
            set(e2e.secret_values(config)),
            {"admin-secret", "provider-secret"},
        )

    def test_public_value_matching_secret_does_not_corrupt_api_group(self) -> None:
        config = {
            "spec": {
                "auth": {
                    "oauthClientID": "kubesphere",
                    "oauthClientSecret": "kubesphere",
                }
            }
        }
        value = {
            "oauthClientSecret": "kubesphere",
            "group": "volcano.kubesphere.io",
        }
        redacted = e2e.redact(value, e2e.secret_values(config))
        self.assertEqual(redacted["oauthClientSecret"], "<redacted>")
        self.assertEqual(redacted["group"], "volcano.kubesphere.io")

    def test_authorization_verdict_is_not_treated_as_header_secret(self) -> None:
        value = {
            "expect": {"authorization": "ALLOW"},
            "headers": {"Authorization": "Bearer abc.def.ghi"},
        }
        redacted = e2e.redact(value)
        self.assertEqual(redacted["expect"]["authorization"], "ALLOW")
        self.assertEqual(redacted["headers"]["Authorization"], "<redacted>")

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

    def test_role_binding_names_remain_unique_after_sanitizing(self) -> None:
        client = mock.Mock()
        client.request.return_value = e2e.HTTPResponse(201, "{}", {})
        state = {"resources": []}
        run_id = "volcano-apiserver-e2e-20260727103203-abcdef"
        e2e.bind_test_role(client, state, "cluster-admin", "admin-user", "ws", "ns", run_id)
        e2e.bind_test_role(client, state, "cluster-viewer", "viewer-user", "ws", "ns", run_id)
        names = [call.args[2]["metadata"]["name"] for call in client.request.call_args_list]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(len(name) <= 40 for name in names))

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
            (project / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")
            (project / "pkg").mkdir()
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
            self.assertEqual(loaded["spec"]["sources"]["backendCode"][0]["language"], "go")
            self.assertEqual(loaded["spec"]["sources"]["backendCode"][0]["path"], ".")

    def test_backend_source_inventory_honors_include_and_exclude(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg").mkdir()
            (root / "vendor").mkdir()
            (root / "pkg" / "handler.go").write_text("package pkg\n", encoding="utf-8")
            (root / "pkg" / "notes.md").write_text("not source\n", encoding="utf-8")
            (root / "vendor" / "ignored.go").write_text("package ignored\n", encoding="utf-8")
            config = {
                "spec": {
                    "sources": {
                        "backendCode": [
                            {
                                "name": "demo",
                                "path": ".",
                                "language": "go",
                                "include": ["./pkg", "./vendor"],
                                "exclude": ["./vendor"],
                            }
                        ]
                    }
                }
            }
            inventory = e2e.source_inventory(root / "kse-e2e.yaml", config)
            self.assertEqual(inventory[0]["files"], ["./pkg/handler.go"])

    def test_scenario_dependencies_are_topologically_ordered(self) -> None:
        scenarios = [
            {"id": "child", "dependsOn": ["parent"]},
            {"id": "parent"},
        ]
        self.assertEqual([item["id"] for item in e2e.order_scenarios(scenarios)], ["parent", "child"])

    def test_scenario_dependency_cycle_is_rejected(self) -> None:
        with self.assertRaises(e2e.E2EError):
            e2e.order_scenarios(
                [
                    {"id": "one", "dependsOn": ["two"]},
                    {"id": "two", "dependsOn": ["one"]},
                ]
            )

    def test_unresolved_variables_find_nested_placeholders(self) -> None:
        self.assertEqual(
            e2e.unresolved_variables(
                {"path": "/widgets/{name}", "body": {"parent": "${parent}"}}
            ),
            ["name", "parent"],
        )

    def test_source_evidence_requires_existing_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "handler.go"
            source.write_text("package demo\n", encoding="utf-8")
            errors = e2e.validate_evidence(
                "scenario",
                [{"type": "source", "reference": "./handler.go:1", "certainty": "confirmed"}],
                root / "kse-e2e.yaml",
            )
            self.assertEqual(errors, [])
            errors = e2e.validate_evidence(
                "scenario",
                [{"type": "source", "reference": "./missing.go", "certainty": "confirmed"}],
                root / "kse-e2e.yaml",
            )
            self.assertTrue(errors)

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

    def test_collection_assertions_support_contains_and_omits(self) -> None:
        fixture = {
            "assertions": [
                {"path": "items", "contains": "own"},
                {"path": "items", "omits": "other"},
            ]
        }
        self.assertEqual(e2e.check_assertions('{"items":["own"]}', fixture), "")
        self.assertIn("omit", e2e.check_assertions('{"items":["own","other"]}', fixture))

    def test_collection_assertions_match_kubernetes_object_names(self) -> None:
        fixture = {
            "assertions": [
                {"path": "items", "contains": "own"},
                {"path": "items", "omits": "other"},
            ]
        }
        body = '{"items":[{"metadata":{"name":"own"}}]}'
        self.assertEqual(e2e.check_assertions(body, fixture), "")
        self.assertIn(
            "omit",
            e2e.check_assertions(
                '{"items":[{"metadata":{"name":"own"}},{"name":"other"}]}',
                fixture,
            ),
        )

    def test_generic_scenario_captures_setup_value_and_registers_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "kse-e2e.yaml"
            config = {
                "spec": {
                    "report": {"directory": "./test-results"},
                    "scenarios": {"variables": {}, "overrides": [], "additional": []},
                }
            }
            state = {
                "runID": "run-1",
                "variables": {"workspace": "ws"},
                "resources": [],
            }
            admin = mock.Mock()
            admin.request.return_value = e2e.HTTPResponse(201, '{"metadata":{"name":"created"}}', {})
            actor = mock.Mock()
            actor.request.return_value = e2e.HTTPResponse(200, '{"metadata":{"name":"created"}}', {})
            plan = {
                "scenarios": [
                    {
                        "id": "read-created",
                        "actor": "workspace-admin",
                        "setup": [
                            {
                                "actor": "admin",
                                "method": "POST",
                                "path": "/widgets",
                                "capture": {"createdName": "metadata.name"},
                                "cleanupPath": "/widgets/${createdName}",
                            }
                        ],
                        "request": {"method": "GET", "path": "/widgets/${createdName}"},
                        "authorization": {
                            "group": "example.io",
                            "version": "v1",
                            "resource": "widgets",
                            "verb": "get",
                            "scope": "workspace",
                        },
                        "expect": {"authorization": "ALLOW", "statuses": [200]},
                        "assertions": [{"path": "metadata.name", "equals": "created"}],
                        "evidence": [
                            {"type": "source", "reference": "./handler.go:1", "certainty": "confirmed"}
                        ],
                    }
                ]
            }
            results = e2e.execute_scenarios(
                plan,
                config_path,
                config,
                state,
                admin,
                {"workspace-admin": actor},
            )
            self.assertEqual(results[0]["result"], "PASS")
            actor.request.assert_called_once_with("GET", "/widgets/created", None, "application/json")
            self.assertEqual(state["resources"][0]["path"], "/widgets/created")

    def test_business_rejection_and_authorization_bypass_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {
                "spec": {
                    "report": {"directory": "./test-results"},
                    "scenarios": {"variables": {}, "overrides": [], "additional": []},
                }
            }
            state = {"runID": "run-1", "variables": {}, "resources": []}
            actor = mock.Mock()
            actor.request.side_effect = [
                e2e.HTTPResponse(400, '{"message":"parent belongs to another workspace"}', {}),
                e2e.HTTPResponse(200, '{"items":[]}', {}),
            ]
            base = {
                "actor": "workspace-admin",
                "authorization": {
                    "group": "example.io",
                    "version": "v1",
                    "resource": "widgets",
                    "verb": "create",
                    "scope": "workspace",
                },
                "evidence": [
                    {"type": "source", "reference": "./handler.go:1", "certainty": "confirmed"}
                ],
            }
            plan = {
                "scenarios": [
                    {
                        **base,
                        "id": "business-rejection",
                        "request": {"method": "POST", "path": "/widgets", "body": {}},
                        "expect": {
                            "authorization": "ALLOW",
                            "statuses": [400],
                            "errorContains": ["parent", "workspace"],
                        },
                    },
                    {
                        **base,
                        "id": "must-be-forbidden",
                        "request": {"method": "POST", "path": "/other/widgets", "body": {}},
                        "expect": {"authorization": "DENY", "statuses": [403]},
                    },
                ]
            }
            results = e2e.execute_scenarios(
                plan,
                root / "kse-e2e.yaml",
                config,
                state,
                mock.Mock(),
                {"workspace-admin": actor},
            )
            self.assertEqual(results[0]["result"], "PASS")
            self.assertEqual(results[1]["result"], "FAIL")
            self.assertEqual(results[1]["likelyFailureLayer"], "authorization bypass")

    def test_cleanup_runs_in_reverse_registration_order(self) -> None:
        admin = mock.Mock()
        admin.request.side_effect = [
            e2e.HTTPResponse(204, "", {}),
            e2e.HTTPResponse(204, "", {}),
            e2e.HTTPResponse(404, "", {}),
            e2e.HTTPResponse(404, "", {}),
        ]
        errors = e2e.cleanup_state(
            admin,
            {
                "resources": [
                    {"method": "DELETE", "path": "/parents/one"},
                    {"method": "DELETE", "path": "/children/two"},
                ]
            },
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            [call.args[:2] for call in admin.request.call_args_list],
            [
                ("DELETE", "/children/two"),
                ("DELETE", "/parents/one"),
                ("GET", "/children/two"),
                ("GET", "/parents/one"),
            ],
        )

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
            e2e.conclusion({"results": [{"result": "NEEDS_CONFIGURATION"}]}),
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
