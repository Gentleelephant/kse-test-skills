# API Routing And Status Codes

How ks-apiserver decides which component serves a request, and what each
failure looks like. Use this when a JSBundle, APIService, or ReverseProxy is
installed but the UI or API does not behave as expected.

All statements below were verified against KSE source, mainly
`pkg/apiserver/apiserver.go`, `pkg/apiserver/request/requestinfo.go`, the
filters under `pkg/apiserver/filters/`, and the extension webhooks under
`pkg/controller/extension/`. Re-check that source before contradicting it.

## Entry Points

| Request path | Classification | Served by | If not matched |
|---|---|---|---|
| `/api/...`, `/apis/...` | Kubernetes request | KubeAPIServer passthrough | KubeAPIServer response |
| `/kapi/...` | groupless KubeSphere resource request | APIService, if `spec.group`/`spec.version` match | KubeAPIServer -> 404 |
| `/kapis/<group>/<version>/<resource>...` | KubeSphere resource request | APIService, if `spec.group`/`spec.version` match | KubeAPIServer -> 404 |
| `/kapis/<group>/<version>` (no resource) | non-resource request | nothing; APIService only handles resource requests | KubeAPIServer -> 404 |
| `/dist/...` | non-resource request | JSBundle, if a bundle is `Available` and `status.link` equals the path | KubeAPIServer -> 404 |
| `/proxy/...` | non-resource request | ReverseProxy, if a matcher hits | KubeAPIServer -> 404 |

`/clusters/<cluster>` is stripped before classification, so every row above
also applies to `/clusters/member-a/kapis/...` and `/clusters/member-a/dist/...`.
Requests for a member cluster are forwarded to that member's ks-apiserver; they
are not handled by the host's extension filters directly. An unknown cluster
name is rejected by the multicluster filter with 400 and the body
`cluster <name> not exists`; it never reaches the extension filters.

All `api`/`apis` requests are marked `IsKubernetesRequest` and skip the
JSBundle, ReverseProxy, and APIService filters entirely. An extension that
exposes resources only through the Kubernetes API of the member cluster does
not need an APIService.

## Minimal Objects

The chart normally creates these; use the shapes below to review or repair one.
All four are cluster-scoped (`extensions.kubesphere.io/v1alpha1`) and should
carry `kubesphere.io/extension-ref` so the InstallPlan controller syncs their
state and `kubectl -l` queries find them.

```yaml
apiVersion: extensions.kubesphere.io/v1alpha1
kind: APIService
metadata:
  name: example
  labels: {kubesphere.io/extension-ref: example}
spec:
  group: example.kubesphere.io
  version: v1alpha1
  service:
    namespace: extension-example
    name: example-backend
    port: 443
---
apiVersion: extensions.kubesphere.io/v1alpha1
kind: ReverseProxy
metadata:
  name: example
  labels: {kubesphere.io/extension-ref: example}
spec:
  matcher:
    path: /proxy/example/*
    method: "*"
  upstream:
    service:
      namespace: extension-example
      name: example-frontend
      port: 443
  directives:
    stripPathPrefix: /proxy/example
    authProxy: true
---
apiVersion: extensions.kubesphere.io/v1alpha1
kind: JSBundle
metadata:
  name: example
  labels: {kubesphere.io/extension-ref: example}
spec:
  rawFrom:
    configMapKeyRef:
      name: example-frontend
      namespace: extension-example
      key: index.js
```

`service` endpoints are HTTPS by default. For a self-signed backend, set
`caBundle` (base64) or, only in test environments, `insecureSkipVerify: true`.
To use plain HTTP, set `url: http://<name>.<namespace>.svc:<port><path>`
instead of `service`.

## Filter Order

```text
RequestInfo -> Authentication -> Multicluster -> Authorization
  -> Auditing -> JSBundle -> ReverseProxy -> APIService -> KubeAPIServer
```

Consequences that decide most "why is this 403/404/503" questions:

- Authentication and authorization run before any extension filter. A valid
  route is not reachable without credentials and RBAC.
