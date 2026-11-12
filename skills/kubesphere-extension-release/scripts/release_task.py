from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPOS = {
    "addons": "kubesphere-extensions/ks-platform-addons",
    "wiz": "WizTelemetry/extensions",
}
REGISTRY = "oci://hub.kubesphere.com.cn/charts"
NAME = re.compile(r"[a-z0-9_-]+")
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]*")
RELEASE = r"[a-z0-9_-]+(?:,? +[a-z0-9_-]+)*"


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in mapping:
                    raise ValueError(f"duplicate YAML key: {key}")
                mapping[key] = self.construct_object(value_node, deep=deep)
            except TypeError as error:
                raise ValueError("YAML mapping keys must be scalar") from error
        return mapping


def load_yaml(text):
    return yaml.load(text, Loader=UniqueLoader)


def require_fields(value, fields, context):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{context} must have exactly these fields: {', '.join(fields)}")


def chart_ref(name, version):
    return f"{REGISTRY}/{name}:{version}"


def validate_time(value, context):
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError(f"{context} must be an ISO timestamp with timezone")


def validate_task(task):
    require_fields(task, ["schema_version", "repo", "source", "museum", "extensions"], "task")
    if type(task["schema_version"]) is not int or task["schema_version"] != 1:
        raise ValueError("unsupported task schema_version")
    if not isinstance(task["repo"], str) or task["repo"] not in REPOS:
        raise ValueError("repo must be addons or wiz")
    for section, fields in (
        ("source", ["workdir", "branch", "pr", "merge_sha"]),
        ("museum", ["workdir", "branch", "pr", "targets"]),
    ):
        record = task[section]
        require_fields(record, fields, section)
        for field in ("workdir", "branch"):
            if record[field] is not None and (
                not isinstance(record[field], str) or not record[field].strip()
            ):
                raise ValueError(f"{section}.{field} must be a nonempty string or null")
        if record["pr"] is not None and (
            type(record["pr"]) is not int or record["pr"] <= 0
        ):
            raise ValueError(f"{section}.pr must be a positive integer or null")
    merge_sha = task["source"]["merge_sha"]
    if merge_sha is not None and (
        not isinstance(merge_sha, str)
        or len(merge_sha) not in (40, 64)
        or not re.fullmatch(r"[a-fA-F0-9]+", merge_sha)
    ):
        raise ValueError("source.merge_sha must be a full commit SHA or null")
    targets = task["museum"]["targets"]
    if not isinstance(targets, list) or any(
        not isinstance(target, str)
        or not target
        or Path(target).is_absolute()
        or any(part in ("", ".", "..") for part in target.split("/"))
        for target in targets
    ):
        raise ValueError("museum.targets must contain relative track directories")
    if len(set(targets)) != len(targets):
        raise ValueError("duplicate museum target")
    extensions = task["extensions"]
    if not isinstance(extensions, dict) or not extensions:
        raise ValueError("extensions must be a nonempty mapping")
    for name, record in extensions.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("invalid extension name")
        require_fields(record, ["version", "dispatch", "run_ids", "verification", "error"], name)
        if not isinstance(record["version"], str) or not VERSION.fullmatch(record["version"]):
            raise ValueError(f"invalid version for {name}")
        if not isinstance(record["run_ids"], list) or any(
            type(run_id) is not int or run_id <= 0 for run_id in record["run_ids"]
        ):
            raise ValueError(f"{name}.run_ids must be positive integers")
        if record["error"] is not None and not isinstance(record["error"], str):
            raise ValueError(f"{name}.error must be a sanitized string or null")
        dispatch = record["dispatch"]
        if dispatch is not None:
            require_fields(dispatch, ["ref", "sha", "attempted_at"], f"{name}.dispatch")
            if not isinstance(dispatch["ref"], str) or not dispatch["ref"].strip():
                raise ValueError(f"{name}.dispatch.ref must be a nonempty string")
            if not isinstance(dispatch["sha"], str) or not re.fullmatch(
                r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", dispatch["sha"]
            ):
                raise ValueError(f"{name}.dispatch.sha must be a full commit SHA")
            validate_time(dispatch["attempted_at"], f"{name}.dispatch.attempted_at")
        evidence = record["verification"]
        if evidence is not None:
            require_fields(evidence, ["chart", "digest", "checked_at"], f"{name}.verification")
            if evidence["chart"] != chart_ref(name, record["version"]):
                raise ValueError(f"stale verification for {name}")
            if not isinstance(evidence["digest"], str) or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", evidence["digest"]
            ):
                raise ValueError(f"invalid OCI digest for {name}")
            validate_time(evidence["checked_at"], f"{name}.verification.checked_at")
    return task


