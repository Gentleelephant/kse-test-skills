# KubeSphere Extension Reference

## Source Model

- A KubeSphere extension root is a Helm-style main chart with KubeSphere metadata. It normally contains `extension.yaml`, `permissions.yaml`, `values.yaml`, localized README files, `static/`, and optional subcharts under `charts/`.
- Discover examples from repositories available in the current workspace. Treat
  them as implementation evidence, not immutable templates, and never assume a
  user-specific absolute path.
- `ksbuilder publish` publishes extension metadata and the chart into a
  KubeSphere cluster. Installation is a separate step driven by an `InstallPlan`;
  see `install-lifecycle.md`.
- `ksbuilder push` submits an extension package or directory to KubeSphere Cloud / Marketplace for review.
- `ksbuilder package` creates a distributable `.tgz` package from an extension directory.
- `ksbuilder lint` and `ksbuilder template` are the primary local validation commands before packaging or publishing.

## extension.yaml

`ksbuilder` requires exactly these eight fields (they carry
`validate:"required"` in `pkg/api/metadata.go`):

- `apiVersion`
- `name`
- `version`
- `displayName`
- `description`
- `category`
- `provider`
- `icon`

These fields are optional and have defaults or no validation:

- `staticFileDirectory`: no default. Relative paths resolve from the extension
  root, so setting it to `static` is a convention, not a requirement.
- `kubeVersion`, `ksVersion`: no default and no format check. Omit only when
  any Kubernetes/KubeSphere version is acceptable.
- `installationMode`: defaults to `HostOnly` when omitted. The installplan
  controller does not read this field, so it does not select member clusters;
  use `spec.clusterScheduling` on the InstallPlan for that.
- `screenshots`, `dependencies`, `images`, `externalDependencies`,
  `annotations`, `namespace`: optional.

Use these checks:

- `name` should be lowercase and stable. Prefer lower-case letters, digits, and hyphens. Keep it short enough for generated Kubernetes object names.
- `version` should be semantic version compatible, including prerelease forms such as `1.5.0-rc.1`.
- `displayName`, `description`, and `provider` should include `en`; include `zh` when the extension is user-facing in Chinese environments.
- `category` should be one of `ai-machine-learning`, `database`,
  `observability`, `integration-delivery`, `networking`, `security`, `storage`,
  `streaming-messaging`, `computing`, `dev-tools`, or `uncategorized`.
  `ksbuilder` maps other values to `uncategorized`.
- `staticFileDirectory` should point to a local directory, normally `static`.
- `icon` and `screenshots` may be relative paths, `http://` or `https://`
  URLs, or `data:image/...` URIs. `ksbuilder` only reads and rewrites local
  files; verify relative paths exist before packaging.
- `dependencies` should match local subcharts under `charts/`.
- Use dependency tag `extension` for host/frontend extension components and `agent` for components that are scheduled to member clusters.
- `installationMode: Multicluster` documents that agent components are expected, but scheduling is driven by `InstallPlan.spec.clusterScheduling`. `HostOnly` is the default.
- `externalDependencies` should declare required extension dependencies when the extension needs another extension to be installed first.
- `images` should list images users or release tooling need to mirror or inspect.

## values.yaml And Subcharts

KubeSphere extension root `values.yaml` is the values overlay for the extension main chart. It is not just a copy of one child chart's values.

Use these rules when converting a Helm chart into an extension or reviewing an existing one:

- Keep a root `values.yaml` at the extension root.
- Put shared KubeSphere runtime settings under root `global` when the extension or child templates use them:
  - `global.imageRegistry`: cluster-wide image registry override.
  - `global.imagePullSecrets`: image pull secrets propagated to child workloads and jobs.
  - `global.clusterInfo`: KubeSphere-provided cluster metadata; common fields are `name` and `role`.
  - `global.nodeSelector`: optional default node selector when child templates support it.
