"""
Tests for rpm_lockfile.shell_commands.
"""

import unittest

import pytest
from rpm_lockfile.shell_commands import (
    ARCH_VALUE_RE,
    analyze_run_commands,
    resolve_bash_expansion,
)


class TestResolveBashExpansion(unittest.TestCase):
    def test_simple_braced_var(self):
        result = resolve_bash_expansion("gcc-${GCC_VERSION}", {"GCC_VERSION": "12"})
        self.assertEqual(result, "gcc-12")

    def test_simple_unbraced_var(self):
        result = resolve_bash_expansion("$VAR", {"VAR": "hello"})
        self.assertEqual(result, "hello")

    def test_default_value_when_unset(self):
        result = resolve_bash_expansion("${VAR:-fallback}", {})
        self.assertEqual(result, "fallback")

    def test_default_value_when_set(self):
        result = resolve_bash_expansion("${VAR:-fallback}", {"VAR": "actual"})
        self.assertEqual(result, "actual")

    def test_conditional_value_when_set(self):
        result = resolve_bash_expansion("pkg${VAR:+-}${VAR}", {"VAR": "1.0"})
        self.assertEqual(result, "pkg-1.0")

    def test_conditional_value_when_unset(self):
        result = resolve_bash_expansion("pkg${VAR:+-}${VAR}", {})
        self.assertEqual(result, "pkg")

    def test_unresolved_var_becomes_empty(self):
        result = resolve_bash_expansion("gcc-${UNKNOWN}", {})
        self.assertEqual(result, "gcc-")

    def test_kernel_devel_pattern_with_version(self):
        result = resolve_bash_expansion(
            "kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}",
            {"KERNEL_VERSION": "5.14.0-427.el9"},
        )
        self.assertEqual(result, "kernel-devel-5.14.0-427.el9")

    def test_kernel_devel_pattern_without_version(self):
        result = resolve_bash_expansion(
            "kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}",
            {},
        )
        self.assertEqual(result, "kernel-devel")

    def test_no_variables(self):
        result = resolve_bash_expansion("plain-package", {})
        self.assertEqual(result, "plain-package")

    def test_multiple_vars_in_one_string(self):
        result = resolve_bash_expansion(
            "${PREFIX}-${NAME}",
            {"PREFIX": "lib", "NAME": "foo"},
        )
        self.assertEqual(result, "lib-foo")


