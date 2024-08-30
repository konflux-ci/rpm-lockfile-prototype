from unittest.mock import patch

from rpm_lockfile.content_origin import Repo
from rpm_lockfile.content_origin.repos import RepoOrigin


def test_collect_simple(tmpdir):
    baseurl = "https://example.com/repo"
    config = [{"repoid": "a", "baseurl": baseurl}]
    origin = RepoOrigin(tmpdir)
    repos = list(origin.collect(config))

    assert repos == [Repo(repoid="a", baseurl=baseurl)]


LABELS = {
    "vcs-ref": "abcdef",
    "architecture": "x86_64",
}

TEMPLATE_CONFIG = {
    "repoid": "a", "baseurl": "https://example.com/{architecture}/repo"
}
EXPANDED_REPO = Repo(repoid="a", baseurl="https://example.com/x86_64/repo")


def test_collect_with_vars_from_image(tmpdir):
    origin = RepoOrigin(tmpdir)
    image = "registry.example.com/image:latest"

    with patch("rpm_lockfile.utils.get_labels") as mock_get_labels:
        mock_get_labels.return_value = LABELS
        repos = list(origin.collect([TEMPLATE_CONFIG | {"varsFromImage": image}]))

    assert repos == [EXPANDED_REPO]
    mock_get_labels.assert_called_once_with(image, None)


def test_collect_with_vars_from_containerfile(tmpdir):
    origin = RepoOrigin(tmpdir)
    (tmpdir / "Containerfile").write_text(
        "FROM registry.example.com/image:latest\nRUN date\n", encoding="utf-8"
    )

    with patch("rpm_lockfile.utils.get_labels") as mock_get_labels:
        mock_get_labels.return_value = LABELS
        repos = list(
            origin.collect(
                [TEMPLATE_CONFIG | {"varsFromContainerfile": "Containerfile"}]
            )
        )

    assert repos == [EXPANDED_REPO]
    mock_get_labels.assert_called_once_with(None, tmpdir / "Containerfile")
