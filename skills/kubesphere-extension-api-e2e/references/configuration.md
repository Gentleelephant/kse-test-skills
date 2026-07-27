# Configuration

## Contents

1. Location and lifecycle
2. Required fields
3. Source configuration
4. Scenario and execution configuration
5. Authentication and safety

## Location And Lifecycle

The only user-maintained file is `<project-root>/kse-e2e.yaml`. All relative
paths resolve from its directory. Generate it with:

```bash
python3 <skill-dir>/scripts/run_e2e.py init --project .
```

Initialization discovers common OpenAPI names, `extension.yaml` or
`Chart.yaml`, and a backend project identified by `go.mod`, `pom.xml`,
Gradle files, `pyproject.toml`, `requirements.txt`, `setup.py`, or
`package.json`. It creates mode `0600` and adds `/kse-e2e.yaml` to the project
`.gitignore`.

Use `assets/kse-e2e.example.yaml` as the complete schema example.

## Required Fields

- `spec.target.ksAPIServer`: KubeSphere API server aggregation endpoint.
- `spec.target.cluster`: cluster used by temporary WorkspaceTemplates.
- `spec.auth`: token or username/password credentials.
- `spec.sources.apiDocs[].path`: Swagger 2 or OpenAPI 3 JSON/YAML.
- `spec.sources.extension.path`: extension root or Helm chart.
- `spec.sources.backendCode[].path`: one or more readable backend source roots.

`renderer: auto` prefers `ksbuilder` for `extension.yaml` and Helm for
`Chart.yaml`. Values files are passed to the renderer. Older ksbuilder
Kubernetes-1.20 compatibility failures use the existing Helm child-chart
fallback and are reported as `helm-fallback`.

## Source Configuration

Each backend source has:

- `name`: stable evidence label.
- `path`: required root, relative to `kse-e2e.yaml` or absolute.
- `language`: `auto`, `go`, `java`, `python`, or `node`.
- `include`: optional files/directories relative to the backend root.
- `exclude`: ignored paths relative to the backend root.

The Agent may read these paths only. Generated source inventory is stored in
the plan. Source code, fixtures, and project-specific E2E programs are never
executed by source analysis.

## Scenario And Execution Configuration

`spec.scenarios.variables` contains values not discoverable from sources or the
cluster. `overrides` supplies valid bodies or corrected SAR metadata for
documented operations. `additional` describes dependency, isolation, response
shaping, and business-rejection scenarios.

Generated output is controlled by:

- `spec.report.directory`
- `spec.report.plan`
- `spec.report.json`
- `spec.report.markdown`

Temporary resources use `spec.execution.resourcePrefix` plus a run ID. Cleanup
is `always` or `never`; `always` is the default.

## Authentication And Safety

Authentication priority is:

1. `adminToken`
2. `adminUsername` plus `adminPassword`

`mode: token` and `mode: password` enforce their respective fields. `mode:
auto` uses the priority above. Credentials remain in `kse-e2e.yaml`; the
runner does not require credential environment variables.

The config must be mode `0600` and untracked by Git. Logs, plans, state files,
JSON, Markdown, and exceptions redact configured and generated secrets.