class TestAnalyzeRunCommands(unittest.TestCase):
    def test_resolves_arg_in_packages(self):
        run_values = ["dnf install -y gcc-${GCC_VERSION} gcc-c++-${GCC_VERSION}"]
        common, arch, _, _, _, _ = analyze_run_commands(run_values, env_vars={"GCC_VERSION": "12"})
        self.assertEqual(common, ["gcc-12", "gcc-c++-12"])
        self.assertEqual(arch, {})

    def test_kernel_devel_conditional_with_version(self):
        run_values = [
            "dnf install -y kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}"
            " kernel-headers${KERNEL_VERSION:+-}${KERNEL_VERSION}"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values, env_vars={"KERNEL_VERSION": "5.14.0"})
        self.assertEqual(common, ["kernel-devel-5.14.0", "kernel-headers-5.14.0"])
        self.assertEqual(arch, {})

    def test_kernel_devel_conditional_without_version(self):
        run_values = [
            "dnf install -y kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}"
            " kernel-headers${KERNEL_VERSION:+-}${KERNEL_VERSION}"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values, env_vars={})
        self.assertEqual(common, ["kernel-devel", "kernel-headers"])
        self.assertEqual(arch, {})

    def test_unresolved_var_skipped(self):
        run_values = ["dnf install -y gcc-${UNKNOWN} make"]
        common, arch, _, _, _, _ = analyze_run_commands(run_values, env_vars={})
        self.assertEqual(common, ["make"])
        self.assertEqual(arch, {})

    def test_no_env_vars_backward_compatible(self):
        run_values = ['INSTALL_PKGS="wget tar" && dnf install -y ${INSTALL_PKGS} git']
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, ["git", "tar", "wget"])
        self.assertEqual(arch, {})

    def test_arch_conditional_x86_64(self):
        run_values = [
            "dnf install -y make && if [ $(arch) = x86_64 ]; then dnf -y install kernel-rt-devel kernel-rt-modules; fi"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, ["make"])
        self.assertEqual(arch, {"x86_64": ["kernel-rt-devel", "kernel-rt-modules"]})

    def test_fallback_install_after_or(self):
        run_values = ["dnf -y install gcc-${GCC_VERSION} gcc-c++-${GCC_VERSION} || dnf -y install gcc gcc-c++"]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, ["gcc", "gcc-c++"])
        self.assertEqual(arch, {})

    def test_if_not_yum_install(self):
        run_values = [
            "if ! yum install -y prometheus-promu; then curl -s -L https://example.com/promu.tar.gz | tar -xzvf -; fi"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, ["prometheus-promu"])
        self.assertEqual(arch, {})

    def test_subshell_arch_conditional_var(self):
        run_values = [
            'ARCH_DEP_PKGS=$(if [ "$(uname -m)" != "s390x" ]; then echo -n mstflint ; fi) && '
            "yum -y install pciutils hwdata kmod $ARCH_DEP_PKGS"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertIn("mstflint", common)
        self.assertIn("pciutils", common)
        self.assertIn("hwdata", common)
        self.assertIn("kmod", common)
        self.assertEqual(arch, {})

    def test_arch_conditional_multiple_arches(self):
        run_values = [
            "if [ $(arch) = x86_64 ]; then dnf -y install kernel-rt-devel; fi && "
            "if [ $(arch) = aarch64 ]; then dnf -y install kernel-64k-devel; fi"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, [])
        self.assertEqual(arch, {"aarch64": ["kernel-64k-devel"], "x86_64": ["kernel-rt-devel"]})

    def test_hosttype_arch_conditional(self):
        run_values = [
            'PACKAGES="git gzip" && '
            "if [ $HOSTTYPE = x86_64 ]; then PACKAGES=\"$PACKAGES realtime-tests\"; fi && "
            "yum install -y $PACKAGES"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertIn("git", common)
        self.assertIn("gzip", common)
        self.assertNotIn("realtime-tests", common)
        self.assertEqual(arch.get("x86_64"), ["realtime-tests"])

    def test_glob_package_pattern_with_resolved_var(self):
        """
        Glob patterns like golang-*$VERSION* should be included after
        variable resolution (e.g. golang-*1.26*). DNF supports globs.
        """
        run_values = ['dnf install -y "golang-*$VERSION*"']
        common, arch, _, _, _, _ = analyze_run_commands(run_values, env_vars={"VERSION": "1.26"})
        self.assertIn("golang-*1.26*", common)
        self.assertEqual(arch, {})

    def test_glob_package_pattern_without_var(self):
        """
        Bare glob patterns like python3* should also be included.
        """
        run_values = ["dnf install -y python3*"]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertIn("python3*", common)
        self.assertEqual(arch, {})

    def test_double_bracket_conditional_with_quoted_var_install(self):
        run_values = [
            'if [[ "$ARCH" == "x86_64" ]]; then '
            "GRUB_PKG=grub2-efi-x64; SHIM_PKG=shim-x64; "
            'elif [[ "$ARCH" == "aarch64" ]]; then '
            "GRUB_PKG=grub2-efi-aa64; SHIM_PKG=shim-aa64; "
            "fi && "
            'dnf install -y "$GRUB_PKG" "$SHIM_PKG"'
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertEqual(common, [])
        self.assertEqual(arch.get("x86_64"), ["grub2-efi-x64", "shim-x64"])
        self.assertEqual(arch.get("aarch64"), ["grub2-efi-aa64", "shim-aa64"])

    def test_version_constraints_stripped(self):
        run_values = ["dnf install -y 'python3.12-setuptools >= 70.3.0' python3.12-pip"]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertIn("python3.12-setuptools", common)
        self.assertIn("python3.12-pip", common)
        self.assertNotIn(">=", common)
        self.assertNotIn("70.3.0", common)

    def test_file_path_package_specs_are_included(self):
        run_values = [
            "yum install --setopt=tsflags=nodocs -y "
            "e2fsprogs xfsprogs util-linux nvme-cli "
            "/usr/lib/udev/scsi_id /usr/bin/xxd"
        ]
        common, arch, _, _, _, _ = analyze_run_commands(run_values)
        self.assertIn("e2fsprogs", common)
        self.assertIn("/usr/lib/udev/scsi_id", common)
        self.assertIn("/usr/bin/xxd", common)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[ $(arch) = x86_64 ]", "x86_64"),
        ("[ $HOSTTYPE = aarch64 ]", "aarch64"),
        ("[ ${HOSTTYPE} == ppc64le ]", "ppc64le"),
        ("[ $(uname -m) = s390x ]", "s390x"),
        ("[ $(uname -p) == x86_64 ]", "x86_64"),
        ("[ $ARCH = aarch64 ]", "aarch64"),
        ('[ ${ARCH} == x86_64 ]', "x86_64"),
        ('[ $(arch) == "x86_64" ]', "x86_64"),
    ],
)
def test_arch_value_regex_matches(text, expected):
    m = ARCH_VALUE_RE.search(text)
    assert m is not None, f"No match for: {text}"
    assert m.group(1) == expected


def test_arch_value_regex_no_match():
    assert ARCH_VALUE_RE.search("echo hello") is None


class TestBuilddepParsing(unittest.TestCase):
    def test_simple_builddep(self):
        run_values = ["dnf builddep -y pkcs11-helper*"]
        _, _, _, _, builddep, _ = analyze_run_commands(run_values)
        self.assertEqual(builddep, ["pkcs11-helper*"])

    def test_builddep_with_flags(self):
        run_values = ["dnf builddep -y --skip-broken --nobest openvpn*"]
        _, _, _, _, builddep, _ = analyze_run_commands(run_values)
        self.assertEqual(builddep, ["openvpn*"])

    def test_builddep_does_not_interfere_with_install(self):
        run_values = ["dnf install -y gcc make && dnf builddep -y pkcs11-helper*"]
        common, arch, _, _, builddep, _ = analyze_run_commands(run_values)
        self.assertEqual(common, ["gcc", "make"])
        self.assertEqual(arch, {})
        self.assertEqual(builddep, ["pkcs11-helper*"])


class TestModuleParsing(unittest.TestCase):
    def test_module_install(self):
        run_values = ["dnf module install -y nodejs:18/development"]
        _, _, _, _, _, modules = analyze_run_commands(run_values)
        self.assertEqual(modules, ["nodejs:18/development"])

    def test_module_enable(self):
        run_values = ["dnf module enable -y nodejs:18"]
        _, _, _, _, _, modules = analyze_run_commands(run_values)
        self.assertEqual(modules, ["nodejs:18"])

    def test_module_without_stream_ignored(self):
        run_values = ["dnf module enable -y nodejs"]
        _, _, _, _, _, modules = analyze_run_commands(run_values)
        self.assertEqual(modules, [])
