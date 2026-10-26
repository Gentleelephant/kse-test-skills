# Install Lifecycle

`ksbuilder publish` does not install anything. It publishes extension metadata
and the chart, then the KubeSphere console (or an explicit `InstallPlan`)
installs it. Verify install state from `InstallPlan`, never from `publish`
success alone.

## Publish Effects

`ksbuilder publish <dir|tgz|oci://...> --kubeconfig ...` applies:

- `Extension <name>` with labels `kubesphere.io/category` and
  `kubesphere.io/managed: "true"`.
- `ExtensionVersion <name>-<version>` with labels
  `kubesphere.io/extension-ref: <name>` and `kubesphere.io/category`.
- For local sources, `ConfigMap extension-<name>-<version>-chart` in
  `kubesphere-system` with key `chart.tgz` and
  `ExtensionVersion.spec.chartDataRef` pointing at it.
- For OCI sources, `ExtensionVersion.spec.chartURL` instead of a ConfigMap.

master prints `Next: open the KubeSphere console ... install it.`; v0.4.7
prints only the created object lines. Use `--dryRun --output <dir>` to inspect
these objects locally without a cluster. Neither version creates an
`InstallPlan` or workloads.

## Objects And Names

| Object | Name | Scope |
|---|---|---|
| `Extension` | `<name>` | cluster |
| `ExtensionVersion` | `<name>-<version>` | cluster |
| `InstallPlan` | `<name>` | cluster, `kubesphere.io/v1alpha1` |
| Chart ConfigMap | `extension-<name>-<version>-chart` | `kubesphere-system` |
| Host Helm release | `<name>` | target namespace |
| Agent Helm release | `<name>-agent` | target namespace on each selected member cluster |
| Install namespace | `ExtensionVersion.spec.namespace`, else `extension-<name>` | cluster |
| Helm executor identity | `helm-executor.<name>` | impersonated in the target namespace; no ServiceAccount object is created |
| Install RBAC | `kubesphere:<name>:helm-executor` (Role, RoleBinding, ClusterRole, ClusterRoleBinding) | target namespace / cluster |

The install namespace is created with labels
`kubesphere.io/workspace: system-workspace` and
`kubesphere.io/extension-ref: <name>`. Helm impersonates
`system:serviceaccount:<targetNamespace>:helm-executor.<name>` so chart
resources are created with the extension's declared `permissions.yaml` rights,
not the caller's. That identity is only a binding subject: the controller
creates the Role/RoleBinding/ClusterRole/ClusterRoleBinding and does not create
a `helm-executor.<name>` ServiceAccount object.

## Minimal InstallPlan

```yaml
apiVersion: kubesphere.io/v1alpha1
kind: InstallPlan
metadata:
  name: example
spec:
  extension:
    name: example
    version: 1.0.0
  enabled: true
  upgradeStrategy: Manual
  config: "{}"            # JSON string, not an object
# optional multi-cluster scheduling:
#  clusterScheduling:
#    placement:
#      clusters: [member-a]
#      clusterSelector:
#        matchLabels: {env: test}
```

`spec.config` is a string containing JSON. `spec.enabled` defaults to false
when omitted, so always set it. `upgradeStrategy` is stored but the installplan
controller does not read it; upgrades happen when `spec.extension.version` or
`spec.config` changes on an installed plan.

Multi-cluster installation is triggered by `spec.clusterScheduling != nil` and
the host reaching `Installed`, not by the
`kubesphere.io/installation-mode` annotation. The annotation is extension
metadata; the installplan controller does not read it. Agent subcharts still
need an `agent` dependency tag and a true dependency `condition`, or the agent
release installs without them.

## State Machine

`InstallPlan.status.state` progresses through:

```text
"" (not started)
  -> Preparing -> Installing -> Installed
                              -> InstallFailed
  -> Upgrading -> Upgraded
              -> UpgradeFailed
  -> Uninstalling -> Uninstalled
                 -> UninstallFailed
```

`Installed` is the stable `Deployed` state. `Preparing` also appears on the
`Extension` object while its InstallPlan state is still empty. Each transition
appends to `status.stateHistory` (last 10 entries) and updates conditions
`Initialized`, `Installed`, `Upgraded`, `Uninstalled`, and `Ready`.

`status` also records `configHash`, `targetNamespace`, `releaseName`, `version`,
`jobName`, `enabled`, and per-cluster `clusterSchedulingStatuses`. A non-empty
list of cluster scheduling statuses means the agent subcharts are installed in
member clusters.

Retry semantics:

- `InstallFailed` plus a changed `spec.config` or `spec.extension.version`
  retries the install (`Installing`, not `Upgrading`).
- `Installed`/`Upgraded` plus a changed config or version runs `helm upgrade`.
- `Preparing`, `Installing`, and `Upgrading` are pending; do not issue another
  mutation. Wait for a terminal state.
