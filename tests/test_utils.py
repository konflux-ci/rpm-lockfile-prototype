import json
import subprocess
from unittest.mock import Mock, mock_open, patch

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
    ],
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
    ],
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
        (
            """FROM registry.io/repository/base
RUN something
""",
            "registry.io/repository/base",
        ),
        (
            """FROM registry.io/repository/build as build
RUN build
FROM registry.io/repository/base
COPY --from=build /artifact /
""",
            "registry.io/repository/base",
        ),
        (
            """FROM registry.io/repository/base AS build
RUN build
FROM build AS runtime
COPY --from=build /artifact /
""",
            "registry.io/repository/base",
        ),
        (
            """FROM registry.io/repository/base AS build
RUN build
FROM registry.io/repository/tester AS test
RUN test
FROM build AS runtime
COPY --from=build /artifact /
""",
            "registry.io/repository/base",
        ),
    ],
)
def test_extract_image(file, expected):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(file) == expected


@pytest.mark.parametrize(
    "file,expected",
    [
        # Simple ARG with default value using ${VAR} syntax
        (
            """ARG BASE_IMAGE=registry.io/repository/base
FROM ${BASE_IMAGE}
RUN something
""",
            "registry.io/repository/base",
        ),
        # Simple ARG with default value using $VAR syntax
        (
            """ARG BASE_IMAGE=registry.io/repository/base
FROM $BASE_IMAGE
RUN something
""",
            "registry.io/repository/base",
        ),
        # ARG with partial expansion
        (
            """ARG REGISTRY=registry.io
ARG NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base:latest
""",
            "registry.io/repository/base:latest",
        ),
        # Multiple ARGs with the last one being used
        (
            """ARG BASE_IMAGE=registry.io/first
ARG BASE_IMAGE=registry.io/second
FROM ${BASE_IMAGE}
""",
            "registry.io/second",
        ),
        # Multi-stage build - should get last stage
        (
            """ARG BUILD_IMAGE=registry.io/build:latest
FROM ${BUILD_IMAGE} as builder
RUN build
ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE}
COPY --from=builder /artifact /
""",
            "registry.io/base:latest",
        ),
        # ARG before first FROM is global
        (
            """ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE} as stage1
RUN something
FROM ${BASE_IMAGE}
RUN other
""",
            "registry.io/base:latest",
        ),
        # Mixed literal and variable in FROM
        (
            """ARG TAG=v1.0
FROM registry.io/repository/base:${TAG}
""",
            "registry.io/repository/base:v1.0",
        ),
        # ARG with complex image spec including digest
        (
            """ARG BASE=registry.io/repo/image:tag@sha256:abcdef123456
FROM ${BASE}
""",
            "registry.io/repo/image:tag@sha256:abcdef123456",
        ),
        # ARG with --platform flag
        (
            """ARG BASE_IMAGE=registry.io/repository/base
FROM --platform=linux/amd64 ${BASE_IMAGE}
""",
            "registry.io/repository/base",
        ),
        # Multiple ARGs on a single line
        (
            """ARG REGISTRY=registry.io NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base:latest
""",
            "registry.io/repository/base:latest",
        ),
        # Multiple ARGs on one line with partial defaults
        (
            """ARG BASE=registry.io/repo TAG=v1.0
FROM ${BASE}:${TAG}
""",
            "registry.io/repo:v1.0",
        ),
        # Quoted ARG values with double quotes - quotes should be stripped
        (
            """ARG BASE="registry.io/repository/base"
FROM ${BASE}
""",
            "registry.io/repository/base",
        ),
        # Quoted ARG values with single quotes - quotes should be stripped
        (
            """ARG BASE='registry.io/repository/base'
FROM ${BASE}
""",
            "registry.io/repository/base",
        ),
        # Mixed quoted and unquoted ARGs
        (
            """ARG REGISTRY="registry.io" NAMESPACE=repository
FROM ${REGISTRY}/${NAMESPACE}/base
""",
            "registry.io/repository/base",
        ),
        # Quoted value in FROM instruction
        (
            """ARG TAG="v1.0"
FROM registry.io/repository/base:${TAG}
""",
            "registry.io/repository/base:v1.0",
        ),
    ],
)
def test_extract_image_with_build_args(file, expected):
    with patch("builtins.open", mock_open(read_data=file)):
        assert utils.extract_image(file) == expected


