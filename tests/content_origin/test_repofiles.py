from unittest.mock import patch, mock_open, Mock, ANY

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


def fake_get_labels(obj, config_dir):
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

    with patch("rpm_lockfile.utils.get_labels", new=fake_get_labels):
        with patch("rpm_lockfile.utils.get_file_from_git") as mock_get_file:
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