- There is no helm rollback. A failed upgrade leaves `UpgradeFailed`; recover
  by fixing the config/version and letting the plan upgrade again, or by
  uninstalling and reinstalling.

## Multi-Cluster Agents

Member clusters are scheduled only through `spec.clusterScheduling`. The
`kubesphere.io/installation-mode` annotation and `extension.yaml`
`installationMode` are metadata; the controller never reads them.

Host gating: `syncClusterSchedulingStatus` returns immediately unless the host
plan state is exactly `Installed` (`StateDeployed`). Agents are not installed,
upgraded, or uninstalled while the host is `Preparing`, `Installing`,
`InstallFailed`, or `Uninstalling`. In the KSE source this was verified
against, an upgraded host becomes `Upgraded`, which no longer equals
`StateDeployed`, so member reconciliation stops after a host upgrade. Re-check
this on the target version before relying on an agent upgrade that follows a
host upgrade.

Always set `placement`. `spec.clusterScheduling` is a pointer and `placement`
is a pointer inside it; `clusterScheduling: {}` or a block containing only
`overrides` leaves `placement` nil, and the controller dereferences it without
a check, so the installplan controller panics. The API server accepts the
object because there is no webhook validation for this field.

Placement rules (`spec.clusterScheduling.placement`):

- `clusters` is an explicit name list and wins whenever it is non-empty. Names
  that do not resolve to a `Cluster` object are skipped silently; they are not
  reported as errors.
- `clusterSelector` is used only when `clusters` is empty. A non-nil but empty
  selector (`clusterSelector: {}`) matches every cluster, including the host.
  Exclude the host with
  `matchExpressions: [{key: cluster-role.kubesphere.io/host, operator: DoesNotExist}]`
  or by naming member clusters explicitly.
- When `clusters` is empty and `clusterSelector` is nil, no member cluster is
  selected.

Dependency enablement is decided by `tags`, not by `condition`:

| Tags on the dependency | Host install | Agent install |
|---|---|---|
| `extension` only | enabled (`tags.extension=true`); its `condition`, if any, keeps its values.yaml default | disabled (`tags.extension=false`, plus `condition=false` when declared) |
| `agent` only | disabled (`tags.agent=false`, plus `condition=false` when declared) | enabled (`tags.agent=true`) |
| both `extension` and `agent` | enabled: Helm enables a chart when any of its tags is true | enabled, same rule |
| no tags | Helm defaults apply on both targets | Helm defaults apply on both targets |

The controller sets both `tags.extension` and `tags.agent` in every install, so
a dependency that carries both tags is enabled on both targets. Give a
component exactly one of the two tags. A dependency with a `condition` is also
forced to `false` when its tag is not the target; without a `condition` it
still follows its tag.

Per-member behavior:

- The agent release is `<name>-agent` in the same target namespace, installed
  with Helm impersonating `helm-executor.<name>` on the member.
- The controller forces `tags.agent=true`, `tags.extension=false`, disables
  every `extension`-tagged dependency condition, and adds
  `global.clusterInfo.name` plus `global.clusterInfo.role` (`host` or
  `member`). Controller image registry and node selector options are appended
  as overrides.
- The agent receives `spec.clusterScheduling.overrides[<clusterName>]` when
  present, otherwise `spec.config`.
- `status.clusterSchedulingStatuses[<clusterName>]` holds an
  `InstallationStatus` with the same fields and states as the host: `state`,
  `configHash`, `targetNamespace`, `releaseName`, `jobName`, `version`,
  `conditions`, and `stateHistory`. A fresh agent install records an
  `Initialized` condition.
- When the per-cluster `configHash` or version differs from the desired value,
  the agent is upgraded (`Upgrading` -> `Upgraded`), independent of the host
  release revision.
- A cluster removed from the selection is uninstalled: the controller removes
  `<name>-agent` and deletes its status entry once the release is gone. If the
  `Cluster` object itself was deleted, the status entry is also removed.
- `spec.enabled: false` mirrors `Disabled` onto the extension objects on the
  host and on every `Deployed` member, but leaves agent releases running.

Deleting the InstallPlan uninstalls every agent release first, then the host
release; see Uninstall above.

Verify:

```bash
# selected clusters and their states
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{range .status.clusterSchedulingStatuses}{@}{"\n"}{end}'
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{range $k,$v := .status.clusterSchedulingStatuses}{$k}{" "}{$v.state}{" "}{$v.releaseName}{"\n"}{end}'
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> -o jsonpath='{.spec.clusterScheduling}'

# per-member evidence (member kubeconfig)
helm --kubeconfig <member-kubeconfig> -n <targetNamespace> list
kubectl --kubeconfig <member-kubeconfig> -n <targetNamespace> get pods,jobs
kubectl --kubeconfig <member-kubeconfig> get jsbundles,apiservices,reverseproxies \
  -l kubesphere.io/extension-ref=<name>
```

A missing agent is usually one of: `clusterScheduling` absent, the host not in
`Installed`, the selected cluster name misspelled, the selector matching
nothing, the agent dependency condition false, or the member cluster unready.

