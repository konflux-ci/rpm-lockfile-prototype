"""
Tests for rpm_lockfile.containerfile_packages.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rpm_lockfile.containerfile_packages import (
    StagePackages,
    analyze_containerfile_stages,
    build_copy_map,
    collect_stage_vars,
    extract_packages_from_file_installs,
    extract_packages_from_scripts,
    select_stage,
)


def _make_entry(instruction: str, value: str) -> dict:
    """
    Build a minimal DockerfileParser structure entry for testing.
    """
    return {"instruction": instruction, "value": value, "startline": 0, "endline": 0, "content": ""}


class TestCollectStageVars(unittest.TestCase):
    def test_arg_with_default(self):
        entries = [_make_entry("ARG", "GCC_VERSION=12")]
        result = collect_stage_vars(entries)
        self.assertEqual(result, {"GCC_VERSION": "12"})

    def test_arg_with_quoted_default(self):
        entries = [_make_entry("ARG", 'GCC_VERSION="12"')]
        result = collect_stage_vars(entries)
        self.assertEqual(result, {"GCC_VERSION": "12"})

    def test_arg_without_default(self):
        entries = [_make_entry("ARG", "KERNEL_VERSION")]
        result = collect_stage_vars(entries)
        self.assertEqual(result, {})

    def test_arg_inherits_global(self):
        entries = [_make_entry("ARG", "KERNEL_VERSION")]
        result = collect_stage_vars(entries, inherited_vars={"KERNEL_VERSION": "5.14"})
        self.assertEqual(result, {"KERNEL_VERSION": "5.14"})

    def test_env_with_equals(self):
        entries = [_make_entry("ENV", "LANG=en_US.UTF-8")]
        result = collect_stage_vars(entries)
        self.assertEqual(result, {"LANG": "en_US.UTF-8"})

    def test_env_references_arg(self):
        entries = [
            _make_entry("ARG", "GCC_VERSION=12"),
            _make_entry("ENV", "GCC_VERSION=${GCC_VERSION}"),
        ]
        result = collect_stage_vars(entries)
        self.assertEqual(result, {"GCC_VERSION": "12"})


class TestBuildCopyMap(unittest.TestCase):
    def test_copy_file_to_directory(self):
        entries = [_make_entry("COPY", "hack/foo.sh /tmp")]
        result = build_copy_map(entries)
        self.assertEqual(result["/tmp/foo.sh"], "hack/foo.sh")
        self.assertEqual(result["/tmp"], "hack/foo.sh")

    def test_copy_file_to_directory_with_trailing_slash(self):
        entries = [_make_entry("COPY", "hack/foo.sh /tmp/")]
        result = build_copy_map(entries)
        self.assertEqual(result["/tmp/foo.sh"], "hack/foo.sh")

    def test_skips_copy_from(self):
        entries = [_make_entry("COPY", "--from=builder /app/bin /usr/local/bin")]
        result = build_copy_map(entries)
        self.assertEqual(result, {})

    def test_copy_with_chown_flag(self):
        entries = [_make_entry("COPY", "--chown=root:root hack/foo.sh /tmp/")]
        result = build_copy_map(entries)
        self.assertEqual(result["/tmp/foo.sh"], "hack/foo.sh")

    def test_copy_file_rename(self):
        # Both directory-style and rename-style entries are kept since
        # we can't distinguish without container filesystem context
        entries = [_make_entry("COPY", "foo.sh /opt/bar.sh")]
        result = build_copy_map(entries)
        self.assertEqual(result["/opt/bar.sh"], "foo.sh")
        self.assertEqual(result["/opt/bar.sh/foo.sh"], "foo.sh")


class TestExtractPackagesFromFileInstalls(unittest.TestCase):
    def test_xargs_dnf_install_from_file(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            pkg_file = source_dir / "main-packages-list.txt"
            pkg_file.write_text("httpd\npython3-pip\n# comment\nqemu-img\n")
            copy_map = {"/tmp/main-packages-list.txt": "main-packages-list.txt"}

            run_values = ["xargs -rtd'\\n' dnf install -y < /tmp/main-packages-list.txt"]
            result, arch_result = extract_packages_from_file_installs(run_values, copy_map, source_dir)
            self.assertEqual(result, ["httpd", "python3-pip", "qemu-img"])
            self.assertEqual(arch_result, {})

    def test_pipe_to_xargs_install(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            pkg_file = source_dir / "main-packages-list.ocp"
            pkg_file.write_text("httpd\nqemu-img\n# comment\nsqlite\n")
            copy_map = {"/tmp/main-packages-list.ocp": "main-packages-list.ocp"}

            run_values = ["grep -vE '^(#|$)' /tmp/main-packages-list.ocp | xargs -rtd'\\n' dnf install -y"]
            result, arch_result = extract_packages_from_file_installs(run_values, copy_map, source_dir)
            self.assertEqual(result, ["httpd", "qemu-img", "sqlite"])
            self.assertEqual(arch_result, {})

    def test_arch_suffix_in_filename(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            x86_file = source_dir / "packages-list.ocp-x86_64"
            x86_file.write_text("biosdevname\n")
            copy_map = {"/tmp/packages-list.ocp": "packages-list.ocp"}

            run_values = ["grep -vE '^(#|$)' /tmp/${PKGS_LIST}-$(arch) | xargs -rtd'\\n' dnf install -y"]
            result, arch_result = extract_packages_from_file_installs(
                run_values,
                copy_map,
                source_dir,
                env_vars={"PKGS_LIST": "packages-list.ocp"},
            )
            self.assertEqual(result, [])
            self.assertEqual(arch_result.get("x86_64"), ["biosdevname"])


class TestExtractPackagesFromScripts(unittest.TestCase):
    def test_script_via_bash_interpreter(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            script = source_dir / "hack" / "install.sh"
            script.parent.mkdir()
            script.write_text('PKGS="nmap-ncat procps-ng"\ndnf install -y ${PKGS}\n')

            copy_map = {"/tmp/install.sh": "hack/install.sh"}
            run_values = ["/bin/bash /tmp/install.sh"]
            result = extract_packages_from_scripts(run_values, source_dir=source_dir, copy_map=copy_map)
            self.assertIn("nmap-ncat", result.packages)
            self.assertIn("procps-ng", result.packages)

    def test_direct_script_invocation(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            script = source_dir / "install-deps.sh"
            script.write_text("dnf install -y git\n")

            run_values = ["/src/install-deps.sh"]
            result = extract_packages_from_scripts(run_values, source_dir=source_dir)
            self.assertIn("git", result.packages)

    def test_bare_script_name_resolved_via_copy_map(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            script = source_dir / "prepare-image.sh"
            script.write_text("dnf install -y httpd qemu-img\n")

            copy_map = {"/bin/prepare-image.sh": "prepare-image.sh"}
            run_values = ["set -euxo pipefail && prepare-image.sh && rm -f /bin/prepare-image.sh"]
            result = extract_packages_from_scripts(run_values, source_dir=source_dir, copy_map=copy_map)
            self.assertIn("httpd", result.packages)
            self.assertIn("qemu-img", result.packages)

    def test_arch_specific_packages_from_script(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            script = source_dir / "prepare-efi.sh"
            script.write_text(
                'dnf install -y grub2 dosfstools\n'
                'if [[ "$ARCH" == "x86_64" ]]; then\n'
                '    GRUB_PKG=grub2-efi-x64\n'
                '    SHIM_PKG=shim-x64\n'
                'elif [[ "$ARCH" == "aarch64" ]]; then\n'
                '    GRUB_PKG=grub2-efi-aa64\n'
                '    SHIM_PKG=shim-aa64\n'
                'fi\n'
                'dnf install -y "$GRUB_PKG" "$SHIM_PKG"\n'
            )

            copy_map = {"/bin/prepare-efi.sh": "prepare-efi.sh"}
            run_values = ["prepare-efi.sh redhat"]
            result = extract_packages_from_scripts(run_values, source_dir=source_dir, copy_map=copy_map)
            self.assertIn("grub2", result.packages)
            self.assertIn("dosfstools", result.packages)
            self.assertEqual(result.arch_packages.get("x86_64"), ["grub2-efi-x64", "shim-x64"])
            self.assertEqual(result.arch_packages.get("aarch64"), ["grub2-efi-aa64", "shim-aa64"])

    def test_bare_update_in_script_sets_has_update(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            script = source_dir / "install-python-deps-ocp.sh"
            script.write_text("yum update -y\npip install some-package\n")

            run_values = [". /cachi2/cachi2.env && /src/install-python-deps-ocp.sh"]
            result = extract_packages_from_scripts(run_values, source_dir=source_dir)
            self.assertTrue(result.has_update)


class TestAnalyzeContainerfileStages(unittest.TestCase):
    def _write_containerfile(self, tmpdir: str, content: str) -> Path:
        path = Path(tmpdir) / "Dockerfile"
        path.write_text(content)
        return path

    def test_per_stage_extraction(self):
        with TemporaryDirectory() as tmpdir:
            content = (
                "FROM builder AS build\n"
                "RUN dnf install -y gcc nmstate-devel git\n"
                "\n"
                "FROM base-rhel9\n"
                "RUN dnf install -y postgresql-server skopeo\n"
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path)
            self.assertEqual(len(stages), 2)
            self.assertEqual(stages[0].base_image, "builder")
            self.assertEqual(stages[0].stage_name, "build")
            self.assertEqual(stages[0].packages, ["gcc", "git", "nmstate-devel"])
            self.assertEqual(stages[1].base_image, "base-rhel9")
            self.assertEqual(stages[1].stage_name, "")
            self.assertEqual(stages[1].packages, ["postgresql-server", "skopeo"])

    def test_shell_variable_expansion(self):
        with TemporaryDirectory() as tmpdir:
            content = (
                'FROM base\n'
                'RUN INSTALL_PKGS=" \\\n'
                '      which tar wget hostname" && \\\n'
                '    dnf install -y --nodocs ${INSTALL_PKGS} gpgme && \\\n'
                '    dnf clean all\n'
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path)
            self.assertEqual(stages[0].packages, ["gpgme", "hostname", "tar", "wget", "which"])

    def test_arg_env_variable_resolution(self):
        with TemporaryDirectory() as tmpdir:
            content = (
                "ARG GCC_VERSION=12\n"
                "ARG KERNEL_VERSION\n"
                "FROM base\n"
                "ARG GCC_VERSION\n"
                "ARG KERNEL_VERSION\n"
                "RUN dnf install -y \\\n"
                "    gcc-${GCC_VERSION} \\\n"
                "    gcc-c++-${GCC_VERSION} \\\n"
                "    kernel-devel${KERNEL_VERSION:+-}${KERNEL_VERSION} \\\n"
                "    make\n"
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path)
            self.assertEqual(stages[0].packages, ["gcc-12", "gcc-c++-12", "kernel-devel", "make"])

    def test_arch_conditional_packages(self):
        with TemporaryDirectory() as tmpdir:
            content = (
                "FROM base\n"
                "RUN dnf install -y make && \\\n"
                "    if [ $(arch) = x86_64 ]; then \\\n"
                "    dnf -y install kernel-rt-devel; \\\n"
                "    fi && \\\n"
                "    if [ $(arch) = aarch64 ]; then \\\n"
                "    dnf -y install kernel-64k-devel; \\\n"
                "    fi\n"
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path)
            self.assertEqual(stages[0].packages, ["make"])
            self.assertEqual(stages[0].arch_packages, {"aarch64": ["kernel-64k-devel"], "x86_64": ["kernel-rt-devel"]})

    def test_copy_then_bash_script(self):
        with TemporaryDirectory() as tmpdir:
            hack_dir = Path(tmpdir) / "hack"
            hack_dir.mkdir()
            (hack_dir / "install.sh").write_text('INSTALL_PKGS="nmap-ncat procps-ng"\ndnf install -y ${INSTALL_PKGS}\n')
            content = "FROM base\nCOPY hack/install.sh /tmp\nRUN /bin/bash /tmp/install.sh\n"
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path, source_dir=Path(tmpdir))
            self.assertIn("nmap-ncat", stages[0].packages)
            self.assertIn("procps-ng", stages[0].packages)

    def test_script_arch_packages_flow_to_stages(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            (source_dir / "prepare-efi.sh").write_text(
                'dnf install -y grub2 dosfstools\n'
                'if [[ "$ARCH" == "x86_64" ]]; then\n'
                '    GRUB_PKG=grub2-efi-x64\n'
                '    SHIM_PKG=shim-x64\n'
                'elif [[ "$ARCH" == "aarch64" ]]; then\n'
                '    GRUB_PKG=grub2-efi-aa64\n'
                '    SHIM_PKG=shim-aa64\n'
                'fi\n'
                'dnf install -y "$GRUB_PKG" "$SHIM_PKG"\n'
            )
            content = (
                "FROM builder AS build\n"
                "COPY prepare-efi.sh /bin/\n"
                "RUN prepare-efi.sh redhat\n"
                "\n"
                "FROM base-rhel9\n"
                "RUN dnf install -y httpd\n"
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path, source_dir=source_dir)
            self.assertIn("grub2", stages[0].packages)
            self.assertIn("dosfstools", stages[0].packages)
            self.assertEqual(stages[0].arch_packages.get("x86_64"), ["grub2-efi-x64", "shim-x64"])
            self.assertEqual(stages[0].arch_packages.get("aarch64"), ["grub2-efi-aa64", "shim-aa64"])
            self.assertEqual(stages[1].packages, ["httpd"])

    def test_detect_update(self):
        with TemporaryDirectory() as tmpdir:
            content = "FROM base\nRUN yum update -y && yum clean all\n"
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path)
            self.assertEqual(stages[0].packages, [])
            self.assertTrue(stages[0].has_update)

    def test_bare_update_in_script_propagates(self):
        with TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir)
            (source_dir / "install-python-deps-ocp.sh").write_text("yum update -y\npip install requests==2.28.0\n")
            content = (
                "FROM builder AS build\n"
                "RUN dnf install -y gcc\n"
                "\n"
                "FROM base-rhel9\n"
                "COPY install-python-deps-ocp.sh /src/\n"
                "RUN . /cachi2/cachi2.env && /src/install-python-deps-ocp.sh\n"
            )
            path = self._write_containerfile(tmpdir, content)
            stages = analyze_containerfile_stages(path, source_dir=source_dir)
            self.assertFalse(stages[0].has_update)
            self.assertTrue(stages[1].has_update)


class TestSelectStage(unittest.TestCase):
    def _make_stages(self) -> list[StagePackages]:
        return [
            StagePackages(base_image="builder", stage_name="build", packages=["gcc", "make"]),
            StagePackages(base_image="registry.redhat.io/ubi9/ubi:latest", stage_name="runtime", packages=["httpd"]),
            StagePackages(base_image="base-rhel9", stage_name="", packages=["skopeo"]),
        ]

    def test_default_returns_last_stage(self):
        stages = self._make_stages()
        result = select_stage(stages)
        self.assertEqual(result.packages, ["skopeo"])

    def test_stage_num_selects_correct_stage(self):
        stages = self._make_stages()
        result = select_stage(stages, stage_num=1)
        self.assertEqual(result.packages, ["gcc", "make"])
        result = select_stage(stages, stage_num=2)
        self.assertEqual(result.packages, ["httpd"])

    def test_stage_num_out_of_range_returns_none(self):
        stages = self._make_stages()
        result = select_stage(stages, stage_num=5)
        self.assertIsNone(result)

    def test_stage_name_selects_correct_stage(self):
        stages = self._make_stages()
        result = select_stage(stages, stage_name="build")
        self.assertEqual(result.packages, ["gcc", "make"])
        result = select_stage(stages, stage_name="runtime")
        self.assertEqual(result.packages, ["httpd"])

    def test_stage_name_no_match_returns_none(self):
        stages = self._make_stages()
        result = select_stage(stages, stage_name="nonexistent")
        self.assertIsNone(result)

    def test_image_pattern_selects_correct_stage(self):
        stages = self._make_stages()
        result = select_stage(stages, image_pattern=r"ubi9")
        self.assertEqual(result.packages, ["httpd"])

    def test_image_pattern_no_match_returns_none(self):
        stages = self._make_stages()
        result = select_stage(stages, image_pattern=r"centos")
        self.assertIsNone(result)

    def test_empty_stages_returns_none(self):
        result = select_stage([])
        self.assertIsNone(result)
