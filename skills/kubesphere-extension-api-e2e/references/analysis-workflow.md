# Permission Analysis Workflow

## Evidence Order

Use this order:

1. API document for method, path, operationId, and request shape.
2. `apiMappings` only when document inference is incomplete.
3. Rendered RoleTemplates for expected scope, aggregation, and rules.
4. Live RoleTemplates and aggregated built-in roles for deployed IAM state.
5. Verified KubeSphere member bindings for the temporary user.
6. API response for runtime enforcement.

Do not create an independent expected-permission matrix. If a RoleTemplate is
wrong, the test follows that configuration because it is the user's declared
expectation.

## Operation Mapping

For every API operation:

- extract group and version from `/kapis/{group}/{version}` or
  `/apis/{group}/{version}`;
- derive cluster, Workspace, or Namespace scope from the path;
- derive resource from the collection path, using the API tag only for generic
  resource placeholders;
- map POST/PUT/PATCH/DELETE to create/update/patch/delete;
- distinguish GET from LIST using collection/item paths and operationId;
- require `apiMappings` when any attribute remains unresolved.

Record whether a mapping came from the API document or configuration.

## Role And Scope Expansion

Match operations against applicable RoleTemplate rules and aggregation labels.
Generate:

- one cluster target for cluster APIs;
- own and other Workspace targets for Workspace APIs;
- own Namespace, sibling Namespace, and other Workspace Namespace targets for
  Namespace APIs.

Cluster roles apply across targets. Workspace roles apply to their Workspace
and its Namespaces. Namespace roles apply only to their invited Namespace.
Authenticated RoleTemplates apply to logged-in users; no-token never inherits
them.

## Membership Setup

Use KubeSphere v1beta1 member APIs, not direct WorkspaceRoleBinding or
RoleBinding creation:

- `/kapis/iam.kubesphere.io/v1beta1/clustermembers`
- `/kapis/iam.kubesphere.io/v1beta1/workspaces/{workspace}/workspacemembers`
- `/kapis/iam.kubesphere.io/v1beta1/namespaces/{namespace}/namespacemembers`

After invitation, read the resulting binding and verify username, roleRef,
scope labels, Namespace, and User subject. Block API testing for that run if
the binding does not become ready before `membershipWait`.

## Result Classification

- no-token 401: PASS;
- authenticated expected DENY 403: PASS;
- expected DENY 2xx: authorization bypass;
- expected ALLOW 401: authentication or Token failure;
- expected ALLOW 403: RoleTemplate aggregation, invitation, binding, or API
  authorization mismatch;
- invitation or binding readiness failure: `SETUP_BLOCKED`;
- write without a valid body: `NOT_TESTED`.
