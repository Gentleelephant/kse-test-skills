# KubeSphere Extension API E2E Reference

## Contents

1. Configuration
2. API document normalization
3. RoleTemplate permission derivation
4. Fixtures
5. Results and diagnostics

## Configuration

`kse-e2e.yaml` is local, secret-bearing test configuration. The runner resolves
relative paths from the configuration file directory.

Required values:

- `spec.target.ksAPIServer`
- `spec.target.cluster`
- one `spec.sources.apiDocs[].path`
- `spec.sources.extension.path`
- either `spec.auth.adminToken`, or `adminUsername` and `adminPassword`

Authentication modes:

- `auto`: prefer `adminToken`; otherwise use password login.
- `token`: require `adminToken`.
- `password`: require `adminUsername` and `adminPassword`.

`renderer: auto` selects `ksbuilder template` when `extension.yaml` exists and
`helm template` when `Chart.yaml` exists. KubeSphere extension rendering takes
precedence when both files exist. Every configured values file is passed with
`--values`. `kubeVersion` is optional; when omitted for an extension, the
runner derives the minimum numeric version from `extension.yaml.kubeVersion`
and passes it to the renderer.

Some older `ksbuilder` releases ignore `--kube-version` and always render as
Kubernetes 1.20. When that exact compatibility failure occurs, the runner
renders the extension's declared child charts with Helm, propagating root
`global` and child-specific values, and records `helm-fallback` as the
renderer. Other ksbuilder errors remain hard failures.

## API Document Normalization

The deterministic importer accepts Swagger 2 and OpenAPI 3 JSON or YAML. For
Markdown or source-code documentation, use the agent to convert operations
into a fixture file.

For each OpenAPI operation, the runner records:

- operation ID, summary, method, and path
- API group and version from `/kapis/<group>/<version>` or
  `/apis/<group>/<version>`
- resource from the first operation tag, or the path after the scope marker
- Kubernetes verb (`list/get/create/update/patch/delete`)
- scope from `workspaces/{workspace}`, `namespaces/{namespace}`, or cluster

Path variables `cluster`, `workspace`, `namespace`, and a generic `resources`
parameter are filled automatically. Item names and other business parameters
must come from fixtures.

## RoleTemplate Permission Derivation

Rendered `RoleTemplate` resources are the design authority. The runner matches
an operation when `apiGroups`, `resources`, and `verbs` all match, including
wildcards.

Role aggregation:

- `aggregate-to-authenticated`: every authenticated test user
- `aggregate-to-cluster-admin` and `aggregate-to-cluster-viewer`: matching
  cluster roles
- `aggregate-to-viewer`: viewer at the RoleTemplate scope
- `aggregate-to-operator`: namespace operator
- admin at cluster, workspace, or namespace scope receives all templates at
  that scope, matching KubeSphere built-in admin aggregation behavior

The runner separately compares rendered RoleTemplate objects with live
RoleTemplates and checks built-in roles' `aggregationRoleTemplates`.

## Fixtures

Fixtures are optional YAML:

```yaml
variables:
  name: sample

operations:
  - operationId: CreateWidget
    # Optional corrections when the API document cannot express the SAR.
    sar:
      group: example.io
      version: v1
      resource: widgets
      verb: create
      scope: workspace
    body:
      apiVersion: example.io/v1
      kind: Widget
      metadata:
        name: ${name}-${role}
    expectedSuccess: [200, 201]
    cleanupPath: /kapis/example.io/v1/workspaces/{workspace}/widgets/${name}
    assertions:
      - path: metadata.name
        equals: sample
```

An operation may be selected by `operationId`, or by both `method` and `path`.
Fixture variables are merged with generated `cluster`, `workspace`,
`namespace`, `otherWorkspace`, `otherNamespace`, `runID`, `role`, and
`username` variables. `requestPath` can replace only the runtime path while
preserving the documented operation identity.

Only fixture-backed writes are executed. An item read containing an unresolved
path variable is `NOT_TESTED`.

## Results And Diagnostics

Canonical JSON contains:

- source and live RoleTemplate evidence
- aggregation drift
- normalized operations and expected permission matrix
- role, method, path, SAR identity, expected status, actual status, duration,
  request ID, sanitized response summary, functional result, diagnostic result,
  likely failure layer, and suggestion
- setup and cleanup errors

Status classification:

- expected allow plus 2xx: functional pass
- expected deny plus 403: functional pass
- no-token plus 401, or anonymous 403: functional pass
- expected deny plus 2xx: high-risk authorization bypass
- 401 for an authenticated user: login or token problem
- 403 mismatch: RoleTemplate, aggregation, binding, or SAR mismatch
- 404: route, APIService, deployment, or fixture problem
- 400: fixture, validation, or admission problem
- 5xx/network error: ks-apiserver, APIService, or upstream availability

Diagnostic checks:

- anonymous 403 should identify the anonymous user, action, and resource
- other 403 responses should identify user, action, resource, and scope
- business errors should identify a field or object and a concrete reason

Functional correctness and diagnostic quality are independent. Either failure
makes the run non-zero.
