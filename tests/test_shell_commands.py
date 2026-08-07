"""
Tests for rpm_lockfile.shell_commands.
"""

import unittest

import pytest

from rpm_lockfile.shell_commands import (
    ARCH_NEQ_VALUE_RE,
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
        result = analyze_run_commands(run_values, env_vars={"GCC_VERSION": "12"})
        self.assertEqual(result.packages, ["gcc-12", "gcc-c++-12"])

    def test_kernel_devel_conditional_with_version(self):
        run_values = [
            (
                "dnf install -y kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}"
                " kernel-headers${KERNEL_VERSION:+-}${KERNEL_VERSION}"
            )
        ]
        result = analyze_run_commands(run_values, env_vars={"KERNEL_VERSION": "5.14.0"})
        self.assertEqual(
            result.packages, ["kernel-devel-5.14.0", "kernel-headers-5.14.0"]
        )

    def test_kernel_devel_conditional_without_version(self):
        run_values = [
            (
                "dnf install -y kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION}"
                " kernel-headers${KERNEL_VERSION:+-}${KERNEL_VERSION}"
            )
        ]
        result = analyze_run_commands(run_values, env_vars={})
        self.assertEqual(result.packages, ["kernel-devel", "kernel-headers"])

    def test_unresolved_var_skipped(self):
        run_values = ["dnf install -y gcc-${UNKNOWN} make"]
        result = analyze_run_commands(run_values, env_vars={})
        self.assertEqual(result.packages, ["make"])

    def test_no_env_vars_backward_compatible(self):
        run_values = ['INSTALL_PKGS="wget tar" && dnf install -y ${INSTALL_PKGS} git']
        result = analyze_run_commands(run_values)
        self.assertEqual(result.packages, ["git", "tar", "wget"])

    def test_arch_conditional_x86_64(self):
        run_values = [
            "dnf install -y make && if [ $(arch) = x86_64 ]; then dnf -y install kernel-rt-devel kernel-rt-modules; fi"
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertEqual(
            result.packages, ["kernel-rt-devel", "kernel-rt-modules", "make"]
        )
        result = analyze_run_commands(run_values, arch="aarch64")
        self.assertEqual(result.packages, ["make"])

    def test_reinstall_packages_parsed(self):
        run_values = ["dnf -y reinstall tzdata && dnf clean all"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.reinstall_targets, ["tzdata"])
        self.assertEqual(result.packages, [])

    def test_reinstall_multiple_packages_parsed(self):
        run_values = ["microdnf -y reinstall tzdata glibc && microdnf clean all"]
        result = analyze_run_commands(run_values)
        self.assertEqual(sorted(result.reinstall_targets), ["glibc", "tzdata"])
        self.assertEqual(result.packages, [])

    def test_reinstall_mixed_with_install(self):
        run_values = [
            "microdnf -y install openssl && microdnf -y reinstall tzdata && microdnf clean all"
        ]
        result = analyze_run_commands(run_values)
        self.assertIn("openssl", result.packages)
        self.assertNotIn("tzdata", result.packages)
        self.assertEqual(result.reinstall_targets, ["tzdata"])

    def test_fallback_install_after_or(self):
        run_values = [
            "dnf -y install gcc-${GCC_VERSION} gcc-c++-${GCC_VERSION} || dnf -y install gcc gcc-c++"
        ]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.packages, ["gcc", "gcc-c++"])

    def test_if_not_yum_install(self):
        run_values = [
            "if ! yum install -y prometheus-promu; then curl -s -L https://example.com/promu.tar.gz | tar -xzvf -; fi"
        ]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.packages, ["prometheus-promu"])

    def test_subshell_arch_conditional_var(self):
        run_values = [
            (
                'ARCH_DEP_PKGS=$(if [ "$(uname -m)" != "s390x" ]; then echo -n mstflint ; fi) && '
                "yum -y install pciutils hwdata kmod $ARCH_DEP_PKGS"
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertIn("mstflint", result.packages)
        self.assertIn("pciutils", result.packages)
        self.assertIn("hwdata", result.packages)
        self.assertIn("kmod", result.packages)

        result = analyze_run_commands(run_values, arch="s390x")
        self.assertNotIn("mstflint", result.packages)
        self.assertIn("pciutils", result.packages)

    def test_subshell_arch_conditional_var_eq(self):
        run_values = [
            (
                'SPECIAL=$(if [ "$(uname -m)" == "x86_64" ]; then echo -n intel-pkg ; fi) && '
                "yum -y install base-pkg $SPECIAL"
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertIn("intel-pkg", result.packages)
        self.assertIn("base-pkg", result.packages)

        result = analyze_run_commands(run_values, arch="s390x")
        self.assertNotIn("intel-pkg", result.packages)
        self.assertIn("base-pkg", result.packages)

    def test_subshell_no_arch_condition(self):
        run_values = ["EXTRA=$(echo extra-pkg) && yum -y install base-pkg $EXTRA"]
        result = analyze_run_commands(run_values)
        self.assertIn("extra-pkg", result.packages)
        self.assertIn("base-pkg", result.packages)

    def test_subshell_arch_conditional_var_go_arch(self):
        run_values = [
            (
                'SPECIAL=$(if [ "$(go env GOARCH)" == "amd64" ]; then echo -n x86-only ; fi) && '
                "yum -y install common-pkg $SPECIAL"
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertIn("x86-only", result.packages)
        self.assertIn("common-pkg", result.packages)

        result = analyze_run_commands(run_values, arch="s390x")
        self.assertNotIn("x86-only", result.packages)
        self.assertIn("common-pkg", result.packages)

    def test_arch_conditional_multiple_arches(self):
        run_values = [
            (
                "if [ $(arch) = x86_64 ]; then dnf -y install kernel-rt-devel; fi && "
                "if [ $(arch) = aarch64 ]; then dnf -y install kernel-64k-devel; fi"
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertEqual(result.packages, ["kernel-rt-devel"])

        result = analyze_run_commands(run_values, arch="aarch64")
        self.assertEqual(result.packages, ["kernel-64k-devel"])

    def test_hosttype_arch_conditional(self):
        run_values = [
            (
                'PACKAGES="git gzip" && '
                'if [ $HOSTTYPE = x86_64 ]; then PACKAGES="$PACKAGES realtime-tests"; fi && '
                "yum install -y $PACKAGES"
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertIn("git", result.packages)
        self.assertIn("gzip", result.packages)
        self.assertIn("realtime-tests", result.packages)

        result = analyze_run_commands(run_values, arch="aarch64")
        self.assertNotIn("realtime-tests", result.packages)

    def test_glob_package_pattern_with_resolved_var(self):
        """
        Glob patterns like golang-*$VERSION* should be included after
        variable resolution (e.g. golang-*1.26*). DNF supports globs.
        """
        run_values = ['dnf install -y "golang-*$VERSION*"']
        result = analyze_run_commands(run_values, env_vars={"VERSION": "1.26"})
        self.assertIn("golang-*1.26*", result.packages)

    def test_glob_package_pattern_without_var(self):
        """
        Bare glob patterns like python3* should also be included.
        """
        run_values = ["dnf install -y python3*"]
        result = analyze_run_commands(run_values)
        self.assertIn("python3*", result.packages)

    def test_double_bracket_conditional_with_quoted_var_install(self):
        run_values = [
            (
                'if [[ "$ARCH" == "x86_64" ]]; then '
                "GRUB_PKG=grub2-efi-x64; SHIM_PKG=shim-x64; "
                'elif [[ "$ARCH" == "aarch64" ]]; then '
                "GRUB_PKG=grub2-efi-aa64; SHIM_PKG=shim-aa64; "
                "fi && "
                'dnf install -y "$GRUB_PKG" "$SHIM_PKG"'
            )
        ]
        result = analyze_run_commands(run_values, arch="x86_64")
        self.assertEqual(
            result.packages, ["grub2-efi-x64", "shim-x64"]
        )
        result = analyze_run_commands(run_values, arch="aarch64")
        self.assertEqual(
            result.packages, ["grub2-efi-aa64", "shim-aa64"]
        )

    def test_version_constraints_stripped(self):
        run_values = ["dnf install -y 'python3.12-setuptools >= 70.3.0' python3.12-pip"]
        result = analyze_run_commands(run_values)
        self.assertIn("python3.12-setuptools", result.packages)
        self.assertIn("python3.12-pip", result.packages)
        self.assertNotIn(">=", result.packages)
        self.assertNotIn("70.3.0", result.packages)

    def test_file_path_package_specs_are_included(self):
        run_values = [
            (
                "yum install --setopt=tsflags=nodocs -y "
                "e2fsprogs xfsprogs util-linux nvme-cli "
                "/usr/lib/udev/scsi_id /usr/bin/xxd"
            )
        ]
        result = analyze_run_commands(run_values)
        self.assertIn("e2fsprogs", result.packages)
        self.assertIn("/usr/lib/udev/scsi_id", result.packages)
        self.assertIn("/usr/bin/xxd", result.packages)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[ $(arch) = x86_64 ]", "x86_64"),
        ("[ $HOSTTYPE = aarch64 ]", "aarch64"),
        ("[ ${HOSTTYPE} == ppc64le ]", "ppc64le"),
        ("[ $(uname -m) = s390x ]", "s390x"),
        ("[ $(uname -p) == x86_64 ]", "x86_64"),
        ("[ $ARCH = aarch64 ]", "aarch64"),
        ("[ ${ARCH} == x86_64 ]", "x86_64"),
        ('[ $(arch) == "x86_64" ]', "x86_64"),
        ("[ $(go env GOARCH) = amd64 ]", "amd64"),
        ("[ $GOARCH = amd64 ]", "amd64"),
        ("[ ${GOARCH} == arm64 ]", "arm64"),
    ],
)
def test_arch_value_regex_matches(text, expected):
    m = ARCH_VALUE_RE.search(text)
    assert m is not None, f"No match for: {text}"
    assert m.group(1) == expected


def test_arch_value_regex_no_match():
    assert ARCH_VALUE_RE.search("echo hello") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ('[ "$(uname -m)" != x86_64 ]', "x86_64"),
        ("[ $(uname -m) != aarch64 ]", "aarch64"),
        ('[ "$(go env GOARCH)" != amd64 ]', "amd64"),
        ("[ $GOARCH != arm64 ]", "arm64"),
        ("[ ${GOARCH} != ppc64le ]", "ppc64le"),
    ],
)
def test_arch_neq_value_regex_matches(text, expected):
    m = ARCH_NEQ_VALUE_RE.search(text)
    assert m is not None, f"No match for: {text}"
    assert m.group(1) == expected


def test_arch_neq_value_regex_no_match():
    assert ARCH_NEQ_VALUE_RE.search("echo hello") is None


class TestListArchConditional(unittest.TestCase):
    """
    Tests for ``[ test ] || cmd`` and ``[ test ] && cmd`` arch-conditional
    patterns in list nodes.
    """

    def test_neq_or_installs_on_matching_arch(self):
        """
        ``[ $(arch) != x86_64 ] || dnf install -y pkg`` installs only on x86_64.
        """
        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] || dnf install -y special-pkg"],
            arch="x86_64",
        )
        self.assertIn("special-pkg", result.packages)

        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] || dnf install -y special-pkg"],
            arch="aarch64",
        )
        self.assertEqual(result.packages, [])

    def test_eq_and_installs_on_matching_arch(self):
        """
        ``[ $(arch) = x86_64 ] && dnf install -y pkg`` installs only on x86_64.
        """
        result = analyze_run_commands(
            ["[ $(arch) = x86_64 ] && dnf install -y special-pkg"],
            arch="x86_64",
        )
        self.assertIn("special-pkg", result.packages)

        result = analyze_run_commands(
            ["[ $(arch) = x86_64 ] && dnf install -y special-pkg"],
            arch="aarch64",
        )
        self.assertEqual(result.packages, [])

    def test_double_bracket_neq_or(self):
        """
        ``[[ $(arch) != aarch64 ]] || yum install -y arm-pkg`` with [[ ]] preprocessing.
        """
        result = analyze_run_commands(
            ["[[ $(arch) != aarch64 ]] || yum install -y arm-pkg"],
            arch="aarch64",
        )
        self.assertIn("arm-pkg", result.packages)

    def test_eq_or_installs_on_non_matching_arch(self):
        """
        ``[ $(arch) = x86_64 ] || dnf install -y pkg`` means "all except x86_64".
        """
        result = analyze_run_commands(
            ["[ $(arch) = x86_64 ] || dnf install -y pkg"],
            arch="aarch64",
        )
        self.assertIn("pkg", result.packages)

        result = analyze_run_commands(
            ["[ $(arch) = x86_64 ] || dnf install -y pkg"],
            arch="x86_64",
        )
        self.assertEqual(result.packages, [])

    def test_neq_and_installs_on_non_matching_arch(self):
        """
        ``[ $(arch) != x86_64 ] && dnf install -y pkg`` means "all except x86_64".
        """
        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] && dnf install -y pkg"],
            arch="aarch64",
        )
        self.assertIn("pkg", result.packages)

        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] && dnf install -y pkg"],
            arch="x86_64",
        )
        self.assertEqual(result.packages, [])

    def test_arch_context_only_applies_to_next_command(self):
        """
        In ``[ test ] || cmd1 && cmd2``, only cmd1 gets the arch context.
        """
        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] || dnf install -y special-pkg && dnf install -y common-pkg"],
            arch="x86_64",
        )
        self.assertIn("common-pkg", result.packages)
        self.assertIn("special-pkg", result.packages)

        result = analyze_run_commands(
            ["[ $(arch) != x86_64 ] || dnf install -y special-pkg && dnf install -y common-pkg"],
            arch="aarch64",
        )
        self.assertIn("common-pkg", result.packages)
        self.assertNotIn("special-pkg", result.packages)

    def test_regular_or_fallback_unchanged(self):
        """
        Regular ``cmd1 || cmd2`` without [ test ] stays unconditional.
        """
        result = analyze_run_commands(
            ["dnf install -y preferred-pkg || dnf install -y fallback-pkg"]
        )
        self.assertIn("preferred-pkg", result.packages)
        self.assertIn("fallback-pkg", result.packages)

    def test_uname_m_neq_or(self):
        """
        ``[ $(uname -m) != s390x ] || dnf install -y s390x-pkg``
        """
        result = analyze_run_commands(
            ["[ $(uname -m) != s390x ] || dnf install -y s390x-pkg"],
            arch="s390x",
        )
        self.assertIn("s390x-pkg", result.packages)

    def test_go_env_goarch_neq_or(self):
        """
        ``[ $(go env GOARCH) != "amd64" ] || yum install -y pkg``
        Go arch name ``amd64`` is normalized to RPM name ``x86_64``.
        """
        result = analyze_run_commands(
            ['[ $(go env GOARCH) != "amd64" ] || yum install -y special-pkg'],
            arch="x86_64",
        )
        self.assertIn("special-pkg", result.packages)

    def test_go_env_goarch_neq_or_subshell(self):
        """
        ``[ $(go env GOARCH) != "amd64" ] || (yum install -y pkg1 pkg2 && other)``
        Go arch name ``amd64`` is normalized to RPM name ``x86_64``.
        """
        result = analyze_run_commands(
            ['[ $(go env GOARCH) != "amd64" ] || (yum install -y llvm-toolset cmake3 gcc-c++ && tar zfx cross.tar.gz)'],
            arch="x86_64",
        )
        self.assertEqual(result.packages, ["cmake3", "gcc-c++", "llvm-toolset"])

    def test_goarch_var_neq_or(self):
        """
        ``[ $GOARCH != "arm64" ] || dnf install -y pkg``
        Go arch name ``arm64`` is normalized to RPM name ``aarch64``.
        """
        result = analyze_run_commands(
            ['[ $GOARCH != "arm64" ] || dnf install -y arm-only-pkg'],
            arch="aarch64",
        )
        self.assertIn("arm-only-pkg", result.packages)


