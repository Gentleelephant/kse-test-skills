---
name: kubesphere-iam-rbac
description: Authenticate to KubeSphere and operate or diagnose individual IAM/RBAC workflows. Use when Codex needs to get a bearer token, inspect ks-apiserver IAM APIs, read RoleTemplates or one aggregated role, create or delete a user, grant or revoke one platform/cluster/workspace/namespace role, call one API, or investigate a specific 401/403. Use kubesphere-extension-api-e2e instead for a complete temporary-environment, multi-role extension API matrix.
---

# KubeSphere IAM/RBAC

## Use This Skill

Use it for a focused authentication, user, role, binding, API-call, or 401/403
task. Do not use it for a complete API document and role matrix; hand that to
`$kubesphere-extension-api-e2e`. Use `$kubesphere-extension-dev` for extension
packaging or publishing.

## Prerequisites

- A confirmed ks-apiserver base URL, not only the Console URL.
- An existing token or administrator credentials supplied in the current task.
- Known target scope and, where applicable, cluster, Workspace, and Namespace.
- Read `references/kubesphere-rbac.md` before judging RoleTemplate aggregation
  or authorization expectations.

## Quick Start

```bash
SKILL_DIR=/path/to/kubesphere-iam-rbac
SERVER=https://ks.example.com

KS_TOKEN=$(python3 "$SKILL_DIR/scripts/ks_admin.py" login \
  --server "$SERVER" --username admin)
python3 "$SKILL_DIR/scripts/ks_admin.py" list-roletemplates \
  --server "$SERVER" --token "$KS_TOKEN" --extension example --output json
```

Use `login --diagnose` for sanitized diagnostics. `login --output json` returns
machine-readable token output when direct token output is inappropriate for
the caller.

## Workflow

1. Confirm server, scope, target names, and credentials.
2. Discover the API and list actual roles; never infer permissions from names.
3. Read RoleTemplates and the aggregated role to derive expected ALLOW/DENY.
4. Use `create-user` or an existing test user.
5. Use `bind-role`; namespace scope requires cluster, Workspace, and Namespace.
6. Read back the created user or binding; the helper performs this verification.
7. Call the API through ks-apiserver and compare the response with the derived
   expectation.
8. Use `unbind-role` and `delete-user`; both verify deletion and treat an
   already absent object as successful cleanup.

```bash
python3 "$SKILL_DIR/scripts/ks_admin.py" bind-role \
  --server "$SERVER" --token "$KS_TOKEN" --subject alice \
  --scope namespace --cluster host --workspace demo --namespace demo-ns --role viewer
python3 "$SKILL_DIR/scripts/ks_admin.py" api-call \
  --server "$SERVER" --token "$ALICE_TOKEN" --method GET \
  --path /clusters/host/apis/example.io/v1/namespaces/demo-ns/widgets \
  --expect-status 200
python3 "$SKILL_DIR/scripts/ks_admin.py" unbind-role \
  --server "$SERVER" --token "$KS_TOKEN" --subject alice \
  --scope namespace --cluster host --workspace demo --namespace demo-ns --role viewer
python3 "$SKILL_DIR/scripts/ks_admin.py" delete-user \
  --server "$SERVER" --token "$KS_TOKEN" --username alice
```

Use `--dry-run` before user, binding, or cleanup writes when the target is not
already confirmed.

## Success Criteria

- The API, RoleTemplate, aggregated role, user, and binding are all identified.
- Writes are read back; cleanup is verified with a 404.
- Expected ALLOW matches 2xx and expected DENY matches 403, including a
  no-token control. A request without `Authorization` becomes
  `system:anonymous` and is denied by RBAC with 403, not 401.
- 401, 404, 409, and 5xx are diagnosed, not counted as clean authorization evidence.
- Cluster conclusions are marked `VERIFIED`, `INFERRED`, or `UNVERIFIED`.

## Common Failures

- `401`: expired, malformed, or wrong-endpoint token, wrong Basic password, or
  an RBAC-excluded route. No token is not a 401; check the endpoint and
  anonymous authentication instead.
- `403`: compare RoleTemplate, aggregation, binding, user token, and path scope.
  A no-token request that reaches this point is behaving correctly.
- `404`: verify server URL, API discovery, group/version, and route prefix.
- `409`: read the existing object and decide whether it satisfies setup.
- `5xx`: inspect ks-apiserver, extension backend, and cluster events; report `PARTIAL`.
- HTML response: `--server` probably points to Console instead of ks-apiserver.
- TLS error: verify endpoint identity before explicitly using `--insecure`.

Every error explanation must include impact, next check, and cleanup/recovery.

## Safety And Cleanup

- Confirm exact server and scope before writes.
- Prefer least privilege and list roles before binding.
- Never persist or report passwords, tokens, client secrets, or Authorization headers.
- Use `--insecure` only after endpoint identity is confirmed.
- Run cleanup in reverse order: unbind roles, then delete the temporary user.

## Result Report

Report conclusion, evidence status, sanitized target, RoleTemplate expectation,
actual method/path/status, diagnosis, cleanup result, and sanitized reproduce
commands. Use `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`.

Use the repository `validation/README.md` template for durable cluster evidence.

## Resources

- `scripts/ks_admin.py`: authentication, IAM operations, JSON output, dry-run,
  verified writes, and verified cleanup.
- `references/kubesphere-rbac.md`: paths, object shapes, scope mapping,
  diagnosis, expected results, and verification commands.
