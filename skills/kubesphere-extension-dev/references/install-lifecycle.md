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
  config: "{}"            # string (JSON or YAML), not an object
# optional multi-cluster scheduling:
#  clusterScheduling:
#    placement:
#      clusters: [member-a]
#      clusterSelector:
#        matchLabels: {env: test}
```

`spec.config` is a string containing JSON or YAML, not an embedded object. The
InstallPlan webhook parses it with a YAML decoder and trims trailing whitespace
from every line, so invalid YAML is rejected and a whitespace-only edit is not a
reliable change. `spec.enabled` defaults to false when omitted, so always set
it. `upgradeStrategy` is stored but the installplan controller does not read
it; upgrades happen when `spec.extension.version` or `spec.config` changes on
an installed plan.

## Create An InstallPlan Explicitly

After a successful publish, replace the example name and version in the
manifest above and save it as `$INSTALLPLAN_FILE`. The manifest values must
match the variables below. Run this only against the confirmed test cluster:

```bash
set -e

KUBECONFIG=/path/to/kubeconfig
EXTENSION_NAME=example
EXTENSION_VERSION=1.0.0
INSTALLPLAN_FILE=/path/to/installplan.yaml

kubectl --kubeconfig "$KUBECONFIG" config current-context

# The published resources must exist before installation.
kubectl --kubeconfig "$KUBECONFIG" get extension "$EXTENSION_NAME"
kubectl --kubeconfig "$KUBECONFIG" get extensionversion \
  "${EXTENSION_NAME}-${EXTENSION_VERSION}"

# Never overwrite a plan that may already be installing or installed.
existing_plan="$(kubectl --kubeconfig "$KUBECONFIG" get installplan \
  "$EXTENSION_NAME" --ignore-not-found -o name)"
if [ -n "$existing_plan" ]; then
  printf 'InstallPlan already exists: %s; inspect it instead of overwriting it.\n' \
    "$existing_plan" >&2
else
  kubectl --kubeconfig "$KUBECONFIG" create --dry-run=server \
    -f "$INSTALLPLAN_FILE"
  kubectl --kubeconfig "$KUBECONFIG" create -f "$INSTALLPLAN_FILE"
fi
```

`kubectl create` deliberately fails rather than modifying an existing
`InstallPlan`; if another actor creates the plan between the check and the
create, inspect that plan instead of retrying with `apply`. After creation, use
the verification commands below and wait for a terminal state.

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

### Trigger semantics (KSE v4.2.x)

The controller compares two things on every reconcile:

- `configChanged`: FNV hash of the **effective config string** differs from
  `status.configHash` on the host, or from
  `status.clusterSchedulingStatuses[<cluster>].configHash` on a member. The
  effective config is `spec.config` on the host; on a member it is the deep
  merge of `spec.config` with `spec.clusterScheduling.overrides[<cluster>]`
  when that override exists.
- `versionChanged`: `spec.extension.version` differs from `status.version`
  (host) or from the per-cluster status version.

Which change triggers what depends on the current state:

| Current state | config changed | only version changed | Controller action |
| --- | --- | --- | --- |
| `""` | n/a | n/a | first install (`upgrade=false`) |
| `InstallFailed` | yes | **no** | re-run install (`upgrade=false`) |
| `Installed` / `Upgraded` | yes | yes | upgrade (`upgrade=true`) |
| `UpgradeFailed` | yes | **no** | re-run upgrade (`upgrade=true`) |
| `Preparing` / `Installing` / `Upgrading` | ignored | ignored | pending; do not mutate |

The important asymmetry: **`versionChanged` is only consulted in the
`Installed`/`Upgraded` state.** In `InstallFailed` or `UpgradeFailed`, changing
only `spec.extension.version` does nothing; change `spec.config` (or the
member override) to re-trigger. The InstallPlan webhook trims trailing
whitespace, so make a real value change rather than a formatting-only edit.
See the two sections below for executable steps.

There is no helm rollback. A failed upgrade stays `UpgradeFailed` until the
config changes; otherwise uninstall and reinstall.

## Upgrade An Installed Extension

Prerequisites before touching the InstallPlan:

1. Publish the target version first: `ksbuilder publish` creates
   `ExtensionVersion <name>-<version>`. The controller resolves the chart by
   that exact object name. On KSE v4.2 a missing object makes the executor Job
   fail (`InstallFailed`/`UpgradeFailed`); older builds returned without
   action. Either way, a version bump to an unpublished version does not work.
2. Only patch a plan in a terminal state. A plan in `Preparing`, `Installing`,
   or `Upgrading` ignores spec changes until the executor Job finishes.

From a healthy `Installed`/`Upgraded` plan there are two kinds of changes
(version and config; a per-cluster override is a config change):

```bash
# 1) Version upgrade: publish the new ExtensionVersion first, then bump the ref.
kubectl --kubeconfig "$KUBECONFIG" patch installplan "$NAME" --type merge \
  -p '{"spec":{"extension":{"name":"example","version":"1.6.0"}}}'

# 2) Config-only upgrade: spec.config is the WHOLE string; patch replaces it.
#    Keep it valid YAML/JSON and change at least one real value.
kubectl --kubeconfig "$KUBECONFIG" patch installplan "$NAME" --type merge \
  -p '{"spec":{"config":"global:\n  imageRegistry: registry.example.com\n"}}'

# 3) Per-cluster values: JSON merge patch adds/replaces one key while keeping
#    the other overrides and placement. On KSE v4.2 it is deep-merged over spec.config.
kubectl --kubeconfig "$KUBECONFIG" patch installplan "$NAME" --type merge \
  -p '{"spec":{"clusterScheduling":{"overrides":{"member-a":"global:\n  imageRegistry: registry.example.com\n"}}}}'
