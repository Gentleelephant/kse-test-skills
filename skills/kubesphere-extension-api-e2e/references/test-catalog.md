# Test Catalog

## Contents

1. Universal baseline
2. Source-derived behavior
3. Result rules
4. Volcano acceptance map

## Universal Baseline

Every complete run covers:

- configuration safety, authentication, APIService routing, and availability;
- source versus live RoleTemplate drift;
- built-in role aggregation and real role bindings;
- authenticated, cluster, workspace, namespace, no-permission, and no-token;
- own-scope allow, read-only write denial, and cross-scope denial;
- 401 unauthenticated versus 403 authenticated-but-forbidden;
- valid documented CRUD where a schema example or override exists;
- request ID, response structure, diagnostic quality, and cleanup recovery.

## Source-Derived Behavior

Add scenarios when source evidence establishes:

- mode-dependent request bodies;
- parent/binding/owner dependencies;
- create/read/update/patch/delete state transitions;
- delete protection for referenced resources;
- workspace and namespace data isolation;
- list filtering and response field trimming;
- admission or business rejection with a field/object-specific error.

A test category is uncovered when its required valid data is missing. Return
`NEEDS_CONFIGURATION` or `NOT_TESTED`, never PASS.

## Result Rules

- Expected allow plus accepted status and assertions: functional PASS.
- Expected deny plus 403: functional PASS.
- No token plus 401, or anonymous 403: functional PASS.
- Expected deny plus 2xx: high-risk authorization bypass.
- 401 for a logged-in user: authentication/token layer.
- 403 mismatch: RoleTemplate, aggregation, binding, or SAR layer.
- 404: documentation, route, APIService, deployment, or setup layer.
- 400/409/422: fixture, admission, or business validation layer.
- 5xx/network failure: ks-apiserver, APIService, or upstream layer.

Functional and diagnostic verdicts are independent. An unclear error fails
diagnostic quality even when authorization behavior is correct.

## Volcano Acceptance Map

The generic plan is equivalent to the Volcano reference only when it covers:

- scheduling-policy-dependent valid Queue shapes;
- all configured cluster/workspace/namespace roles;
- Resource Pool, Job Queue, QueueBinding, and VolcanoJob dependencies;
- namespace visibility limited to authorized queues;
- namespace responses omitting management fields;
- unauthorized Queue use by VolcanoJob;
- Resource Pool rejection by QueueBinding;
- cross-workspace label and parent injection;
- deletion of Queue referenced by a binding or child;
- CRUD state verification and tenant A/B isolation;
- separate authorization and error-quality verdicts.

Run the generic skill and Volcano reference independently to compare coverage
and conclusions. The generic skill must never call the reference program.
