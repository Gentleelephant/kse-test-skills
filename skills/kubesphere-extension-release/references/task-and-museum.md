# Task Records And Museum Updates

## Helper

Run `python3 "$SKILL_DIR/scripts/release_task.py" --help`. Python 3.10+ and
the PyYAML dependency in `requirements.txt` are needed. The helper executes no
Git commits, pushes, PR creation, comments, or workflow dispatches.

Initialize a task outside Git; initialization refuses to overwrite an existing
record:

```bash
python3 "$SKILL_DIR/scripts/release_task.py" init \
  --file "$TASK_FILE" --repo addons \
  --extension devops=1.2.5 --extension gateway=1.0.0 \
  --target v11.4.0 --target latest
```

Use `--repo wiz` for Wiz. Already published Charts can start here without a PR;
select their source repository for mapping, leaving source PR/run fields empty.
Omit targets until the user supplies them; an empty target set blocks museum.

`show --file "$TASK_FILE"` validates and prints the local record, without network
access. To checkpoint changes, prepare a full replacement JSON in a temporary
file, then run:

```bash
python3 "$SKILL_DIR/scripts/release_task.py" save \
  --file "$TASK_FILE" --input "$CANDIDATE_FILE"
```

The record has these editable fields:

- `repo`: `addons` or `wiz`.
- `source`: `workdir`, `branch`, `pr` (number), `merge_sha`.
- `museum`: `workdir`, `branch`, `pr` (number), `targets` (track strings).
- `extensions`: a map from name to `version`, `dispatch`, `run_ids`,
  `verification`, `error`. Run IDs are associated Chart workflow runs, not
  Release workflow runs. Append attempts in order; inspection uses the last
  recorded run for each extension while retaining earlier attempts for diagnosis.

Nullable fields start as `null`. Each verification records the full Chart URI,
OCI digest, and UTC check time. It is populated by `verify-chart`; do not
manufacture success by editing it. Expected version changes require clearing
run IDs, dispatch, and verification. Before a manual workflow dispatch, save its
intent in that extension's `dispatch`, for example:

```json
{
  "ref": "main",
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "attempted_at": "2026-11-12T10:00:00+08:00"
}
```

An intent is not evidence that a workflow started. On resumption, discover the
actual remote run before retrying. Store a sanitized short failure cause in `error`,
not raw subprocess output. Unknown fields are rejected to reduce accidental
credential storage; that is not a secret scanner for arbitrary text values.
Keep all task/candidate files outside repositories. Do not run simultaneous
writers against one task.

`inspect --file "$TASK_FILE"` performs read-only authenticated GitHub queries
for the recorded source PR, effective Wiz instruction, and associated Chart
runs. It reports a suggested next action, not authorization or proof of artifact
publication. With no PR it reports a choice of source contribution or
artifact-only verification. This is one bounded inspection, not a polling loop.
The agent must also check current workflows, source refs, and run association
as described in [repository-workflow.md](repository-workflow.md).

## Verify Charts

```bash
python3 "$SKILL_DIR/scripts/release_task.py" verify-chart \
  --file "$TASK_FILE" --extension devops
```

This uses `helm pull` against `oci://hub.kubesphere.com.cn/charts/devops` with
the recorded `--version`, into a temporary directory. It reads the downloaded
archive without extracting it, requires matching top-level Chart/extension
metadata, and captures the OCI digest returned by Helm. It then checkpoints
that extension's evidence. A failed check records an error that blocks museum
updates; the last successful observation is retained to detect digest changes
across repeated attempts, not treated as a current successful check.
Credentials are supplied by the existing Helm registry login, never arguments
saved into task JSON.

Verify every extension again after resuming before a museum write. A version
can be overwritten at the registry; report a changed digest for the same
version and resolve its provenance before continuing. The helper refuses to
replace previously recorded evidence with a different digest.

Do not infer unavailable Charts from a failed pull alone; authentication and
network failures are also possible. Record the failure and inspect locally.

## Museum

Fetch `kubesphere-extensions/extension-museum` and prepare an isolated branch
for `Gentleelephant/extension-museum`. Do not guess targets from directory
names. Commercial or customer-specific tracks are not interchangeable.

Preview:

```bash
python3 "$SKILL_DIR/scripts/release_task.py" museum \
  --file "$TASK_FILE" --root "$MUSEUM_WORKDIR"
```

Apply the reviewed append operation with the same command plus `--write`.
If the user explicitly chose a successful subset, pass repeated
`--only NAME`; otherwise all task extensions must have verification evidence
and no recorded error.

The helper validates every selected file before writing any:

- Targets must be explicit relative directories inside the museum root.
- Each existing `config.yaml` must be a mapping with a block `charts` list of
  scalar strings. Empty block lists are supported; flow lists such as
  `charts: []`, aliases that would change other fields, and duplicate YAML
  keys are rejected rather than reformatted.
- Append the missing full `oci://hub.kubesphere.com.cn/charts/<name>:<version>`
  references. Preserve existing versions, ordering, comments, and other fields.
- Parse the result and prove that only the selected `charts` list changed.
  Output JSON containing additions and a unified diff for each file.

Existing exact references yield no changes. Missing tracks and malformed YAML
are errors; do not create new tracks or replace configs automatically.
The helper performs atomic replacement per file, not a transaction across files.
If an OS write fails midway, inspect the diff and rerun; appends are idempotent.

Review `git diff --check` and the exact config diff. Submit only changed task
configs using the contribution sequence in `repository-workflow.md`. Store the
museum PR number and report its link. No museum image build or release dispatch
belongs to this default workflow.
