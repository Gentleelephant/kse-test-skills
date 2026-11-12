from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_task.py"
SPEC = importlib.util.spec_from_file_location("release_task", SCRIPT)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
DIGEST = "sha256:" + "a" * 64
SHA = "b" * 40


def observation(name, version):
    return {
        "chart": release.chart_ref(name, version), "digest": DIGEST,
        "checked_at": "2026-11-12T00:00:00+00:00",
    }


class ReleaseTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "state" / "task.json"
        self.task = release.init_task("addons", ["devops=1.2.5"], ["v11.4.0", "latest"])

    def config(self, target="v11.4.0", text=None):
        path = self.root / "museum" / target / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((text or "charts:\n  - oci://hub.kubesphere.com.cn/charts/devops:1.2.4\n").encode())
        return path

    def verified(self, name="devops"):
        record = self.task["extensions"][name]
        record["verification"] = observation(name, record["version"])

    def invoke(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                status = release.main(list(arguments))
        return status, output.getvalue(), errors.getvalue()

    def helm(self, metadata=None, digest=DIGEST, fail=False):
        def run(arguments, **kwargs):
            self.assertEqual(arguments[:3], ["helm", "pull", f"{release.REGISTRY}/devops"])
            self.assertEqual(arguments[3:5], ["--version", "1.2.5"])
            if fail:
                return subprocess.CompletedProcess(arguments, 1, "", "secret-registry-password")
            destination = Path(arguments[6])
            archive = destination / "devops-1.2.5.tgz"
            content = (metadata or "name: devops\nversion: 1.2.5\n").encode()
            with tarfile.open(archive, "w:gz") as package:
                member = tarfile.TarInfo("devops/Chart.yaml")
                member.size = len(content)
                package.addfile(member, io.BytesIO(content))
            return subprocess.CompletedProcess(arguments, 0, "", f"Digest: {digest}\n")
        return mock.patch.object(release.subprocess, "run", side_effect=run)

    def github(self, merged=False, closed=False, title="Fix devops", comments=None, run=None):
        repo = release.REPOS[self.task["repo"]]
        self.task["source"]["pr"] = 12
        responses = {
            f"repos/{repo}/pulls/12": {
                "merged": merged, "state": "closed" if closed or merged else "open",
                "title": title, "merge_commit_sha": SHA if merged else None,
            },
            f"repos/{repo}/issues/12/comments": comments or [],
        }
        if run:
            responses[f"repos/{repo}/actions/runs/{run['id']}"] = run

        def invoke(arguments, **kwargs):
            self.assertEqual(arguments[:2], ["gh", "api"])
            self.assertEqual(len(arguments), 3)
            return subprocess.CompletedProcess(arguments, 0, json.dumps(responses[arguments[2]]), "")
        return mock.patch.object(release.subprocess, "run", side_effect=invoke)

    def test_init_checkpoint_and_no_overwrite(self):
        release.save_task(self.file, self.task, exclusive=True)
        self.assertEqual(release.read_task(self.file), self.task)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        original = self.file.read_bytes()
        with self.assertRaises(FileExistsError):
            release.save_task(self.file, self.task, exclusive=True)
        self.assertEqual(self.file.read_bytes(), original)

    def test_save_validates_candidate_and_preserves_original_on_error(self):
        release.save_task(self.file, self.task)
        self.task["source"]["branch"] = "release-devops"
        candidate = self.root / "candidate.json"
        release.save_task(candidate, self.task)
        status, _, _ = self.invoke("save", "--file", str(self.file), "--input", str(candidate))
        self.assertEqual(status, 0)
        self.assertEqual(release.read_task(self.file)["source"]["branch"], "release-devops")
        candidate.write_text('{"token": "do-not-store"}')
        status, _, _ = self.invoke("save", "--file", str(self.file), "--input", str(candidate))
        self.assertEqual(status, 2)
        self.assertEqual(release.read_task(self.file), self.task)

    def test_version_change_cannot_reuse_runs(self):
        release.save_task(self.file, self.task)
        self.task["extensions"]["devops"].update(version="1.2.6", run_ids=[99])
        candidate = self.root / "candidate.json"
        release.save_task(candidate, self.task)
        status, _, errors = self.invoke("save", "--file", str(self.file), "--input", str(candidate))
        self.assertEqual(status, 2)
        self.assertIn("clearing run IDs", errors)
        self.assertEqual(release.read_task(self.file)["extensions"]["devops"]["version"], "1.2.5")

    def test_invalid_task_fields_targets_and_pairs(self):
        invalid_tasks = []
        for field, value in (("token", "secret"), ("repo", []), ("schema_version", True)):
            task = copy.deepcopy(self.task)
            task[field] = value
            invalid_tasks.append(task)
        task = copy.deepcopy(self.task)
        task["source"]["pr"] = True
        invalid_tasks.append(task)
        for targets in (["../latest"], ["/tmp"], ["latest", "latest"], ["a/./b"]):
            task = copy.deepcopy(self.task)
            task["museum"]["targets"] = targets
            invalid_tasks.append(task)
        for task in invalid_tasks:
            with self.subTest(task=task), self.assertRaises(ValueError):
                release.validate_task(task)
        with self.assertRaises(ValueError):
            release.init_task("addons", ["devops=1", "devops=2"], [])

    def test_stale_artifact_evidence_rejected(self):
        self.verified()
        self.task["extensions"]["devops"]["version"] = "1.2.6"
        with self.assertRaisesRegex(ValueError, "stale verification"):
            release.validate_task(self.task)

    def test_show_is_offline(self):
        release.save_task(self.file, self.task)
        with mock.patch.object(release.subprocess, "run") as run:
            status, output, _ = self.invoke("show", "--file", str(self.file))
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output), self.task)
        run.assert_not_called()

    def test_addons_waits_for_merge_without_comment(self):
        with self.github():
            self.assertEqual(release.inspect_task(self.task)["action"], "await_merge")

    def test_closed_unmerged_is_blocked_for_both_repos(self):
        for repo in release.REPOS:
            self.task["repo"] = repo
            with self.github(closed=True):
                self.assertEqual(release.inspect_task(self.task)["action"], "blocked_closed_unmerged")

    def test_wiz_instruction_is_prepared_before_merge(self):
        self.task["repo"] = "wiz"
        with self.github():
            self.assertEqual(release.inspect_task(self.task)["action"], "prepare_release_instruction")
        with self.github(comments=[{"body": "/release devops"}]):
            self.assertEqual(release.inspect_task(self.task)["action"], "await_merge")

    def test_wiz_title_priority_and_first_matching_comment(self):
        comments = [{"body": "Please check\n/release gateway"}, {"body": "/release devops"}]
        self.assertEqual(release.release_names("release devops", comments), ["devops"])
        self.assertEqual(release.release_names("Fix devops", comments), ["gateway"])
        self.assertEqual(release.release_names("release devops, gateway", []), ["devops", "gateway"])
        self.assertEqual(release.release_names("Fix devops", [{"body": "/release devops\r\n"}]), [])

    def test_late_wiz_comment_does_not_mean_published(self):
        self.task["repo"] = "wiz"
        with self.github(merged=True, comments=[{"body": "/release devops"}]):
            result = release.inspect_task(self.task)
        self.assertEqual(result["action"], "inspect_existing_publication_before_dispatch")
        self.assertEqual(result["merge_sha"], SHA)

    def test_resume_pending_failed_successful_runs(self):
        self.task["extensions"]["devops"]["run_ids"] = [99]
        for status, conclusion, action in (
            ("in_progress", None, "await_chart_runs"),
            ("completed", "failure", "diagnose_chart_runs"),
            ("completed", "success", "verify_charts_and_source_refs"),
        ):
            run = {"id": 99, "status": status, "conclusion": conclusion, "head_sha": SHA}
            with self.subTest(status=status, conclusion=conclusion), self.github(merged=True, run=run):
                original = copy.deepcopy(self.task)
                self.assertEqual(release.inspect_task(self.task)["action"], action)
                self.assertEqual(self.task, original)
                self.assertIsNone(self.task["extensions"]["devops"]["verification"])

    def test_missing_extension_run_still_requires_discovery(self):
        self.task["extensions"]["gateway"] = {
            "version": "1.0.0", "dispatch": None,
            "run_ids": [], "verification": None, "error": None,
        }
        self.task["extensions"]["devops"]["run_ids"] = [99]
        run = {"id": 99, "status": "completed", "conclusion": "success", "head_sha": SHA}
        with self.github(merged=True, run=run):
            self.assertEqual(
                release.inspect_task(self.task)["action"], "inspect_existing_publication_before_dispatch",
            )

    def test_chart_pull_verifies_and_checkpoints_digest(self):
        release.save_task(self.file, self.task)
        with self.helm():
            evidence = release.verify_chart(self.file, self.task, "devops")
        self.assertEqual(evidence["digest"], DIGEST)
        self.assertEqual(release.read_task(self.file)["extensions"]["devops"]["verification"], evidence)
        with self.helm():
            release.verify_chart(self.file, self.task, "devops")
        self.assertIsNone(self.task["extensions"]["devops"]["error"])

    def test_version_mismatch_blocks_museum(self):
        release.save_task(self.file, self.task)
        with self.helm(metadata="name: devops\nversion: 1.2.6\n"):
            with self.assertRaisesRegex(ValueError, "name/version"):
                release.verify_chart(self.file, self.task, "devops")
        self.assertIsNone(release.read_task(self.file)["extensions"]["devops"]["verification"])
        with self.assertRaisesRegex(ValueError, "no successful verification"):
            release.update_museum(self.task, self.root, None)

    def test_changed_digest_stays_blocked_across_retries(self):
        self.verified()
        release.save_task(self.file, self.task)
        for _ in range(2):
            with self.helm(digest="sha256:" + "c" * 64):
                with self.assertRaisesRegex(ValueError, "digest changed"):
                    release.verify_chart(self.file, self.task, "devops")
            self.task = release.read_task(self.file)
            self.assertEqual(self.task["extensions"]["devops"]["verification"]["digest"], DIGEST)
            self.assertIsNotNone(self.task["extensions"]["devops"]["error"])
            with self.assertRaisesRegex(ValueError, "no successful verification"):
                release.update_museum(self.task, self.root, None)

    def test_missing_digest_never_marks_chart_verified(self):
        release.save_task(self.file, self.task)
        with self.helm(digest=""):
            with self.assertRaisesRegex(ValueError, "unambiguous OCI digest"):
                release.verify_chart(self.file, self.task, "devops")
        self.assertIsNone(self.task["extensions"]["devops"]["verification"])
        self.assertIsNotNone(self.task["extensions"]["devops"]["error"])

    def test_auth_failure_retains_baseline_but_blocks_museum(self):
        self.verified()
        release.save_task(self.file, self.task)
        with self.helm(fail=True):
            with self.assertRaisesRegex(ValueError, "helm failed"):
                release.verify_chart(self.file, self.task, "devops")
        self.assertEqual(self.task["extensions"]["devops"]["verification"]["digest"], DIGEST)
        with self.assertRaisesRegex(ValueError, "no successful verification"):
            release.update_museum(self.task, self.root, None)

    def test_pull_failure_does_not_expose_raw_credentials(self):
        release.save_task(self.file, self.task)
        with self.helm(fail=True):
            status, _, errors = self.invoke("verify-chart", "--file", str(self.file), "--extension", "devops")
        self.assertEqual(status, 2)
        self.assertNotIn("secret-registry-password", errors + self.file.read_text())
        self.assertIsNotNone(release.read_task(self.file)["extensions"]["devops"]["error"])

    def test_archive_metadata_checks_all_top_level_manifests(self):
        archive = self.root / "chart.tgz"
        with tarfile.open(archive, "w:gz") as package:
            for filename, content in (
                ("devops/Chart.yaml", b"name: devops\nversion: 1.2.5\n"),
                ("devops/extension.yaml", b"name: devops\nversion: 1.2.6\n"),
            ):
                member = tarfile.TarInfo(filename)
                member.size = len(content)
                package.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "name/version"):
            release.archive_metadata(archive, "devops", "1.2.5")

    def test_archive_links_are_not_followed(self):
        archive = self.root / "chart.tgz"
        with tarfile.open(archive, "w:gz") as package:
            member = tarfile.TarInfo("devops/Chart.yaml")
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            package.addfile(member)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            release.archive_metadata(archive, "devops", "1.2.5")

    def test_nested_subchart_cannot_prove_artifact_identity(self):
        archive = self.root / "chart.tgz"
        content = b"name: devops\nversion: 1.2.5\n"
        with tarfile.open(archive, "w:gz") as package:
            member = tarfile.TarInfo("other/charts/devops/Chart.yaml")
            member.size = len(content)
            package.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "no matching top-level metadata"):
            release.archive_metadata(archive, "devops", "1.2.5")

    def test_multi_track_preview_write_and_repeat_preserve_text(self):
        text = (
            "# museum\ncharts: # supported releases\n"
            "  - 'oci://hub.kubesphere.com.cn/charts/devops:1.2.4' # keep\n"
            "# unrelated comment\nsettings: {enabled: true}\n"
        )
        paths = [self.config(target, text) for target in self.task["museum"]["targets"]]
        os.chmod(paths[0], 0o640)
        self.verified()
        plans = release.update_museum(self.task, self.root / "museum", None)
        self.assertEqual(len(plans), 2)
        self.assertTrue(all(path.read_text() == text for path in paths))
        release.update_museum(self.task, self.root / "museum", None, write=True)
        expected = text.replace(
            "# unrelated comment", f"  - {release.chart_ref('devops', '1.2.5')}\n# unrelated comment",
        )
        self.assertTrue(all(path.read_text() == expected for path in paths))
        self.assertEqual(stat.S_IMODE(paths[0].stat().st_mode), 0o640)
        repeated = release.update_museum(self.task, self.root / "museum", None, write=True)
        self.assertTrue(all(plan["additions"] == [] and plan["diff"] == "" for plan in repeated))

    def test_empty_block_indentless_and_no_final_newline(self):
        reference = release.chart_ref("devops", "1.2.5")
        for original, expected in (
            ("charts:\nother: yes\n", f"charts:\n  - {reference}\nother: yes\n"),
            ("charts: # empty", f"charts: # empty\n  - {reference}\n"),
            ("charts:\n- old", f"charts:\n- old\n- {reference}\n"),
        ):
            with self.subTest(original=original):
                actual, additions = release.append_charts(original, [reference, reference])
                self.assertEqual(actual, expected)
                self.assertEqual(additions, [reference])

    def test_crlf_preserved(self):
        replacement, _ = release.append_charts("charts:\r\n  - old\r\nother: true\r\n", ["oci://host/x:1"])
        self.assertEqual(replacement, "charts:\r\n  - old\r\n  - oci://host/x:1\r\nother: true\r\n")

    def test_reject_invalid_yaml_shapes_and_alias_side_effects(self):
        for original in (
            "charts: []\n",
            "charts:\n  - 123\n",
            "charts:\n  - old\ncharts:\n  - duplicate\n",
            "charts:\n  - old\nother: {key: 1, key: 2}\n",
            "charts: &shared\n  - old\nother: *shared\n",
            "other: &shared\n  - old\ncharts: *shared\n",
            "other: true\n",
            "charts: [old\n",
        ):
            with self.subTest(original=original), self.assertRaises((ValueError, release.yaml.YAMLError)):
                release.append_charts(original, ["oci://host/x:1"])

    def test_all_configs_validated_before_first_write(self):
        first = self.config()
        original = first.read_bytes()
        self.config("latest", "charts: [broken\n")
        self.verified()
        with self.assertRaises(release.yaml.YAMLError):
            release.update_museum(self.task, self.root / "museum", None, write=True)
        self.assertEqual(first.read_bytes(), original)

    def test_missing_and_escaping_targets_do_not_write(self):
        first = self.config()
        original = first.read_bytes()
        self.verified()
        with self.assertRaises(FileNotFoundError):
            release.update_museum(self.task, self.root / "museum", None, write=True)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "config.yaml").write_text("charts:\n  - old\n")
        (self.root / "museum" / "latest").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "inside museum"):
            release.update_museum(self.task, self.root / "museum", None, write=True)
        self.assertEqual(first.read_bytes(), original)

    def test_batch_gate_and_explicit_subset(self):
        self.config()
        self.config("latest")
        self.task["extensions"]["gateway"] = {
            "version": "1.0.0", "dispatch": None,
            "run_ids": [], "verification": None, "error": "publish failed",
        }
        self.verified()
        with self.assertRaisesRegex(ValueError, "gateway"):
            release.update_museum(self.task, self.root / "museum", None, write=True)
        plans = release.update_museum(self.task, self.root / "museum", ["devops"], write=True)
        self.assertTrue(all(plan["additions"] == [release.chart_ref("devops", "1.2.5")] for plan in plans))

    def test_explicit_targets_required(self):
        self.task["museum"]["targets"] = []
        with self.assertRaisesRegex(ValueError, "explicit museum targets"):
            release.update_museum(self.task, self.root, None)

    def test_dispatch_intent_roundtrip_and_validation(self):
        self.task["extensions"]["devops"]["dispatch"] = {
            "ref": "main", "sha": SHA, "attempted_at": "2026-11-12T10:00:00+08:00",
        }
        release.save_task(self.file, self.task)
        self.assertEqual(release.read_task(self.file), self.task)
        self.task["extensions"]["devops"]["dispatch"]["attempted_at"] = "2026-11-12"
        with self.assertRaisesRegex(ValueError, "timezone"):
            release.validate_task(self.task)

    def test_failed_older_run_does_not_mask_successful_retry(self):
        self.task["source"]["pr"] = 12
        self.task["extensions"]["devops"]["run_ids"] = [98, 99]
        responses = [
            {"merged": True, "state": "closed", "title": "Fix devops", "merge_commit_sha": SHA},
            {"id": 98, "status": "completed", "conclusion": "failure", "head_sha": SHA},
            {"id": 99, "status": "completed", "conclusion": "success", "head_sha": SHA},
        ]
        with mock.patch.object(release, "github", side_effect=responses):
            result = release.inspect_task(self.task)
        self.assertEqual(result["action"], "verify_charts_and_source_refs")
        self.assertEqual(len(result["runs"]["devops"]), 2)

    def test_cli_roundtrip(self):
        status, output, errors = self.invoke(
            "init", "--file", str(self.file), "--repo", "addons",
            "--extension", "devops=1.2.5", "--target", "v11.4.0",
        )
        self.assertEqual(status, 0, errors)
        self.task = json.loads(output)
        self.config()
        with self.helm():
            status, _, errors = self.invoke(
                "verify-chart", "--file", str(self.file), "--extension", "devops",
            )
        self.assertEqual(status, 0, errors)
        status, output, errors = self.invoke(
            "museum", "--file", str(self.file), "--root", str(self.root / "museum"), "--write",
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(json.loads(output)[0]["additions"], [release.chart_ref("devops", "1.2.5")])


if __name__ == "__main__":
    unittest.main()