def test_extract_image_with_undefined_build_arg():
    """ARG without default value should raise clear error when referenced."""
    file = """ARG BASE_IMAGE
FROM ${BASE_IMAGE}
"""
    with (
        patch("builtins.open", mock_open(read_data=file)),
        pytest.raises(
            RuntimeError, match="ARG 'BASE_IMAGE' is used but has no default value"
        ),
    ):
        utils.extract_image(file)


def test_extract_image_with_partial_undefined_build_args():
    """Multiple undefined ARGs should fail on first undefined variable."""
    file = """ARG REGISTRY
ARG NAMESPACE
FROM ${REGISTRY}/${NAMESPACE}/image
"""
    with (
        patch("builtins.open", mock_open(read_data=file)),
        pytest.raises(
            RuntimeError, match="ARG 'REGISTRY' is used but has no default value"
        ),
    ):
        utils.extract_image(file)


def test_extract_image_with_mixed_defined_undefined_args():
    """Should fail when any referenced ARG is undefined."""
    file = """ARG REGISTRY=registry.io
ARG NAMESPACE
FROM ${REGISTRY}/${NAMESPACE}/image
"""
    with (
        patch("builtins.open", mock_open(read_data=file)),
        pytest.raises(
            RuntimeError, match="ARG 'NAMESPACE' is used but has no default value"
        ),
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


def test_extract_image_extra_args_override_default():
    """extra_args override ARG defaults declared in the Containerfile."""
    file = """ARG BASE_IMAGE=registry.io/default:latest
FROM ${BASE_IMAGE}
"""
    with patch("builtins.open", mock_open(read_data=file)):
        result = utils.extract_image(
            file, extra_args={"BASE_IMAGE": "registry.io/override:latest"}
        )
        assert result == "registry.io/override:latest"


def test_extract_image_extra_args_supply_missing_default():
    """extra_args can provide a value for an ARG with no default."""
    file = """ARG BASE_IMAGE
FROM ${BASE_IMAGE}
"""
    with patch("builtins.open", mock_open(read_data=file)):
        result = utils.extract_image(
            file,
            extra_args={
                "BASE_IMAGE": "registry.redhat.io/rhel9/rhel-bootc:latest@sha256:4140526fa1f9bec23eb35065dbb33ef8ed4d48039c024d3f04dc5a54b86f3e58"
            },
        )
        assert result == "registry.redhat.io/rhel9/rhel-bootc:latest@sha256:4140526fa1f9bec23eb35065dbb33ef8ed4d48039c024d3f04dc5a54b86f3e58"


def test_extract_image_extra_args_partial_override():
    """extra_args can override some ARGs while others use their Containerfile defaults."""
    file = """ARG REGISTRY=registry.io
ARG NAMESPACE=default
FROM ${REGISTRY}/${NAMESPACE}/base:latest
"""
    with patch("builtins.open", mock_open(read_data=file)):
        result = utils.extract_image(
            file, extra_args={"NAMESPACE": "custom"}
        )
        assert result == "registry.io/custom/base:latest"


def test_extract_image_extra_args_unused_key_ignored():
    """extra_args keys not referenced in the Containerfile are silently ignored."""
    file = """ARG BASE_IMAGE=registry.io/base:latest
FROM ${BASE_IMAGE}
"""
    with patch("builtins.open", mock_open(read_data=file)):
        result = utils.extract_image(
            file, extra_args={"UNRELATED": "something", "BASE_IMAGE": "registry.io/override:latest"}
        )
        assert result == "registry.io/override:latest"


def test_extract_image_extra_args_missing_arg_still_raises():
    """If an ARG is referenced but supplied neither in Containerfile nor extra_args, raise."""
    file = """ARG BASE_IMAGE
FROM ${BASE_IMAGE}
"""
    with (
        patch("builtins.open", mock_open(read_data=file)),
        pytest.raises(
            RuntimeError, match="ARG 'BASE_IMAGE' is used but has no default value"
        ),
    ):
        utils.extract_image(file, extra_args={"OTHER": "value"})


@pytest.mark.parametrize(
    "file,stage_num,stage_name,image_pattern,expected",
    [
        # Extract specific stage by number with ARGs
        (
            """ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=registry.io/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""",
            1,
            None,
            None,
            "registry.io/builder:latest",
        ),
        # Extract specific stage by name with ARGs
        (
            """ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=registry.io/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""",
            None,
            "builder",
            None,
            "registry.io/builder:latest",
        ),
        # Extract by image pattern with ARGs
        (
            """ARG BUILD_IMG=registry.io/builder:latest
ARG BASE_IMG=example.com/base:latest
FROM ${BUILD_IMG} as builder
RUN build
FROM ${BASE_IMG} as runtime
COPY --from=builder /artifact /
""",
            None,
            None,
            "example.com",
            "example.com/base:latest",
        ),
        # Stage-specific ARG (ARG after FROM)
        (
            """ARG GLOBAL_IMG=registry.io/global:latest
FROM registry.io/build:latest as builder
ARG BUILDER_TAG=v1.0
RUN echo ${BUILDER_TAG}
FROM ${GLOBAL_IMG} as runtime
RUN something
""",
            None,
            "runtime",
            None,
            "registry.io/global:latest",
        ),
        # ARG overridden in stage
        (
            """ARG BASE_IMG=registry.io/base:latest
FROM ${BASE_IMG} as stage1
ARG BASE_IMG=registry.io/override:latest
RUN something
FROM ${BASE_IMG} as stage2
""",
            None,
            "stage2",
            None,
            "registry.io/override:latest",
        ),
    ],
)
def test_extract_image_with_build_args_and_filters(
    file, stage_num, stage_name, image_pattern, expected
):
    with patch("builtins.open", mock_open(read_data=file)):
        assert (
            utils.extract_image(
                file,
                stage_num=stage_num,
                stage_name=stage_name,
                image_pattern=image_pattern,
            )
            == expected
        )


@pytest.mark.parametrize(
    "template,vars,expected",
    [
        ("foo{x}bar", {"x": "X"}, "fooXbar"),
        ("{x}{y}", {"x": "X", "y": "Y"}, "XY"),
        ("foo{x}bar}", {}, "foo{x}bar}"),
        ("foobar", {}, "foobar"),
        ("foobar", {"x": "X"}, "foobar"),
    ],
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
        ["skopeo", "inspect", "--no-tags", f"docker://{image_url}"],
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

    with (
        patch("subprocess.run") as mock_run,
        patch("rpm_lockfile.utils.find_git_root", return_value=None),
    ):
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels({"varsFromContainerfile": "Containerfile"}, tmpdir)

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", "--no-tags", f"docker://{image}"],
        check=True,
        stdout=subprocess.PIPE,
    )


@pytest.mark.parametrize(
    "filter",
    [
        pytest.param({"stageNum": 2}, id="stageNum"),
        pytest.param({"stageName": "something"}, id="stageName"),
        pytest.param({"imagePattern": "example.com"}, id="imagePattern"),
    ],
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

    with (
        patch("subprocess.run") as mock_run,
        patch("rpm_lockfile.utils.find_git_root", return_value=None),
    ):
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels(
            {"varsFromContainerfile": {"file": "Containerfile"} | filter},
            tmpdir,
        )

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", "--no-tags", f"docker://{image}"],
        check=True,
        stdout=subprocess.PIPE,
    )


def test_get_labels_from_containerfile_with_argfile(tmpdir):
    """argFile values are passed as extra_args to resolve ARGs with no default."""
    image = "registry.example.com/image:latest"
    containerfile = tmpdir / "Containerfile"
    containerfile.write_text("ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nRUN date\n", encoding="utf-8")
    argfile = tmpdir / "build-args.env"
    argfile.write_text(f"BASE_IMAGE={image}\n", encoding="utf-8")

    with (
        patch("subprocess.run") as mock_run,
        patch("rpm_lockfile.utils.find_git_root", return_value=None),
    ):
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels(
            {"varsFromContainerfile": {"file": "Containerfile", "argFile": "build-args.env"}},
            tmpdir,
        )

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", "--no-tags", f"docker://{image}"],
        check=True,
        stdout=subprocess.PIPE,
    )


