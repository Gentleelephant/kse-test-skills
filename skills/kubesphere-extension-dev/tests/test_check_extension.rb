# frozen_string_literal: true

require "fileutils"
require "json"
require "minitest/autorun"
require "open3"
require "tmpdir"

SCRIPT = File.expand_path("../scripts/check_extension.rb", __dir__)
LINT_SCRIPT = File.expand_path("../scripts/lint_extension.sh", __dir__)

class ExtensionCheckerTest < Minitest::Test
  def valid_extension(root)
    FileUtils.mkdir_p("#{root}/static")
    FileUtils.mkdir_p("#{root}/charts/backend/templates")
    File.write("#{root}/static/icon.svg", "<svg/>")
    File.write("#{root}/README.md", "# Example")
    File.write("#{root}/README_zh.md", "# 示例")
    File.write("#{root}/CHANGELOG.md", "# Changelog")
    File.write(
      "#{root}/extension.yaml",
      <<~YAML
        apiVersion: kubesphere.io/v1alpha1
        name: example
        version: 1.0.0
        displayName: {en: Example, zh: 示例}
        description: {en: Example extension, zh: 示例扩展}
        category: observability
        provider:
          en: {name: Example}
          zh: {name: 示例}
        staticFileDirectory: static
        icon: static/icon.svg
        kubeVersion: ">=1.21.0"
        ksVersion: ">=4.1.0"
        installationMode: HostOnly
        dependencies:
          - name: backend
            tags: [extension]
        images: ["example/backend:v1"]
      YAML
    )
    File.write(
      "#{root}/values.yaml",
      <<~YAML
        global:
          imageRegistry: ""
          imagePullSecrets: []
          clusterInfo: {}
        backend: {}
      YAML
    )
    File.write("#{root}/charts/backend/Chart.yaml", "apiVersion: v2\nname: backend\nversion: 1.0.0\n")
    File.write("#{root}/charts/backend/values.yaml", "image: {repository: example/backend}\n")
    File.write("#{root}/charts/backend/templates/deployment.yaml", "image: {{ .Values.global.imageRegistry }}/example/backend\n")
    File.write(
      "#{root}/permissions.yaml",
      <<~YAML
        kind: ClusterRole
        rules:
          - apiGroups: [example.io]
            resources: [widgets]
            verbs: [get, list]
      YAML
    )
  end

  def run_cli(*args)
    Open3.capture3("ruby", SCRIPT, *args)
  end

  def test_help_and_missing_input_exit_codes
    stdout, = run_cli("--help")
    assert_includes stdout, "--format FORMAT"
    _, stderr, status = run_cli("/does/not/exist")
    assert_equal 2, status.exitstatus
    assert_includes stderr, "does not exist"
  end

  def test_valid_extension_supports_json_output
    Dir.mktmpdir do |root|
      valid_extension(root)
      stdout, stderr, status = run_cli("--format", "json", root)
      assert_equal 0, status.exitstatus, "#{stderr}\n#{stdout}"
      report = JSON.parse(stdout)
      assert_equal root, report["extension"]
      assert_equal 0, report.dig("summary", "FAIL")
      assert report["checks"].any? { |item| item["code"] == "extension_yaml.present" }
    end
  end

  def test_missing_manifest_and_invalid_yaml_are_findings
    Dir.mktmpdir do |root|
      _, _, status = run_cli(root)
      assert_equal 1, status.exitstatus
      File.write("#{root}/extension.yaml", "name: [broken\n")
      stdout, _, status = run_cli("--format", "json", root)
      assert_equal 1, status.exitstatus
      assert JSON.parse(stdout)["checks"].any? { |item| item["code"] == "yaml.invalid" }
    end
  end

  def fake_ksbuilder(bin_dir, body)
    path = "#{bin_dir}/ksbuilder"
    File.write(path, "#!/usr/bin/env bash\n#{body}\n")
    File.chmod(0o755, path)
    path
  end

  def run_lint(*args, path: ENV.fetch("PATH"))
    Open3.capture3({ "PATH" => path }, LINT_SCRIPT, *args)
  end

  def test_lint_gate_fails_on_error_lines_despite_zero_exit
    Dir.mktmpdir do |root|
      bin_dir = "#{root}/bin"
      extension_dir = "#{root}/ext"
      FileUtils.mkdir_p([bin_dir, extension_dir])
      fake_ksbuilder(
        bin_dir,
        <<~BASH
          if [ "$1" = "lint" ]; then
            echo "ERROR: invalid extension name \\"Bad_Name\\":"
            exit 0
          fi
          exit 0
        BASH
      )

      _, stderr, status = run_lint("--no-template", extension_dir, path: "#{bin_dir}:#{ENV.fetch('PATH')}")
      assert_equal 1, status.exitstatus
      assert_includes stderr, "printed ERROR lines but exited 0"
    end
  end

  def test_lint_gate_passes_clean_lint_and_reports_template_failure
    Dir.mktmpdir do |root|
      bin_dir = "#{root}/bin"
      extension_dir = "#{root}/ext"
      FileUtils.mkdir_p([bin_dir, extension_dir])
      fake_ksbuilder(
        bin_dir,
        <<~BASH
          if [ "$1" = "lint" ]; then
            echo "1 chart(s) linted, 0 chart(s) failed"
            exit 0
          fi
          if [ "$MODE" = "template-fail" ]; then
            echo "Error: failed to render template" >&2
            exit 1
          fi
          echo "rendered"
        BASH
      )
      path = "#{bin_dir}:#{ENV.fetch('PATH')}"

      stdout, _, status = run_lint("--no-template", extension_dir, path: path)
      assert_equal 0, status.exitstatus
      assert_includes stdout, "PASS: ksbuilder lint reported no errors"

      _, stderr, status = run_lint("--no-template", "/does/not/exist", path: path)
      assert_equal 2, status.exitstatus
      assert_includes stderr, "does not exist"

      _, stderr, status = Open3.capture3(
        { "PATH" => path, "MODE" => "template-fail" }, LINT_SCRIPT, extension_dir
      )
      assert_equal 1, status.exitstatus
      assert_includes stderr, "ksbuilder template exited 1"
    end
  end

  def test_metadata_required_fields_match_ksbuilder
    Dir.mktmpdir do |root|
      File.write(
        "#{root}/extension.yaml",
        <<~YAML
          apiVersion: kubesphere.io/v1alpha1
          name: minimal
          version: 1.0.0
          displayName: {en: Minimal}
          description: {en: Minimal extension}
          category: observability
          provider: {en: {name: Example}}
          icon: "data:image/png;base64,iVBORw0KGgo="
        YAML
      )

      stdout, stderr, status = run_cli("--format", "json", root)
      assert_equal 0, status.exitstatus, "#{stderr}\n#{stdout}"
      report = JSON.parse(stdout)
      assert_equal 0, report.dig("summary", "FAIL")
      codes = report["checks"].map { |item| item["code"] }
      assert_includes codes, "extension_yaml.icon.embedded"
      %w[staticFileDirectory kubeVersion ksVersion installationMode].each do |field|
        assert_includes codes, "extension_yaml.#{field}.missing"
      end
      refute_includes codes, "extension_yaml.icon.missing"
    end
  end

  def test_non_image_data_uri_is_checked_as_a_local_path
    Dir.mktmpdir do |root|
      valid_extension(root)
      manifest = "#{root}/extension.yaml"
      File.write(
        manifest,
        File.read(manifest).sub(
          "icon: static/icon.svg",
          'icon: "data:text/plain;base64,SGVsbG8="'
        )
      )

      stdout, stderr, status = run_cli("--format", "json", root)
      assert_equal 1, status.exitstatus, "#{stderr}\n#{stdout}"
      report = JSON.parse(stdout)
      codes = report["checks"].map { |item| item["code"] }
      # ksbuilder only embeds data:image; other data: URIs are opened as paths.
      assert_includes codes, "extension_yaml.icon.embedded"
      assert_includes codes, "extension_yaml.icon.missing"
    end
  end

  def test_unknown_category_is_a_warning
    Dir.mktmpdir do |root|
      valid_extension(root)
      File.write(
        "#{root}/extension.yaml",
        File.read("#{root}/extension.yaml").sub("category: observability", "category: database-tools")
      )

      stdout, _, status = run_cli("--format", "json", root)
      assert_equal 0, status.exitstatus
      report = JSON.parse(stdout)
      check = report["checks"].find { |item| item["code"] == "extension_yaml.category.unknown" }
      refute_nil check
      assert_equal "WARN", check["level"]
    end
  end

  def test_dependency_installation_mode_globals_and_sensitive_permissions
    Dir.mktmpdir do |root|
      valid_extension(root)
      extension = File.read("#{root}/extension.yaml")
        .sub("installationMode: HostOnly", "installationMode: Multicluster")
        .sub("tags: [extension]", "tags: [custom]")
      File.write("#{root}/extension.yaml", extension)
      File.write("#{root}/values.yaml", "backend: {}\n")
      File.write(
        "#{root}/permissions.yaml",
        "kind: ClusterRole\nrules:\n- apiGroups: ['*']\n  resources: [secrets]\n  verbs: ['*']\n"
      )

      stdout, stderr, status = run_cli("--format", "json", root)
      assert_equal 0, status.exitstatus, "#{stderr}\n#{stdout}"
      codes = JSON.parse(stdout)["checks"].map { |item| item["code"] }
      assert_includes codes, "installationMode.multicluster.agent"
      assert_includes codes, "values_yaml.global.missing"
      assert codes.any? { |code| code.end_with?(".sensitive") }
      assert codes.any? { |code| code.end_with?(".verbs") }
    end
  end
end
