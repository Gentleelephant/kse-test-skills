---
name: kubesphere-extension-api-e2e
description: Plan, run, diagnose, and clean up RoleTemplate-driven KubeSphere extension API permission tests from one project-root kse-e2e.yaml. Use when Codex must map API documentation to RoleTemplate rules, invite temporary users into cluster, workspace, and namespace roles through KubeSphere member APIs, test own-scope and cross-scope access through ks-apiserver, or distinguish authentication, binding, aggregation, and API authorization failures.
---

# KubeSphere Extension API Permission E2E

Use RoleTemplates as the expected permission design. Do not create a second
user-maintained permission matrix and do not make plugin business E2E part of
the default workflow.

## Contract

- Work from the extension project root.
- Keep the only required user configuration at `./kse-e2e.yaml`.
- Resolve relative paths from the configuration directory.
- Read API documents and render the configured extension or Helm chart.
- Call extension APIs through ks-apiserver.
- Write plans, reports, and recovery state only below `report.directory`.

If the configuration is absent, stop before accessing a cluster and offer:

```bash
python3 <skill-dir>/scripts/run_e2e.py init --project .
```

Read `references/configuration.md` when creating or changing configuration.

## Workflow

1. Run `validate` to check configuration, API documents, RoleTemplate
   rendering, credentials, APIService routes, KubeSphere v1beta1 member APIs,
   and every registered cluster's `Ready` condition. An unavailable member
   cluster can block Workspace and WorkspaceRole finalizers even when the test
   Workspace is placed only on `host`, so execution must stop before creating
   resources.
2. Run `plan` to map each documented operation to
   group/version/resource/verb/scope and derive ALLOW/DENY from RoleTemplates.
3. Add an `apiMappings` entry only for operations whose authorization
   attributes cannot be inferred reliably.
4. Review the sanitized plan. It always uses two Workspaces and creates three
   real Kubernetes `core/v1 Namespace` resources through
   `/api/v1/namespaces`: two in Workspace A and one in Workspace B. The runner
   verifies each Namespace's `kubesphere.io/workspace` label before inviting
   users or testing extension APIs.
5. Run with `--execute`. The runner creates users and invites them through
   `clustermembers`, `workspacemembers`, and `namespacemembers`, then verifies
   the resulting binding before testing extension APIs.
6. Treat invitation, role readiness, or membership synchronization failures as
   `SETUP_BLOCKED`, not extension API permission failures.

```bash
python3 <skill-dir>/scripts/run_e2e.py validate --config ./kse-e2e.yaml
python3 <skill-dir>/scripts/run_e2e.py plan --config ./kse-e2e.yaml
python3 <skill-dir>/scripts/run_e2e.py run --config ./kse-e2e.yaml --execute
```

Clean an interrupted run by ID:

```bash
python3 <skill-dir>/scripts/run_e2e.py cleanup \
  --config ./kse-e2e.yaml --run-id <run-id> --execute
```

Read `references/test-catalog.md` before claiming complete permission coverage.

## Result Rules

- Rendered RoleTemplates are expected permissions.
- Live RoleTemplates show the deployed design.
- Aggregated roles and verified member bindings show effective IAM setup.
- API responses show runtime enforcement.
- Own-scope access follows matching RoleTemplate rules.
- Workspace roles may apply to Namespaces in their own Workspace.
- Sibling Namespace and other Workspace targets must be tested separately.
- No Token must return 401; an authenticated user without permission must
  return 403.
- A write without a valid request body is `NOT_TESTED`, never PASS.
- Expected DENY plus 2xx is a high-risk authorization bypass.

## Safety

- Keep `kse-e2e.yaml` mode `0600`, ignored by Git, and never committed.
- Never print or persist passwords, tokens, OAuth secrets, temporary passwords,
  or complete Authorization headers.
- Require explicit `--execute` before creating or deleting live resources.
- Default cleanup to `always`.

## Resources

- `scripts/run_e2e.py`: initialize, validate, plan, invite members, execute,
  report, and clean up.
- `assets/kse-e2e.example.yaml`: complete minimal configuration template.
- `references/configuration.md`: configuration fields and defaults.
- `references/analysis-workflow.md`: API-to-RoleTemplate mapping rules.
- `references/test-catalog.md`: required role and scope coverage.
- `references/scenario-schema.md`: optional request fixtures for write APIs;
  not required for permission planning.
