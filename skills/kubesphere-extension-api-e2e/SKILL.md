---
name: kubesphere-extension-api-e2e
description: Initialize, plan, run, diagnose, and clean up multi-role KubeSphere extension API end-to-end tests. Use when Codex needs to create a local kse-e2e.yaml, render extension or Helm charts, derive expected API permissions from RoleTemplates, create temporary cluster/workspace/namespace role users, compare expected and actual ks-apiserver responses, evaluate authorization error quality, or clean resources left by an interrupted test.
---

# KubeSphere Extension API E2E

Use this skill for complete, repeatable API authorization tests. Use
`$kubesphere-iam-rbac` instead for a single login, binding, RoleTemplate query,
or one-off 401/403 investigation.

## Workflow

1. Work from the extension project root.
2. Look for `kse-e2e.yaml`. If it is absent, stop before accessing a cluster and
   offer:

```bash
python3 <skill-dir>/scripts/run_e2e.py init --project .
```

3. Read `references/test-plan-schema.md` when configuring fixtures, path
   overrides, custom roles, or interpreting report fields.
4. Validate before planning or running:

```bash
python3 <skill-dir>/scripts/run_e2e.py validate --config ./kse-e2e.yaml
python3 <skill-dir>/scripts/run_e2e.py plan --config ./kse-e2e.yaml
```

5. Show the user the sanitized target server, cluster, roles, generated scopes,
   and cleanup policy before a live run. Require the explicit execution flag:

```bash
python3 <skill-dir>/scripts/run_e2e.py run --config ./kse-e2e.yaml --execute
```

6. If a run is interrupted, use the run ID printed by the runner:

```bash
python3 <skill-dir>/scripts/run_e2e.py cleanup \
  --config ./kse-e2e.yaml --run-id <run-id> --execute
```

## Evidence Rules

- Treat rendered Chart RoleTemplates as the intended design.
- Treat live RoleTemplates as the deployed design.
- Inspect aggregated built-in roles and test bindings as the effective IAM
  configuration.
- Treat API responses as runtime behavior.
- Report drift between these layers separately. Do not redefine the expected
  result from a broken live deployment.
- Call extension APIs through ks-apiserver, never the backend Service unless
  the API documentation explicitly requires it.
- Do not use an invalid write body as authorization evidence. When an OpenAPI
  example or fixture is unavailable, mark that write case `NOT_TESTED`.

## Safety

- `kse-e2e.yaml` may contain test-environment credentials. Keep it mode `0600`,
  ignored by Git, and never committed.
- Refuse validation or execution when Git already tracks the config.
- Never print or persist passwords, bearer tokens, OAuth secrets, or complete
  Authorization headers.
- Default cleanup to `always`. Use `never` only when the user deliberately
  wants to preserve a disposable test environment for investigation.
- Never run against a target other than the exact ks-apiserver and cluster in
  the sanitized preflight summary.

## Result

Report `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`. Keep functional authorization
and diagnostic-message quality as separate verdicts. Highlight expected deny
plus actual 2xx as a high-risk authorization bypass.

## Resources

- `scripts/run_e2e.py`: configuration initializer, validator, planner, runner,
  reporter, and cleanup command.
- `assets/kse-e2e.example.yaml`: local configuration template.
- `references/test-plan-schema.md`: configuration, fixture, permission
  derivation, and report contracts.
