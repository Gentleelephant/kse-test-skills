---
name: kubesphere-iam-rbac
description: Authenticate to KubeSphere and test IAM/RBAC workflows. Use when Codex needs to get a KubeSphere bearer token from username/password, inspect ks-apiserver APIs, read RoleTemplate permissions, create or update KubeSphere users, list roles, grant platform/cluster/workspace/namespace roles, or verify whether extension API access matches the designed permissions.
---

# KubeSphere IAM/RBAC

## Overview

Use this skill to operate KubeSphere authentication, role binding, and extension API access-control tests from a terminal. Keep credentials, bearer tokens, cluster URLs, and kubeconfigs out of persisted skill files and final answers unless the user explicitly asks to reveal them.

## Workflow

1. Collect the target `--server` URL, username/password or existing `--token`, and the KubeSphere version if known. When an admin token is needed in a test environment and the user has provided a default admin test password in the current task context, try username `admin` with that password.
2. If the request involves testing an extension API, collect or read the API doc and infer API group, version, resource, scope, verbs, and ks-apiserver path templates.
3. Read `references/kubesphere-rbac.md` when API details, RoleTemplate aggregation, role scope, or expected status codes are unclear.
4. Prefer `scripts/ks_admin.py` for repeatable operations:

```bash
python3 scripts/ks_admin.py login --server https://ks.example.com --username admin
python3 scripts/ks_admin.py create-user --server https://ks.example.com --token "$KS_TOKEN" --username alice --email alice@example.com --password '...'
python3 scripts/ks_admin.py list-roletemplates --server https://ks.example.com --token "$KS_TOKEN" --extension volcano
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope platform --role platform-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope cluster --cluster host --role cluster-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope workspace --workspace demo --role demo-admin
python3 scripts/ks_admin.py bind-role --server https://ks.example.com --token "$KS_TOKEN" --subject alice --scope namespace --cluster host --namespace demo --role admin
python3 scripts/ks_admin.py api-call --server https://ks.example.com --token "$KS_TOKEN" --path /apis/example.io/v1/namespaces/demo/widgets --expect-status 200
```

5. For API access-control tests, derive expected permissions from RoleTemplate resources first, then bind or reuse users that represent those roles, get each user's token, call the API through ks-apiserver, and compare actual HTTP status with the derived expectation.
6. For one-off investigation, use `login` first, then query discovery endpoints with `curl -H "Authorization: Bearer $KS_TOKEN"`.
7. Verify every write by reading the created user, role binding, or test API object after the operation.
8. Finish with the standard result report format below. Do not invent a different report shape for each test.

## Standard Result Report

Every access-control test must end with a concise report in this order:

1. `Conclusion`: one of `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`.
2. `Scope`: target environment, KubeSphere version when known, ks-apiserver URL, extension/API under test, cluster/workspace/namespace, and test time.
3. `Evidence Sources`: API doc used, RoleTemplates inspected, aggregated roles inspected, users and bindings used, and discovery endpoints checked.
4. `Permission Matrix`: expected permissions derived from RoleTemplates and actual role aggregation.
5. `Interface Results`: one block or row per API interface, summarizing whether that interface passed, whether actual behavior matched the expected permissions, and the confidence level.
6. `API Results`: one row per role/user/API call with method, path, expected decision, expected HTTP status, actual HTTP status, and result.
7. `Mismatches`: only the failed or suspicious rows, with likely cause and next check.
8. `Reproduce`: sanitized commands or script invocations. Never include plaintext passwords or bearer tokens.

Use these result meanings:

- `PASS`: all tested API calls matched the RoleTemplate-derived expectation.
- `FAIL`: at least one tested API call contradicts the expected authorization behavior.
- `PARTIAL`: some checks passed, but coverage is incomplete or a non-authority issue such as `404` prevents a final authorization judgment for part of the matrix.
- `BLOCKED`: testing could not produce meaningful authorization evidence, for example no valid token, no API doc, no reachable ks-apiserver, or missing permission to read RoleTemplates.

For `Permission Matrix`, prefer this table shape:

```text
Role/Binding | Source RoleTemplate | Aggregated Role Checked | Resource | Verbs Expected Allow | Verbs Expected Deny | Notes
```

For `API Results`, prefer this table shape:

```text
Role/User | Method | Path | Expected | Expected HTTP | Actual HTTP | Result | Notes
```

For `Interface Results`, prefer this table shape:

```text
Interface | Method(s) | Resource/Path | Tested Roles | Expected Summary | Actual Summary | Result | Confidence | Notes
```

`Interface Results` is mandatory when the user provides API documentation or asks to test specific interfaces. Each documented interface must appear as `PASS`, `FAIL`, `PARTIAL`, or `NOT_TESTED`; do not hide untested interfaces.

Expected decisions must be `ALLOW`, `DENY`, or `UNKNOWN`. Treat `UNKNOWN` as `PARTIAL` unless the user explicitly asked for exploratory discovery only.

## Safety Rules

- Treat authentication and RBAC changes as live administrative actions. Confirm the exact target server and scope before writing.
- Use least privilege by default. Prefer namespace or workspace roles over cluster or platform roles when they satisfy the goal.
- Do not log plaintext passwords or tokens. Prefer environment variables or interactive prompts. Treat default admin test passwords as sensitive: they may be used to obtain a token in test environments, but final reports and persisted logs must show them as `<redacted>`.
- Do not assume role names. List roles first when the role name is not provided or may differ by installation.
- Do not guess API authorization expectations from role names alone. Read RoleTemplate resources and actual aggregated roles before judging an API result.
- Call extension APIs through ks-apiserver unless the user or API doc explicitly says to call a backend service directly.
- When TLS is self-signed, use `--insecure` only after the user confirms the endpoint identity.

## Resource Guide

- `scripts/ks_admin.py`: login, user creation, role and RoleTemplate listing, role-binding, and ks-apiserver API-call helper using only Python standard library and curl when available.
- `references/kubesphere-rbac.md`: API paths, RoleTemplate-driven permission testing, object shapes, scope mapping, verification commands, and troubleshooting.
