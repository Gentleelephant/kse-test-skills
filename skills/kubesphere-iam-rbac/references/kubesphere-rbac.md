# KubeSphere IAM/RBAC Reference

## Core Model

- KubeSphere extension APIs are normally called through `ks-apiserver`. Do not call an extension backend workload directly unless the API doc explicitly requires it.
- API paths may be Kubernetes-style `/api` or `/apis`, or KubeSphere-style `/kapis`. In multi-cluster paths, KubeSphere commonly prefixes the cluster segment before the API path.
- Access-control expectations should come from the extension's `RoleTemplate` resources and the actual aggregated roles in the cluster, not from role names alone.
- Role names are installation-specific. Always list roles before binding. Workspace roles may be named like `<workspace>-admin`, `<workspace>-regular`, `<workspace>-viewer`, and `<workspace>-self-provisioner` on KubeSphere 4.x installations.

## API Test Workflow

1. Read the user-provided API doc and extract:
   - API group and version.
   - Resource name and subresources.
   - Scope: platform/global, cluster, workspace, or namespace/project.
   - Supported verbs: `get`, `list`, `watch`, `create`, `update`, `patch`, `delete`.
   - ks-apiserver path templates.
2. Discover the live API before testing:

```bash
curl -sk "$SERVER/apis" -H "Authorization: Bearer $KS_TOKEN"
curl -sk "$SERVER/kapis" -H "Authorization: Bearer $KS_TOKEN"
kubectl --context "$CONTEXT" api-resources | grep -i '<group-or-resource>'
```

3. Read relevant `RoleTemplate` resources and derive the designed permission matrix.
4. Confirm the target built-in or custom roles actually contain the expected aggregated rules.
5. Create or reuse users for the selected roles, then log in as each user to get a token.
6. Call each API through `ks-apiserver` with each user's token.
7. Compare expected vs actual status:
   - expected allow + actual `2xx`: pass.
   - expected deny + actual `403`: pass.
   - expected allow + actual `403`: fail; check RoleTemplate aggregation, binding, token, path scope, and cluster/namespace segment.
   - expected deny + actual `2xx`: fail; permission may be too broad or backend authorization may be missing.
   - `401`: token is missing, expired, malformed, or issued by a different endpoint.
   - `404`: verify the path and API discovery first; do not treat it as an authorization result.
   - `409`: object already exists; read it and decide whether it satisfies the test setup.
8. Produce the standard final report. Keep tokens, passwords, and full secret-bearing request bodies out of the report.

## Standard Final Result

The final result is a test report, not just raw command output. It must be stable enough that two agents testing different extensions can compare results directly.

Use this top-level structure:

```markdown
**Conclusion**
`PASS` | `FAIL` | `PARTIAL` | `BLOCKED`

**Scope**
- Environment:
- KubeSphere:
- ks-apiserver:
- Extension/API:
- Cluster:
- Workspace:
- Namespace:
- Test time:

**Evidence Sources**
| Type | Name/Path | Result |
| --- | --- | --- |

**Permission Matrix**
| Role/Binding | Source RoleTemplate | Aggregated Role Checked | Resource | Verbs Expected Allow | Verbs Expected Deny | Notes |
| --- | --- | --- | --- | --- | --- | --- |

**Interface Results**
| Interface | Method(s) | Resource/Path | Tested Roles | Expected Summary | Actual Summary | Result | Confidence | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

**API Results**
| Role/User | Method | Path | Expected | Expected HTTP | Actual HTTP | Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |

**Mismatches**
| Role/User | API | Expected | Actual | Likely Cause | Next Check |
| --- | --- | --- | --- | --- | --- |

**Reproduce**
Sanitized commands, with tokens referenced as environment variables.
```

Conclusion rules:

- `PASS`: every executed authorization check has a known expectation and matches it.
- `FAIL`: at least one known expectation is contradicted, such as expected `DENY` but actual `2xx`, or expected `ALLOW` but actual `403`.
- `PARTIAL`: some useful checks ran, but at least one row cannot be judged as authorization evidence. Common causes are `404` path uncertainty, missing API object prerequisites, incomplete role coverage, or expectation `UNKNOWN`.
- `BLOCKED`: no meaningful authorization judgment can be made. Common causes are invalid credentials, no token, ks-apiserver unreachable, missing API documentation, or inability to read RoleTemplates.

Row-level result rules:

- `PASS`: expected `ALLOW` and actual HTTP is `2xx`; expected `DENY` and actual HTTP is `403`.
- `FAIL`: expected `ALLOW` and actual HTTP is `401` or `403`; expected `DENY` and actual HTTP is `2xx`.
- `PARTIAL`: actual HTTP is `404`, `409`, or `5xx`, or the expected decision is `UNKNOWN`.
- `BLOCKED`: the row was not executed because setup failed.

