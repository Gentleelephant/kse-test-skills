#!/usr/bin/env bash
# Gate for ksbuilder lint/template.
#
# ksbuilder's builtin checks print "ERROR: ..." to stdout but still exit 0, so
# an exit code alone is not a reliable gate. This wrapper captures the output
# and fails when an ERROR line appears at all.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: lint_extension.sh [--no-template] EXTENSION_DIR

Runs `ksbuilder lint EXTENSION_DIR --with-subcharts` and, unless
--no-template is given, `ksbuilder template EXTENSION_DIR`. Fails when either
command exits non-zero or when the output contains ERROR lines.

Exit codes: 0 pass, 1 findings, 2 invalid usage or missing tools.
EOF
}

run_template=1
if [ "${1:-}" = "--no-template" ]; then
  run_template=0
  shift
fi

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

extension_dir=$1
if [ ! -d "$extension_dir" ]; then
  echo "extension directory does not exist: $extension_dir" >&2
  exit 2
fi

for tool in ksbuilder mktemp grep; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "required tool not found in PATH: $tool" >&2
    exit 2
  fi
done

workdir=$(mktemp -d "${TMPDIR:-/tmp}/ksbuilder-gate.XXXXXX")
trap 'rm -rf "$workdir"' EXIT

lint_log="$workdir/lint.log"
set +e
ksbuilder lint "$extension_dir" --with-subcharts >"$lint_log" 2>&1
lint_status=$?
set -e
cat "$lint_log"

if [ "$lint_status" -ne 0 ]; then
  echo "FAIL: ksbuilder lint exited $lint_status" >&2
  exit 1
fi

if grep -Eq '(^ERROR:|\[ERROR\])' "$lint_log"; then
  echo "FAIL: ksbuilder lint printed ERROR lines but exited 0:" >&2
  grep -En '(^ERROR:|\[ERROR\])' "$lint_log" >&2
  exit 1
fi

if [ "$run_template" -eq 1 ]; then
  template_log="$workdir/template.log"
  set +e
  ksbuilder template "$extension_dir" >"$template_log" 2>&1
  template_status=$?
  set -e

  if [ "$template_status" -ne 0 ]; then
    echo "FAIL: ksbuilder template exited $template_status" >&2
    tail -n 80 "$template_log" >&2
    exit 1
  fi
  if grep -Eq '(^ERROR:|\[ERROR\])' "$template_log"; then
    echo "FAIL: ksbuilder template printed ERROR lines:" >&2
    grep -En '(^ERROR:|\[ERROR\])' "$template_log" >&2
    exit 1
  fi
  echo "PASS: ksbuilder template rendered without errors"
fi

echo "PASS: ksbuilder lint reported no errors"
