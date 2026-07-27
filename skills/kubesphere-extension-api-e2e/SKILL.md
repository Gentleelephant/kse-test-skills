---
name: kubesphere-extension-api-e2e
description: Analyze, plan, run, diagnose, and clean up complete multi-role KubeSphere extension API E2E tests from one project-root kse-e2e.yaml. Use when Codex must inspect API documents, extension or Helm charts, RoleTemplates, backend source code, and a live ks-apiserver to derive authorization, CRUD, tenant-isolation, business-rejection, response-shaping, error-quality, and cleanup scenarios without relying on project-specific E2E code.
---

# KubeSphere Extension API E2E

Use this skill to produce repeatable tests for any KubeSphere extension. Do not
call a project's `cmd/e2e` or other dedicated test program. Such code may be
read only as a coverage reference.

Use `$kubesphere-iam-rbac` instead for one login, binding, RoleTemplate query,
or focused 401/403 diagnosis.

## Contract

- Work from the extension project root.
- The only user-maintained configuration is `./kse-e2e.yaml`.
- Resolve every relative path from that file's directory.
- Read every configured `spec.sources.backendCode` tree without modifying it.
- Write generated plans, reports, and recovery state only below
  `spec.report.directory`.
- Treat the runner as the deterministic executor. The Agent, not regex
  heuristics, interprets arbitrary backend business semantics.

If `./kse-e2e.yaml` is absent, stop before accessing a cluster and offer:

```bash
python3 <skill-dir>/scripts/run_e2e.py init --project .
```

Read `references/configuration.md` when creating or changing configuration.

## Workflow

1. Run `validate` to check the private config, API documents, chart rendering,
   backend source roots, credentials, and live RoleTemplate access.
2. Read `references/analysis-workflow.md`, then inspect API documents, rendered
   RoleTemplates, route/SAR code, handlers, services, admission, models, and
   errors in that order.
3. Express source-derived write bodies and business rules in
   `spec.scenarios.overrides` and `spec.scenarios.additional`. Read
   `references/scenario-schema.md` before editing them. Every derived scenario
   must cite API, RoleTemplate, or source file/line evidence and its certainty.
4. Run `plan`. Review every `needsConfiguration` item; fill only facts that
   cannot be discovered. Never invent a valid request body or expected result.
5. Show the sanitized target, cluster, roles, source roots, cleanup policy, and
   remaining gaps before live execution.
6. Run with the explicit execution flag. Compare functional authorization and
   diagnostic-message quality independently.

```bash
python3 <skill-dir>/scripts/run_e2e.py validate --config ./kse-e2e.yaml
python3 <skill-dir>/scripts/run_e2e.py plan --config ./kse-e2e.yaml
python3 <skill-dir>/scripts/run_e2e.py run --config ./kse-e2e.yaml --execute
```

If interrupted, clean by run ID:

```bash
python3 <skill-dir>/scripts/run_e2e.py cleanup \
  --config ./kse-e2e.yaml --run-id <run-id> --execute
```

Read `references/test-catalog.md` before claiming complete coverage.

## Evidence And Result Rules

- Rendered Chart RoleTemplates are intended design.
- Live RoleTemplates are deployed design.
- Aggregated built-in roles and bindings are effective IAM configuration.
- API responses are runtime behavior.
- Backend source establishes business validation, dependencies, filtering,
  response shaping, and intended error details.
- Report drift between layers separately. Never redefine expectation from a
  broken deployment.
- Call extension APIs through ks-apiserver.
- An invalid write returning 400 is not authorization evidence.
- Missing facts are `NEEDS_CONFIGURATION`; unavailable valid writes are
  `NOT_TESTED`; neither may be reported as PASS.
- Expected deny plus actual 2xx is a high-risk authorization bypass.

## Safety

- `kse-e2e.yaml` may contain test credentials. Keep it mode `0600`, ignored by
  Git, and never committed.
- Refuse validation or execution if Git tracks it.
- Never print or persist passwords, tokens, OAuth secrets, temporary user
  passwords, or complete Authorization headers.
- Default cleanup is `always`.

## Resources

- `scripts/run_e2e.py`: init, validation, deterministic planning, scenario
  execution, reporting, and cleanup.
- `assets/kse-e2e.example.yaml`: complete root configuration template.
- `references/configuration.md`: configuration contract and defaults.
- `references/analysis-workflow.md`: Agent source-analysis procedure.
- `references/scenario-schema.md`: generic scenario primitives.
- `references/test-catalog.md`: required coverage and Volcano acceptance map.
