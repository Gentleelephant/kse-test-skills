---
name: kubesphere-extension-api-e2e
description: Plan, run, diagnose, and clean up RoleTemplate-driven KubeSphere extension API permission tests from one project-root kse-e2e.yaml. Use when Codex must map API documentation to RoleTemplate rules, invite temporary users into cluster, workspace, and namespace roles through KubeSphere member APIs, test own-scope and cross-scope access through ks-apiserver, or distinguish authentication, binding, aggregation, and API authorization failures.
---

# KubeSphere Extension API Permission E2E

## Use This Skill

Use it for a complete temporary-environment, multi-role extension API
permission matrix. Use `$kubesphere-iam-rbac` for one focused IAM operation or
401/403. Use `$kubesphere-extension-dev` for packaging and publishing.

RoleTemplates are the expected permission design. Do not maintain a second
manual permission matrix or make business E2E part of the default workflow.

## Prerequisites

- Run from the extension project root.
- Keep the only required user configuration at `./kse-e2e.yaml`.
- Provide API documents and an extension/Helm source containing RoleTemplates.
- Use a disposable KubeSphere test environment with every registered cluster
  `Ready=True`.
- Read `references/configuration.md` before configuration changes and
  `references/test-catalog.md` before claiming complete coverage.

## Quick Start

Run through the isolated dependency environment:

```bash
SKILL_DIR=/path/to/kubesphere-extension-api-e2e
RUNNER="uv run --with-requirements $SKILL_DIR/requirements.txt python $SKILL_DIR/scripts/run_e2e.py"

$RUNNER init --project .
$RUNNER doctor --config ./kse-e2e.yaml
$RUNNER validate --config ./kse-e2e.yaml
$RUNNER plan --config ./kse-e2e.yaml
$RUNNER run --config ./kse-e2e.yaml --execute
```

Without `uv`, install `requirements.txt` into an isolated virtual environment.
If configuration is absent, stop before cluster access and run `init`.

## Workflow

1. **Doctor** checks Python/PyYAML, ksbuilder, kubectl, private configuration,
   ks-apiserver authentication, member APIs, and cluster readiness.
2. **Validate** checks configuration, API documents, RoleTemplate rendering,
   credentials, APIService routes, membership APIs, and every cluster.
   Errors are categorized as `CONFIGURATION_ERROR`, `ENVIRONMENT_ERROR`, or
   `CLUSTER_ERROR`.
3. **Plan** maps every operation to group/version/resource/verb/scope, records
   mapping evidence, and derives ALLOW/DENY from the builtin role rules plus
   the RoleTemplates those roles aggregate. Rows whose outcome depends on
   request identity (Rego self-service rules) are `UNVERIFIED`, not guessed.
4. Review unresolved mappings, source/live RoleTemplate drift, roles, own and
   cross-scope targets, and write fixtures. Add `apiMappings` only when needed.
5. **Run** with explicit `--execute`. The runner creates temporary users,
   Workspaces, Namespaces, memberships, and verified bindings before API calls.
6. Interpret `PASS`, `FAIL`, `PARTIAL`, `NOT_TESTED`, and `SETUP_BLOCKED`
   separately. Expected DENY plus 2xx is a high-risk bypass.
7. Default cleanup is `always`. Recover an interrupted run by ID:

```bash
$RUNNER cleanup --config ./kse-e2e.yaml --run-id <run-id> --execute
```

Cleanup verifies removals. Repeating cleanup after state removal returns PASS
with `alreadyClean: true`.

## Success Criteria

- Doctor and validate pass before resource creation.
- Every documented operation appears in the plan and result.
- Own, sibling Namespace, other Workspace, authenticated/no-permission, and
  no-token controls are covered as applicable.
- No-token denial returns 403, exactly like an authenticated denial. A
  request without `Authorization` is authenticated as `system:anonymous` and
  fails RBAC; 401 means the request never reached the RBAC layer.
- Unauthenticated requests are allowed only by the `anonymous` GlobalRole
  rules (`config.kubesphere.io/configs` get/list, `/static/images/*` GET).
- Writes without valid fixtures are `NOT_TESTED`, never PASS.
- Setup failures are `SETUP_BLOCKED`, not extension authorization failures.
- Cleanup has no errors or unexplained residual resources.
- Only captured cluster evidence is `VERIFIED`; otherwise report
  `INFERRED` or `UNVERIFIED`.

## Common Failures

- Missing configuration: run `init`; keep the file `0600` and Git-ignored.
- Missing PyYAML: use the Quick Start `uv` command.
- Unresolved mapping: add one exact `apiMappings` entry.
- APIService failure: inspect APIService, backend Service/Pod, and discovery.
- Member API failure: verify KubeSphere v1beta1 IAM member endpoints.
- Cluster not ready: stop before resource creation to avoid finalizer cleanup issues.
- Binding timeout: inspect invitation, roleRef, scope labels, user subject, and aggregation.
- No-token request returns 401 instead of 403: confirm the URL targets
  ks-apiserver (not Console), anonymous authentication is enabled, and the
  route is not on the RBAC-excluded path list. Treat it as an
  authentication-layer defect, not as expected denial.
- Interrupted run: use the recorded run ID with `cleanup --execute`.

Every failure must state what failed, impact, next check, and recovery.

## Safety And Cleanup

- Keep `kse-e2e.yaml` mode `0600`, Git-ignored, and untracked.
- Never print or persist passwords, tokens, OAuth secrets, temporary passwords,
  or full Authorization headers.
- Require `--execute` for live creation or deletion.
- Resolve report paths below `report.directory`.
- Default cleanup to `always`; preserve state when cleanup cannot be verified.

## Result Report

Report conclusion, evidence status, sanitized scope and versions, plan evidence,
permission matrix, interface/API results, mismatches, setup status, cleanup
status, untested rows, and sanitized reproduction commands.

Use the repository `validation/README.md` template for durable cluster evidence.

## Resources

- `scripts/run_e2e.py`: doctor, init, validate, plan, run, report, and cleanup.
- `assets/kse-e2e.example.yaml`: credential-free configuration template.
- `references/configuration.md`: configuration and validation categories.
- `references/analysis-workflow.md`: API-to-RoleTemplate rules.
- `references/test-catalog.md`: required coverage and evidence.
- `references/scenario-schema.md`: optional valid write fixtures.
