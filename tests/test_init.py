import os
import tempfile
from unittest.mock import mock_open, patch
from xml.etree import ElementTree

import pytest

import rpm_lockfile
from rpm_lockfile import (
    _apply_excludes,
    _get_containerfile_extra_args,
    assumed_provides,
    schema,
    utils,
)


@pytest.mark.parametrize(
    "arch,expected",
    [
        pytest.param("x86_64", {"glibc", "bash", "openssl"}, id="x86_64"),
        pytest.param("s390x", {"glibc", "zsh", "ant"}, id="s390x"),
    ],
)
def test_read_container_yaml(arch, expected):
    contents = """
        flatpak:
          packages:
          - glibc
          - name: bash
            platforms:
              only: x86_64
          - name: zsh
            platforms:
              not: x86_64
          - name: openssl
            platforms:
              only: [x86_64]
          - name: ant
            platforms:
              not: [x86_64]
        """
    with patch("builtins.open", mock_open(read_data=contents)):
        assert rpm_lockfile.read_packages_from_container_yaml(arch) == expected


@pytest.mark.parametrize(
    "input,expected",
    [
        (["foo", "bar"], ["foo", "bar"]),
        ([{"name": "foo"}], ["foo"]),
        ([{"name": "foo", "arches": {"only": ["ppc64le"]}}], ["foo"]),
        ([{"name": "foo", "arches": {"not": ["ppc64le"]}}], []),
        ([{"name": "foo", "arches": {"only": ["s390x"]}}], []),
        ([{"name": "foo", "arches": {"not": ["s390x"]}}], ["foo"]),
    ],
)
def test_filter_for_arch(input, expected):
    assert sorted(rpm_lockfile.filter_for_arch("ppc64le", input)) == sorted(expected)


class TestAssumeProvides:
    def test_schema_accepts_assume_provides(self):
        config = {
            "contentOrigin": {"repos": []},
            "assumeProvides": ["nvidia-kmod", "cuda-libs"],
        }
        schema.validate(config)

    def test_schema_rejects_invalid_assume_provides(self):
        config = {
            "contentOrigin": {"repos": []},
            "assumeProvides": "not-a-list",
        }
        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_create_assumed_provides_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = assumed_provides.create_repo(
                tmpdir, ["nvidia-kmod", "cuda-libs"]
            )
            repomd_path = os.path.join(repo_dir, "repodata", "repomd.xml")
            assert os.path.exists(repomd_path)

            ns = {"repo": "http://linux.duke.edu/metadata/repo"}
            tree = ElementTree.parse(repomd_path)
            data_types = {el.get("type") for el in tree.findall("repo:data", ns)}
            assert "primary" in data_types
            assert "filelists" in data_types
            assert "other" in data_types

    def test_create_assumed_provides_repo_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = assumed_provides.create_repo(tmpdir, [])
            repomd_path = os.path.join(repo_dir, "repodata", "repomd.xml")
            assert os.path.exists(repomd_path)


class TestPackagesFromContainerfileSchema:
    def test_accepts_string(self):
        config = {
            "contentOrigin": {"repos": []},
            "packagesFromContainerfile": "Containerfile",
        }
        schema.validate(config)

    def test_accepts_dict_with_file(self):
        config = {
            "contentOrigin": {"repos": []},
            "packagesFromContainerfile": {
                "file": "Containerfile",
                "stageNum": 1,
            },
        }
        schema.validate(config)

    def test_rejects_invalid_type(self):
        config = {
            "contentOrigin": {"repos": []},
            "packagesFromContainerfile": 42,
        }
        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_works_with_packages_and_bare(self):
        config = {
            "contentOrigin": {"repos": []},
            "context": {"bare": True},
            "packages": ["extra-pkg"],
            "packagesFromContainerfile": "Containerfile",
        }
        schema.validate(config)


class TestContainerfileArgFileSchema:
    def test_accepts_argfile_in_context_containerfile(self):
        config = {
            "contentOrigin": {"repos": []},
            "context": {
                "containerfile": {
                    "file": "Containerfile",
                    "argFile": "build-args.env",
                }
            },
        }
        schema.validate(config)

    def test_accepts_argfile_in_packages_from_containerfile(self):
        config = {
            "contentOrigin": {"repos": []},
            "packagesFromContainerfile": {
                "file": "Containerfile",
                "argFile": "build-args.env",
            },
        }
        schema.validate(config)

    def test_rejects_unknown_property(self):
        config = {
            "contentOrigin": {"repos": []},
            "context": {
                "containerfile": {
                    "file": "Containerfile",
                    "unknownKey": "value",
                }
            },
        }
        with pytest.raises(SystemExit):
            schema.validate(config)