Interface-level result rules:

- Every API interface from the provided API doc must be listed in `Interface Results`.
- `PASS`: all required role/user calls for that interface matched expectations.
- `FAIL`: at least one required role/user call for that interface contradicted expectations.
- `PARTIAL`: the interface was tested, but coverage or evidence is incomplete. Examples: only allow cases were tested, deny cases were not tested, a required role was unavailable, or the API returned `404`/`409`/`5xx`.
- `NOT_TESTED`: the interface was in the API doc but no call was executed. Explain why in `Notes`.
- The overall conclusion cannot be `PASS` if any documented interface is `FAIL`, `PARTIAL`, or `NOT_TESTED`.
- The overall conclusion should be `PARTIAL` when all executed checks pass but at least one documented interface is `PARTIAL` or `NOT_TESTED`.

Confidence levels:

- `High`: API doc, discovery, RoleTemplate rules, aggregated roles, bindings, tokens, and positive/negative API calls all support the result.
- `Medium`: authorization calls match expectations, but one supporting source is incomplete, such as aggregation not inspected or only a subset of roles tested.
- `Low`: result is based on limited evidence, inferred expectations, unstable setup, or responses that are not clean authorization signals.
- `None`: use only for `NOT_TESTED` rows.

Per-interface required details:

- Interface name from the API doc, not just the raw URL.
- HTTP method or Kubernetes verb being tested.
- Exact ks-apiserver path used.
- Resource identity: API group, version, resource, subresource, and scope.
- Roles/users tested.
- Expected allow/deny summary derived from RoleTemplates.
- Actual observed summary from API calls.
- Whether the interface behavior matched the expected permission design.
- Confidence level and the reason for that confidence.

Expected HTTP status guidance:

- `ALLOW`: usually `200` for read, `201` for create, `200` or `204` for update/delete, unless the API doc says otherwise.
- `DENY`: `403`.
- `UNKNOWN`: leave expected HTTP as `UNKNOWN` and explain what evidence is missing.

Evidence source expectations:

- API doc: record the doc path or URL and the API paths inferred from it.
- RoleTemplates: record names, scopes, aggregation labels, and matching rules.
- Aggregated roles: record the actual role names checked. Do not only say "admin" or "viewer" if the installation uses names like `<workspace>-viewer`.
- Users/bindings: record usernames and binding names, but not passwords or tokens.
- Discovery: record whether `/apis`, `/kapis`, or `kubectl api-resources` confirmed the API.

Example concise result:

```markdown
**Conclusion**
`FAIL`

**Scope**
- Environment: 17-26
- KubeSphere: v4.2.2-beta.0
- ks-apiserver: http://172.31.17.26:30881
- Extension/API: volcano jobs
- Cluster: host
- Workspace: n/a
- Namespace: demo
- Test time: 2026-07-17 14:30 CST

**Evidence Sources**
| Type | Name/Path | Result |
| --- | --- | --- |
| API doc | user-provided volcano API doc | paths extracted |
| RoleTemplate | volcano-job-viewer | grants get/list/watch to viewer |
| Aggregated role | viewer | rule present |
| Discovery | /clusters/host/apis/batch.volcano.sh/v1alpha1 | resource found |

**Permission Matrix**
| Role/Binding | Source RoleTemplate | Aggregated Role Checked | Resource | Verbs Expected Allow | Verbs Expected Deny | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| viewer | volcano-job-viewer | viewer | batch.volcano.sh/jobs | get,list,watch | create,update,patch,delete | namespace scope |
| admin | volcano-job-admin | admin | batch.volcano.sh/jobs | get,list,watch,create,update,patch,delete | none | namespace scope |

**Interface Results**
| Interface | Method(s) | Resource/Path | Tested Roles | Expected Summary | Actual Summary | Result | Confidence | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| List Volcano jobs | GET | batch.volcano.sh/v1alpha1 jobs, namespace demo | viewer, admin | viewer/admin allow list | both returned 200 | PASS | High | RoleTemplate, aggregation, and API call all aligned |
| Create Volcano job | POST | batch.volcano.sh/v1alpha1 jobs, namespace demo | viewer, admin | viewer deny, admin allow | viewer returned 201, admin returned 201 | FAIL | High | viewer behavior contradicts RoleTemplate-derived expectation |

**API Results**
| Role/User | Method | Path | Expected | Expected HTTP | Actual HTTP | Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| viewer/alice | GET | /clusters/host/apis/batch.volcano.sh/v1alpha1/namespaces/demo/jobs | ALLOW | 200 | 200 | PASS | list allowed |
| viewer/alice | POST | /clusters/host/apis/batch.volcano.sh/v1alpha1/namespaces/demo/jobs | DENY | 403 | 201 | FAIL | viewer can create |

**Mismatches**
| Role/User | API | Expected | Actual | Likely Cause | Next Check |
| --- | --- | --- | --- | --- | --- |
| viewer/alice | POST jobs | DENY 403 | 201 | backend or aggregation grants write too broadly | inspect viewer role rules and backend authorization |

**Reproduce**
`KS_TOKEN_ALICE=<redacted>`
`python3 scripts/ks_admin.py api-call --server "$SERVER" --token "$KS_TOKEN_ALICE" --method GET --path /clusters/host/apis/batch.volcano.sh/v1alpha1/namespaces/demo/jobs --expect-status 200`
```