- Put child-chart overrides under a top-level key matching the child chart name, for example `vector-logging:` overrides `charts/vector-logging/values.yaml`.
- A dependency `condition` may use a separate alias key such as `kubePrometheusStack.enabled` while the real child override key remains `kube-prometheus-stack:`. If using this pattern, ensure both keys are present and their purpose is clear: alias key controls enablement; child-name key carries child chart values.
- Do not flatten child chart values into root unless the main chart templates are written for that flattened shape. Helm passes parent values into a dependency by dependency name or alias, so `charts/<name>` normally reads `.Values` from `rootValues[<name>]` plus globals.
- When wrapping an upstream chart, preserve the upstream child `values.yaml` as the source of defaults. Root `values.yaml` should override only extension-specific defaults, KubeSphere integration values, and user-facing knobs.
- Keep root values and child values consistent. If root `values.yaml` overrides `foo.bar`, confirm `charts/<child>/values.yaml` or templates actually define and use `foo.bar`.

### Image And Tag Overrides

Image values need special care because these extensions often separate repository/registry overrides from version defaults:

- Prefer root `global.imageRegistry` for registry replacement when child templates support it, using helpers such as `.Values.global.imageRegistry | default .Values.image.registry`.
- Keep child chart default tags in child `values.yaml` or `Chart.yaml appVersion` when possible.
- It is intentional in several local examples for root `values.yaml` to comment out image `tag` fields, for example `# tag: "v0.81.0"`. This means the extension root does not override the child chart default tag.
- Do not uncomment or set a root image tag merely to make the value visible. Set it only when the extension intentionally overrides the child chart's version.
- If a root value uses `tag: ""` or `tag: null`, inspect the child template. It is usually valid only when the template has a fallback such as `.Values.image.tag | default .Chart.AppVersion`.
- Keep `extension.yaml.images` aligned with the effective rendered images, including images whose tags come from child defaults or `Chart.yaml appVersion`.

### Global Parameters In Child Charts

When adapting a normal Helm chart into a KubeSphere extension, update child chart templates or helpers to consume the global values if the extension needs KubeSphere-level overrides:

- Image rendering should use `global.imageRegistry` before the chart's local registry.
- Pods and hook jobs should include `global.imagePullSecrets` when present.
- Agent or member-cluster components should use `global.clusterInfo.name` for cluster labels, log fields, remote write labels, or command arguments.
- Templates that behave differently on host/member clusters may use `global.clusterInfo.role`.
- Workloads and hook jobs may default node placement from `global.nodeSelector`, while still allowing component-level overrides.

Always verify this with `ksbuilder template /path/to/extension --dry-run=client` and inspect the rendered image names, pull secrets, cluster labels, and enabled/disabled child charts.

## permissions.yaml

`permissions.yaml` declares install-time authorization needed by the extension. Keep it minimal:

- Prefer exact API groups and resources over `'*'`.
- Prefer exact verbs over `'*'`; request `get/list/watch` for read-only behavior.
- Use `resourceNames` where only named resources are needed.
- Treat these resources as sensitive and require a clear reason: `secrets`, `clusterrolebindings`, `rolebindings`, `mutatingwebhookconfigurations`, `validatingwebhookconfigurations`, `customresourcedefinitions`, and broad RBAC resources.
- If permissions are broader than the extension's chart resources appear to require, report the risk and ask for the intended reason before narrowing them.

## RoleTemplates For Extension Permissions

`permissions.yaml` grants the install-time executor rights. Runtime users need
`RoleTemplate` objects in the chart so KubeSphere built-in roles aggregate the
extension's API rules.

Place them in the chart, usually `charts/<component>/templates/roletemplate.yaml`,
and guard them so rendering works on clusters without the CRD:

```yaml
{{- if .Capabilities.APIVersions.Has "iam.kubesphere.io/v1beta1/RoleTemplate" }}
apiVersion: iam.kubesphere.io/v1beta1
kind: RoleTemplate
metadata:
  name: cluster-view-myresources
  labels:
    iam.kubesphere.io/scope: cluster
    iam.kubesphere.io/category: cluster-my-extension
    iam.kubesphere.io/aggregate-to-cluster-viewer: ""
    kubesphere.io/managed: "true"
spec:
  displayName:
    en: My Resource Viewing
    zh: 我的资源查看
  rules:
    - apiGroups: ["extensions.kubesphere.io"]
      resources: ["myresources"]
      verbs: ["get", "list", "watch"]
{{- end }}
```

Rules that are easy to get wrong:

- `iam.kubesphere.io/scope` is required and selects the role type:
  `global` -> GlobalRole, `cluster` -> ClusterRole, `workspace` ->
  WorkspaceRole, `namespace` -> Role. A wrong scope never reaches the role you
  meant.
