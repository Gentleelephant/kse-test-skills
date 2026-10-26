#!/usr/bin/env bash
# Report the ksbuilder version and the capability flags this skill depends on.
#
# Usage: probe_ksbuilder.sh [KSBUILDER_BIN]
#
# Read-only: it runs only `ksbuilder version` and `ksbuilder <cmd> --help`,
# so it never touches a cluster, a kubeconfig, or the working tree.
# Prints stable key=value lines on stdout. Exit 0 when the probe ran,
# 2 on usage error or when the binary is not found.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: probe_ksbuilder.sh [KSBUILDER_BIN]

Reports owner/release capabilities that differ between ksbuilder versions:
  ksbuilder.version
  ksbuilder.publish.chartmuseum_target
  ksbuilder.publish.dry_run
  ksbuilder.package.skip_dependency_update
  ksbuilder.package.dependency_update_default
  ksbuilder.template.dependency_update
  ksbuilder.unpublish.kubeconfig_flag
EOF
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
esac

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

ksbuilder_bin=${1:-ksbuilder}

if ! command -v "$ksbuilder_bin" >/dev/null 2>&1; then
  echo "probe_ksbuilder.sh: ksbuilder binary not found: $ksbuilder_bin" >&2
  exit 2
fi

version=$("$ksbuilder_bin" version 2>/dev/null | head -n 1 || true)
version=${version:-unknown}

help_of() {
  "$ksbuilder_bin" "$1" --help 2>&1 || true
}

publish_help=$(help_of publish)
package_help=$(help_of package)
template_help=$(help_of template)
unpublish_help=$(help_of unpublish)

# Match the flag as a whole token so `--target` does not match `--target-foo`.
# Escape ERE metacharacters so a flag is never interpreted as a pattern.
has_flag() {
  local flag
  flag=$(printf '%s' "$2" | sed 's/[.[\*^$()+?{}|]/\\&/g')
  printf '%s\n' "$1" | grep -Eq -- "(^|[[:space:],])${flag}([[:space:]=,]|\$)"
}

report() {
  if has_flag "$2" "$3"; then
    printf '%s=true\n' "$1"
  else
    printf '%s=false\n' "$1"
  fi
}

printf 'ksbuilder.binary=%s\n' "$ksbuilder_bin"
printf 'ksbuilder.version=%s\n' "$version"
# `--target` alone is only evidence once chartmuseum is a documented value.
if has_flag "$publish_help" --target &&
  printf '%s\n' "$publish_help" | grep -qi 'chartmuseum'; then
  printf 'ksbuilder.publish.chartmuseum_target=true\n'
else
  printf 'ksbuilder.publish.chartmuseum_target=false\n'
fi
report ksbuilder.publish.dry_run "$publish_help" --dryRun
report ksbuilder.package.skip_dependency_update "$package_help" --skip-dependency-update
report ksbuilder.template.dependency_update "$template_help" --dependency-update
report ksbuilder.unpublish.kubeconfig_flag "$unpublish_help" --kubeconfig

# The flag exists only on versions that run `helm dependency update` by default.
if has_flag "$package_help" --skip-dependency-update; then
  printf 'ksbuilder.package.dependency_update_default=true\n'
else
  printf 'ksbuilder.package.dependency_update_default=false\n'
fi

if [ "$version" = "0.0.0" ]; then
  echo "probe_ksbuilder.sh: version is 0.0.0 (common for source builds); trust the capability flags, not the version" >&2
fi
