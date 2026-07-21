import logging

import pytest
from unittest.mock import Mock

from rpm_lockfile.utils import pin_context_versions


def _make_pkg(name, version, release, epoch=0):
    pkg = Mock()
    pkg.name = name
    pkg.epoch = epoch
    pkg.version = version
    pkg.release = release
    return pkg


class TestPinContextVersions:
    def test_pins_matching_packages(self):
        installed = [
            _make_pkg("kernel-core", "5.14.0", "570.120.1.el9_6"),
            _make_pkg("kernel-modules", "5.14.0", "570.120.1.el9_6"),
        ]
        solvables = {"kernel-core", "kernel-devel", "kernel-headers", "gcc"}

        result = pin_context_versions(installed, solvables, ["kernel-*"])

        assert "kernel-core-5.14.0-570.120.1.el9_6" in result
        assert "kernel-devel-5.14.0-570.120.1.el9_6" in result
        assert "kernel-headers-5.14.0-570.120.1.el9_6" in result
        assert "gcc" in result
        assert "kernel-core" not in result
        assert "kernel-devel" not in result

    def test_no_match_returns_unchanged(self):
        installed = [_make_pkg("bash", "5.1.8", "9.el9")]
        solvables = {"kernel-devel", "gcc"}

        result = pin_context_versions(installed, solvables, ["kernel-*"])

        assert result == {"kernel-devel", "gcc"}

    def test_multiple_patterns(self):
        installed = [
            _make_pkg("kernel-core", "5.14.0", "570.120.1.el9_6"),
            _make_pkg("glibc", "2.34", "100.el9"),
        ]
        solvables = {"kernel-devel", "glibc-devel", "gcc"}

        result = pin_context_versions(
            installed, solvables, ["kernel-*", "glibc*"]
        )

        assert "kernel-devel-5.14.0-570.120.1.el9_6" in result
        assert "glibc-devel-2.34-100.el9" in result
        assert "gcc" in result

    def test_raises_on_version_mismatch(self):
        installed = [
            _make_pkg("kernel-core", "5.14.0", "570.120.1.el9_6"),
            _make_pkg("kernel-tools", "5.14.0", "570.128.1.el9_6"),
        ]
        solvables = {"kernel-devel"}

        with pytest.raises(RuntimeError, match="different versions"):
            pin_context_versions(installed, solvables, ["kernel-*"])

    def test_no_installed_packages_warns(self, caplog):
        solvables = {"kernel-devel"}

        with caplog.at_level(logging.WARNING):
            result = pin_context_versions([], solvables, ["kernel-*"])

        assert "no installed packages matched" in caplog.text
        assert result == {"kernel-devel"}

    def test_empty_patterns_no_change(self):
        installed = [_make_pkg("kernel-core", "5.14.0", "570.120.1.el9_6")]
        solvables = {"kernel-devel", "gcc"}

        result = pin_context_versions(installed, solvables, [])

        assert result == {"kernel-devel", "gcc"}

    def test_non_matching_solvables_untouched(self):
        installed = [_make_pkg("kernel-core", "5.14.0", "570.120.1.el9_6")]
        solvables = {"vim", "tmux", "git"}

        result = pin_context_versions(installed, solvables, ["kernel-*"])

        assert result == {"vim", "tmux", "git"}

    def test_epoch_included_when_nonzero(self):
        installed = [_make_pkg("dbus", "1.12.20", "8.el9", epoch=1)]
        solvables = {"dbus", "gcc"}

        result = pin_context_versions(installed, solvables, ["dbus"])

        assert "dbus-1:1.12.20-8.el9" in result
        assert "gcc" in result