def test_get_labels_from_containerfile_argfile_overrides_default(tmpdir):
    """argFile values take precedence over ARG defaults in the Containerfile."""
    default_image = "registry.example.com/default:latest"
    override_image = "registry.example.com/override:latest"
    containerfile = tmpdir / "Containerfile"
    containerfile.write_text(
        f"ARG BASE_IMAGE={default_image}\nFROM ${{BASE_IMAGE}}\nRUN date\n",
        encoding="utf-8",
    )
    argfile = tmpdir / "build-args.env"
    argfile.write_text(f"BASE_IMAGE={override_image}\n", encoding="utf-8")

    with (
        patch("subprocess.run") as mock_run,
        patch("rpm_lockfile.utils.find_git_root", return_value=None),
    ):
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        labels = utils.get_labels(
            {"varsFromContainerfile": {"file": "Containerfile", "argFile": "build-args.env"}},
            tmpdir,
        )

    assert labels == INSPECT_OUTPUT["Labels"]
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", "--no-tags", f"docker://{override_image}"],
        check=True,
        stdout=subprocess.PIPE,
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


@pytest.mark.parametrize(
    "content,expected",
    [
        ("FOO=bar\nBAZ=qux\n", {"FOO": "bar", "BAZ": "qux"}),
        ("FOO=bar\n\n# comment\nBAZ=qux\n", {"FOO": "bar", "BAZ": "qux"}),
        ("FOO=\"bar baz\"\nQUX='hello'\n", {"FOO": "bar baz", "QUX": "hello"}),
        ("FOO=bar=baz\n", {"FOO": "bar=baz"}),
        ("  FOO = bar  \n", {"FOO": "bar"}),
        ("NO_EQUALS_LINE\nFOO=bar\n", {"FOO": "bar"}),
        ("=valuewithnokey\nFOO=bar\n", {"FOO": "bar"}),
    ],
)
def test_load_variables_file(content, expected, tmp_path):
    fn = tmp_path / "vars.conf"
    fn.write_text(content, encoding="utf-8")
    assert utils.load_variables_file("vars.conf", str(tmp_path)) == expected


