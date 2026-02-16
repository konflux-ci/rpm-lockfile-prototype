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
    "image_spec",
    [
        "example.com/image:latest",
        "example.com/image@sha256:abcdef",
        "example.com/image:latest@sha256:0123456",
        "registry.example.com/image:latest@sha256:0123456",
        "registry.example.com/namespace/image:stable",
    ],
)
def test_check_image_spec_correct(image_spec):
    assert utils.check_image_spec(image_spec)


@pytest.mark.parametrize(
    "image_spec",
    [
        "fedora",
        "image@sha256:abcdef",
        "image:latest@sha256:0123456",
        "namespace/image:stable",
    ],
)
def test_check_image_spec_wrong(image_spec):
    assert not utils.check_image_spec(image_spec)


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
        ("""FROM registry.io/repository/base AS build
RUN build
FROM build AS runtime
COPY --from=build /artifact /
""", "registry.io/repository/base"),
        ("""FROM registry.io/repository/base AS build
RUN build
FROM registry.io/repository/tester AS test
RUN test
FROM build AS runtime
COPY --from=build /artifact /
""", "registry.io/repository/base"),
    ]
)
def test_extract_image(file, expected):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(file) == expected


@pytest.mark.parametrize(
    "file,expected",
    [
        # Simple ARG with default value using ${VAR} syntax
        ("""ARG BASE_IMAGE=registry.io/repository/base
FROM ${BASE_IMAGE}
RUN something
""", "registry.io/repository/base"),
        # Simple ARG with default value using $VAR syntax
        ("""ARG BASE_IMAGE=registry.io/repository/base
FROM $BASE_IMAGE
RUN something
""", "registry.io/repository/base"),
        # ARG with partial expansion
        ("""ARG REGISTRY=registry.io
ARG NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base:latest
""", "registry.io/repository/base:latest"),
        # Multiple ARGs with the last one being used
        ("""ARG BASE_IMAGE=registry.io/first
ARG BASE_IMAGE=registry.io/second
FROM ${BASE_IMAGE}
""", "registry.io/second"),
        # Multi-stage build - should get last stage
        ("""ARG BUILD_IMAGE=registry.io/build:latest
FROM ${BUILD_IMAGE} as builder
RUN build
ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE}
COPY --from=builder /artifact /
""", "registry.io/base:latest"),
        # ARG before first FROM is global
        ("""ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE} as stage1
RUN something
FROM ${BASE_IMAGE}
RUN other
""", "registry.io/base:latest"),
        # Mixed literal and variable in FROM
        ("""ARG TAG=v1.0
FROM registry.io/repository/base:${TAG}
""", "registry.io/repository/base:v1.0"),
        # ARG with complex image spec including digest
        ("""ARG BASE=registry.io/repo/image:tag@sha256:abcdef123456
FROM ${BASE}
""", "registry.io/repo/image:tag@sha256:abcdef123456"),
        # ARG with --platform flag
        ("""ARG BASE_IMAGE=registry.io/repository/base
FROM --platform=linux/amd64 ${BASE_IMAGE}
""", "registry.io/repository/base"),
        # Multiple ARGs on a single line
        ("""ARG REGISTRY=registry.io NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base:latest
""", "registry.io/repository/base:latest"),
        # Multiple ARGs on one line with partial defaults
        ("""ARG BASE=registry.io/repo TAG=v1.0
FROM ${BASE}:${TAG}
""", "registry.io/repo:v1.0"),
        # Quoted ARG values with double quotes - quotes should be stripped
        ("""ARG BASE="registry.io/repository/base"
FROM ${BASE}
""", "registry.io/repository/base"),
        # Quoted ARG values with single quotes - quotes should be stripped
        ("""ARG BASE='registry.io/repository/base'
FROM ${BASE}
""", "registry.io/repository/base"),
        # Mixed quoted and unquoted ARGs
        ("""ARG REGISTRY="registry.io" NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base
""", "registry.io/repository/base"),
        # Quoted value in FROM instruction
        ("""ARG TAG="v1.0"
FROM registry.io/repository/base:${TAG}
""", "registry.io/repository/base:v1.0"),
    ]
)
def test_extract_image_with_build_args(file, expected):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(file) == expected


def test_extract_image_with_undefined_build_arg():
    """ARG without default value should raise clear error when referenced."""
    file = """ARG BASE_IMAGE
FROM ${BASE_IMAGE}
"""
    with patch("builtins.open", mock_open(read_data=file)):
        with pytest.raises(
            RuntimeError, match="ARG 'BASE_IMAGE' is used but has no default value"
        ):
            utils.extract_image(file)


def test_extract_image_with_partial_undefined_build_args():
    """Multiple undefined ARGs should fail on first undefined variable."""
    file = """ARG REGISTRY
ARG NAMESPACE
FROM ${REGISTRY}/${NAMESPACE}/image
"""
    with patch("builtins.open", mock_open(read_data=file)):
        with pytest.raises(
            RuntimeError, match="ARG 'REGISTRY' is used but has no default value"
        ):
            utils.extract_image(file)


def test_extract_image_with_mixed_defined_undefined_args():
    """Should fail when any referenced ARG is undefined."""
    file = """ARG REGISTRY=registry.io
ARG NAMESPACE
FROM ${REGISTRY}/${NAMESPACE}/image
"""
    with patch("builtins.open", mock_open(read_data=file)):
        with pytest.raises(
            RuntimeError, match="ARG 'NAMESPACE' is used but has no default value"
        ):
            utils.extract_image(file)


def test_extract_image_with_unused_undefined_args():
    """ARGs without defaults are OK if they're not referenced in FROM."""
    file = """ARG UNUSED_VAR
ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE}
"""
    with patch("builtins.open", mock_open(read_data=file)):
        result = utils.extract_image(file)
        assert result == "registry.io/base:latest"


@pytest.mark.parametrize(
    "file,stage_num,stage_name,image_pattern,expected",
    [
        # Extract specific stage by number with ARGs
        ("""ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=registry.io/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""", 1, None, None, "registry.io/builder:latest"),
        # Extract specific stage by name with ARGs
        ("""ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=registry.io/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""", None, "builder", None, "registry.io/builder:latest"),
        # Extract by image pattern with ARGs
        ("""ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=example.com/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""", None, None, "example.com", "example.com/base:latest"),
        # Stage-specific ARG (ARG after FROM)
        ("""ARG GLOBAL_IMG=registry.io/global:latest
FROM registry.io/build:latest as builder
ARG BUILDER_TAG=v1.0
RUN echo ${BUILDER_TAG}
FROM ${GLOBAL_IMG} as runtime
RUN something
""", None, "runtime", None, "registry.io/global:latest"),
        # ARG overridden in stage
        ("""ARG BASE_IMG=registry.io/base:latest
FROM ${BASE_IMG} as stage1
ARG BASE_IMG=registry.io/override:latest
RUN something
FROM ${BASE_IMG} as stage2
""", None, "stage2", None, "registry.io/override:latest"),
    ]
)
def test_extract_image_with_build_args_and_filters(
    file, stage_num, stage_name, image_pattern, expected
):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(
            file,
            stage_num=stage_num,
            stage_name=stage_name,
            image_pattern=image_pattern
        ) == expected


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


def test_get_labels_from_scratch():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = RuntimeError("This should not happen")
        labels = utils.get_labels({"varsFromImage": "scratch"}, "/top")

    assert labels == {}
    mock_run.assert_not_called()


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
