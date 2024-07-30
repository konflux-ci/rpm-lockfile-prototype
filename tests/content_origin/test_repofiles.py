import json
import subprocess
from unittest.mock import patch, mock_open, Mock, ANY

import pytest

from rpm_lockfile.content_origin import Repo, repofiles


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
    assert repofiles.subst_vars(template, vars) == expected


REPOFILE = """
[repo-0]
baseurl = https://example.com/repo
"""
REPO = Repo(repoid="repo-0", baseurl="https://example.com/repo")


def test_collect_local():
    origin = repofiles.RepofileOrigin("/test")
    with patch("builtins.open", mock_open(read_data=REPOFILE)) as m:
        repos = list(origin.collect(["test.repo"]))

    assert repos == [REPO]
    m.assert_called_once_with("/test/test.repo")


def test_collect_http():
    origin = repofiles.RepofileOrigin("/test")
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"

    repos = origin.collect([repourl])

    assert list(repos) == [REPO]
    origin.session.get.assert_called_once_with(repourl, timeout=ANY)


def test_collect_local_complex():
    origin = repofiles.RepofileOrigin("/test")
    with patch("builtins.open", mock_open(read_data=REPOFILE)) as m:
        repos = list(origin.collect([{"location": "test.repo"}]))

    assert repos == [REPO]
    m.assert_called_once_with("/test/test.repo")


def test_collect_http_complex():
    origin = repofiles.RepofileOrigin("/test")
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"

    repos = origin.collect([{"location": repourl}])

    assert list(repos) == [REPO]
    origin.session.get.assert_called_once_with(repourl, timeout=ANY)


INSPECT_OUTPUT = {
    "Labels": {
        "vcs-ref": "abcdef",
        "architecture": "x86_64",
    },
    "Os": "linux",
}


def test_collect_http_with_vars_from_image():
    origin = repofiles.RepofileOrigin("/test")
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"
    image = "registry.example.com/image:latest"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=json.dumps(INSPECT_OUTPUT)
        )
        repos = list(
            origin.collect(
                [{"location": f"{repourl}?x={{vcs-ref}}", "varsFromImage": image}]
            )
        )

    assert repos == [REPO]
    origin.session.get.assert_called_once_with(f"{repourl}?x=abcdef", timeout=ANY)
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image}"], check=True, stdout=subprocess.PIPE
    )


def test_collect_http_with_vars_from_containerfile():
    origin = repofiles.RepofileOrigin("/test")
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"
    image = "registry.example.com/image:latest"
    CONTAINERFILE = f"FROM {image}\nRUN date\n"

    with patch("builtins.open", mock_open(read_data=CONTAINERFILE)) as m_open, \
            patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            stdout=json.dumps(INSPECT_OUTPUT)
        )
        repos = list(
            origin.collect(
                [
                    {
                        "location": f"{repourl}?x={{vcs-ref}}",
                        "varsFromContainerfile": "Containerfile",
                    }
                ],
            )
        )

    assert repos == [REPO]
    origin.session.get.assert_called_once_with(f"{repourl}?x=abcdef", timeout=ANY)
    m_open.assert_called_once_with("/test/Containerfile")
    mock_run.assert_called_once_with(
        ["skopeo", "inspect", f"docker://{image}"], check=True, stdout=subprocess.PIPE
    )