```

Expect the plan to move `Preparing -> Upgrading -> Installed`, with
`status.version` equal to the new version (for a version bump), a new
`status.configHash`, `Upgraded=True` and `Ready=True` conditions, and a new
entry in `status.stateHistory`. Check members separately with
`status.clusterSchedulingStatuses`.

Live evidence (KSE v4.2.1, 2026-11-18): `whizard-logging` stayed at version
`1.5.0` but recorded two `Upgrading -> Installed` cycles on 2026-11-18 — a
config-only change upgrades without a version bump. `grafana` shows an
`Upgrading -> Installed` cycle with `Upgraded=True`, `Installed=True`, and
`Initialized=True`. `cicd-runner-tekton` shows `UpgradeFailed` entries followed
by `Upgrading -> Installed` and `Upgraded=True`.

Caveats:

- `upgradeStrategy: Manual` (the default) is not read by the controller; it
  does not gate or trigger upgrades.
- Host gating still applies: members are reconciled only while the host is in
  the deployed state. After a host upgrade, verify each
  `status.clusterSchedulingStatuses[<cluster>]` rather than assuming the agent
  release was upgraded; some versions stop member reconciliation once the host
  leaves the deployed state.
- A version bump also changes the `ExtensionVersion` object looked up by the
  controller; never delete or replace that object while a plan references it.

## Re-Trigger A Failed Install Or Upgrade

Use this when the executor Job failed and `status.state` is `InstallFailed`
or `UpgradeFailed`. Patching `spec` is sufficient to re-schedule work: the
controller watches every InstallPlan update (`For(&corev1alpha1.InstallPlan{})`
with no event filter), so the patch itself enqueues a reconcile. There is no
separate "reschedule" annotation or API in the controller, and
`kubesphere.io/force-delete` is only a stuck-uninstall escape hatch — do not
use it to retry an install.

1. Capture evidence first: the failing Job and its logs (see Verification
   Commands), plus `status.conditions` and `status.stateHistory`.
2. Fix the underlying cause (chart values, image, RBAC, member connectivity).
3. Make a **real content change** to the config used by the affected target:

```bash
# Host: InstallFailed or UpgradeFailed -> change spec.config.
kubectl --kubeconfig "$KUBECONFIG" patch installplan "$NAME" --type merge \
  -p '{"spec":{"config":"<full config string with the fix applied>"}}'

# One member: change only that cluster's override (merge patch keeps other keys).
kubectl --kubeconfig "$KUBECONFIG" patch installplan "$NAME" --type merge \
  -p '{"spec":{"clusterScheduling":{"overrides":{"member-a":"<fixed YAML string>"}}}}'

# Do NOT do only this while failed (v4.2 ignores versionChanged in failed states):
# kubectl patch installplan "$NAME" --type merge \
#   -p '{"spec":{"extension":{"version":"1.6.0"}}}'
```

4. Watch for a fresh `Preparing -> Installing` (from `InstallFailed`) or
   `Preparing -> Upgrading` (from `UpgradeFailed`) transition. If the state
   does not change, the config hash did not change — re-check the effective
   config (including any override merge) and the webhook's whitespace trim.
5. If the plan is `Preparing`/`Installing`/`Upgrading`, do not patch it; the
   controller explicitly returns while the executor Job is running.

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
- The agent's effective config is `spec.config` plus, on KSE v4.2, a deep
  merge of `spec.clusterScheduling.overrides[<clusterName>]` when present;
  older builds used the override string alone. A change to either changes that
  cluster's `configHash` and re-triggers that agent only.
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

## Evidence Basis

`INFERRED` from KSE source (local checkout
`/Users/zhangpeng/GolandProjects/temp/kse`, commit `5e3360f7`,
`v4.2.0-beta.2-303-g5e3360f79`):

- `pkg/controller/core/installplan_controller.go`: `SetupWithManager` watches
  `InstallPlan` with no event filter; `syncInstallPlanStatus` and
  `syncClusterAgentStatus` contain the state-dependent trigger switch;
  `syncClusterSchedulingStatus` gates on host state `Deployed`;
  `installOrUpgradeExtension` writes `configHash`, `version`, `releaseName`,
  and `jobName`; `updateStateAndConditions` maps `Installed`/`Upgraded` to the
  deployed state and maintains `stateHistory`/conditions.
- `pkg/controller/core/util.go`: `clusterConfig`, `merge`, `configChanged`,
  and `versionChanged` define the effective config and change detection.
- `pkg/controller/core/installplan_webhook.go`: YAML parsing validation and
  trailing-whitespace trimming in `Default`.

`VERIFIED` from a live KSE v4.2.1 cluster (`ks-apiserver:v4.2.1`, host plus
member `17-29`) on 2026-11-18: 13 InstallPlans inspected; `cicd-runner-tekton`
was `Upgraded` with `UpgradeFailed`/`Upgrading`/`Installed` history and
`Upgraded=True`; `whizard-logging` and `whizard-monitoring` were `Installed`
with two and one config-only `Upgrading -> Installed` cycles respectively at
unchanged versions; `grafana` was `Installed` with an `Upgrading -> Installed`
cycle; `vector` had per-cluster `Installed` statuses with `<name>-agent`
releases on both `host` and `17-29`. Sanitized evidence is recorded in
`validation/runs/2026-11-18-kse-v4.2.1-installplan-triggers.md`.
