# frozen_string_literal: true

require "fileutils"
require "json"
require "minitest/autorun"
require "open3"
require "tmpdir"

SCRIPT = File.expand_path("../scripts/check_extension.rb", __dir__)

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