- `iam.kubesphere.io/auto-aggregate: "true"` belongs on the **target role**
  (GlobalRole/WorkspaceRole/ClusterRole/Role), not on the RoleTemplate. Built-in
  roles already carry it; adding it to a RoleTemplate has no effect.
- A template is aggregated only when its labels satisfy the target role's
  `aggregationRoleTemplates.roleSelector.matchLabels`. Scope is the common
  requirement; some roles add more:
  - Roles `admin`, `operator`, `viewer` in a namespace (from BuiltinRoles
    `project-*`): `scope=namespace` plus `kubesphere.io/managed: "true"`;
    `operator` and `viewer` also need `iam.kubesphere.io/aggregate-to-operator`
    or `-viewer`.
  - Workspace roles: `scope=workspace`; `regular`, `viewer`, and
    `self-provisioner` add `aggregate-to-regular` / `-viewer` /
    `-self-provisioner`.
  - `cluster-admin`: `scope=cluster`; `cluster-viewer`:
    `aggregate-to-cluster-viewer`.
  - `platform-admin`: `scope=global`; `authenticated`:
    `aggregate-to-authenticated` plus `scope=global`.

  Read the target role's selector before choosing labels; these selectors belong
  to the KubeSphere release and can change.
- Keep `kubesphere.io/managed: "true"` on namespace-scope templates. Without it
  `admin`, `operator`, and `viewer` do not select the template, so users with
  those roles receive no extension permissions.
- `iam.kubesphere.io/role-template-rules` and
  `iam.kubesphere.io/dependencies` drive Console permission menus and display
  only. The authorization path reads `spec.rules`.
- Aggregation is one-time per template: once the target role lists the template
  in `aggregationRoleTemplates.templateNames`, editing `spec.rules` does not
  update that role. To roll out changed rules, remove the name from
  `templateNames` (or delete and recreate the role) so it aggregates again.
  Newly created roles do pick up existing templates.
- The installer adds `kubesphere.io/extension-ref`; do not set it manually.

Verify the target role, not the template:

```bash
kubectl get roles.iam.kubesphere.io viewer -n <namespace> -o yaml
kubectl get clusterroles.iam.kubesphere.io cluster-viewer -o yaml
kubectl get globalroles.iam.kubesphere.io platform-admin -o yaml
```

Confirm the template name appears in `aggregationRoleTemplates.templateNames`
and the expected rules in `rules`. Hand live access checks to
`$kubesphere-iam-rbac` or `$kubesphere-extension-api-e2e`.

## Local Checks

Run checks in this order for existing source:

```bash
ruby skills/kubesphere-extension-dev/scripts/check_extension.rb --format json /path/to/extension
skills/kubesphere-extension-dev/scripts/lint_extension.sh /path/to/extension
```

`lint_extension.sh` runs `ksbuilder lint --with-subcharts` and
`ksbuilder template`, and fails on any `ERROR:`/`[ERROR]` line even when
ksbuilder exits `0`. ksbuilder's builtin checks (DNS name, global image
registry, global node selector) print errors to stdout without changing the
exit code, so a plain `ksbuilder lint` is not a gate.

When `ksbuilder template` output is large, render to a temporary directory or inspect specific templates with `--show-only`. Do not commit rendered output unless the user asks for generated artifacts.

## Create And Develop

Use `ksbuilder create` for a normal interactive extension skeleton. If starting from an existing chart package, use the current local `ksbuilder create --help` and `ksbuilder createsimple --help` output to choose the right command.

For frontend work:

- Confirm the frontend chart creates a `JSBundle` and any reverse proxy/service resources needed by Console.
- Verify bundle URLs resolve through KubeSphere Console after publish.
- Check that `frontend/extensions/<extension-name>/` exists when the project includes source-level frontend code.
- See `routing-and-status-codes.md` for the exact `/dist` link contract and the 404/503 failure split.

### Frontend Menus And ExtensionEntry

The bundle entry point may export `menus`, or the chart may create an
`ExtensionEntry`. `ExtensionEntry` wins when both describe the same menu; it is
not merged with the bundle's `menus`.

