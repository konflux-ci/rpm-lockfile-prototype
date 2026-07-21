import os
import tempfile
from unittest.mock import patch, mock_open
from xml.etree import ElementTree

import pytest

import rpm_lockfile
from rpm_lockfile import assumed_provides, schema

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
            data_types = {
                el.get("type") for el in tree.findall("repo:data", ns)
            }
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
