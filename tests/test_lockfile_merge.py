"""Tests for rpm_lockfile.lockfile_merge."""

import unittest

from rpm_lockfile.lockfile_merge import merge_arch_results


class TestMergeArchResults(unittest.TestCase):
    def test_single_result_passthrough(self):
        result = {
            "arch": "x86_64",
            "packages": [{"url": "http://example/pkg.rpm", "name": "pkg"}],
            "source": [],
            "module_metadata": [],
        }
        self.assertIs(merge_arch_results([result]), result)

    def test_merges_distinct_packages(self):
        bare = {
            "arch": "x86_64",
            "packages": [{"url": "http://example/a.rpm", "name": "a"}],
            "source": [],
            "module_metadata": [],
        }
        image = {
            "arch": "x86_64",
            "packages": [{"url": "http://example/b.rpm", "name": "b"}],
            "source": [],
            "module_metadata": [],
        }
        merged = merge_arch_results([bare, image])
        self.assertEqual(
            {pkg["name"] for pkg in merged["packages"]},
            {"a", "b"},
        )

    def test_deduplicates_by_url(self):
        first = {
            "arch": "x86_64",
            "packages": [{"url": "http://example/pkg.rpm", "name": "pkg", "evr": "1-1"}],
            "source": [],
            "module_metadata": [],
        }
        second = {
            "arch": "x86_64",
            "packages": [{"url": "http://example/pkg.rpm", "name": "pkg", "evr": "2-1"}],
            "source": [],
            "module_metadata": [],
        }
        merged = merge_arch_results([first, second])
        self.assertEqual(len(merged["packages"]), 1)
        self.assertEqual(merged["packages"][0]["evr"], "1-1")