## RoleTemplate-Driven Expectations

List RoleTemplates for an extension when labels are available:

```bash
kubectl --context "$CONTEXT" get roletemplates.iam.kubesphere.io \
  -l kubesphere.io/extension-ref=<extension-name> -o yaml
```

If the extension label is absent or inconsistent, filter by API group, resource, category, or name:

```bash
kubectl --context "$CONTEXT" get roletemplates.iam.kubesphere.io -o yaml
```

Important fields:

```yaml
metadata:
  labels:
    iam.kubesphere.io/scope: namespace
    iam.kubesphere.io/aggregate-to-viewer: ""
    iam.kubesphere.io/aggregate-to-operator: ""
    iam.kubesphere.io/aggregate-to-admin: ""
    iam.kubesphere.io/aggregate-to-cluster-viewer: ""
    iam.kubesphere.io/aggregate-to-cluster-admin: ""
    iam.kubesphere.io/aggregate-to-platform-admin: ""
  annotations:
    iam.kubesphere.io/dependencies: ...
spec:
  rules:
  - apiGroups: ["batch.volcano.sh"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch"]
```

Interpretation rules:

- `iam.kubesphere.io/scope` decides whether the permission belongs to platform, cluster, workspace, or namespace/project roles.
- `iam.kubesphere.io/aggregate-to-*` labels indicate which built-in role receives the rules.
- `spec.rules` is the designed permission set for API authorization.
- Dependency annotations may affect UI permission selection and should be reported when explaining why a permission exists, but API authorization is governed by the aggregated rules.

After deriving expectations, inspect the actual roles. Examples:

```bash
kubectl --context "$CONTEXT" get globalroles.iam.kubesphere.io platform-admin -o yaml
kubectl --context "$CONTEXT" get clusterroles.iam.kubesphere.io cluster-viewer -o yaml
kubectl --context "$CONTEXT" get workspaceroles.iam.kubesphere.io <workspace>-viewer -o yaml
kubectl --context "$CONTEXT" -n "$NAMESPACE" get roles.iam.kubesphere.io viewer -o yaml
```

## Authentication

OAuth password grant, common on KubeSphere 3.x/4.x:

```bash
curl -sk -X POST "$SERVER/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&username=$USERNAME&password=$PASSWORD&client_id=kubesphere&client_secret=kubesphere"
```

KubeSphere IAM login fallback:

```bash
curl -sk -X POST "$SERVER/kapis/iam.kubesphere.io/v1alpha2/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}'
```

Use the returned `access_token`, `token`, or token-like field as:

```bash
Authorization: Bearer <token>
```

## User Resource

Discover supported IAM versions first:

```bash
kubectl --context "$CONTEXT" get --raw /apis/iam.kubesphere.io
```

KubeSphere 4.x may prefer `iam.kubesphere.io/v1beta1` while still serving `v1alpha2` users.

Create or inspect users with the IAM API group when available:

```text
/apis/iam.kubesphere.io/v1alpha2/users
/apis/iam.kubesphere.io/v1alpha2/users/{username}
/apis/iam.kubesphere.io/v1beta1/users
/apis/iam.kubesphere.io/v1beta1/users/{username}
```

Typical user object:

```json
{
  "apiVersion": "iam.kubesphere.io/v1alpha2",
  "kind": "User",
  "metadata": {"name": "alice"},
  "spec": {
    "email": "alice@example.com",
    "password": "initial-password"
  }
}
```

Some installations require an annotation or status transition for password reset. If create succeeds but login fails, inspect the created object and KubeSphere docs for that cluster version.

## Role Scopes and Bindings

