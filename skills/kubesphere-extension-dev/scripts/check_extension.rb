#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"
require "pathname"
require "date"

Check = Struct.new(:level, :code, :message)

class ExtensionChecker
  SEMVER = /\A\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\z/
  NAME = /\A[a-z0-9][a-z0-9-]*[a-z0-9]\z|\A[a-z0-9]\z/
  SENSITIVE_RESOURCES = %w[
    secrets
    clusterrolebindings
    rolebindings
    mutatingwebhookconfigurations
    validatingwebhookconfigurations
    customresourcedefinitions
  ].freeze
  KUBESPHERE_GLOBAL_KEYS = %w[
    imageRegistry
    imagePullSecrets
    clusterInfo
    nodeSelector
  ].freeze

  def initialize(root)
    @root = Pathname.new(root).expand_path
    @checks = []
  end

  def run
    unless @root.directory?
      fail_check("root.missing", "Extension directory does not exist: #{@root}")
      return report
    end

    check_extension_yaml
    check_permissions_yaml
    check_repository_docs
    report
  end

  private

  def check_extension_yaml
    path = @root.join("extension.yaml")
    unless path.file?
      fail_check("extension_yaml.missing", "Missing extension.yaml")
      return
    end

    data = load_yaml_file(path)
    return unless data.is_a?(Hash)

    pass("extension_yaml.present", "Found extension.yaml")
    required = %w[
      apiVersion name version displayName description category provider
      staticFileDirectory icon kubeVersion ksVersion installationMode
    ]
    required.each do |key|
      value = data[key]
      if blank?(value)
        fail_check("extension_yaml.#{key}.missing", "extension.yaml missing required field #{key}")
      else
        pass("extension_yaml.#{key}.present", "extension.yaml has #{key}")
      end
    end

    check_name(data["name"])
    check_version(data["version"])
    check_i18n("displayName", data["displayName"])
    check_i18n("description", data["description"])
    check_provider(data["provider"])
    check_static_files(data)
    check_charts_and_dependencies(data)
    check_values_yaml(data)
    check_external_dependencies(data["externalDependencies"])
    check_images(data["images"])
  end

  def check_name(name)
    return if blank?(name)

    if name.match?(NAME)
      pass("extension_yaml.name.format", "Extension name uses lowercase DNS-style characters")
    else
      fail_check("extension_yaml.name.format", "Extension name should use lowercase letters, digits, and hyphens: #{name.inspect}")
    end

    warn("extension_yaml.name.length", "Extension name is longer than 32 characters; generated resource names may become too long") if name.length > 32
  end

  def check_version(version)
    return if blank?(version)

    if version.to_s.match?(SEMVER)
      pass("extension_yaml.version.semver", "Version looks semantic: #{version}")
    else
      fail_check("extension_yaml.version.semver", "Version should look like 1.2.3 or 1.2.3-rc.1: #{version.inspect}")
    end
  end

  def check_i18n(field, value)
    unless value.is_a?(Hash)
      warn("extension_yaml.#{field}.i18n", "#{field} should usually be a language map with en and zh entries")
      return
    end

    pass("extension_yaml.#{field}.en", "#{field} has en") unless blank?(value["en"])
    warn("extension_yaml.#{field}.en", "#{field} should include en fallback text") if blank?(value["en"])
    warn("extension_yaml.#{field}.zh", "#{field} should include zh text for Chinese environments") if blank?(value["zh"])
  end

  def check_provider(provider)
    unless provider.is_a?(Hash)
      warn("extension_yaml.provider.shape", "provider should include localized provider metadata")
      return
    end

    %w[en zh].each do |locale|
      info = provider[locale]
      warn("extension_yaml.provider.#{locale}", "provider should include #{locale} metadata") unless info.is_a?(Hash) && !blank?(info["name"])
    end
  end

  def check_static_files(data)
    static_dir_name = data["staticFileDirectory"]
    static_dir = blank?(static_dir_name) ? nil : @root.join(static_dir_name.to_s)

    if static_dir && static_dir.directory?
      pass("static.dir.present", "Static directory exists: #{static_dir.relative_path_from(@root)}")
    elsif static_dir
      fail_check("static.dir.missing", "staticFileDirectory does not exist: #{static_dir.relative_path_from(@root)}")
    end

    check_relative_file("extension_yaml.icon", data["icon"])

    Array(data["screenshots"]).each_with_index do |screenshot, index|
      check_relative_file("extension_yaml.screenshots.#{index}", screenshot)
    end
  end

  def check_relative_file(code, value)
    return if blank?(value)
    return warn(code, "Remote URL not checked: #{value}") if value.to_s.match?(%r{\Ahttps?://})

    rel = value.to_s.sub(%r{\A\./}, "")
    path = @root.join(rel)
    if path.file?
      pass("#{code}.exists", "Referenced file exists: #{rel}")
    else
      fail_check("#{code}.missing", "Referenced file is missing: #{rel}")
    end
  end

  def check_charts_and_dependencies(data)
    charts_dir = @root.join("charts")
    dependency_names = Array(data["dependencies"]).map { |dep| dep["name"] if dep.is_a?(Hash) }.compact
    chart_names = charts_dir.directory? ? charts_dir.children.select(&:directory?).map { |p| p.basename.to_s }.sort : []

    if charts_dir.directory?
      pass("charts.dir.present", "Found charts directory with #{chart_names.length} subchart(s)")
    else
      warn("charts.dir.missing", "No charts directory found; this is unusual for installable extensions")
    end

    chart_names.each do |chart|
      chart_root = charts_dir.join(chart)
      check_chart(chart, chart_root)
    end

    dependency_names.each do |name|
      if chart_names.include?(name)
        pass("dependencies.#{name}.chart", "Dependency has matching local subchart: #{name}")
      else
        warn("dependencies.#{name}.chart", "Dependency has no matching charts/#{name} subchart; confirm it is generated, vendored elsewhere, or intentionally external")
      end
    end

    (chart_names - dependency_names).each do |name|
      warn("charts.#{name}.dependency", "Subchart charts/#{name} is not listed in extension.yaml dependencies")
    end

    check_dependency_tags(data["dependencies"], data["installationMode"])
  end

  def check_chart(name, chart_root)
    %w[Chart.yaml values.yaml].each do |file|
      path = chart_root.join(file)
      if path.file?
        pass("charts.#{name}.#{file}", "charts/#{name}/#{file} exists")
      else
        warn("charts.#{name}.#{file}", "charts/#{name}/#{file} is missing")
      end
    end

    warn("charts.#{name}.templates", "charts/#{name}/templates is missing") unless chart_root.join("templates").directory?
  end

  def check_dependency_tags(dependencies, installation_mode)
    deps = Array(dependencies).select { |dep| dep.is_a?(Hash) }
    tags_by_dep = deps.to_h { |dep| [dep["name"], Array(dep["tags"]).map(&:to_s)] }
    all_tags = tags_by_dep.values.flatten

    tags_by_dep.each do |name, tags|
      if tags.empty?
        warn("dependencies.#{name}.tags", "Dependency #{name} has no tags; expected extension or agent when scheduling matters")
      elsif (tags & %w[extension agent]).empty?
        warn("dependencies.#{name}.tags", "Dependency #{name} tags do not include extension or agent: #{tags.join(', ')}")
      else
        pass("dependencies.#{name}.tags", "Dependency #{name} has scheduling tag(s): #{tags.join(', ')}")
      end
    end

    case installation_mode
    when "Multicluster"
      warn("installationMode.multicluster.agent", "Multicluster extension should normally have at least one agent dependency") unless all_tags.include?("agent")
    when "HostOnly"
      warn("installationMode.hostonly.agent", "HostOnly extension has agent dependency tag; confirm this is intentional") if all_tags.include?("agent")
    when nil
      # Already reported as required field.
    else
      fail_check("installationMode.value", "installationMode should be HostOnly or Multicluster, got #{installation_mode.inspect}")
    end
  end

  def check_values_yaml(extension_data)
    path = @root.join("values.yaml")
    unless path.file?
      warn("values_yaml.missing", "Missing root values.yaml; extension roots normally provide global settings and child chart overrides")
      return
    end

    values = load_yaml_file(path)
    return unless values.is_a?(Hash)

    pass("values_yaml.present", "Found root values.yaml")
    check_root_global_values(values)
    check_values_dependency_keys(values, extension_data["dependencies"])
    check_commented_image_tags(path)
    check_child_global_consumption(values, extension_data["dependencies"])
  end

  def check_root_global_values(values)
    global = values["global"]
    unless global.is_a?(Hash)
      warn("values_yaml.global.missing", "Root values.yaml should usually define global for KubeSphere image registry, pull secrets, and cluster info")
      return
    end

    pass("values_yaml.global.present", "Root values.yaml defines global")
    %w[imageRegistry clusterInfo].each do |key|
      if global.key?(key)
        pass("values_yaml.global.#{key}", "Root global defines #{key}")
      else
        warn("values_yaml.global.#{key}", "Root global should define #{key} when child charts need KubeSphere runtime overrides")
      end
    end

    %w[imagePullSecrets nodeSelector].each do |key|
      pass("values_yaml.global.#{key}", "Root global defines #{key}") if global.key?(key)
    end
  end

  def check_values_dependency_keys(values, dependencies)
    Array(dependencies).each do |dep|
      next unless dep.is_a?(Hash)

      name = dep["name"]
      condition = dep["condition"].to_s
      condition_root = condition.split(".").first unless condition.empty?

      if !blank?(name) && values.key?(name)
        pass("values_yaml.dependencies.#{name}", "Root values.yaml has child override key #{name}")
      elsif !blank?(condition_root) && values.key?(condition_root)
        pass("values_yaml.dependencies.#{name || condition_root}.condition", "Root values.yaml has condition key #{condition_root} for #{condition}")
        warn("values_yaml.dependencies.#{name}.override", "Dependency #{name} has no root override key named #{name}; confirm the condition alias is enough or add #{name}: for child values")
      elsif !blank?(name)
        warn("values_yaml.dependencies.#{name}", "Root values.yaml has no top-level key for dependency #{name}; child chart defaults may be used without extension-level overrides")
      end
    end
  end

  def check_commented_image_tags(path)
    commented = path.readlines.grep(/^\s*#\s*tag\s*:/)
    return if commented.empty?

    pass("values_yaml.image_tags.commented", "Root values.yaml has #{commented.length} commented image tag override(s); treat them as intentional inheritance from child values or Chart.appVersion")
  end

  def check_child_global_consumption(values, dependencies)
    return unless values["global"].is_a?(Hash)

    Array(dependencies).each do |dep|
      next unless dep.is_a?(Hash)

      name = dep["name"]
      next if blank?(name)

      chart_root = @root.join("charts", name)
      next unless chart_root.directory?

      used = global_keys_used_by_chart(chart_root)
      next if used.empty?

      used.each do |key|
        if values["global"].key?(key)
          pass("values_yaml.global.#{key}.used_by_#{name}", "charts/#{name} consumes global.#{key} and root global defines it")
        else
          warn("values_yaml.global.#{key}.used_by_#{name}", "charts/#{name} consumes global.#{key}, but root values.yaml global does not define it")
        end
      end
    end
  end

  def global_keys_used_by_chart(chart_root)
    keys = []
    chart_root.glob("**/*.{yaml,yml,tpl}").each do |file|
      next unless file.file?

      text = file.read
      text.scan(/(?:\.Values\.global|global)\.([A-Za-z0-9_]+)/) do |match|
        key = match.first
        keys << key if KUBESPHERE_GLOBAL_KEYS.include?(key)
      end
    end
    keys.uniq.sort
  end

  def check_external_dependencies(deps)
    return if deps.nil?

    Array(deps).each_with_index do |dep, index|
      unless dep.is_a?(Hash)
        warn("externalDependencies.#{index}.shape", "externalDependencies entry should be a map")
        next
      end

      %w[name type version required].each do |key|
        warn("externalDependencies.#{index}.#{key}", "external dependency #{index} missing #{key}") if dep[key].nil?
      end
    end
  end

  def check_images(images)
    if images.nil?
      warn("extension_yaml.images.missing", "images is missing; release and mirroring workflows may not know required images")
      return
    end

    Array(images).each_with_index do |image, index|
      warn("extension_yaml.images.#{index}", "Image entry should be a non-empty string") unless image.is_a?(String) && !image.strip.empty?
    end
  end

  def check_permissions_yaml
    path = @root.join("permissions.yaml")
    unless path.file?
      warn("permissions_yaml.missing", "Missing permissions.yaml")
      return
    end

    docs = load_yaml_stream(path)
    return unless docs

    pass("permissions_yaml.present", "Found permissions.yaml")
    docs.compact.each_with_index do |doc, index|
      unless doc.is_a?(Hash)
        warn("permissions_yaml.doc#{index}.shape", "permissions.yaml document #{index + 1} is not a map")
        next
      end

      kind = doc["kind"]
      rules = doc["rules"]
      warn("permissions_yaml.doc#{index}.kind", "permissions.yaml document #{index + 1} missing kind") if blank?(kind)
      warn("permissions_yaml.doc#{index}.rules", "permissions.yaml document #{index + 1} missing rules") unless rules.is_a?(Array)

      Array(rules).each_with_index do |rule, rule_index|
        check_permission_rule(index, rule_index, kind, rule)
      end
    end
  end

  def check_permission_rule(doc_index, rule_index, kind, rule)
    unless rule.is_a?(Hash)
      warn("permissions_yaml.doc#{doc_index}.rule#{rule_index}.shape", "Permission rule is not a map")
      return
    end

    verbs = Array(rule["verbs"]).map(&:to_s)
    resources = Array(rule["resources"]).map(&:to_s)

    warn("permissions_yaml.doc#{doc_index}.rule#{rule_index}.verbs", "#{kind || 'Permission'} rule uses wildcard verbs") if verbs.include?("*")
    warn("permissions_yaml.doc#{doc_index}.rule#{rule_index}.resources", "#{kind || 'Permission'} rule uses wildcard resources") if resources.include?("*")

    sensitive = resources & SENSITIVE_RESOURCES
    sensitive.each do |resource|
      warn("permissions_yaml.doc#{doc_index}.rule#{rule_index}.sensitive", "#{kind || 'Permission'} rule requests sensitive resource: #{resource}")
    end
  end

  def check_repository_docs
    %w[README.md README_zh.md CHANGELOG.md].each do |file|
      path = @root.join(file)
      if path.file?
        pass("docs.#{file}", "#{file} exists")
      else
        warn("docs.#{file}", "#{file} is missing")
      end
    end
  end

  def load_yaml_file(path)
    YAML.safe_load(path.read, permitted_classes: [Date, Time], aliases: true)
  rescue Psych::Exception => e
    fail_check("yaml.invalid", "#{path.relative_path_from(@root)} is invalid YAML: #{e.message}")
    nil
  end

  def load_yaml_stream(path)
    YAML.load_stream(path.read)
  rescue Psych::Exception => e
    fail_check("yaml.invalid", "#{path.relative_path_from(@root)} is invalid YAML: #{e.message}")
    nil
  end

  def blank?(value)
    value.nil? || (value.respond_to?(:empty?) && value.empty?)
  end

  def pass(code, message)
    @checks << Check.new("PASS", code, message)
  end

  def warn(code, message)
    @checks << Check.new("WARN", code, message)
  end

  def fail_check(code, message)
    @checks << Check.new("FAIL", code, message)
  end

  def report
    fail_count = @checks.count { |check| check.level == "FAIL" }
    warn_count = @checks.count { |check| check.level == "WARN" }
    pass_count = @checks.count { |check| check.level == "PASS" }

    puts "Extension: #{@root}"
    puts "Summary: PASS=#{pass_count} WARN=#{warn_count} FAIL=#{fail_count}"
    puts

    @checks.sort_by { |check| [%w[FAIL WARN PASS].index(check.level), check.code] }.each do |check|
      puts "#{check.level}\t#{check.code}\t#{check.message}"
    end

    fail_count.zero? ? 0 : 1
  end
end

if ARGV.length != 1
  warn "Usage: ruby #{File.basename($PROGRAM_NAME)} <extension-dir>"
  exit 2
end

exit ExtensionChecker.new(ARGV[0]).run
