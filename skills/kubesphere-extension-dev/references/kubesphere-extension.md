# KubeSphere Extension Reference

## Source Model

- A KubeSphere extension root is a Helm-style main chart with KubeSphere metadata. It normally contains `extension.yaml`, `permissions.yaml`, `values.yaml`, localized README files, `static/`, and optional subcharts under `charts/`.
- Local examples are available at `/Users/zhangpeng/GolandProjects/github.com/Gentleelephant/extensions`. Treat those as implementation examples, not as immutable templates.
- `ksbuilder publish` publishes an extension into a KubeSphere cluster for testing.
- `ksbuilder push` submits an extension package or directory to KubeSphere Cloud / Marketplace for review.
- `ksbuilder package` creates a distributable `.tgz` package from an extension directory.
- `ksbuilder lint` and `ksbuilder template` are the primary local validation commands before packaging or publishing.

## extension.yaml

Require these fields for normal extension packages:

- `apiVersion`
- `name`
- `version`
- `displayName`
- `description`
- `category`
- `provider`
- `staticFileDirectory`
- `icon`
- `kubeVersion`
- `ksVersion`
- `installationMode`

Use these checks:

- `name` should be lowercase and stable. Prefer lower-case letters, digits, and hyphens. Keep it short enough for generated Kubernetes object names.
- `version` should be semantic version compatible, including prerelease forms such as `1.5.0-rc.1`.
- `displayName`, `description`, and `provider` should include `en`; include `zh` when the extension is user-facing in Chinese environments.
- `staticFileDirectory` should point to a local directory, normally `static`.
- `icon` and `screenshots` may be relative paths under `staticFileDirectory`; verify the files exist before packaging.
- `dependencies` should match local subcharts under `charts/`.
- Use dependency tag `extension` for host/frontend extension components and `agent` for components that are scheduled to member clusters.
- `installationMode: Multicluster` should be used when agent components must be scheduled to selected clusters. `HostOnly` is for host-only extensions.
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

## Local Checks

Run checks in this order for existing source:

```bash
ruby skills/kubesphere-extension-dev/scripts/check_extension.rb /path/to/extension
ksbuilder lint /path/to/extension --with-subcharts
ksbuilder template /path/to/extension --dry-run=client
```

When `ksbuilder template` output is large, render to a temporary directory or inspect specific templates with `--show-only`. Do not commit rendered output unless the user asks for generated artifacts.

## Create And Develop

Use `ksbuilder create` for a normal interactive extension skeleton. If starting from an existing chart package, use the current local `ksbuilder create --help` and `ksbuilder createsimple --help` output to choose the right command.

For frontend work:

- Confirm the frontend chart creates a `JSBundle` and any reverse proxy/service resources needed by Console.
- Verify bundle URLs resolve through KubeSphere Console after publish.
- Check that `frontend/extensions/<extension-name>/` exists when the project includes source-level frontend code.

For backend work:

- Confirm the backend chart creates an `APIService` when exposing extension APIs through KubeSphere.
- Verify the service URL, API group, and version match the backend service.
- Use `$kubesphere-iam-rbac` for RoleTemplate-driven access-control tests.

When converting an existing Helm chart:

- Place the original chart under `charts/<dependency-name>` unless `ksbuilder createsimple` produces a different structure intentionally.
- Add the dependency to `extension.yaml.dependencies` with `agent` or `extension` tags.
- Create root `values.yaml` with `global` and one top-level override section per dependency.
- Move only extension-specific overrides into root `values.yaml`; leave default image tags and broad upstream defaults in the child chart.
- Patch child templates to consume KubeSphere globals before expecting marketplace installation to handle registry, pull secrets, or cluster identity correctly.

## Package, Publish, Push

Package locally:

```bash
ksbuilder package /path/to/extension
```

Verify the generated archive name and metadata against `extension.yaml` `name` and `version`.

Publish to a test cluster:

```bash
ksbuilder publish /path/to/extension-or-package --kubeconfig /path/to/kubeconfig
kubectl --kubeconfig /path/to/kubeconfig get extensions.extensions.kubesphere.io
kubectl --kubeconfig /path/to/kubeconfig get extensionversions.extensions.kubesphere.io
```

Then inspect workloads, services, ConfigMaps, and extension-specific resources in the namespaces used by the extension.

Submit to KubeSphere Cloud / Marketplace:

```bash
ksbuilder login
ksbuilder push /path/to/extension-or-package
ksbuilder list
ksbuilder get <extension-name>
```

Cloud API tokens are secrets. Use interactive prompts or environment variables; do not store tokens in repository files or final reports.

Unpublish from a test cluster:

```bash
ksbuilder unpublish <extension-name> --kubeconfig /path/to/kubeconfig
```

Verify extension resources and generated ConfigMaps are removed or intentionally retained.

## Local Repository Release Convention

The local extension collection release flow reads the release version directly from each target extension's `extension.yaml`.

- PR title trigger: `release <ext>` or `release <ext1>, <ext2>`.
- PR comment trigger: `/release <ext>` or `/release <ext1> <ext2>`.
- Manual tag trigger: `<extension-name>/<version>`, for example `whizard-logging/v1.5.0` or `whizard-auditing/2.0.0`.
- CI does not calculate, bump, or modify versions.
- Release notes are read from `CHANGELOG.md` for auto-release; keep a matching version section.

## Troubleshooting

- Missing extension in marketplace after `publish`: verify `Extension` and `ExtensionVersion` resources, generated chart ConfigMaps, and namespace events.
- Install succeeds but UI missing: inspect `JSBundle`, frontend service, reverse proxy, Console `/pstatic/dist/<name>/index.js`, and browser network errors.
- Backend API missing: inspect `APIService`, service DNS, backend Pod readiness, API group/version, and ks-apiserver discovery.
- Multi-cluster install missing agent: verify `installationMode`, dependency tags, selected clusters, and agent subchart resources.
- Permission denied during install: inspect `permissions.yaml`, installer service account permissions, and cluster events.
- Cloud push fails due package size: static icon and screenshots may be uploaded and rewritten by ksbuilder; ensure the final package stays within KubeSphere Cloud limits.