def read_task(path):
    return validate_task(json.loads(path.read_text(encoding="utf-8")))


def atomic_write(path, text, exclusive=False, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, mode)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_task(path, task, exclusive=False):
    validate_task(task)
    atomic_write(path, json.dumps(task, ensure_ascii=False, indent=2) + "\n", exclusive)


def init_task(repo, extensions, targets):
    records = {}
    for specification in extensions:
        name, separator, version = specification.partition("=")
        if not separator or name in records:
            raise ValueError("extensions must be unique NAME=VERSION pairs")
        records[name] = {
            "version": version, "dispatch": None, "run_ids": [],
            "verification": None, "error": None,
        }
    return validate_task({
        "schema_version": 1,
        "repo": repo,
        "source": {"workdir": None, "branch": None, "pr": None, "merge_sha": None},
        "museum": {"workdir": None, "branch": None, "pr": None, "targets": targets},
        "extensions": records,
    })


def command(arguments):
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"{arguments[0]} could not complete; inspect tools/auth locally") from error
    if result.returncode:
        raise ValueError(
            f"{arguments[0]} failed (exit {result.returncode}); inspect auth/network/logs locally"
        )
    return result


def github(endpoint):
    return json.loads(command(["gh", "api", endpoint]).stdout)


def release_names(title, comments):
    match = re.fullmatch(rf"release ({RELEASE})", title)
    if match:
        return match.group(1).replace(",", "").split()
    for comment in comments:
        for line in comment["body"].split("\n"):
            match = re.fullmatch(rf"/release ({RELEASE})", line)
            if match:
                return match.group(1).replace(",", "").split()
    return []


def inspect_task(task):
    repo = REPOS[task["repo"]]
    pr_number = task["source"]["pr"]
    if pr_number is None:
        return {"action": "prepare_source_or_verify_existing_charts", "runs": {}}
    pr = github(f"repos/{repo}/pulls/{pr_number}")
    if not pr["merged"] and pr["state"] == "closed":
        return {"action": "blocked_closed_unmerged", "runs": {}}
    effective = []
    if task["repo"] == "wiz":
        comments = github(f"repos/{repo}/issues/{pr_number}/comments")
        effective = release_names(pr["title"], comments)
    runs = {}
    for name, record in task["extensions"].items():
        runs[name] = []
        for run_id in record["run_ids"]:
            run = github(f"repos/{repo}/actions/runs/{run_id}")
            runs[name].append({
                "id": run["id"], "status": run["status"],
                "conclusion": run["conclusion"], "head_sha": run["head_sha"],
            })
    if not pr["merged"]:
        action = (
            "prepare_release_instruction"
            if task["repo"] == "wiz" and set(effective) != set(task["extensions"])
            else "await_merge"
        )
    else:
        action = "inspect_existing_publication_before_dispatch"
        recorded = [extension_runs[-1] for extension_runs in runs.values() if extension_runs]
        if any(run["status"] != "completed" for run in recorded):
            action = "await_chart_runs"
        elif any(run["conclusion"] != "success" for run in recorded):
            action = "diagnose_chart_runs"
        elif all(runs.values()):
            action = "verify_charts_and_source_refs"
    return {
        "action": action, "effective_release_extensions": effective,
        "merge_sha": pr["merge_commit_sha"] if pr["merged"] else None, "runs": runs,
    }


