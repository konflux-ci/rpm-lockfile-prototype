import pytest

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
