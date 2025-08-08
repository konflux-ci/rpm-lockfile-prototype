import tempfile
import os
from pathlib import Path
from unittest.mock import patch, Mock

import yaml

import rpm_lockfile
from rpm_lockfile import PackageItem, strip_suffix, _arch_matches


class TestPackageItem:
    """Test the PackageItem dataclass."""

    def test_as_dict_with_sourcerpm(self):
        """Test as_dict method when sourcerpm is present."""
        item = PackageItem(
            url="https://example.com/bash-1.0-1.rpm",
            repoid="test-repo",
            size=1024,
            checksum="sha256:abcdef",
            name="bash",
            evr="1.0-1.el9",
            sourcerpm="bash-1.0-1.src.rpm",
        )

        result = item.as_dict()
        expected = {
            "url": "https://example.com/bash-1.0-1.rpm",
            "repoid": "test-repo",
            "size": 1024,
            "checksum": "sha256:abcdef",
            "name": "bash",
            "evr": "1.0-1.el9",
            "sourcerpm": "bash-1.0-1.src.rpm",
        }

        assert result == expected

    def test_as_dict_without_sourcerpm(self):
        """Test as_dict method when sourcerpm is None."""
        item = PackageItem(
            url="https://example.com/bash-1.0-1.rpm",
            repoid="test-repo",
            size=1024,
            checksum="sha256:abcdef",
            name="bash",
            evr="1.0-1.el9",
            sourcerpm=None,
        )

        result = item.as_dict()
        expected = {
            "url": "https://example.com/bash-1.0-1.rpm",
            "repoid": "test-repo",
            "size": 1024,
            "checksum": "sha256:abcdef",
            "name": "bash",
            "evr": "1.0-1.el9",
        }

        assert result == expected
        assert "sourcerpm" not in result

    def test_from_dnf(self):
        """Test creating PackageItem from DNF package object."""
        # Mock DNF package
        mock_pkg = Mock()
        mock_pkg.remote_location.return_value = "https://example.com/bash-1.0-1.rpm"
        mock_pkg.repoid = "test-repo"
        mock_pkg.downloadsize = 1024
        mock_pkg.chksum = (1, b"\xab\xcd\xef")  # (hash_type, bytes)
        mock_pkg.name = "bash"
        mock_pkg.evr = "1.0-1.el9"
        mock_pkg.sourcerpm = "bash-1.0-1.src.rpm"

        # Mock hawkey functions
        with patch("rpm_lockfile.hawkey.chksum_name") as mock_chksum_name:
            mock_chksum_name.return_value = "sha256"

            item = PackageItem.from_dnf(mock_pkg)

        assert item.url == "https://example.com/bash-1.0-1.rpm"
        assert item.repoid == "test-repo"
        assert item.size == 1024
        assert item.checksum == "sha256:abcdef"
        assert item.name == "bash"
        assert item.evr == "1.0-1.el9"
        assert item.sourcerpm == "bash-1.0-1.src.rpm"


class TestStripSuffix:
    """Test the strip_suffix utility function."""

    def test_strip_existing_suffix(self):
        """Test stripping a suffix that exists."""
        assert strip_suffix("hello.txt", ".txt") == "hello"
        assert strip_suffix("package.src.rpm", ".src.rpm") == "package"

    def test_strip_nonexistent_suffix(self):
        """Test attempting to strip a suffix that doesn't exist."""
        assert strip_suffix("hello.txt", ".rpm") == "hello.txt"
        assert strip_suffix("package", ".src.rpm") == "package"

    def test_strip_empty_suffix(self):
        """Test stripping empty suffix."""
        # Empty suffix results in s[:-0] which gives empty string
        assert strip_suffix("hello", "") == ""

    def test_strip_whole_string(self):
        """Test when suffix is the whole string."""
        assert strip_suffix(".txt", ".txt") == ""