- `/dist/*` is in the always-allow path authorizer, together with `/oauth/*`,
  `/.well-known/openid-configuration`, `/kapis/version`, `/version`, `/metrics`,
  `/healthz`, and `/openapi/v2`. Bundle downloads therefore bypass RBAC, and a
  missing bundle is a 404, not a 403.
- `/proxy/*` is a non-resource URL and needs `nonResourceURLs` in RBAC. The
  built-in `platform-admin` role allows `*`; the built-in anonymous role allows
  only `/static/images/*`, so an unauthenticated proxy call is a 403.
- `/kapis/<group>/<version>/<resource>` is authorized as a resource request:
  the caller needs RBAC for that API group, resource, and verb. A RoleTemplate
  that grants the extension's API group is what makes the route usable. An
  APIService without matching RBAC yields 403 from ks-apiserver before the
  backend is contacted.
- A `/clusters/<member>` request that reaches an unready member is 503 from the
  multicluster filter. A 401 answered by the member's ks-apiserver is rewritten
  to 511 on the host, so 511 means "member rejected the forwarded credentials".

## JSBundle

A `JSBundle` declares the frontend asset the console injects. The chart
normally creates it; the webhook supplies defaults.

- `status.link` is defaulted to `/dist/<kubesphere.io/extension-ref label>/index.js`
  when empty. It is a status field, not a spec field.
- Validation requires `status.link` to start with `/dist/<extension-ref label>`
  when that label exists, and rejects a link already used by another JSBundle.
- The filter matches only when `status.state == Available` and
  `status.link == request path` exactly. Query strings do not affect matching.
- Content sources, in order: `spec.raw` (base64), `spec.rawFrom.configMapKeyRef`,
  `spec.rawFrom.secretKeyRef`, then `spec.rawFrom.url` or `spec.rawFrom.service`.
- The response is served with
  `Content-Type: application/javascript; charset=utf-8`.

Failure behavior:

| Symptom | Cause |
|---|---|
| 404 on the bundle URL | No bundle with that exact link, or the bundle is `Disabled` (extension disabled) or not yet synced |
| 503 | Listing failed, remote fetch failed, TLS setup failed, or the raw content could not be decoded |
| Bundle served but UI broken | Content is stale, truncated, or the console loaded a different link; compare `status.link` with the URL in browser devtools |

`spec.rawFrom.service` uses the same endpoint rule as APIService and
ReverseProxy: the service form is always HTTPS with port 443 by default. A
plain-HTTP asset server must be referenced through `url`.

## APIService

`APIService` is the extension equivalent of the Kubernetes aggregation layer:
it maps an API group plus version to an upstream endpoint.

- Only resource requests are considered. `spec.group` and `spec.version` must
  both equal the request's group and version; there is no wildcard.
- The webhook rejects a second APIService with the same group and version.
- Requests are proxied with the original path and query preserved. The
  directive `spec.group` is not stripped from the path.
- Every proxied request carries `X-Remote-User`, `X-Remote-Group`, and
  `X-Remote-Extra-*` headers built from the authenticated user, so the backend
  can implement its own authorization on top of KubeSphere's.

Failure behavior:

| Status | Cause |
|---|---|
| 503 `apiService <name> is not available` | `status.state != Available`, meaning the InstallPlan is disabled or status has not synced yet |
| 503 | Endpoint URL parse failure or TLS transport setup failure |
| 500 | ks-apiserver could not list APIServices |
| 404 | No APIService for this group/version, or the request had no resource segment |
| 403 | The caller lacks RBAC for the group/resource; the backend was never reached |
| 502 | The backend connection failed or the response transport failed |

`spec.group`/`spec.version` only route traffic. Discovery entries, swagger, and
console permission metadata are separate concerns; do not infer them from a
working proxy.

## ReverseProxy

`ReverseProxy` proxies non-resource paths, used by extensions that serve their
own UI or static files under `/proxy`.

- Only paths starting with `/proxy` are considered.
- Matching rules: `matcher.method` must equal the request method or `*`;
  `matcher.path` must equal the path exactly or prefix-match when it ends in
  `*`. The prefix is the path with the trailing `*` removed.
- The webhook rejects a matcher that duplicates an existing path with the same
  method (or `*`) or that overlaps an existing trailing-`*` prefix.
