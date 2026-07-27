---
name: kubesphere-iam-rbac
description: Authenticate to KubeSphere and operate or diagnose individual IAM/RBAC workflows. Use when Codex needs to get a bearer token, inspect ks-apiserver IAM APIs, read RoleTemplates or one aggregated role, create a user, grant one platform/cluster/workspace/namespace role, call one API, or investigate a specific 401/403. Use kubesphere-extension-api-e2e instead for a complete temporary-environment, multi-role extension API matrix.
---

# KubeSphere IAM/RBAC

## Overview

Use this skill to operate KubeSphere authentication, role binding, and extension API access-control tests from a terminal. Keep credentials, bearer tokens, cluster URLs, and kubeconfigs out of persisted skill files and final answers unless the user explicitly asks to reveal them.

## Workflow

1. Collect the target `--server` URL, username/password or existing `--token`, and the KubeSphere version if known. `--server` should point to `ks-apiserver`, not just the browser console URL. When an admin token is needed in a test environment and the user has provided a default admin test password in the current task context, try username `admin` with that password.
2. If the request involves testing an extension API, collect or read the API doc and infer API group, version, resource, scope, verbs, and ks-apiserver path templates.
3. Read `references/kubesphere-rbac.md` when API details, RoleTemplate aggregation, role scope, or expected status codes are unclear.
4. Prefer `scripts/ks_admin.py` for repeatable operations:

```bash
python3 scripts/ks_admin.py login --server https://ks.example.com --username admin
python3 scripts/ks_admin.py login --server https://ks.example.com --username admin --diagnose
python3 scripts/ks_admin.py create-user --server https://ks.example.com --token "$KS_TOKEN" --username alice --email alice@example.com --password '...'
python3 scripts/ks_admin.py list-roletemplates --server https://ks.example.com --token "$KS_TOKEN" --extension volcano
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope platform --role platform-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope cluster --cluster host --role cluster-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope workspace --workspace demo --role demo-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope namespace --cluster host --namespace demo --role admin
python3 scripts/ks_admin.py api-call --server https://ks.example.com --token "$KS_TOKEN" --path /apis/example.io/v1/namespaces/demo/widgets --expect-status 200
```

5. For a focused API access-control check, derive the expected permission from
   RoleTemplate resources first, then bind or reuse the selected user, obtain
   its token, call the API through ks-apiserver, and compare the response.
   For a complete role matrix with temporary tenants, fixtures, reports, and
   cleanup, hand off to `$kubesphere-extension-api-e2e`.
6. For one-off investigation, use `login` first, then query discovery endpoints with `curl -H "Authorization: Bearer $KS_TOKEN"`. If login fails, rerun with `--diagnose` and inspect only the sanitized HTTP status, path, and response shape.
7. Verify every write by reading the created user, role binding, or test API object after the operation.
8. Finish with the focused result format below.

## Focused Result

For a single IAM operation or authorization diagnosis, report:

1. `Conclusion`: one of `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`.
2. `Target`: sanitized ks-apiserver, cluster/workspace/namespace, user, and role.
3. `Expected`: RoleTemplate rule and aggregated role evidence.
4. `Actual`: operation, path, status, and sanitized response summary.
5. `Diagnosis`: likely failure layer and next check.
6. `Reproduce`: sanitized command with credentials referenced indirectly.

Do not use this focused format for a complete API document or role matrix;
hand that work to `$kubesphere-extension-api-e2e`.

## Safety Rules

- Treat authentication and RBAC changes as live administrative actions. Confirm the exact target server and scope before writing.
- Use least privilege by default. Prefer namespace or workspace roles over cluster or platform roles when they satisfy the goal.
- Do not log plaintext passwords or tokens. Prefer environment variables or interactive prompts. Treat default admin test passwords as sensitive: they may be used to obtain a token in test environments, but final reports and persisted logs must show them as `<redacted>`.
- Do not assume role names. List roles first when the role name is not provided or may differ by installation.
- Do not guess API authorization expectations from role names alone. Read RoleTemplate resources and actual aggregated roles before judging an API result.
- Call extension APIs through ks-apiserver unless the user or API doc explicitly says to call a backend service directly.
- When TLS is self-signed, use `--insecure` only after the user confirms the endpoint identity.
- KubeSphere 3.x commonly obtains API tokens from `POST /oauth/token`. KubeSphere/KSE 4.1 removed `iam.kubesphere.io/v1alpha2`, so do not depend on `/kapis/iam.kubesphere.io/v1alpha2/login` except as a legacy fallback.

## Resource Guide

- `scripts/ks_admin.py`: login, user creation, role and RoleTemplate listing, role-binding, and ks-apiserver API-call helper using only Python standard library and curl when available.
- `references/kubesphere-rbac.md`: API paths, RoleTemplate-driven permission testing, object shapes, scope mapping, verification commands, and troubleshooting.
