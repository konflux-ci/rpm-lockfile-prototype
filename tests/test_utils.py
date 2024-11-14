import json
import subprocess
from unittest.mock import patch, mock_open, Mock

import pytest

from rpm_lockfile import utils


@pytest.fixture(autouse=True)
def reset_label_cache():
    utils.inspect_image.cache_clear()


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
    "image_spec,expected",
    [
        ("example.com/image:latest", "example.com/image:latest"),
        ("example.com/image@sha256:abcdef", "example.com/image@sha256:abcdef"),
        ("example.com/image:latest@sha256:0123456", "example.com/image@sha256:0123456"),
    ],
)
def test_strip_tag(image_spec, expected):
    assert utils.strip_tag(image_spec) == expected


@pytest.mark.parametrize(
    "repo,tag,digest,expected",
    [
        ("example.com/img", "tag", "sha256:abc", "example.com/img:tag@sha256:abc"),
        ("example.com/img", None, "sha256:abc", "example.com/img@sha256:abc"),
        ("example.com/img", "tag", None, "example.com/img:tag"),
    ],
)
def test_make_image_spec(repo, tag, digest, expected):
    assert utils.make_image_spec(repo, tag, digest) == expected


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


@pytest.mark.parametrize(
    "image_spec,image_url",
    [
        ("registry.example.com/image:latest", "registry.example.com/image:latest"),
        (
            "registry.example.com/image@sha256:abcdef",
            "registry.example.com/image@sha256:abcdef",
        ),
        (
            "registry.example.com/image:latest@sha256:abcdef",
            "registry.example.com/image@sha256:abcdef",
        ),
    ],
)
def test_get_labels_from_image(image_spec, image_url):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels({"varsFromImage": image_spec}, "/top")

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image_url}"],
        check=True,
        stdout=subprocess.PIPE,
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
    "content,hash",
    [
        ("", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        ("hello\n", "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"),
    ],
)
def test_hash_file(content, hash, tmp_path):
    fn = tmp_path / "something"
    fn.write_text(content, encoding="utf-8")
    assert utils.hash_file(fn) == hash
