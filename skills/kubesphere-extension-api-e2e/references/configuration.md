# Configuration

## Location

Keep the only required user configuration at
`<project-root>/kse-e2e.yaml`. Resolve all relative paths from that file.
Generate a private template with:

```bash
python3 <skill-dir>/scripts/run_e2e.py init --project .
```

Then run the dependency, configuration, connectivity, and cluster preflight:

```bash
uv run --with-requirements <skill-dir>/requirements.txt \
  python <skill-dir>/scripts/run_e2e.py doctor --config ./kse-e2e.yaml
```

Initialization creates mode `0600` and adds `/kse-e2e.yaml` to the project
`.gitignore`. The file is plain YAML with `schemaVersion: 1`; it is not a
Kubernetes object.

## Required Fields

- `target.ksAPIServer`: ks-apiserver aggregation endpoint.
- `target.cluster`: target KubeSphere cluster.
- `auth`: administrator Token or username/password.
- `sources.apiDocs[].path`: Swagger 2 or OpenAPI 3 document.
- `sources.extension.path`: extension root or Helm chart containing
  RoleTemplates.
- `roles.include`: built-in roles to test.
- `execution`: resource prefix, readiness waits, and cleanup policy.
- `report`: generated plan and result paths.

`sources.backendCode` and `scenarios` are optional. They are not required for
RoleTemplate permission planning.

## API Mappings

The runner derives group, version, resource, verb, and scope from each API
path and operationId. Supply an override only when the plan reports an
unresolved or inaccurate mapping:

```yaml
apiMappings:
  GetSchedulingPolicy:
    apiGroup: scheduling.volcano.sh
    version: v1beta1
    resource: scheduling-policy
    verb: get
    scope: cluster
```

Keys may be an operationId or exact `METHOD path`. Every mapping must provide
all five fields. Expected permissions come from the builtin role's own rules
(including the `authenticated` and `anonymous` identities) plus the
RoleTemplates each role aggregates. Rows that depend on the caller's subject
through Rego self-service rules are reported as `UNVERIFIED` rather than
guessed.

## Temporary Environment

The topology is fixed so every run has comparable scope checks:

```text
Workspace A
├── Namespace A1: Namespace roles are invited here
└── Namespace A2: same-Workspace denial target

Workspace B
└── Namespace B1: cross-Workspace denial target
```

Workspace Admin and Viewer are invited only to Workspace A. Namespace Admin,
Operator, and Viewer are invited only to Namespace A1. Namespace-only users
are not also invited to the Workspace because that would add unrelated
Workspace permissions.

`execution.roleAggregationWait` controls role readiness.
`execution.membershipWait` controls invitation/binding readiness. Cleanup is
`always` or `never`; default to `always`.

## Authentication And Safety

Authentication priority is:

1. `auth.adminToken`
2. `auth.adminUsername` plus `auth.adminPassword`

Credentials stay in `kse-e2e.yaml`; no credential environment variables are
required. Refuse a Git-tracked or group/world-readable configuration. Redact
credentials from logs, plans, state, reports, and exceptions.

## Validation Categories

- `CONFIGURATION_ERROR`: schema, source path, API mapping, scenario, duration,
  or private-file problem. Fix it locally before retrying.
- `ENVIRONMENT_ERROR`: missing dependency, invalid credentials, TLS, server
  address, or connectivity problem.
- `CLUSTER_ERROR`: APIService, IAM member API, or registered-cluster readiness
  problem. Do not create temporary resources until it is resolved.

`doctor` emits JSON checks with status, detail, and the next action. Missing
ksbuilder or kubectl is a warning during preflight but must be resolved before
the corresponding publish or cluster-verification step.
