from unittest.mock import patch, mock_open

import pytest

import rpm_lockfile


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
