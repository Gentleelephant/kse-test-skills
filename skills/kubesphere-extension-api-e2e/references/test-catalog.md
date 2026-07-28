# Permission Test Catalog

## Required Baseline

Every complete run covers:

- configuration safety and administrator authentication;
- API document parsing and explicit unresolved-mapping reporting;
- rendered versus live RoleTemplate drift;
- built-in role aggregation;
- KubeSphere v1beta1 member API availability;
- all registered KubeSphere clusters are `Ready=True`; otherwise stop before
  resource creation because Workspace cleanup can be blocked by a member
  cluster finalizer;
- verified cluster, Workspace, and Namespace member bindings;
- cluster-admin and cluster-viewer;
- workspace-admin and workspace-viewer;
- namespace-admin, namespace-operator, and namespace-viewer;
- authenticated/no-permission and no-token controls;
- own scope and cross-scope behavior;
- 401 unauthenticated versus 403 authenticated denial;
- cleanup recovery by run ID.

## Scope Matrix

- The runner creates three real Kubernetes `core/v1 Namespace` resources
  through `/api/v1/namespaces`, reads them back, and verifies their
  `kubesphere.io/workspace` ownership labels before permission testing.
- Workspace API: own Workspace and other Workspace.
- Namespace API: invited Namespace, sibling Namespace in the same Workspace,
  and Namespace in another Workspace.
- Cluster API: cluster target.

Workspace roles are not invited to Workspace B. Namespace roles are not
invited to Namespace A2 or Namespace B1. Namespace-only users receive no
Workspace role.

## Result Rules

- Matching RoleTemplate rule plus accepted API status: PASS.
- Missing matching RoleTemplate rule plus 403: PASS.
- No Token plus 401: PASS.
- Logged-in denied user plus 403: PASS.
- Expected deny plus 2xx: high-risk authorization bypass.
- Missing request data for a write: `NOT_TESTED`.
- Role, invitation, binding, or synchronization failure:
  `SETUP_BLOCKED`, not an extension API result.

Business CRUD, admission rejection, field trimming, and plugin-specific
dependencies are optional extensions and are not required to claim complete
permission coverage.

## Verification Evidence

- `VERIFIED`: a live run records target versions, Git commit, sanitized
  configuration, plan, expected/actual matrix, resource snapshots, and
  successful cleanup.
- `INFERRED`: source, rendered templates, or offline tests support the claim,
  but the target cluster has not demonstrated it.
- `UNVERIFIED`: no current evidence exists.

Use `validation/README.md` for the durable evidence summary. Keep raw,
secret-bearing output only in access-controlled CI artifacts or the configured
local report directory.
