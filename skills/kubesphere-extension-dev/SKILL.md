---
name: kubesphere-extension-dev
description: Develop, inspect, test, package, publish, submit, unpublish, and troubleshoot KubeSphere extensions. Use when Codex works with KubeSphere extension directories containing extension.yaml, permissions.yaml, Helm charts, frontend/backend subcharts, JSBundle/APIService resources, ksbuilder create/package/lint/template/publish/push/unpublish workflows, extension release checks, or install issues in a KubeSphere cluster.
---

# KubeSphere Extension Dev

## Overview

Use this skill to work on KubeSphere extensions from source directories through local validation, ksbuilder packaging, cluster test publishing, KubeSphere Cloud submission, and troubleshooting. Keep kubeconfigs, Cloud API tokens, registry credentials, and bearer tokens out of persisted skill files and final answers unless the user explicitly asks to reveal them.

## Workflow

1. Identify the extension root. Prefer a directory that contains `extension.yaml`; common examples live under `/Users/zhangpeng/GolandProjects/github.com/Gentleelephant/extensions/<extension-name>`.
2. Read `references/kubesphere-extension.md` before making structural, packaging, publishing, or release decisions.
3. For an existing extension, run the bundled checker first:

```bash
ruby skills/kubesphere-extension-dev/scripts/check_extension.rb /path/to/extension
```

4. Follow with ksbuilder and Helm-oriented validation:

```bash
ksbuilder lint /path/to/extension --with-subcharts
ksbuilder template /path/to/extension --dry-run=client
```

5. For packaging, use `ksbuilder package /path/to/extension`, then verify the package name and version against `extension.yaml`.
6. For test-cluster publishing, use `ksbuilder publish <extension-dir-or-package> --kubeconfig <path>` and verify the resulting extension resources, workloads, services, and ConfigMaps in the target cluster.
7. For KubeSphere Cloud / Marketplace submission, use `ksbuilder login`, then `ksbuilder push <extension-dir-or-package>`, then `ksbuilder list` or `ksbuilder get <name>`. Do not confuse Cloud `push` with cluster `publish`.
8. For cluster rollback, use `ksbuilder unpublish <extension-name>` against the same target kubeconfig and verify resources are removed.
9. If the task becomes an IAM/RBAC access-control test for extension APIs, use `$kubesphere-iam-rbac` for token, RoleTemplate, role-binding, and ks-apiserver permission verification.

## What To Check

- `extension.yaml`: required metadata, semantic version, name consistency, `staticFileDirectory`, icon/screenshot paths, dependencies, conditions, external dependencies, images, and `installationMode`.
- Root `values.yaml`: `global` parameters, child-chart override keys, dependency `condition` keys, image registry/pull-secret propagation, and intentional commented image tag overrides.
- `permissions.yaml`: least privilege, sensitive resources, wildcard rules, and whether requested permissions match the extension's actual resources.
- Helm charts: `charts/<dependency-name>/Chart.yaml`, child `values.yaml`, templates, dependency tags, local subchart references, and whether templates consume `.Values.global.*` where KubeSphere injects cluster/image settings.
- Frontend resources: frontend chart, `JSBundle`, reverse proxy, service URLs, static asset paths, and console bundle paths.
- Backend resources: backend chart, `APIService`, service URL, API group/version, and multi-cluster agent placement.
- Release readiness: `README.md`, `README_zh.md`, `CHANGELOG.md`, package version, release tag convention, and repository CI expectations.

## Result Report

End inspection or troubleshooting tasks with a concise report:

1. `Conclusion`: `PASS`, `FAIL`, `PARTIAL`, or `BLOCKED`.
2. `Scope`: extension name/version, source directory, target cluster or Cloud target when applicable, and test time.
3. `Checks Run`: bundled checker, ksbuilder commands, Helm/Kubernetes commands, and files inspected.
4. `Findings`: failed checks first, then warnings, each with file/path and recommended fix.
5. `Package/Publish Result`: package path, cluster resources, or Cloud submission status when applicable.
6. `Reproduce`: sanitized commands. Never include plaintext tokens, kubeconfig contents, registry credentials, or passwords.

## Resource Guide

- `scripts/check_extension.rb`: static checker for extension directories using Ruby standard library YAML.
- `references/kubesphere-extension.md`: KubeSphere extension structure, ksbuilder workflows, validation rules, release conventions, and troubleshooting notes.
