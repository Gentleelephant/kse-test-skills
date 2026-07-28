# Cluster Validation Evidence

Cluster support is `UNVERIFIED` until a sanitized record is added under
`validation/runs/`. Offline unit tests, source inspection, and rendered
templates support `INFERRED` claims only.

## Release Gate

A live validation record must show:

- date, tester, repository commit, and skill revision;
- KubeSphere/KSE and Kubernetes versions;
- host/member topology and sanitized target names;
- tested extension name/version and API document revision;
- commands and exit codes for preflight, publish, IAM/RBAC, E2E, and cleanup;
- expected/actual authorization matrix;
- Extension, JSBundle, APIService, workload, Service, ConfigMap, and event evidence;
- failure injection for invalid credentials, unavailable member cluster, and
  membership/binding timeout;
- interrupted-run recovery and repeated-cleanup result;
- confirmation that credentials and Authorization headers were redacted.

The gate passes only when the happy path and verified cleanup pass, no
high-risk secret or authorization issue remains, and every unsupported or
untested claim is explicitly marked.

## Manual Sequence

1. Run extension checker, ksbuilder lint/template/package, publish, resource
   verification, and unpublish against the confirmed test kubeconfig.
2. Run focused IAM create/bind/ALLOW/DENY/unbind/delete checks for platform,
   cluster, Workspace, and Namespace scopes.
3. Run API E2E `doctor`, `validate`, `plan`, `run --execute`, and repeated
   `cleanup --execute`.
4. Repeat the relevant preflight with each injected failure and confirm no
   resources were created or leaked.

Keep raw, secret-bearing output outside Git in access-controlled artifacts.
Commit only the sanitized summary.

## Record Template

Create `validation/runs/YYYY-MM-DD-<kse-version>-<short-sha>.md`:

```markdown
# KubeSphere Skills Cluster Validation

- Evidence status: VERIFIED | INFERRED | UNVERIFIED
- Date:
- Tester:
- Commit:
- KubeSphere/KSE:
- Kubernetes:
- Topology:
- Extension/API:

## Commands And Results

| Stage | Sanitized command/artifact | Exit/result | Evidence |
| --- | --- | --- | --- |

## Permission Matrix

| Scope | Role | Method/path | Expected | Actual | Result |
| --- | --- | --- | --- | --- | --- |

## Failure Injection

| Failure | Expected protection | Actual | Result |
| --- | --- | --- | --- |

## Cleanup

- First cleanup:
- Repeated cleanup:
- Residual-resource queries:
- Result:

## Unverified Items

- None, or list every remaining gap.
```