class TestArchMatches:
    """Test the _arch_matches function for architecture filtering."""

    def test_empty_spec(self):
        """Test with empty architecture specification."""
        assert _arch_matches({}, "x86_64") is True
        assert _arch_matches({}, "aarch64") is True

    def test_only_single_arch(self):
        """Test 'only' specification with single architecture."""
        spec = {"only": "x86_64"}
        assert _arch_matches(spec, "x86_64") is True
        assert _arch_matches(spec, "aarch64") is False

    def test_only_multiple_arches(self):
        """Test 'only' specification with multiple architectures."""
        spec = {"only": ["x86_64", "aarch64"]}
        assert _arch_matches(spec, "x86_64") is True
        assert _arch_matches(spec, "aarch64") is True
        assert _arch_matches(spec, "s390x") is False

    def test_not_single_arch(self):
        """Test 'not' specification with single architecture."""
        spec = {"not": "x86_64"}
        assert _arch_matches(spec, "x86_64") is False
        assert _arch_matches(spec, "aarch64") is True

    def test_not_multiple_arches(self):
        """Test 'not' specification with multiple architectures."""
        spec = {"not": ["x86_64", "s390x"]}
        assert _arch_matches(spec, "x86_64") is False
        assert _arch_matches(spec, "s390x") is False
        assert _arch_matches(spec, "aarch64") is True

    def test_only_and_not_combined(self):
        """Test combining 'only' and 'not' specifications."""
        spec = {"only": ["x86_64", "aarch64"], "not": "aarch64"}
        assert _arch_matches(spec, "x86_64") is True
        assert _arch_matches(spec, "aarch64") is False  # excluded by 'not'
        assert _arch_matches(spec, "s390x") is False  # not in 'only'


