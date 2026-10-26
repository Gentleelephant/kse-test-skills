# frozen_string_literal: true

require "fileutils"
require "minitest/autorun"
require "open3"
require "shellwords"
require "tmpdir"

PROBE_SCRIPT = File.expand_path("../scripts/probe_ksbuilder.sh", __dir__)

class ProbeKsbuilderTest < Minitest::Test
  # Build a fake ksbuilder that answers only `version` and `<cmd> --help`.
  # Help lines are emitted with printf so the generated script has no nested
  # heredocs whose indentation could break the closing delimiter.
  def fake_ksbuilder(dir, version:, helps:)
    path = "#{dir}/ksbuilder"
    body = +"#!/usr/bin/env bash\n"
    body << "if [ \"\$1\" = version ]; then printf '%s\\n' #{Shellwords.escape(version)}; exit 0; fi\n"
    body << "case \"\$1:\$2\" in\n"
    helps.each do |command, lines|
      body << "  #{command}:--help)\n"
      lines.each { |line| body << "    printf '%s\\n' #{Shellwords.escape(line)}\n" }
      body << "    ;;\n"
    end
    body << "  *)\n"
    body << "    echo \"unexpected command: \$1 \$2\" >&2\n"
    body << "    exit 1\n"
    body << "    ;;\n"
    body << "esac\n"
    File.write(path, body)
    File.chmod(0o755, path)
    path
  end

  def run_probe(*args)
    Open3.capture3(PROBE_SCRIPT, *args)
  end

  def parse(stdout)
    stdout.lines.each_with_object({}) do |line, result|
      key, value = line.strip.split("=", 2)
      result[key] = value if key && value
    end
  end

  def v047_helps
    {
      "publish" => [
        "Usage:", "  ksbuilder publish [flags]", "", "Flags:",
        "      --dryRun              generate the local template without applying to the cluster",
        "      --output string       the output path of the local template",
        "      --kubeconfig string   kubeconfig file path of the target cluster"
      ],
      "package" => [
        "Usage:", "  ksbuilder package [flags]", "", "Flags:",
        "  -h, --help   help for package"
      ],
      "template" => [
        "Usage:", "  ksbuilder template [flags]", "", "Flags:",
        "      --dependency-update   update dependencies if they are missing"
      ],
      "unpublish" => [
        "Usage:", "  ksbuilder unpublish [flags]", "", "Flags:",
        "      --kubeconfig string   kubeconfig file path of the target cluster"
      ]
    }
  end

  def master_helps
    {
      "publish" => [
        "Usage:", "  ksbuilder publish [flags]", "", "Flags:",
        "      --dryRun              generate the local template without applying to the cluster",
        "      --target string       publish target: cluster (default) or chartmuseum",
        "      --repo string         chartmuseum URL (required when --target=chartmuseum)"
      ],
      "package" => [
        "Usage:", "  ksbuilder package [flags]", "", "Flags:",
        "      --skip-dependency-update   skip running helm dependency update before packaging"
      ],
      "template" => [
        "Usage:", "  ksbuilder template [flags]", "", "Flags:",
        "      --dependency-update   update dependencies if they are missing"
      ],
      "unpublish" => [
        "Usage:", "  ksbuilder unpublish [flags]", "", "Global Flags:",
        "      --kubeconfig string   path to the kubeconfig file for cluster access"
      ]
    }
  end

  def test_v047_capabilities_are_reported_from_help
    Dir.mktmpdir do |root|
      bin = fake_ksbuilder(root, version: "0.0.0", helps: v047_helps)
      stdout, stderr, status = run_probe(bin)
      assert_equal 0, status.exitstatus, stderr
      result = parse(stdout)
      assert_equal "0.0.0", result["ksbuilder.version"]
      assert_equal "false", result["ksbuilder.publish.chartmuseum_target"]
      assert_equal "true", result["ksbuilder.publish.dry_run"]
      assert_equal "false", result["ksbuilder.package.skip_dependency_update"]
      assert_equal "false", result["ksbuilder.package.dependency_update_default"]
      assert_equal "true", result["ksbuilder.template.dependency_update"]
      assert_equal "true", result["ksbuilder.unpublish.kubeconfig_flag"]
      assert_includes stderr, "version is 0.0.0"
    end
  end

  def test_master_capabilities_include_chartmuseum_and_dependency_update
    Dir.mktmpdir do |root|
      bin = fake_ksbuilder(root, version: "1.4.0", helps: master_helps)
      stdout, stderr, status = run_probe(bin)
      assert_equal 0, status.exitstatus, stderr
      result = parse(stdout)
      assert_equal "1.4.0", result["ksbuilder.version"]
      assert_equal "true", result["ksbuilder.publish.chartmuseum_target"]
      assert_equal "true", result["ksbuilder.package.skip_dependency_update"]
      assert_equal "true", result["ksbuilder.package.dependency_update_default"]
      assert_equal "true", result["ksbuilder.unpublish.kubeconfig_flag"]
      refute_includes stderr, "version is 0.0.0"
    end
  end

  def test_target_flag_without_chartmuseum_is_not_reported_as_supported
    Dir.mktmpdir do |root|
      helps = v047_helps.merge(
        "publish" => [
          "Usage:", "  ksbuilder publish [flags]", "", "Flags:",
          "      --target string   publish target: cluster"
        ]
      )
      bin = fake_ksbuilder(root, version: "1.0.0", helps: helps)
      stdout, stderr, status = run_probe(bin)
      assert_equal 0, status.exitstatus, stderr
      assert_equal "false", parse(stdout)["ksbuilder.publish.chartmuseum_target"]
    end
  end

  def test_missing_binary_and_usage_errors_exit_2
    Dir.mktmpdir do |root|
      _, stderr, status = run_probe("#{root}/missing-ksbuilder")
      assert_equal 2, status.exitstatus
      assert_includes stderr, "not found"

      _, stderr, status = run_probe("a", "b")
      assert_equal 2, status.exitstatus
      assert_includes stderr, "Usage:"
    end
  end
end
