# Repository Contribution And Publication

## Mapping

| Alias | Upstream | Fork | Extension directory |
| --- | --- | --- | --- |
| `addons` | `kubesphere-extensions/ks-platform-addons` | `Gentleelephant/ks-platform-addons` | `charts/<name>` |
| `wiz` | `WizTelemetry/extensions` | `Gentleelephant/extensions` | `<name>` |
| museum | `kubesphere-extensions/extension-museum` | `Gentleelephant/extension-museum` | `<track>/config.yaml` |

These are this user's defaults. Confirm actual repository identity before
writing. All currently publish to `oci://hub.kubesphere.com.cn/charts`.
Read the current default branch and workflow definitions each time; the behavior
below was inspected on 2026-11-12 and is not a permanent upstream contract.
Wiz may require authenticated `gh` when a connector cannot read the private repo.

## Prepare And Submit

Resolve the default branch with `gh repo view OWNER/REPO --json defaultBranchRef`.
Inspect `git status --short`, `git remote -v`, and applicable AGENTS.md files.
Reuse correct remotes; use another remote name instead of changing a user's
existing remote. Fetch upstream and fork. Create a dedicated worktree from the
fetched upstream base, or clone into a task directory when no checkout exists.
Do not pull into dirty work or rebase someone else's active branch automatically.
For a resumed task, use its recorded branch instead of creating another one.

After requested modifications and dev checks:

1. Review `git diff --check` and the task diff; stage only explicit task paths.
2. Commit and push the branch to the fork when contribution is authorized.
3. Query existing PRs by upstream, base, and `Gentleelephant:<branch>` (including
   closed PRs). Reuse an open PR; a merged PR is a resume point, not a new PR.
   A closed, unmerged PR needs an explicit resolution rather than a duplicate.
4. Create a missing PR using `gh pr create --repo OWNER/REPO --base BASE
   --head Gentleelephant:BRANCH --title TITLE --body-file BODY_FILE`.
   Use a real temporary UTF-8 body file, not shell-interpolated multiline text.

Use the same sequence for museum. Source PR bodies explain changes and checks.
Museum bodies list source PR, extension versions, selected tracks, workflow run
links where available, and OCI verification digests.

## Wiz Release Instructions

Read `.github/workflows/release.yml` and
`.github/workflows/helm-chart-publish.yaml` through authenticated GitHub APIs.
The Release workflow currently runs on PR `closed` events targeting `main`, or
tags matching `*/*`. It does **not** run on `issue_comment`.

It selects extensions in this order:

1. Title matching `^release [a-z0-9_-]+(,? +[a-z0-9_-]+)*$`.
2. Otherwise the first matching line across fetched PR comments:
   `^/release [a-z0-9_-]+(,? +[a-z0-9_-]+)*$`.

For example, `/release whizard-logging whizard-monitoring` must be present
**before closure**. A release title takes priority over comments. Another matching
comment can be ignored if an earlier one exists. Read comments in API order and
inspect pagination; the workflow's own non-paginated API read can miss later
comments. If the effective instruction differs from the task, fix an authorized
title/comment you control or explain the conflict. Do not append another command
and claim it supersedes the old one. Never edit someone else's comment.

When publication is authorized, prepare the effective instruction on the open
PR using `gh pr comment --repo WizTelemetry/extensions NUMBER --body-file FILE`.
Inspect first and reuse an equivalent existing instruction. The helper's
`inspect` command reads the PR and the first comment page, matching the current
workflow; its result is advisory and performs no write.

On closure, the workflow reads `<name>/extension.yaml`, creates tags
`<name>/<version>` and GitHub Releases, and explicitly dispatches
`helm-chart-publish.yaml` on `main` with inputs `extension-name` and
`chart-version`. The chart workflow also supports tag-triggered publication.
The Release workflow currently lacks a merged-only condition: check `merged`
yourself; closed-unmerged is blocked.

A “Releases completed” bot comment only means publication was dispatched.
For an already merged PR missing its release instruction, adding a late comment
does not trigger publication. Inspect tags, Releases, runs, and OCI first. If
publication is still missing, explain the state and, within an authorized release
task, dispatch the existing Chart workflow for the expected name/version:

```bash
gh workflow run helm-chart-publish.yaml --repo WizTelemetry/extensions \
  --ref main -f extension-name=NAME -f chart-version=VERSION
```

Read the manifest at the selected ref first. Requested `chart-version` must equal
its version, because packaging takes the manifest version. Do not recreate tags
or rerun the entire Release workflow to repair a missing Chart; that workflow's
tag/release creation is not idempotent.

## Addons Publication

Read `.github/workflows/helm-chart-publish.yaml`. Current triggers:

- Tags matching `*-v*`, interpreted as `<name>-v<version>`.
- `workflow_dispatch` with required `chart-name`.

After source PR merge, inspect an existing tag-triggered/manual run or an already
published artifact before dispatching:

```bash
gh workflow run helm-chart-publish.yaml \
  --repo kubesphere-extensions/ks-platform-addons \
  --ref main -f chart-name=NAME
```

The workflow packages `charts/<name>` and reads its `extension.yaml` version.
It accepts one extension per dispatch; dispatch the task extensions separately.
The workflow uses the selected checkout ref and has no expected-version input.

## Ref, Run, And Artifact Evidence

Before either manual dispatch, confirm that the selected ref contains the merged
change and its manifest still matches the task version. `main` can move after a
merge, especially during upstream review. If a later change bumped the version,
do not publish that newer version under this task. Use an existing appropriate
tag/ref only after checking the current workflow supports it and it contains the
expected source; otherwise report the mismatch for resolution.

Record a dispatch attempt's time, ref, and expected extension before sending it.
`gh workflow run` does not reliably return a run ID. Discover the resulting run
with the workflow, event, ref, source SHA, and extension input/log evidence.
Runs for different extensions can share a workflow and SHA. If association is
ambiguous, stop and inspect rather than dispatch again or choose the newest run.
Do not persist raw logs or credential-bearing output.

Use `gh run view RUN_ID --repo OWNER/REPO` for recorded runs. Check the actual
`head_sha` and manifest versions there. Wiz's Release and Chart workflows may
checkout different revisions; do not infer the Chart source from the Release
tag alone. Record run IDs only for their proven extension association.

A pending run is a reason to save and yield. A failed run needs diagnosis before
retrying. Permissions to open PRs do not imply permission to dispatch upstream
Actions. Surface a permission error; never silently publish from the fork.

After a successful run, use `verify-chart` from
[task-and-museum.md](task-and-museum.md). An artifact-only entry point can verify
an already published Chart without a source PR or workflow run. Report artifact
identity as verified and source provenance as unverified if no run/ref evidence
exists; do not claim the artifact contains an unproven source change.