class TestReadPackagesFromTreefile:
    """Test the read_packages_from_treefile function."""

    def test_simple_packages(self):
        """Test reading simple package list."""
        treefile_content = {"packages": ["bash", "curl", "git"]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(treefile_content, f)
            f.flush()

            try:
                packages = rpm_lockfile.read_packages_from_treefile("x86_64", f.name)
                assert packages == {"bash", "curl", "git"}
            finally:
                os.unlink(f.name)

    def test_arch_specific_packages(self):
        """Test reading architecture-specific packages."""
        treefile_content = {
            "packages": ["bash", "curl"],
            "packages-x86_64": ["intel-ucode"],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(treefile_content, f)
            f.flush()

            try:
                # For x86_64, should get both general and arch-specific packages
                packages = rpm_lockfile.read_packages_from_treefile("x86_64", f.name)
                assert packages == {"bash", "curl", "intel-ucode"}

                # For other arch, should only get general packages
                packages = rpm_lockfile.read_packages_from_treefile("aarch64", f.name)
                assert packages == {"bash", "curl"}
            finally:
                os.unlink(f.name)

    def test_repo_packages(self):
        """Test reading repo-packages section."""
        treefile_content = {
            "packages": ["bash"],
            "repo-packages": [{"repo": "updates", "packages": ["kernel", "systemd"]}],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(treefile_content, f)
            f.flush()

            try:
                packages = rpm_lockfile.read_packages_from_treefile("x86_64", f.name)
                assert packages == {"bash", "kernel", "systemd"}
            finally:
                os.unlink(f.name)

    def test_packages_with_spaces(self):
        """Test that package entries with spaces are split."""
        treefile_content = {"packages": ["bash curl", "git wget"]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(treefile_content, f)
            f.flush()

            try:
                packages = rpm_lockfile.read_packages_from_treefile("x86_64", f.name)
                assert packages == {"bash", "curl", "git", "wget"}
            finally:
                os.unlink(f.name)

    def test_include_files(self):
        """Test including other treefile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create base treefile
            base_content = {"packages": ["base-package"]}
            base_file = tmpdir / "base.yaml"
            with base_file.open("w") as f:
                yaml.dump(base_content, f)

            # Create main treefile that includes base
            main_content = {"include": "base.yaml", "packages": ["main-package"]}
            main_file = tmpdir / "main.yaml"
            with main_file.open("w") as f:
                yaml.dump(main_content, f)

            packages = rpm_lockfile.read_packages_from_treefile(
                "x86_64", str(main_file)
            )
            assert packages == {"base-package", "main-package"}

    def test_include_multiple_files(self):
        """Test including multiple treefiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create first include
            include1_content = {"packages": ["pkg1"]}
            include1_file = tmpdir / "include1.yaml"
            with include1_file.open("w") as f:
                yaml.dump(include1_content, f)

            # Create second include
            include2_content = {"packages": ["pkg2"]}
            include2_file = tmpdir / "include2.yaml"
            with include2_file.open("w") as f:
                yaml.dump(include2_content, f)

            # Create main treefile
            main_content = {
                "include": ["include1.yaml", "include2.yaml"],
                "packages": ["main-pkg"],
            }
            main_file = tmpdir / "main.yaml"
            with main_file.open("w") as f:
                yaml.dump(main_content, f)

            packages = rpm_lockfile.read_packages_from_treefile(
                "x86_64", str(main_file)
            )
            assert packages == {"pkg1", "pkg2", "main-pkg"}

    def test_arch_include(self):
        """Test arch-specific includes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create arch-specific treefile
            arch_content = {"packages": ["arch-specific-pkg"]}
            arch_file = tmpdir / "x86_64.yaml"
            with arch_file.open("w") as f:
                yaml.dump(arch_content, f)

            # Create main treefile with arch-include
            main_content = {
                "packages": ["main-pkg"],
                "arch-include": {"x86_64": "x86_64.yaml"},
            }
            main_file = tmpdir / "main.yaml"
            with main_file.open("w") as f:
                yaml.dump(main_content, f)

            # For x86_64, should include arch-specific packages
            packages = rpm_lockfile.read_packages_from_treefile(
                "x86_64", str(main_file)
            )
            assert packages == {"main-pkg", "arch-specific-pkg"}

            # For other arch, should not include arch-specific packages
            packages = rpm_lockfile.read_packages_from_treefile(
                "aarch64", str(main_file)
            )
            assert packages == {"main-pkg"}


class TestFilterForArch:
    """Test the filter_for_arch function with additional cases."""

    def test_mixed_strings_and_objects(self):
        """Test filtering mixed string and object specifications."""
        packages = [
            "simple-package",
            {"name": "arch-package", "arches": {"only": "x86_64"}},
            "another-simple",
            {"name": "excluded-package", "arches": {"not": "x86_64"}},
        ]

        result = list(rpm_lockfile.filter_for_arch("x86_64", packages))
        expected = ["simple-package", "arch-package", "another-simple"]
        assert sorted(result) == sorted(expected)

    def test_empty_input(self):
        """Test with empty input."""
        result = list(rpm_lockfile.filter_for_arch("x86_64", []))
        assert result == []

    def test_only_strings(self):
        """Test with only string package specifications."""
        packages = ["bash", "curl", "git"]
        result = list(rpm_lockfile.filter_for_arch("any-arch", packages))
        assert sorted(result) == sorted(packages)


class TestHelperFunctions:
    """Test various helper functions."""

    def test_copy_local_rpmdb(self):
        """Test copy_local_rpmdb function."""
        with patch("rpm_lockfile.shutil.copytree") as mock_copytree:
            with patch("rpm_lockfile.utils.RPMDB_PATH", "var/lib/rpm"):
                rpm_lockfile.copy_local_rpmdb("/tmp/cache")

                mock_copytree.assert_called_once_with(
                    "/var/lib/rpm", "/tmp/cache/var/lib/rpm"
                )

    def test_mkdir(self):
        """Test mkdir helper function."""
        with patch("rpm_lockfile.os.mkdir") as mock_mkdir:
            result = rpm_lockfile.mkdir("/test/dir")

            mock_mkdir.assert_called_once_with("/test/dir")
            assert result == "/test/dir"


class TestRpmdbPreparers:
    """Test rpmdb preparer functions."""

    def test_empty_rpmdb(self):
        """Test empty_rpmdb preparer."""
        preparer = rpm_lockfile.empty_rpmdb()

        # Should return a context manager that yields a temporary directory
        with preparer("x86_64") as root_dir:
            assert os.path.exists(root_dir)

    def test_local_rpmdb(self):
        """Test local_rpmdb preparer."""
        with patch("rpm_lockfile.copy_local_rpmdb") as mock_copy:
            preparer = rpm_lockfile.local_rpmdb()

            with preparer("x86_64") as root_dir:
                mock_copy.assert_called_once_with(root_dir)
                assert os.path.exists(root_dir)

    def test_image_rpmdb(self):
        """Test image_rpmdb preparer."""
        with patch("rpm_lockfile.containers.setup_rpmdb") as mock_setup:
            preparer = rpm_lockfile.image_rpmdb("registry.example.com/image:latest")

            with preparer("x86_64") as root_dir:
                mock_setup.assert_called_once_with(
                    root_dir, "registry.example.com/image:latest", "x86_64"
                )
                assert os.path.exists(root_dir)