class TestBuilddepParsing(unittest.TestCase):
    def test_simple_builddep(self):
        run_values = ["dnf builddep -y pkcs11-helper*"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.builddep_packages, ["pkcs11-helper*"])

    def test_builddep_with_flags(self):
        run_values = ["dnf builddep -y --skip-broken --nobest openvpn*"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.builddep_packages, ["openvpn*"])

    def test_builddep_does_not_interfere_with_install(self):
        run_values = ["dnf install -y gcc make && dnf builddep -y pkcs11-helper*"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.packages, ["gcc", "make"])
        self.assertEqual(result.builddep_packages, ["pkcs11-helper*"])

    def test_build_dep_hyphenated(self):
        run_values = ["dnf build-dep tuned.spec -y"]
        result = analyze_run_commands(run_values)
        self.assertIn("tuned.spec", result.builddep_packages)

    def test_build_dep_hyphenated_with_install(self):
        run_values = [
            (
                "dnf install -y gcc rpm-build && cd assets/tuned/daemon "
                "&& dnf build-dep tuned.spec -y"
            )
        ]
        result = analyze_run_commands(run_values)
        self.assertIn("gcc", result.packages)
        self.assertIn("tuned.spec", result.builddep_packages)


class TestModuleParsing(unittest.TestCase):
    def test_module_install(self):
        run_values = ["dnf module install -y nodejs:18/development"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.module_specs, ["nodejs:18/development"])

    def test_module_enable(self):
        run_values = ["dnf module enable -y nodejs:18"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.module_specs, ["nodejs:18"])

    def test_module_without_stream_ignored(self):
        run_values = ["dnf module enable -y nodejs"]
        result = analyze_run_commands(run_values)
        self.assertEqual(result.module_specs, [])


