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
  Run `scripts/probe_ksbuilder.sh` when package/publish behavior matters; the
  binary may print `0.0.0` for its version and flags differ between builds.
- `kubectl` plus an explicitly selected test kubeconfig for cluster work.
- Read `references/kubesphere-extension.md` before structural or release
  changes, and `references/install-lifecycle.md` before judging install,
  upgrade, enable/disable, multi-cluster, or uninstall behavior.
- Read `references/routing-and-status-codes.md` before diagnosing a missing or
  unreachable JSBundle, APIService, ReverseProxy, or Console route.

Never persist kubeconfigs, Cloud tokens, registry credentials, or bearer
tokens in the skill or its reports.

## Quick Start

```bash
SKILL_DIR=/path/to/kubesphere-extension-dev
EXTENSION_DIR=/path/to/extension

ruby "$SKILL_DIR/scripts/check_extension.rb" --format json "$EXTENSION_DIR"
"$SKILL_DIR/scripts/lint_extension.sh" "$EXTENSION_DIR"
```

The checker exits `0` when no failures are found, `1` for extension findings,
and `2` for invalid CLI input or runtime setup.

`lint_extension.sh` runs `ksbuilder lint --with-subcharts` and
`ksbuilder template`, and exits `1` when either fails or when the output
contains `ERROR:`/`[ERROR]` lines. This gate is required because ksbuilder's
builtin checks (name, global image registry, global node selector) print
`ERROR:` to stdout but still exit `0`. Use `--no-template` to skip rendering.

## Workflow

Follow this order:

1. **Preflight**: resolve the extension root and check Ruby, ksbuilder,
   kubectl, kubeconfig, and target context.
2. **Lint**: run the bundled checker and `scripts/lint_extension.sh`; treat
   any `ERROR:`/`[ERROR]` line as a failure even when ksbuilder exits `0`.
3. **Template**: inspect images, pull secrets, cluster metadata, dependency
   conditions, RBAC, JSBundle, and APIService resources in the rendered output.
4. **Package**: run `ksbuilder package`; compare archive name and version with
   `extension.yaml`.
5. **Publish**: run `ksbuilder publish ... --kubeconfig ...` only against the
   confirmed test cluster. This publishes `Extension`, `ExtensionVersion`, and
   the chart ConfigMap; it does not install anything.
6. **Install**: confirm the published `Extension` and `ExtensionVersion` exist.
   If no `InstallPlan <extension-name>` exists, save the minimal manifest from
   `references/install-lifecycle.md`, validate it with
   `kubectl create --dry-run=server -f ...`, then create it with
   `kubectl create -f ...`.
   `create` refuses to overwrite an existing plan. Wait for `status.state`
   `Installed`/`Upgraded`; a `Preparing`, `Installing`, or `Upgrading` plan is
   still pending.
7. **Verify**: inspect InstallPlan conditions, target namespace, release name,
   JSBundle, APIService, Deployment, Service, ConfigMap, Pod readiness, and
   relevant events.
8. **Uninstall**: delete the InstallPlan (or run `ksbuilder unpublish`), wait
   for the finalizer to clear, then verify the host and agent releases,
   workloads, services, and generated resources are gone or explicitly
   documented as retained.

`publish` publishes metadata and the chart. `push` submits to KubeSphere Cloud /
Marketplace. `unpublish` deletes the InstallPlan and published metadata. Never
substitute one for another, and never report install success from `publish`
output alone.

## Success Criteria

- Static checker, lint, and template complete without failures; no `ERROR:`
  lines are ignored because ksbuilder exited `0`.
- Package metadata matches `extension.yaml`.
- InstallPlan exists and reaches `Installed`/`Upgraded` with `Ready` evidence;
  advertised APIs/UI assets are reachable from that installation.
- `spec.enabled: false` is reported as disabled but still installed, not as
  uninstalled.
- Uninstall removes the releases and chart resources; the retained namespace
  and helm-executor RBAC (Role, RoleBinding, ClusterRole, ClusterRoleBinding)
  are listed as intentional residues.
- Claims use `VERIFIED`, `INFERRED`, or `UNVERIFIED`; only captured cluster
  evidence may be marked `VERIFIED`.

## Common Failures

- Missing extension: locate the nearest parent containing `extension.yaml`.
- Lint clean but broken release: rerun `scripts/lint_extension.sh`; ksbuilder
  builtin checks print `ERROR:` without a non-zero exit code.
- Template failure: rerun with the same values and inspect the named template.
- `publish` succeeded but nothing is installed: no InstallPlan exists; publish
  only applies metadata and the chart.
- InstallPlan stuck in `Preparing`/`Installing`: inspect the executor Job and
  namespace events; do not re-apply the plan.
- InstallPlan will not delete: the `kubesphere.io/installplan-protection`
  finalizer is waiting for Helm uninstall; capture the Job and events before
  considering `kubesphere.io/force-delete`.
- UI missing: inspect JSBundle, frontend Service, Console bundle URL, and events;
  use `references/routing-and-status-codes.md` to separate a 404 (no matching
  `status.link`) from a 503 (state not `Available`). If the bundle loads but the
  menu is absent, inspect `ExtensionEntry` and the bundle's `menus`; an
  `ExtensionEntry` overrides `menus`, and unknown entry keys are silently
  ignored.
- API missing: inspect APIService, backend Service/Pod, discovery, and
  group/version; use `references/routing-and-status-codes.md` for the
  403/404/502/503 split, which usually decides whether RBAC, routing, or the
  backend is at fault.
- Agent missing: check `spec.clusterScheduling`, dependency tags/conditions,
  selected clusters, and `global.clusterInfo` (the controller does not read the
  `installation-mode` annotation); see the multi-cluster section of
  `references/install-lifecycle.md`.
- Permission failure: inspect `permissions.yaml`, installer service account,
  RoleTemplates, and events.
- Built-in role lacks extension permissions: compare the RoleTemplate's `scope`
  and `aggregate-to-*`/`kubesphere.io/managed` labels with the target role's
  `aggregationRoleTemplates.roleSelector`, then re-read the role; editing an
  already-aggregated template does not propagate.

For every failure report: what failed, the impact, the next diagnostic command,
and the safe rollback or cleanup command.

## Safety And Cleanup

- Confirm kubeconfig context and extension name before publish, install, or
  unpublish.
- Prefer local lint/template/package before any cluster mutation.
- Treat secrets, binding resources, webhooks, CRDs, and wildcard RBAC as sensitive.
- Use `kubesphere.io/force-delete` only after capturing the stuck executor Job,
  Pod, and events; it skips normal Helm uninstall.
- Do not claim rollback success until absence or intentional retention is
  verified. There is no Helm rollback: a failed upgrade is recovered by fixing
  the plan and upgrading again, or by uninstalling and reinstalling.

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
- `scripts/lint_extension.sh`: lint/template gate that fails on ERROR lines
  even when ksbuilder exits `0`.
- `scripts/probe_ksbuilder.sh`: read-only capability probe; reports version and
  whether this binary supports chartmuseum publish, package dependency update,
  and template dependency update as stable `ksbuilder.*=true|false` lines.
- `references/kubesphere-extension.md`: structure, validation, release, publish,
  verification, rollback, and troubleshooting details.
- `references/install-lifecycle.md`: InstallPlan contract, state machine,
  naming, enable/disable semantics, multi-cluster agents, uninstall, residuals,
  and verification.
- `references/routing-and-status-codes.md`: ks-apiserver entry points, filter
  order, JSBundle/APIService/ReverseProxy contracts, 401/403/404/500/502/503/511
  causes, and the diagnosis sequence.