def test_load_variables_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        utils.load_variables_file("nonexistent.conf", str(tmp_path))


class TestFindGitRoot:
    def test_finds_root_from_subdirectory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "sub"
        subdir.mkdir()
        assert utils.find_git_root(str(subdir)) == str(tmp_path)

    def test_finds_root_when_config_dir_is_git_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert utils.find_git_root(str(tmp_path)) == str(tmp_path)

    def test_returns_none_when_not_in_git_repo(self, tmp_path):
        assert utils.find_git_root(str(tmp_path)) is None


class TestCheckInGitRepo:
    def test_allows_path_inside_repo(self, tmp_path):
        git_root = str(tmp_path)
        target = str(tmp_path / "subdir" / "file.txt")
        with patch("rpm_lockfile.utils.find_git_root", return_value=git_root):
            # Should not raise
            utils.check_in_git_repo(target, git_root)

    def test_allows_path_when_not_in_git_repo(self, tmp_path):
        target = "/etc/passwd"
        with patch("rpm_lockfile.utils.find_git_root", return_value=None):
            # No git repo — no restriction
            utils.check_in_git_repo(target, str(tmp_path))

    def test_blocks_absolute_path_outside_repo(self, tmp_path):
        git_root = str(tmp_path)
        with (
            patch("rpm_lockfile.utils.find_git_root", return_value=git_root),
            pytest.raises(ValueError, match="outside the git repository"),
        ):
            utils.check_in_git_repo("/etc/passwd", git_root)

    def test_blocks_traversal_outside_repo(self, tmp_path):
        git_root = str(tmp_path)
        config_dir = str(tmp_path / "config")
        target = str(tmp_path / "config" / ".." / ".." / "etc" / "passwd")
        with (
            patch("rpm_lockfile.utils.find_git_root", return_value=git_root),
            pytest.raises(ValueError, match="outside the git repository"),
        ):
            utils.check_in_git_repo(target, config_dir)

    def test_relative_to_enforces_git_boundary(self, tmp_path):
        git_root = str(tmp_path)
        with (
            patch("rpm_lockfile.utils.find_git_root", return_value=git_root),
            pytest.raises(ValueError, match="outside the git repository"),
        ):
            utils.relative_to(git_root, "/etc/passwd")

    def test_load_variables_file_enforces_git_boundary(self, tmp_path):
        git_root = str(tmp_path)
        with (
            patch("rpm_lockfile.utils.find_git_root", return_value=git_root),
            pytest.raises(ValueError, match="outside the git repository"),
        ):
            utils.load_variables_file("/etc/passwd", git_root)


