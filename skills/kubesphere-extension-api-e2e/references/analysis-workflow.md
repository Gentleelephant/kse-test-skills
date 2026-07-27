# Backend Analysis Workflow

## Contents

1. Evidence order
2. Route and authorization analysis
3. Business behavior analysis
4. Scenario production
5. Confidence rules

## Evidence Order

Analyze only paths declared in `spec.sources`. Use this order:

1. API documents: operations, schemas, examples, documented statuses.
2. Rendered RoleTemplates: scope, rules, aggregation labels, resource names.
3. Route registration: actual path variables and handler mapping.
4. Authorization code: SAR group, version, resource, verb, workspace,
   namespace, and name.
5. Handler/service/model/admission code: valid bodies, dependencies,
   validation, filtering, mutation, and deletion protection.
6. Error construction: status and diagnostic fields.
7. Live objects: deployed RoleTemplates, aggregation, APIService, and runtime
   behavior.

Do not infer a business rule from a symbol name alone. Trace the request from
route to the enforcing branch.

## Route And Authorization Analysis

For each OpenAPI operation:

- Confirm the registered path and HTTP method.
- Identify collection versus item behavior.
- Record the exact SAR attributes.
- Match the SAR against source RoleTemplates.
- Expand expectations across configured roles and scopes.
- Add own-tenant, other-tenant, cluster, and unauthenticated variants where
  the route supports them.

Correct inaccurate OpenAPI inference with an operation override rather than
changing RoleTemplate-derived policy.

## Business Behavior Analysis

Look for:

- required fields, defaults, enums, and mode-dependent request shapes;
- references to parent, owner, binding, policy, or target resources;
- create-before-use and delete-protection checks;
- workspace/namespace label enforcement;
- list selectors and post-query filtering;
- response DTOs or field removal;
- status-specific error constructors and message text.

Use existing unit tests and dedicated E2E code only as evidence and coverage
comparison. Never invoke them from this skill.

## Scenario Production

Place valid documented-operation bodies in `scenarios.overrides`. Place
multi-step or business-specific behavior in `scenarios.additional`. Every
additional scenario should contain:

- a stable ID and actor;
- dependencies and setup;
- one request and explicit expectation;
- assertions/captures/cleanup as needed;
- evidence with source path and line;
- `certainty: confirmed` when an enforcing branch was traced, otherwise
  `certainty: inferred`.

If a runtime value cannot be derived, create a `NEEDS_CONFIGURATION` scenario
whose reason names the exact missing `spec.scenarios.variables` key.

## Confidence Rules

- `confirmed`: explicit schema, RoleTemplate rule, or enforcing source branch.
- `inferred`: consistent evidence exists but the enforcing branch is indirect.
- `runtime`: expectation depends on a discovered cluster mode or policy.

Never turn an inference into a confirmed rule because the live response
happened to match it. Reports must retain the evidence and certainty.
