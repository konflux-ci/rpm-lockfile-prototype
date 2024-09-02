import json
import subprocess
from unittest.mock import patch, mock_open, Mock

import pytest
import yaml

from rpm_lockfile import utils


@pytest.mark.parametrize(
    "dir,path,expected",
    [
        ("/topdir", "subdir", "/topdir/subdir"),
        ("/topdir", "/root", "/root"),
        ("/topdir", None, None),
    ]
)
def test_relative_to(dir, path, expected):
    assert utils.relative_to(dir, path) == expected


@pytest.mark.parametrize(
    "files,expected",
    [
        (["Containerfile"], "Containerfile"),
        (["Dockerfile"], "Dockerfile"),
        (["Containerfile", "Dockerfile"], "Containerfile"),
        (["foobar"], None),
        ([], None),
    ]
)
def test_find_containerfile(tmpdir, files, expected):
    for fn in files:
        (tmpdir / fn).write_text("", encoding="utf-8")
    actual = utils.find_containerfile(tmpdir)
    if expected:
        assert actual == tmpdir / expected
    else:
        assert actual is None


@pytest.mark.parametrize(
    "file,expected",
    [
        ("""FROM registry.io/repository/base
RUN something
""", "registry.io/repository/base"),
        ("""FROM registry.io/repository/build as build
RUN build
FROM registry.io/repository/base
COPY --from=build /artifact /
""", "registry.io/repository/base"),
    ]
)
def test_extract_image(file, expected):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(file) == expected


@pytest.mark.parametrize(
    "template,vars,expected",
    [
        ("foo{x}bar", {"x": "X"}, "fooXbar"),
        ("{x}{y}", {"x": "X", "y": "Y"}, "XY"),
        ("foo{x}bar}", {}, "foo{x}bar}"),
        ("foobar", {}, "foobar"),
        ("foobar", {"x": "X"}, "foobar"),
    ]
)
def test_subst_vars(template, vars, expected):
    assert utils.subst_vars(template, vars) == expected


INSPECT_OUTPUT = {
    "Labels": {
        "vcs-ref": "abcdef",
        "architecture": "x86_64",
    },
    "Os": "linux",
}


def test_get_labels_from_image():
    image = "registry.example.com/image:latest"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels({"varsFromImage": image}, "/top")

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image}"], check=True, stdout=subprocess.PIPE
    )


def test_get_labels_from_containerfile(tmpdir):
    image = "registry.example.com/image:latest"
    containerfile = tmpdir / "Containerfile"
    containerfile.write_text(f"FROM {image}\nRUN date\n", encoding="utf-8")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels({"varsFromContainerfile": "Containerfile"}, tmpdir)

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image}"], check=True, stdout=subprocess.PIPE
    )


@pytest.mark.parametrize(
    "filter",
    [
        pytest.param({"stageNum": 2}, id="stageNum"),
        pytest.param({"stageName": "something"}, id="stageName"),
        pytest.param({"imagePattern": "example.com"}, id="imagePattern"),
    ]
)
def test_get_labels_from_containerfile_stage(tmpdir, filter):
    image = "registry.example.com/image:latest"
    containerfile = tmpdir / "Containerfile"
    containerfile.write_text(
        "\n".join(
            [
                "FROM --platform=amd64 foobar:latest AS builder",
                "RUN id",
                f"FROM {image} AS something",
                "RUN date",
                "FROM foobar:latest AS last",
                "RUN pwd",
            ]
        ),
        encoding="utf-8",
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels(
            {"varsFromContainerfile": {"file": "Containerfile"} | filter},
            tmpdir,
        )

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image}"], check=True, stdout=subprocess.PIPE
    )


@pytest.mark.parametrize(
    "arch,expected",
    [
        pytest.param("x86_64", {"glibc", "bash", "openssl"}, id="x8_64"),
        pytest.param("s390x", {"glibc", "zsh", "ant"}, id="s390x"),
    ],
)
def test_read_container_yaml_single_pkg(arch, expected):
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
        assert utils.read_packages_from_container_yaml(arch) == expected


@pytest.mark.parametrize(
    "arch,expected",
    [
        pytest.param("x86_64", {"glibc", "openssl", "fzf", "bash"}, id="x86_64"),
        pytest.param("s390x", {"glibc", "openssl", "fzf", "zsh"}, id="s390x"),
    ],
)
def test_read_from_treefile(tmp_path, arch, expected):
    treefiles = {
        "toplevel.yaml": {
            "packages": ["glibc"],
            "repo-packages": [{"packages": ["openssl"], "repo": "custom"}],
            "include": ["child.yaml"],
            "arch-include": {"x86_64": "x86_64.yaml", "s390x": "s390x.yaml"},
        },
        "child.yaml": {"packages": ["fzf"]},
        "s390x.yaml": {"packages": ["zsh"]},
        "x86_64.yaml": {"packages": ["bash"]},
    }

    for filename, content in treefiles.items():
        with (tmp_path / filename).open("w") as f:
            yaml.dump(content, f)

    assert (
        utils.read_packages_from_treefile(arch, tmp_path / "toplevel.yaml") == expected
    )
