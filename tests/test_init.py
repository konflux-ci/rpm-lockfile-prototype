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


def test_split_solvables_separates_comps_groups():
    regular, groups = rpm_lockfile._split_solvables(
        {"bash", "@core", "@standard", "@nodejs:20"}
    )
    assert regular == {"bash", "@nodejs:20"}
    assert groups == {"core", "standard"}


def test_find_comps_group_matches_by_id():
    class Group:
        def __init__(self, id_, name):
            self.id = id_
            self.name = name
            self.ui_name = name

    class Comps:
        def __init__(self):
            self.groups = [Group("core", "Core"), Group("standard", "Standard")]

    group = rpm_lockfile._find_comps_group(Comps(), "core")
    assert group is not None
    assert group.id == "core"


def test_find_comps_group_matches_by_name():
    class Group:
        def __init__(self, id_, name):
            self.id = id_
            self.name = name
            self.ui_name = name

    class Comps:
        def __init__(self):
            self.groups = [Group("core", "Core"), Group("standard", "Standard")]

    group = rpm_lockfile._find_comps_group(Comps(), "Standard")
    assert group is not None
    assert group.id == "standard"