## Enabled Is Not Uninstall

Setting `spec.enabled: false` on an installed plan does not remove workloads.
The controller sets `JSBundle`, `APIService`, `ReverseProxy`, and
`ExtensionEntry` status to `Disabled`, mirrors `status.enabled: false`, and
leaves Pods, Services, and the Helm release in place. The console hides the
extension, but its API objects still exist.

Use this to verify the toggle:

```bash
kubectl --kubeconfig "$KUBECONFIG" get installplan example \
  -o jsonpath='{.spec.enabled}{" spec / "}{.status.enabled}{" status\n"}'
kubectl --kubeconfig "$KUBECONFIG" get jsbundles,apiservices,extensionentries \
  -l kubesphere.io/extension-ref=example
```

## Uninstall

Deletion is the uninstall trigger. `InstallPlan` carries the finalizer
`kubesphere.io/installplan-protection`, so it stays in `Terminating` until the
helm release is removed:

1. If `status.releaseName` is empty, nothing was installed: the finalizer is
   removed immediately.
2. Otherwise the controller uninstalls every entry in
   `status.clusterSchedulingStatuses` (release `<name>-agent` on each member
   cluster) and then the host release `<name>` via a helm executor Job. State
   becomes `Uninstalling`, then `Uninstalled`.
3. `Uninstalled` sets the extension's status message, removes the finalizer,
   and the API server then deletes the InstallPlan object.

There is no helm rollback and no history-based restore. Reinstallation creates
a fresh release.

`kubesphere.io/force-delete` forces the helm delete when the normal uninstall
is stuck:

```bash
kubectl --kubeconfig "$KUBECONFIG" annotate installplan example \
  kubesphere.io/force-delete=true
```

This calls `ForceDelete` for the release, marks the plan `Uninstalled`, and
removes the finalizer. Use it only after capturing the stuck Job/Pod and
events, and expect orphaned resources if the release metadata was corrupt.

### Residual Objects

After a clean uninstall, the Helm release and all chart-managed resources are
gone, but these remain:

- `Extension` and `ExtensionVersion` (metadata and chart ConfigMap).
- The target namespace and the `kubesphere:<name>:helm-executor` Role,
  RoleBinding, ClusterRole, and ClusterRoleBinding. There is no
  `helm-executor.<name>` ServiceAccount object to delete.
- The `InstallPlan` object only until its finalizer clears.

`ksbuilder unpublish <name> --kubeconfig ...` removes the published metadata.
Order differs by version; run `scripts/probe_ksbuilder.sh` to identify the
build, because `ksbuilder version` may print `0.0.0`:

- master: delete `InstallPlan <name>` and wait up to one minute for it to
  disappear (2s poll). If Helm uninstall is still running, unpublish fails at
  this step. Then delete `ConfigMap extension-<name>-<version>-chart` for every
  ExtensionVersion plus those ExtensionVersions, and finally `Extension <name>`.
- v0.4.7: no wait. It issues deletes for the chart ConfigMaps and
  ExtensionVersions, then `InstallPlan <name>`, then `Extension <name>`. The
  plan may still be `Terminating` when unpublish returns; wait for its
  finalizer and report the plan state separately from "unpublish succeeded".

Neither version deletes the target namespace or the helm-executor
Role/ClusterRole bindings. Report those as intentional residuals or clean them
explicitly.

## Verification Commands

```bash
# publish/install state
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{.status.state}{" "}{.status.targetNamespace}{" "}{.status.releaseName}{"\n"}'
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{range .status.stateHistory[*]}{.state}{" "}{.lastTransitionTime}{"\n"}{end}'

# helm-side evidence and the executor job that failed
kubectl --kubeconfig "$KUBECONFIG" -n <targetNamespace> get jobs \
  -l kubesphere.io/extension-ref=<name>
kubectl --kubeconfig "$KUBECONFIG" -n <targetNamespace> logs job/<jobName>

# advertised resources
kubectl --kubeconfig "$KUBECONFIG" get jsbundles,apiservices,reverseproxies,extensionentries \
  -l kubesphere.io/extension-ref=<name>

# multi-cluster agent
kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
  -o jsonpath='{range .status.clusterSchedulingStatuses}{@}{"\n"}{end}'
```

A successful `publish` with no `InstallPlan` means the extension is published
but not installed. A plan stuck in `Preparing`/`Installing` means the executor
Job is pending or failing; inspect the Job and namespace events before
retrying. `UninstallFailed` plus a lingering finalizer is the case for
`kubesphere.io/force-delete` after evidence capture.

## Reporting

Report the plan state and conditions as `VERIFIED` only from captured cluster
output. Distinguish:

- `publish` success (metadata present) from install success (plan `Installed`
  and resources `Available`).
- `enabled: false` (hidden, still running) from uninstalled (release gone).
- intentional residuals (namespace, helm-executor RBAC) from unexplained
  leftovers.
