from unittest.mock import ANY, Mock, mock_open, patch

import pytest

from rpm_lockfile.content_origin import Repo, repofiles

REPOFILE = """
[repo-0]
baseurl = https://example.com/repo
"""
REPO = Repo(repoid="repo-0", kwargs={"baseurl": ["https://example.com/repo"]})


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


def fake_get_labels(obj, config_dir, base_vars=None, tls_verify=None):
    obj.pop("varsFromContainerfile", None)
    obj.pop("varsFromImage", None)
    return {
        "vcs-ref": "abcdef",
        "architecture": "x86_64",
    }


def test_collect_http_with_vars_from_image():
    origin = repofiles.RepofileOrigin("/test")
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"
    image = "registry.example.com/image:latest"

    with patch("rpm_lockfile.utils.get_labels", new=fake_get_labels):
        repos = list(
            origin.collect(
                [{"location": f"{repourl}?x={{vcs-ref}}", "varsFromImage": image}]
            )
        )

    assert repos == [REPO]
    origin.session.get.assert_called_once_with(f"{repourl}?x=abcdef", timeout=ANY)


def test_collect_http_with_vars_from_containerfile(tmpdir):
    origin = repofiles.RepofileOrigin(tmpdir)
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"
    (tmpdir / "Containerfile").write_text(
        "FROM registry.example.com/image:latest\nRUN date\n", encoding="utf-8"
    )

    with patch("rpm_lockfile.utils.get_labels", new=fake_get_labels):
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


def test_collect_git_with_vars_from_image(tmpdir):
    origin = repofiles.RepofileOrigin("/test")
    giturl = "https://example.com/repo.git"
    repofile = "test.repo"

    (tmpdir / repofile).write_text(REPOFILE, encoding="utf-8")

    image = "registry.example.com/image:latest"

    with (
        patch("rpm_lockfile.utils.get_labels", new=fake_get_labels),
        patch("rpm_lockfile.utils.get_file_from_git") as mock_get_file,
    ):
        mock_get_file.return_value = str(tmpdir / repofile)
        repos = list(
            origin.collect(
                [
                    {
                        "giturl": giturl,
                        "file": repofile,
                        "gitref": "{vcs-ref}",
                        "varsFromImage": image,
                    }
                ]
            )
        )

    assert repos == [REPO]
    mock_get_file.assert_called_once_with(giturl, "abcdef", "test.repo")


def test_collect_http_with_global_variables():
    origin = repofiles.RepofileOrigin("/test", variables={"vcs-ref": "abcdef"})
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    repourl = "http://example.com/test.repo"

    repos = list(origin.collect([{"location": f"{repourl}?x={{vcs-ref}}"}]))

    assert repos == [REPO]
    origin.session.get.assert_called_once_with(f"{repourl}?x=abcdef", timeout=ANY)


REPOFILE_DISABLED = """
[enabled-repo]
baseurl = https://example.com/enabled

[disabled-repo]
baseurl = https://example.com/disabled
enabled = 0
"""

REPOFILE_NO_ENABLED_KEY = """
[implicit-enabled]
baseurl = https://example.com/implicit
"""


def test_parse_repofile_skips_disabled():
    origin = repofiles.RepofileOrigin("/test")
    repos = list(origin.parse_repofile(REPOFILE_DISABLED))

    assert len(repos) == 1
    assert repos[0].repoid == "enabled-repo"


@pytest.mark.parametrize("value", ["0", "false", "False", "FALSE", "no", "No"])
def test_parse_repofile_skips_disabled_boolean_spellings(value):
    """DNF accepts 0/false/no (any case) as false for the enabled option."""
    origin = repofiles.RepofileOrigin("/test")
    contents = f"[off]\nbaseurl = https://example.com/off\nenabled = {value}\n"

    assert list(origin.parse_repofile(contents)) == []


@pytest.mark.parametrize("value", ["1", "true", "True", "yes"])
def test_parse_repofile_keeps_enabled_boolean_spellings(value):
    origin = repofiles.RepofileOrigin("/test")
    contents = f"[on]\nbaseurl = https://example.com/on\nenabled = {value}\n"

    assert [r.repoid for r in origin.parse_repofile(contents)] == ["on"]


def test_parse_repofile_enabled_by_default():
    origin = repofiles.RepofileOrigin("/test")
    repos = list(origin.parse_repofile(REPOFILE_NO_ENABLED_KEY))

    assert len(repos) == 1
    assert repos[0].repoid == "implicit-enabled"


def test_collect_local_glob(tmpdir):
    (tmpdir / "a.repo").write_text(REPOFILE, encoding="utf-8")
    (tmpdir / "b.repo").write_text(REPOFILE_NO_ENABLED_KEY, encoding="utf-8")

    origin = repofiles.RepofileOrigin("/test")
    repos = list(origin.collect_local(str(tmpdir / "*.repo")))

    assert len(repos) == 2
    repo_ids = {r.repoid for r in repos}
    assert repo_ids == {"repo-0", "implicit-enabled"}


def test_collect_local_glob_no_match():
    origin = repofiles.RepofileOrigin("/test")
    with pytest.raises(FileNotFoundError, match="No files matching"):
        list(origin.collect_local("/nonexistent/path/*.repo"))


def test_collect_local_absolute_path(tmpdir):
    (tmpdir / "test.repo").write_text(REPOFILE, encoding="utf-8")

    origin = repofiles.RepofileOrigin("/other")
    repos = list(origin.collect_local(str(tmpdir / "test.repo")))

    assert repos == [REPO]


@pytest.mark.parametrize("tls_verify", [None, True, False])
def test_collect_forwards_tls_verify_to_get_labels(tls_verify):
    origin = repofiles.RepofileOrigin("/test", tls_verify=tls_verify)
    origin.session = Mock()
    origin.session.get.return_value = Mock(text=REPOFILE)
    seen = []

    def spy(obj, config_dir, base_vars=None, tls_verify=None):
        seen.append(tls_verify)
        return fake_get_labels(obj, config_dir, base_vars, tls_verify)

    with patch("rpm_lockfile.utils.get_labels", new=spy):
        list(
            origin.collect(
                [
                    {
                        "location": "http://example.com/t.repo?x={vcs-ref}",
                        "varsFromImage": "reg/img:1",
                    }
                ]
            )
        )

    assert seen == [tls_verify]