```yaml
{{- if .Capabilities.APIVersions.Has "extensions.kubesphere.io/v1alpha1/ExtensionEntry" }}
apiVersion: extensions.kubesphere.io/v1alpha1
kind: ExtensionEntry
metadata:
  name: {{ include "frontend.fullname" . }}-entries
spec:
  entries:
    - name: my-extension
      parent: global
      title: My Extension
      icon: my-icon
      link: /my-extension
      order: 10
{{- end }}
```

- `ExtensionEntry` is cluster-scoped. Render it from the frontend chart; do not
  apply it with `kubectl`, because only chart resources get the installer's
  `kubesphere.io/extension-ref` label and `status.state` sync.
- `spec.entries` is `[]runtime.RawExtension` with unknown fields preserved. The
  API accepts misspelled keys and Console silently ignores them; use the
  documented menu fields (`parent`, `name`, `link`, `title`, `icon`, `order`,
  `desc`, `authKey`, `authAction`, `skipAuth`). Extra keys seen in third-party
  manifests are not verified as effective.
- `link` and `desc` take effect only with `parent: global`.
- In 4.2, supported top-level parents are `global`, `access`, `cluster`,
  `workspace`, and `platformSettings`, plus nested forms such as
  `cluster.<group>.<item>`. The old `topbar` and `project` parents no longer
  display; migrate them to `global` and `workspace`.
- `order: 0` renders first. Authorization uses `name` as the key unless
  `authKey`/`authAction` select a different permission; `skipAuth` bypasses the
  check.
- Verify the rendered object and its state:

```bash
kubectl get extensionentries.extensions.kubesphere.io \
  -l kubesphere.io/extension-ref=<extension-name> -o yaml
```

For backend work:

- Confirm the backend chart creates an `APIService` when exposing extension APIs through KubeSphere.
- Verify the service URL, API group, and version match the backend service. The
  service form is always HTTPS (`https://<name>.<namespace>.svc:<port><path>`,
  port 443 by default); a plain-HTTP backend must be referenced through `url`.
- Use `$kubesphere-iam-rbac` for RoleTemplate-driven access-control tests.
- See `routing-and-status-codes.md` for filter order and why a correct
  APIService can still return 403 before reaching the backend.

When converting an existing Helm chart:

- Place the original chart under `charts/<dependency-name>` unless `ksbuilder createsimple` produces a different structure intentionally.
- Add the dependency to `extension.yaml.dependencies` with `agent` or `extension` tags.
- Create root `values.yaml` with `global` and one top-level override section per dependency.
- Move only extension-specific overrides into root `values.yaml`; leave default image tags and broad upstream defaults in the child chart.
- Patch child templates to consume KubeSphere globals before expecting marketplace installation to handle registry, pull secrets, or cluster identity correctly.

## Package, Publish, Push

Command behavior differs between ksbuilder builds, and `ksbuilder version`
prints `0.0.0` for many source builds. Probe the binary first:

```bash
skills/kubesphere-extension-dev/scripts/probe_ksbuilder.sh
```

It prints stable `ksbuilder.*=true|false` lines derived from each subcommand's
`--help`, including `ksbuilder.publish.chartmuseum_target`,
`ksbuilder.package.skip_dependency_update`, and
`ksbuilder.package.dependency_update_default`.

| Command | Working tree | Cluster / Cloud |
|---|---|---|
| `package <dir>` | Writes `<name>-<version>.tgz` into the **current directory**, not next to the source; the source itself is copied to a temp dir. master runs `helm dependency update` when dependencies are declared (skip with `--skip-dependency-update`) and requires a version on remote dependencies; v0.4.7 does neither and packages only the subcharts already under `charts/`. Neither build excludes `static/`; only `push` strips it. | none |
| `publish <dir>` | `--dryRun --output <dir>` writes one `<Kind>.yaml` per object into the output directory. A source directory is packaged from a temp copy without running dependency update, so missing subcharts stay missing. | Applies `Extension`, `ExtensionVersion`, and (local source) the chart ConfigMap. Never creates an `InstallPlan` or workloads. |
| `publish oci://...` | none | Sets `spec.chartURL` instead of a chart ConfigMap. |
| `publish --target=chartmuseum --repo=...` | Packages in a temp dir with dependency update, like `package`. | master only. Uploads the `.tgz` only; no KubeSphere objects. Auth/TLS flags: `--username`, `--password`, `--ca-bundle`, `--insecure-skip-tls-verify`. |
| `push <dir\|tgz>` | none; local icon/screenshots and static files are handled in a temp copy. | Uploads assets and submits the package to KubeSphere Cloud for review. It does not run dependency update. |
| `unpublish <name>` | none | Deletes published objects; order differs by version (see below). |

