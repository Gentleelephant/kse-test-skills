---
name: kubesphere-extension-dev
description: Develop, inspect, test, package, publish, submit, unpublish, and troubleshoot KubeSphere extensions. Use when Codex works with KubeSphere extension directories containing extension.yaml, permissions.yaml, Helm charts, frontend/backend subcharts, JSBundle/APIService resources, ksbuilder create/package/lint/template/publish/push/unpublish workflows, extension release checks, or install issues in a KubeSphere cluster.
---

# KubeSphere Extension Dev

## Use This Skill

Use it for extension structure, static validation, Helm rendering, packaging,
test-cluster publishing, Marketplace submission, rollback, and installation
diagnosis.

Do not use it for a complete multi-role permission matrix; use
`$kubesphere-extension-api-e2e`. Use `$kubesphere-iam-rbac` for one login,
binding, RoleTemplate inspection, or 401/403 diagnosis.

## Prerequisites

- An extension root containing `extension.yaml`.
- Ruby for the bundled checker.
- `ksbuilder` for lint, rendering, package, publish, push, and unpublish.
- `kubectl` plus an explicitly selected test kubeconfig for cluster work.
- Read `references/kubesphere-extension.md` before structural or release changes.

Never persist kubeconfigs, Cloud tokens, registry credentials, or bearer
tokens in the skill or its reports.

## Quick Start

```bash
SKILL_DIR=/path/to/kubesphere-extension-dev
EXTENSION_DIR=/path/to/extension

ruby "$SKILL_DIR/scripts/check_extension.rb" --format json "$EXTENSION_DIR"
ksbuilder lint "$EXTENSION_DIR" --with-subcharts
ksbuilder template "$EXTENSION_DIR" --dry-run=client
```

The checker exits `0` when no failures are found, `1` for extension findings,
and `2` for invalid CLI input or runtime setup.

## Workflow

Follow this order:

1. **Preflight**: resolve the extension root and check Ruby, ksbuilder,
   kubectl, kubeconfig, and target context.
2. **Lint**: run the bundled checker and `ksbuilder lint --with-subcharts`.
3. **Template**: run `ksbuilder template --dry-run=client`; inspect images,
   pull secrets, cluster metadata, dependency conditions, RBAC, JSBundle, and
   APIService resources.
4. **Package**: run `ksbuilder package`; compare archive name and version with
   `extension.yaml`.
5. **Publish**: run `ksbuilder publish ... --kubeconfig ...` only against the
   confirmed test cluster.
6. **Verify**: inspect Extension, ExtensionVersion, JSBundle, APIService,
   Deployment, Service, ConfigMap, Pod readiness, and relevant events.
7. **Unpublish**: run `ksbuilder unpublish`; verify extension objects,
   workloads, services, ConfigMaps, and generated resources are gone or
   explicitly documented as retained.

`publish` installs into a test cluster. `push` submits to KubeSphere Cloud /
Marketplace. Never substitute one for the other.

## Success Criteria

- Static checker, lint, and template complete without failures.
- Package metadata matches `extension.yaml`.
- Published resources become ready and advertised APIs/UI assets are reachable.
- Unpublish leaves no unexplained extension resources.
- Claims use `VERIFIED`, `INFERRED`, or `UNVERIFIED`; only captured cluster
  evidence may be marked `VERIFIED`.

## Common Failures

- Missing extension: locate the nearest parent containing `extension.yaml`.
- Template failure: rerun with the same values and inspect the named template.
- UI missing: inspect JSBundle, frontend Service, Console bundle URL, and events.
- API missing: inspect APIService, backend Service/Pod, discovery, and group/version.
- Agent missing: check `installationMode`, dependency tags, selected clusters,
  and `global.clusterInfo`.
- Permission failure: inspect `permissions.yaml`, installer service account,
  RoleTemplates, and events.

For every failure report: what failed, the impact, the next diagnostic command,
and the safe rollback or cleanup command.

## Safety And Cleanup

- Confirm kubeconfig context and extension name before publish or unpublish.
- Prefer local lint/template/package before any cluster mutation.
- Treat secrets, binding resources, webhooks, CRDs, and wildcard RBAC as sensitive.
- Do not claim rollback success until absence or intentional retention is verified.

## Result Report

Report:

1. `Conclusion`: `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`.
2. `Evidence Status`: `VERIFIED`, `INFERRED`, or `UNVERIFIED`.
3. `Scope`: name/version, source, target, and time.
4. `Checks Run`: checker, ksbuilder, kubectl, and inspected files.
5. `Findings`: failures first, with evidence and fixes.
6. `Package/Publish/Cleanup`: artifacts, resources, readiness, and residuals.
7. `Reproduce`: sanitized commands only.

Use the repository `validation/README.md` template for durable cluster evidence.

## Resources

- `scripts/check_extension.rb`: text/JSON static checker.
- `references/kubesphere-extension.md`: structure, validation, release, publish,
  verification, rollback, and troubleshooting details.