class TestExcludePackages:
    def test_schema_accepts_exclude_packages(self):
        config = {
            "contentOrigin": {"repos": []},
            "excludePackages": ["centos-pkg", "okd-only-pkg"],
        }
        schema.validate(config)

    def test_schema_rejects_invalid_type(self):
        config = {
            "contentOrigin": {"repos": []},
            "excludePackages": "not-a-list",
        }
        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_schema_rejects_empty_string_item(self):
        config = {
            "contentOrigin": {"repos": []},
            "excludePackages": [""],
        }
        with pytest.raises(SystemExit):
            schema.validate(config)

    def test_schema_accepts_with_packages_from_containerfile(self):
        config = {
            "contentOrigin": {"repos": []},
            "packagesFromContainerfile": "Containerfile",
            "excludePackages": ["centos-release"],
        }
        schema.validate(config)

    def test_apply_excludes_removes_from_install(self):
        pkgs = {"bash", "tar", "okd-only-pkg"}
        result = _apply_excludes(pkgs, {"okd-only-pkg"})
        assert result == {"bash", "tar"}

    def test_apply_excludes_removes_from_reinstall(self):
        pkgs = {"bash", "centos-release-nfv-openvswitch"}
        result = _apply_excludes(pkgs, {"centos-release-nfv-openvswitch"})
        assert result == {"bash"}

    def test_apply_excludes_removes_from_upgrade(self):
        pkgs = {"bash", "okd-only-pkg", "tar"}
        result = _apply_excludes(pkgs, {"okd-only-pkg"})
        assert result == {"bash", "tar"}

    def test_apply_excludes_empty_excludes_is_noop(self):
        pkgs = {"bash", "tar"}
        result = _apply_excludes(pkgs, set())
        assert result == {"bash", "tar"}

    def test_apply_excludes_nonexistent_entry_is_safe(self):
        pkgs = {"bash", "tar"}
        result = _apply_excludes(pkgs, {"not-installed"})
        assert result == {"bash", "tar"}

    def test_exclude_packages_variable_substitution(self):
        variables = {"OKD_PKG": "centos-release-nfv-openvswitch"}
        items = ["{OKD_PKG}", "another-pkg"]
        result = utils.subst_vars_in_list(items, variables)
        assert result == ["centos-release-nfv-openvswitch", "another-pkg"]

    def test_exclude_packages_variable_substitution_no_match_unchanged(self):
        variables = {"OTHER": "something"}
        items = ["{OKD_PKG}"]
        result = utils.subst_vars_in_list(items, variables)
        assert result == ["{OKD_PKG}"]


class TestGetContainerfileExtraArgs:
    def test_returns_none_when_no_containerfile(self, tmp_path):
        assert _get_containerfile_extra_args(str(tmp_path), {}) is None

    def test_returns_none_when_containerfile_is_string(self, tmp_path):
        context = {"containerfile": "Containerfile"}
        assert _get_containerfile_extra_args(str(tmp_path), context) is None

    def test_returns_none_when_no_argfile_key(self, tmp_path):
        context = {"containerfile": {"file": "Containerfile", "stageNum": 1}}
        assert _get_containerfile_extra_args(str(tmp_path), context) is None

    def test_loads_argfile_when_specified(self, tmp_path):
        argfile = tmp_path / "build-args.env"
        argfile.write_text("BASE_IMAGE=registry.example.com/image:latest\n")
        context = {"containerfile": {"file": "Containerfile", "argFile": "build-args.env"}}
        result = _get_containerfile_extra_args(str(tmp_path), context)
        assert result == {"BASE_IMAGE": "registry.example.com/image:latest"}

    def test_raises_if_argfile_missing(self, tmp_path):
        context = {"containerfile": {"file": "Containerfile", "argFile": "nonexistent.env"}}
        with pytest.raises(FileNotFoundError):
            _get_containerfile_extra_args(str(tmp_path), context)
