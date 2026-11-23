import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import rpm_lockfile
from rpm_lockfile.batch import BatchContext
from rpm_lockfile.batch import main as batch_main


class BatchTests(unittest.TestCase):
    def test_batch_continues_after_failure_and_preserves_failed_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = [
                {"input": name, "output": str(root / f"{name}.lock.yaml")}
                for name in ("first", "broken", "last")
            ]
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"jobs": jobs}))
            (root / "broken.lock.yaml").write_text("old content")
            (root / "last.lock.yaml").write_text("previous content")
            (root / "last.lock.yaml").chmod(0o640)
            (root / "reference").write_text("reference")
            calls = []

            def resolve(argv, *, batch_context):
                calls.append(argv[0])
                if argv[0] == "broken":
                    raise RuntimeError("synthetic failure")
                Path(argv[2]).write_text(argv[0])

            with patch(
                "rpm_lockfile.batch._load_config",
                return_value=({"context": {"image": "example.test/image"}}, ".", {}),
            ), patch("rpm_lockfile.main", side_effect=resolve):
                self.assertEqual(batch_main(["--manifest", str(manifest)]), 1)

            self.assertEqual(calls, ["first", "broken", "last"])
            self.assertEqual((root / "first.lock.yaml").read_text(), "first")
            self.assertEqual((root / "broken.lock.yaml").read_text(), "old content")
            self.assertEqual((root / "last.lock.yaml").read_text(), "last")
            self.assertEqual(
                stat.S_IMODE((root / "first.lock.yaml").stat().st_mode),
                stat.S_IMODE((root / "reference").stat().st_mode),
            )
            self.assertEqual(
                stat.S_IMODE((root / "last.lock.yaml").stat().st_mode), 0o640
            )

    def test_reuses_template_but_isolates_each_installroot(self):
        with tempfile.TemporaryDirectory() as directory:
            context = BatchContext(Path(directory))
            calls = []

            def prepare(destination, image, arch, tls_verify=None):
                calls.append((image, arch))
                (destination / "rpmdb").write_text("original")

            try:
                with patch(
                    "rpm_lockfile.batch.containers.setup_rpmdb", side_effect=prepare
                ):
                    provider = context.image_rpmdb("example.test/image:tag")
                    with provider("x86_64") as first:
                        (Path(first) / "rpmdb").write_text("changed")
                    with provider("x86_64") as second:
                        self.assertEqual(
                            (Path(second) / "rpmdb").read_text(), "original"
                        )
                self.assertEqual(calls, [("example.test/image:tag", "x86_64")])
            finally:
                context.close()

    def test_metadata_cache_is_shared_only_for_matching_repos(self):
        with tempfile.TemporaryDirectory() as directory:
            context = BatchContext(Path(directory))
            one = [SimpleNamespace(repoid="repo", kwargs={"baseurl": ["file:///one"]})]
            two = [SimpleNamespace(repoid="repo", kwargs={"baseurl": ["file:///two"]})]
            try:
                with patch.dict("os.environ", {}, clear=True):
                    first = context.dnf_cache_dir("image", "x86_64", one)
                    self.assertEqual(
                        first, context.dnf_cache_dir("image", "x86_64", one)
                    )
                    self.assertNotEqual(
                        first, context.dnf_cache_dir("image", "x86_64", two)
                    )
                    self.assertNotEqual(
                        first, context.dnf_cache_dir("image", "aarch64", one)
                    )
            finally:
                context.close()

    def test_old_input_named_batch_still_uses_legacy_parser(self):
        with patch("rpm_lockfile.batch.main") as batch:
            with self.assertRaises(FileNotFoundError):
                rpm_lockfile.main(["batch"])
            batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
