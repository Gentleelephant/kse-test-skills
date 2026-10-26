# Optional Request Fixture Schema

Use this file only when a write API needs a valid body for runtime permission
enforcement. Fixtures do not define expected authorization; RoleTemplates do.

## Contents

1. Operation overrides
2. Additional scenarios
3. Steps, variables, and cleanup
4. Assertions and evidence
5. Missing configuration

## Operation Overrides

Overrides attach valid data to a documented operation:

```yaml
scenarios:
  variables:
    widgetName: sample-widget
  overrides:
    - operationId: createWidget
      sar:
        group: example.io
        version: v1
        resource: widgets
        verb: create
        scope: workspace
      body:
        metadata:
          name: ${widgetName}-${role}
      expectedSuccess: [200, 201]
      cleanupPath: /kapis/example.io/v1/workspaces/{workspace}/widgets/${widgetName}-${role}
      assertions:
        - path: metadata.name
          equals: ${widgetName}-${role}
```

Select by `operationId`, or by exact `method` and `path`. `requestPath` may
correct only the runtime URL. `sar` also accepts `nonResourcePath` (for
`nonResourceURLs` rules) and `resourceName` (for `resourceNames` rules); both
are required for those rules to match, because the generic group/resource/verb
triple cannot express them.

## Additional Scenarios

Use a generic scenario for dependencies and business behavior:

```yaml
- id: reject-cross-workspace-parent
  actor: workspace-admin
  dependsOn: [create-parent-in-other-workspace]
  setup:
    - actor: admin
      method: POST
      path: /kapis/example.io/v1/workspaces/{otherWorkspace}/widgets
      body:
        metadata:
          name: ${otherParent}
      expectedStatuses: [200, 201]
      cleanup:
        path: /kapis/example.io/v1/workspaces/{otherWorkspace}/widgets/${otherParent}
  request:
    method: POST
    path: /kapis/example.io/v1/workspaces/{workspace}/widgets
    body:
      metadata:
        name: ${runID}-child
      spec:
        parent: ${otherParent}
  authorization:
    group: example.io
    version: v1
    resource: widgets
    verb: create
    scope: workspace
  expect:
    authorization: ALLOW
    statuses: [400, 403, 409, 422]
    errorContains: [parent]
  evidence:
    - type: source
      reference: ./pkg/widgets/validation.go:84
      certainty: confirmed
```

`authorization: ALLOW` means IAM permits the request even when business
validation is expected to reject it. Use `DENY` only for authorization denial;
`statuses` never relaxes it, so a `DENY` row must still return 403.

## Steps, Variables, And Cleanup

Available generated variables include `runID`, `cluster`, `workspace`,
`otherWorkspace`, `namespace`, `siblingNamespace`, `otherNamespace`, `role`,
`username`, and `resources`.

`capture` maps variable names to dotted JSON response paths:

```yaml
capture:
  resourceVersion: metadata.resourceVersion
```

Later steps and scenarios may use `${resourceVersion}`. `dependsOn` is
topologically ordered; a failed dependency blocks its consumers.

Cleanup accepts a path, object, or list. Objects support `method`, `path`, and
optional `body`; default method is DELETE. Cleanup runs in reverse
registration order with the administrator client.

## Assertions And Evidence

Assertions support:

- `equals`
- `exists: true`
- `absent: true`
- `contains`
- `omits`

Evidence entries use `type`, `reference`, and `certainty`. References should
point to an API document operation, RoleTemplate name, or backend file and
line. Evidence is copied into the generated plan and final result.

## Missing Configuration

Represent an unresolved scenario explicitly:

```yaml
- id: create-provider-backed-resource
  status: NEEDS_CONFIGURATION
  reason: set scenarios.variables.providerID
  evidence:
    - type: source
      reference: ./pkg/providers/create.go:51
      certainty: confirmed
```

Do not supply a guessed body merely to make a write test executable.