- Directives: `method`, `stripPathPrefix`, `stripPathSuffix`, `headerUp`
  (with `+`/`-` prefixes to add/remove), `headerDown`, `rewrite`, `replace`,
  `pathRegexp`, `wrapTransport`, `authProxy`, `rejectForwardingRedirects`.
- `authProxy: true` adds the same `X-Remote-*` identity headers as APIService.
- `status.state != Available` is a 503 with `upstream <name> is not available`.
- Bad directives, an unparsable upstream URL, or TLS setup failure is a 503.
- An upstream transport failure is a 502; the upstream's own status is passed
  through unchanged.

Order matters: matchers are evaluated as listed. The first matching
ReverseProxy decides the result; a later, more specific matcher does not get a
second chance.

## ExtensionEntry

`ExtensionEntry` has `spec.entries` (raw JSON entries injected into the
console) and only a `status.state`. It does not proxy or serve requests itself;
it only contributes console navigation/menu data. Its state is synced from the
InstallPlan `spec.enabled` exactly like the other three objects. A missing menu
entry is therefore an InstallPlan/status problem, not a routing problem.

## Status Code Map

| Status | Typical cause | First check |
|---|---|---|
| 400 | `/clusters/<name>` refers to a cluster that does not exist | Cluster object and spelling |
| 401 | Invalid, expired, or malformed token; unknown auth scheme | Token issuer, server URL, expiration |
| 403 | RBAC denied; anonymous caller; `/proxy` without `nonResourceURLs` | RoleTemplate/bindings, user name, path class |
| 404 | No route: unknown `/kapis` group, unmatched or disabled JSBundle, no matching `/proxy` matcher, unmatched `/dist` path | Object exists, `status.state`, exact link/path |
| 500 | APIService list failure or internal authorizer error | ks-apiserver logs |
| 502 | Upstream connection/transport failure; bad gateway | Backend Pods, Service endpoints, TLS, upstream logs |
| 503 | Object not `Available` (disabled or unsynced), unparsable endpoint, TLS setup failure, unready member cluster | `status.state`, plan `spec.enabled`, member cluster state, ks-apiserver logs |
| 504 | The request exceeded the ks-apiserver request timeout and the inner handler had not responded | Backend latency, stuck upstream, client timeout setting |
| 511 | A member cluster's ks-apiserver returned 401 for forwarded credentials | Member cluster auth, token audience, cluster agent |

A request with no `Authorization` header is not a 401: it is authenticated as
`system:anonymous` and normally denied with 403. See
`$kubesphere-iam-rbac` for the full 401/403 model.

## Diagnosis Sequence

1. Confirm the extension is installed and enabled:

   ```bash
   kubectl --kubeconfig "$KUBECONFIG" get installplan <name> \
     -o jsonpath='{.spec.enabled}{" "}{.status.state}{" "}{.status.enabled}{"\n"}'
   ```

2. Confirm the exposed object is `Available` and has the expected route:

   ```bash
   kubectl --kubeconfig "$KUBECONFIG" get jsbundles,apiservices,reverseproxies,extensionentries \
     -l kubesphere.io/extension-ref=<name> -o yaml
   ```

3. Reproduce with curl and keep the status code:

   ```bash
   curl -sk -o /dev/null -w '%{http_code}\n' "$SERVER<path>" \
     -H "Authorization: Bearer $TOKEN"
   ```

4. Interpret by path class:
   - `/dist/...` -> JSBundle link/state; 404 means no exact match.
   - `/proxy/...` -> ReverseProxy matcher/state; 403 means RBAC, 502 means the
     upstream is unreachable.
   - `/kapis/<group>/<version>/...` -> APIService group/version/state; 403
     means resource RBAC, 503 means disabled or unsynced, 502 means the
     backend failed.
5. If ks-apiserver returns 5xx, read its logs for the filter that rejected the
   request; the messages above are emitted verbatim.
6. Test member-cluster paths through the host (`/clusters/<member>/...`). A 503
   there is usually the member cluster being unready, not the extension.

Do not report a routable extension API as verified until the status code,
`status.state`, and the backend identity (pod, service, TLS mode) have all been
captured.