Platform scope:

- Role resources: `globalroles.iam.kubesphere.io`.
- Binding resources: `/apis/iam.kubesphere.io/v1beta1/globalrolebindings`.
- Use only for platform-wide permissions.

Cluster scope:

- Role resources: `/apis/iam.kubesphere.io/v1beta1/clusterroles`.
- Binding resources: `/apis/iam.kubesphere.io/v1beta1/clusterrolebindings`.
- Subject kind is usually `User` and subject name is the KubeSphere username.

Workspace scope:

- Role resources: `/apis/iam.kubesphere.io/v1beta1/workspaceroles`.
- Binding resources: `/apis/iam.kubesphere.io/v1beta1/workspacerolebindings`.
- Include the workspace name in `metadata.labels["kubesphere.io/workspace"]`.
- Verify existing role names. On some KubeSphere 4.x clusters, built-in workspace roles are workspace-prefixed.

Namespace/project scope:

- Prefer KubeSphere IAM `RoleBinding` at `/apis/iam.kubesphere.io/v1beta1/namespaces/{namespace}/rolebindings`.
- Common role names are `admin`, `operator`, and `viewer`.
- Controllers may create Kubernetes roles named like `kubesphere:iam:admin`, but the primary KubeSphere role binding is the IAM object.

## ks-apiserver Path Patterns

Use the exact API doc when available. Common patterns:

```text
/api/v1/...
/apis/<group>/<version>/...
/apis/<group>/<version>/namespaces/<namespace>/<resource>
/kapis/<group>/<version>/...
/clusters/<cluster>/api/v1/...
/clusters/<cluster>/apis/<group>/<version>/...
/clusters/<cluster>/apis/<group>/<version>/namespaces/<namespace>/<resource>
/clusters/<cluster>/kapis/<group>/<version>/...
```

For namespaced Kubernetes-style extension resources:

```bash
curl -sk "$SERVER/clusters/$CLUSTER/apis/$GROUP/$VERSION/namespaces/$NAMESPACE/$RESOURCE" \
  -H "Authorization: Bearer $USER_TOKEN"
```

For cluster-scoped Kubernetes-style extension resources:

```bash
curl -sk "$SERVER/clusters/$CLUSTER/apis/$GROUP/$VERSION/$RESOURCE" \
  -H "Authorization: Bearer $USER_TOKEN"
```

## Example Role Matrix

Choose roles based on the API scope and RoleTemplate aggregation.

Namespace/project API:

- `admin`: expect all verbs allowed if RoleTemplate aggregates write rules to admin.
- `operator`: expect operational write verbs only when RoleTemplate grants them.
- `viewer`: expect `get/list/watch`; expect write verbs to return `403`.
- same role in another namespace: expect `403`.
- user without a binding: expect `403`.

Cluster API:

- `cluster-admin`: expect administrative verbs allowed when RoleTemplate grants them.
- `cluster-viewer`: expect `get/list/watch`; expect write verbs to return `403`.
- namespace or workspace-only user: usually expect `403`.
- platform admin: often allowed, but still verify actual aggregated rules.

Workspace API:

- workspace admin/viewer/operator expectations must come from the workspace-scope RoleTemplates and actual workspace role names.
- user bound in another workspace should be denied.

Platform API:

- platform admin is the primary administrative role.
- regular or authenticated roles should be tested from RoleTemplate-derived expectations, not assumptions.

## Verification Commands

After user creation:

```bash
curl -sk "$SERVER/apis/iam.kubesphere.io/v1alpha2/users/$USERNAME" \
  -H "Authorization: Bearer $KS_TOKEN"
```

After a role grant, read the matching IAM binding:

```bash
curl -sk "$SERVER/apis/iam.kubesphere.io/v1beta1/globalrolebindings/$BINDING" \
  -H "Authorization: Bearer $KS_TOKEN"

curl -sk "$SERVER/apis/iam.kubesphere.io/v1beta1/clusterrolebindings/$BINDING" \
  -H "Authorization: Bearer $KS_TOKEN"

curl -sk "$SERVER/apis/iam.kubesphere.io/v1beta1/workspacerolebindings/$BINDING" \
  -H "Authorization: Bearer $KS_TOKEN"

curl -sk "$SERVER/apis/iam.kubesphere.io/v1beta1/namespaces/$NAMESPACE/rolebindings/$BINDING" \
  -H "Authorization: Bearer $KS_TOKEN"
```

When direct KubeSphere IAM writes fail, use kubeconfig access only if the user has provided or approved it. Do not assume platform or workspace permissions have equivalent Kubernetes RBAC objects.