@pytest.mark.parametrize(
    "items,variables,expected",
    [
        (
            ["nvidia-driver-{VER}", "other-pkg"],
            {"VER": "580.0"},
            ["nvidia-driver-580.0", "other-pkg"],
        ),
        (
            [{"name": "nvidia-driver-{VER}", "arches": {"only": "x86_64"}}],
            {"VER": "580.0"},
            [{"name": "nvidia-driver-580.0", "arches": {"only": "x86_64"}}],
        ),
        (
            ["pkg-{A}-{B}"],
            {"A": "1", "B": "2"},
            ["pkg-1-2"],
        ),
        (
            ["no-placeholders"],
            {"VER": "580.0"},
            ["no-placeholders"],
        ),
        (
            ["pkg-{VER}"],
            {},
            ["pkg-{VER}"],
        ),
    ],
)
def test_subst_vars_in_list(items, variables, expected):
    assert utils.subst_vars_in_list(items, variables) == expected


def test_load_variables_inline(tmp_path):
    sources = [{"inline": {"FOO": "bar", "BAZ": "qux"}}]
    assert utils.load_variables(sources, str(tmp_path)) == {"FOO": "bar", "BAZ": "qux"}


def test_load_variables_file_source(tmp_path):
    fn = tmp_path / "vars.conf"
    fn.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    sources = [{"file": "vars.conf"}]
    assert utils.load_variables(sources, str(tmp_path)) == {"FOO": "bar", "BAZ": "qux"}


def test_load_variables_ordering(tmp_path):
    fn = tmp_path / "vars.conf"
    fn.write_text("FOO=from-file\nBAZ=only-file\n", encoding="utf-8")
    sources = [
        {"file": "vars.conf"},
        {"inline": {"FOO": "from-inline", "QUX": "only-inline"}},
    ]
    result = utils.load_variables(sources, str(tmp_path))
    assert result == {"FOO": "from-inline", "BAZ": "only-file", "QUX": "only-inline"}


def test_load_variables_image(tmp_path):
    with patch("rpm_lockfile.utils._get_image_labels") as mock:
        mock.return_value = {"vcs-ref": "abc123", "architecture": "x86_64"}
        sources = [{"image": "registry.example.com/image:latest"}]
        result = utils.load_variables(sources, str(tmp_path))
    assert result == {"vcs-ref": "abc123", "architecture": "x86_64"}
    mock.assert_called_once_with("registry.example.com/image:latest")


def test_load_variables_containerfile(tmp_path):
    with patch("rpm_lockfile.utils._get_containerfile_labels") as mock:
        mock.return_value = {"vcs-ref": "abc123"}
        sources = [{"containerfile": "Containerfile"}]
        result = utils.load_variables(sources, str(tmp_path))
    assert result == {"vcs-ref": "abc123"}
    mock.assert_called_once_with("Containerfile", str(tmp_path))


def test_load_variables_empty(tmp_path):
    assert utils.load_variables([], str(tmp_path)) == {}


def test_load_variables_unknown_source(tmp_path):
    with pytest.raises(RuntimeError, match="Unknown variable source"):
        utils.load_variables([{"bogus": "value"}], str(tmp_path))


def test_get_labels_with_base_vars():
    obj = {"repoid": "a", "baseurl": "https://example.com/repo"}
    result = utils.get_labels(obj, "/top", base_vars={"FOO": "bar", "BAZ": "qux"})
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_get_labels_base_vars_overridden_by_source():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout=json.dumps(INSPECT_OUTPUT))
        obj = {
            "varsFromImage": "registry.example.com/image:latest",
            "architecture": "override-me",
        }
        result = utils.get_labels(
            obj, "/top", base_vars={"architecture": "ppc64le", "custom": "kept"}
        )
    assert result["architecture"] == "x86_64"
    assert result["custom"] == "kept"
    assert result["vcs-ref"] == "abcdef"