class TestVariablePackageManager(unittest.TestCase):
    def test_variable_resolves_to_microdnf(self):
        run_values = ["${DNF} install -y openssh-clients"]
        result = analyze_run_commands(run_values, env_vars={"DNF": "microdnf"})
        self.assertIn("openssh-clients", result.packages)

    def test_variable_resolves_to_dnf(self):
        run_values = ["${DNF} install -y gcc"]
        result = analyze_run_commands(run_values, env_vars={"DNF": "dnf"})
        self.assertIn("gcc", result.packages)

    def test_variable_in_conditional(self):
        run_values = [
            (
                "if ! rpm -q openssh-clients; then ${DNF} install -y openssh-clients "
                "&& ${DNF} clean all && rm -rf /var/cache/dnf/*; fi"
            )
        ]
        result = analyze_run_commands(run_values, env_vars={"DNF": "microdnf"})
        self.assertIn("openssh-clients", result.packages)

    def test_variable_multiple_commands(self):
        run_values = [
            "if ! rpm -q openssh-clients; then ${DNF} install -y openssh-clients && ${DNF} clean all; fi",
            "if ! rpm -q libvirt-libs; then ${DNF} install -y libvirt-libs && ${DNF} clean all; fi",
            "if ! command -v tar; then ${DNF} install -y tar && ${DNF} clean all; fi",
        ]
        result = analyze_run_commands(run_values, env_vars={"DNF": "microdnf"})
        self.assertEqual(result.packages, ["libvirt-libs", "openssh-clients", "tar"])

    def test_variable_with_default(self):
        run_values = ["${DNF:-microdnf} install -y tar"]
        result = analyze_run_commands(run_values)
        self.assertIn("tar", result.packages)
