---
name: kubesphere-extension-release
description: Prepare fork branches, contribute KubeSphere extension changes, resume upstream PR releases, verify OCI Charts, and submit extension-museum updates for WizTelemetry/extensions and kubesphere-extensions/ks-platform-addons. Use for the cross-repository contribution and release workflow, including resuming an existing PR or syncing published Charts to selected museum tracks.
---

# KubeSphere Extension Release

## Entry Points

Use this skill to coordinate source contribution, Chart publication, and museum
PRs. Resume from any completed stage; do not repeat source changes for an
existing PR or an already published Chart.

Examples:

- “修改 addons 里的 X 和 Y，检查后提交上游 PR。”
- “继续这个 PR 的发布，成功后同步到 museum 的 v11.4.0 和 latest。”
- “Chart 已经发布，更新 museum 的 v11.4.0 并提交 PR。”

Read [repository-workflow.md](references/repository-workflow.md) for repository
mapping, GitHub commands, and release differences. Read
[task-and-museum.md](references/task-and-museum.md) when recording progress,
verifying artifacts, or editing museum.

## Tools And Scope

- Use authenticated `gh`, Git, Python 3.10+, and PyYAML (see `requirements.txt`).
  Use Helm with OCI support for artifact verification. Check tools needed by
  the current stage; source preparation does not require Helm or a cluster.
- Reuse `$kubesphere-extension-dev` for extension inspection, lint, rendering,
  and packaging. Resolve its sibling directory or installed skill; if missing,
  report the missing validation dependency instead of claiming checks passed.
- Test-cluster `ksbuilder publish` and Marketplace `ksbuilder push` are different
  operations from this skill's OCI publication. Do not run them for this flow.
- Operate within the user's requested stages. “检查状态” is read-only.
  “继续发布并提交 museum PR” authorizes those stages; do not ask for the same
  authorization at every command. A status result is not write authorization.
- Do not merge PRs, change upstream workflows, install this skill, or configure
  unattended execution unless separately requested.

## Workflow

1. **Resolve the task.** Identify source repository, extension names, and change
   goal or existing source PR. Inspect the actual repository and remote state.
   Read versions from manifests; an intentional version change follows the
   user's requirement, not an inferred release bump. Museum targets can be
   supplied later but must be explicit before that stage.
2. **Prepare isolated source work.** Fetch the latest upstream default branch.
   Use a dedicated worktree/clone and task branch. Check repository-local
   instructions. Preserve dirty work, existing remotes, and unrelated commits;
   never reset, clean, overwrite a branch, or force-push as routine preparation.
3. **Change and check.** Modify the requested extensions, run applicable dev
   checks, and inspect the diff and package metadata. Do not claim validation
   when required tools are missing. No default cluster installation tests.
4. **Contribute.** If submission is in scope, stage explicit task paths, commit,
   push the fork branch, and create or reuse an upstream PR. Record the source
   PR, branch, workdir, and expected extension versions.
5. **Prepare release and yield.** For Wiz, arrange the effective release title
   or comment before PR closure when publication is authorized. For addons,
   wait for merge before workflow dispatch. Save progress and return the PR
   link and next action when upstream review is pending.
6. **Resume publication.** Re-read PR merge state, current workflow definitions,
   effective release instructions, existing runs, and manifests at the actual
   publication ref. Check for existing Charts before a new dispatch. Follow
   the repository-specific instructions; never publish a closed, unmerged PR.
   Associate run IDs using extension/version inputs and source evidence, not
   just the newest workflow name or a nearby timestamp.
7. **Verify each artifact.** Pull the exact OCI Chart with `verify-chart`.
   Successful dispatch, a GitHub Release, a bot comment, or a green workflow
   alone is insufficient. Save individual failures and pending runs. Default
   to waiting for the entire batch; update a successful subset only when the
   user explicitly selects it.
8. **Contribute museum changes.** Fetch the museum upstream default branch into
   isolated task work. Require selected tracks. Reverify Charts on resumption,
   preview `museum`, then use `--write`. Review YAML and Git diffs, push the
   fork branch, and create or reuse an upstream PR. Include source PR, versions,
   tracks, and verification evidence. No diff means no empty commit or PR.

## Progress And Completion

Keep a local JSON task outside Git, normally under
`~/.local/state/kse-release/<task-id>.json`. Use the helper to initialize,
validate, and save it. Record each external operation immediately; if interrupted
between dispatch and recording, inspect remote evidence before dispatching again.
Do not put credentials, raw command logs, or private download URLs in the task.

Local state locates work; it does not override GitHub or OCI truth. On resume,
recheck recorded PRs/runs and pull Charts again. If expected versions change,
clear their old run IDs, dispatch record, and verification before proceeding.

Do not wait indefinitely for merge or Actions. Make one inspection pass, save
the pending state, and return a resumable next action. On failure, record the
affected extension and cause; do not automatically redispatch failed jobs or
repeat an ambiguous write. Explain permission failures with the next required
action rather than falling back to a fork workflow lacking upstream secrets.

Report source PR, per-extension publication/verification result, museum PR,
and anything pending. The requested flow finishes when the museum upstream PR
exists (or selected references already exist). Museum merge, image builds, and
museum releases are outside the default scope.