def archive_metadata(archive, name, version):
    found = False
    with tarfile.open(archive, "r:gz") as package:
        for filename in (f"{name}/Chart.yaml", f"{name}/extension.yaml"):
            members = [member for member in package.getmembers() if member.name == filename]
            if not members:
                continue
            if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
                raise ValueError("unsafe or ambiguous Chart metadata")
            stream = package.extractfile(members[0])
            if stream is None:
                raise ValueError("unreadable Chart metadata")
            with stream:
                metadata = load_yaml(stream.read().decode("utf-8"))
            if not isinstance(metadata, dict) or metadata.get("name") != name or str(
                metadata.get("version")
            ) != version:
                raise ValueError("Chart archive name/version does not match task")
            found = True
    if not found:
        raise ValueError("Chart archive has no matching top-level metadata")


def verify_chart(path, task, name):
    if name not in task["extensions"]:
        raise ValueError("extension is not in task")
    record = task["extensions"][name]
    previous = record["verification"]
    try:
        with tempfile.TemporaryDirectory(prefix="kse-chart-") as directory:
            result = command([
                "helm", "pull", f"{REGISTRY}/{name}", "--version", record["version"],
                "--destination", directory,
            ])
            digests = set(re.findall(
                r"Digest:\s*(sha256:[a-f0-9]{64})", result.stdout + "\n" + result.stderr,
            ))
            if len(digests) != 1:
                raise ValueError("Helm did not return one unambiguous OCI digest")
            digest = digests.pop()
            if previous is not None and previous["digest"] != digest:
                raise ValueError("OCI digest changed for the same version; resolve provenance")
            archives = list(Path(directory).glob("*.tgz"))
            if len(archives) != 1:
                raise ValueError("Helm did not download exactly one Chart archive")
            archive_metadata(archives[0], name, record["version"])
        record["verification"] = {
            "chart": chart_ref(name, record["version"]), "digest": digest,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        record["error"] = None
    except (ValueError, OSError, tarfile.TarError, yaml.YAMLError) as error:
        record["verification"] = previous
        record["error"] = "Chart verification failed; inspect OCI metadata/auth/network locally"
        save_task(path, task)
        raise ValueError(str(error)) from error
    save_task(path, task)
    return record["verification"]


def append_charts(text, references):
    document = load_yaml(text)
    root = yaml.compose(text, Loader=UniqueLoader)
    if not isinstance(document, dict) or not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise ValueError("config must be a block YAML mapping")
    pairs = [(key, value) for key, value in root.value if key.value == "charts"]
    if len(pairs) != 1:
        raise ValueError("config must have exactly one charts key")
    key_node, value_node = pairs[0]
    existing = document["charts"]
    lines = text.splitlines(keepends=True)
    if existing is None and re.fullmatch(
        r" *charts:[ \t]*(?:#[^\r\n]*)?\r?\n?", lines[key_node.start_mark.line],
    ):
        existing = []
        insertion = key_node.start_mark.line + 1
        indentation = " " * (key_node.start_mark.column + 2)
    elif (
        isinstance(existing, list)
        and all(isinstance(entry, str) for entry in existing)
        and isinstance(value_node, yaml.SequenceNode)
        and not value_node.flow_style
        and value_node.value
        and value_node.start_mark.line > key_node.start_mark.line
    ):
        first = value_node.value[0]
        last = value_node.value[-1]
        match = re.match(r"( *)- +", lines[first.start_mark.line])
        if match is None:
            raise ValueError("unsupported charts list indentation")
        indentation = match.group(1)
        insertion = last.end_mark.line + (last.end_mark.column != 0)
    else:
        raise ValueError("charts must be a block list of strings, without a sequence alias")
    additions = list(dict.fromkeys(reference for reference in references if reference not in existing))
    if not additions:
        return text, []
    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = "".join(lines[:insertion])
    if prefix and not prefix.endswith("\n"):
        prefix += newline
    replacement = (
        prefix
        + "".join(f"{indentation}- {reference}{newline}" for reference in additions)
        + "".join(lines[insertion:])
    )
    expected = dict(document)
    expected["charts"] = existing + additions
    if load_yaml(replacement) != expected:
        raise ValueError("append would change content outside charts; edit manually")
    return replacement, additions


def update_museum(task, root, only, write=False):
    targets = task["museum"]["targets"]
    if not targets:
        raise ValueError("explicit museum targets are required")
    selected = list(task["extensions"]) if only is None else list(dict.fromkeys(only))
    references = []
    for name in selected:
        if name not in task["extensions"]:
            raise ValueError(f"unknown extension: {name}")
        record = task["extensions"][name]
        if record["verification"] is None or record["error"]:
            raise ValueError(f"{name} has no successful verification; verify the batch or select --only")
        references.append(chart_ref(name, record["version"]))
    root = root.resolve(strict=True)
    plans = []
    for target in targets:
        path = (root / target / "config.yaml").resolve(strict=True)
        if not path.is_relative_to(root) or path == root:
            raise ValueError("museum config must resolve inside museum root")
        original = path.read_bytes().decode("utf-8")
        replacement, additions = append_charts(original, references)
        plans.append((path, original, replacement, additions))
    if len({path for path, _, _, _ in plans}) != len(plans):
        raise ValueError("museum targets resolve to the same config")
    if write:
        for path, original, replacement, additions in plans:
            if additions:
                if path.read_bytes().decode("utf-8") != original:
                    raise ValueError("config changed since preview; inspect and retry")
                atomic_write(path, replacement, mode=stat.S_IMODE(path.stat().st_mode))
    return [
        {
            "file": str(path.relative_to(root)), "additions": additions,
            "diff": "".join(difflib.unified_diff(
                original.splitlines(keepends=True), replacement.splitlines(keepends=True),
                fromfile=str(path.relative_to(root)), tofile=str(path.relative_to(root)),
            )),
        }
        for path, original, replacement, additions in plans
    ]


def parser():
    arguments = argparse.ArgumentParser(description="Checkpoint KSE release tasks and append museum Charts")
    commands = arguments.add_subparsers(dest="command", required=True)
    for name in ("init", "show", "save", "inspect", "verify-chart", "museum"):
        subcommand = commands.add_parser(name)
        subcommand.add_argument("--file", required=True, type=Path)
        if name == "init":
            subcommand.add_argument("--repo", required=True, choices=sorted(REPOS))
            subcommand.add_argument("--extension", required=True, action="append")
            subcommand.add_argument("--target", action="append", default=[])
        elif name == "save":
            subcommand.add_argument("--input", required=True, type=Path)
        elif name == "verify-chart":
            subcommand.add_argument("--extension", required=True)
        elif name == "museum":
            subcommand.add_argument("--root", required=True, type=Path)
            subcommand.add_argument("--only", action="append")
            subcommand.add_argument("--write", action="store_true")
    return arguments


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "init":
            task = init_task(arguments.repo, arguments.extension, arguments.target)
            save_task(arguments.file, task, exclusive=True)
            result = task
        else:
            task = read_task(arguments.file)
            if arguments.command == "save":
                candidate = read_task(arguments.input)
                for name, record in candidate["extensions"].items():
                    previous = task["extensions"].get(name)
                    if previous and previous["version"] != record["version"] and (
                        record["dispatch"] is not None
                        or record["run_ids"]
                        or record["verification"] is not None
                    ):
                        raise ValueError("version changes require clearing run IDs, dispatch, and verification")
                if task["repo"] != candidate["repo"]:
                    raise ValueError("repository changes require a new task")
                save_task(arguments.file, candidate)
                result = candidate
            elif arguments.command == "show":
                result = task
            elif arguments.command == "inspect":
                result = inspect_task(task)
            elif arguments.command == "verify-chart":
                result = verify_chart(arguments.file, task, arguments.extension)
            else:
                result = update_museum(task, arguments.root, arguments.only, arguments.write)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, yaml.YAMLError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