Package locally:

```bash
ksbuilder package /path/to/extension
```

Verify the generated archive name and metadata against `extension.yaml` `name`
and `version`, and confirm the archive contains the expected subcharts:

```bash
tar -tzf <name>-<version>.tgz | grep '^<name>/charts/'
```

Publish to a test cluster:

```bash
ksbuilder publish /path/to/extension-or-package --kubeconfig /path/to/kubeconfig
kubectl --kubeconfig /path/to/kubeconfig get extensions.extensions.kubesphere.io
kubectl --kubeconfig /path/to/kubeconfig get extensionversions.extensions.kubesphere.io
```

Publish applies `Extension <name>`, `ExtensionVersion <name>-<version>`, and,
for local sources, `ConfigMap extension-<name>-<version>-chart` in
`kubesphere-system`. master prints `Next: open the KubeSphere console ...
install it.`; v0.4.7 prints only the created object lines. Neither creates an
`InstallPlan` or any workload. Use `publish --dryRun --output <dir>` to inspect
the applied objects locally.

Install is a separate step. Confirm the console created `InstallPlan <name>`,
or follow [Create An InstallPlan Explicitly](install-lifecycle.md#create-an-installplan-explicitly).
That guarded procedure stops on a failed preflight or existing plan, validates
the manifest with `kubectl create --dry-run=server`, and uses `kubectl create`
only for a new plan.

```bash
kubectl --kubeconfig /path/to/kubeconfig get installplan <name> \
  -o jsonpath='{.status.state}{" "}{.status.targetNamespace}{" "}{.status.releaseName}{"\n"}'
kubectl --kubeconfig /path/to/kubeconfig get installplan <name> \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

Wait for `Installed` or `Upgraded`. `Preparing`, `Installing`, and `Upgrading`
mean the executor Job is still running; inspect
`-n <targetNamespace> jobs -l kubesphere.io/extension-ref=<name>` and its logs
instead of re-applying the plan. `InstallFailed` retries automatically after a
config or version change.

Capture verification evidence:

```bash
kubectl --kubeconfig /path/to/kubeconfig get jsbundles,apiservices,reverseproxies,extensionentries \
  -l kubesphere.io/extension-ref=<name>
kubectl --kubeconfig /path/to/kubeconfig -n <targetNamespace> get deployments,services,configmaps,jobs
kubectl --kubeconfig /path/to/kubeconfig -n <targetNamespace> get events --sort-by=.lastTimestamp
```

Record the exact objects belonging to the extension rather than treating the
presence of unrelated cluster resources as success.

Disable versus uninstall:

```bash
kubectl --kubeconfig /path/to/kubeconfig patch installplan <name> --type merge \
  -p '{"spec":{"enabled":false}}'
```

`spec.enabled: false` only sets JSBundle, APIService, ReverseProxy, and
ExtensionEntry status to `Disabled` and mirrors `status.enabled: false`.
Workloads and the Helm release remain. Delete the InstallPlan to uninstall.

Submit to KubeSphere Cloud / Marketplace:

```bash
ksbuilder login
ksbuilder push /path/to/extension-or-package
ksbuilder list
ksbuilder get <extension-name>
```

`ksbuilder login` stores the token base64-encoded (not encrypted) in
`~/.ksbuilder/config.json`, which is written with mode `0644`. Never cat, copy,
or commit that file; run `ksbuilder logout` to remove it. Use interactive
prompts or environment variables for tokens, and never put them in repository
files or final reports.

The 1 MB extension-size limit is a KubeSphere Cloud documentation constraint,
not a local check: `push` performs no size check and fails only at upload.

Uninstall and unpublish from a test cluster:

```bash
kubectl --kubeconfig /path/to/kubeconfig delete installplan <extension-name>
ksbuilder unpublish <extension-name> --kubeconfig /path/to/kubeconfig
```

Deleting the InstallPlan is the uninstall trigger. The finalizer
`kubesphere.io/installplan-protection` holds the object until Helm removes the
host release `<name>` and every agent release `<name>-agent`. The state moves
through `Uninstalling` to `Uninstalled`; only then does the object disappear.
There is no Helm rollback.

If uninstall is stuck, capture the executor Job, Pod, and events, then annotate
the plan with `kubesphere.io/force-delete=true`. That calls Helm force-delete
and clears the finalizer.

`ksbuilder unpublish` deletes published metadata. The order depends on the
version:

- master: deletes `InstallPlan <name>` first and waits up to one minute for it
  to disappear (2s poll). If Helm uninstall is still running, unpublish fails
  at this step. It then deletes each `ConfigMap
  extension-<name>-<version>-chart`, each `ExtensionVersion <name>-<version>`,
  and finally `Extension <name>`.
- v0.4.7: does not wait. It issues deletes for the chart ConfigMaps and
  ExtensionVersions first, then `InstallPlan <name>`, then `Extension <name>`.
  The plan may still be `Terminating` after unpublish returns; wait for its
  finalizer and report the plan state separately.

Neither version deletes the target namespace or the
`kubesphere:<name>:helm-executor` Role, RoleBinding, ClusterRole, and
ClusterRoleBinding. Helm only impersonates `helm-executor.<name>`; no
ServiceAccount object exists for it. Unpublish also does not directly remove
Helm releases, persistent volumes, or other workloads on member clusters; agent
cleanup depends on the InstallPlan deletion completing on the host.

Verify extension resources and generated ConfigMaps are removed or intentionally retained.
Also recheck JSBundle, APIService, ReverseProxy, ExtensionEntry, workloads,
Services, ConfigMaps, and events. Unexplained residual resources make rollback
`PARTIAL` or `FAIL`, not `PASS`.

## Local Repository Release Convention

This convention is specific to the extension repository this skill was prepared
against; confirm it in the target repository before relying on the triggers. The
local extension collection release flow reads the release version directly from
each target extension's `extension.yaml`.

- PR title trigger: `release <ext>` or `release <ext1>, <ext2>`.
- PR comment trigger: `/release <ext>` or `/release <ext1> <ext2>`.
- Manual tag trigger: `<extension-name>/<version>`, for example `whizard-logging/v1.5.0` or `whizard-auditing/2.0.0`.
- CI does not calculate, bump, or modify versions.
- Release notes are read from `CHANGELOG.md` for auto-release; keep a matching version section.

## Troubleshooting

- Missing extension in marketplace after `publish`: verify `Extension` and
  `ExtensionVersion` resources, the `extension-<name>-<version>-chart`
  ConfigMap, and namespace events. `publish` does not install; a missing
  `InstallPlan` means the install step never ran.
- InstallPlan pending or failed: inspect `status.conditions`,
  `status.stateHistory`, the executor Job in the target namespace, and its
  logs. `InstallFailed` retries after a config or version change.
- InstallPlan will not delete: Helm uninstall is still running or stuck; check
  the executor Job and events before using `kubesphere.io/force-delete`.
- UI missing after install: inspect `JSBundle`, frontend service, reverse
  proxy, Console `/pstatic/dist/<name>/index.js`, and browser network errors.
  Check that its status is `Available`, not `Disabled`. Use
  `routing-and-status-codes.md` to tell an unmatched link (404) from an
  unavailable bundle (503).
- Backend API missing: inspect `APIService`, service DNS, backend Pod
  readiness, API group/version, and ks-apiserver discovery. A 403 means RBAC
  denied before the backend was contacted; 502/503 means the backend or
  endpoint is broken. See `routing-and-status-codes.md`.
- Multi-cluster install missing agent: verify `spec.clusterScheduling` selects
  the cluster, the dependency has the `agent` tag and its `condition` is true,
  and inspect `status.clusterSchedulingStatuses` plus the `<name>-agent`
  release on the member cluster. The controller does not read the
  `kubesphere.io/installation-mode` annotation. See the multi-cluster section
  of `install-lifecycle.md` for selection precedence, host-first gating, and
  per-cluster overrides.
- Permission denied during install: inspect `permissions.yaml`, installer service account permissions, and cluster events.
- Cloud push fails due package size: static icon and screenshots may be uploaded and rewritten by ksbuilder; ensure the final package stays within KubeSphere Cloud limits.
