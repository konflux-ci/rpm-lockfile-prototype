"""Integration tests for rpm-lockfile-prototype.

These tests invoke the tool as a subprocess and validate the output lockfile
against committed expected lockfiles.

Test cases are autodiscovered from the data/ directory:
  - data/success/<name>/  -- expected to succeed, lockfile compared to expected.lock.yaml
  - data/failure/<name>/  -- expected to fail with non-zero exit code

If a test case directory contains expected.stdout or expected.stderr, each
non-empty, non-comment line is checked as a substring that must appear
somewhere in the corresponding output stream.
"""

import pytest

from .conftest import (
    FAILURE_DIR,
    SUCCESS_DIR,
    load_expected,
    load_expected_stderr,
    load_expected_stdout,
    run_test_case,
    run_test_case_ok,
)

SUCCESSFUL_CASES = sorted(d.name for d in SUCCESS_DIR.iterdir() if d.is_dir())
FAILURE_CASES = sorted(d.name for d in FAILURE_DIR.iterdir() if d.is_dir())


def _check_output(test_case, result):
    """Check stdout and stderr against expected patterns if defined."""
    for stream_name, loader in [("stdout", load_expected_stdout), ("stderr", load_expected_stderr)]:
        patterns = loader(test_case)
        if patterns is None:
            continue
        actual = getattr(result, stream_name)
        for pattern in patterns:
            assert pattern in actual, (
                f"Expected pattern not found in {stream_name}: {pattern!r}\n"
                f"Actual {stream_name}:\n{actual}"
            )


@pytest.mark.parametrize("test_case", SUCCESSFUL_CASES)
def test_lockfile_matches_expected(
    test_case, tmp_path, shared_cache, _skopeo_wrapper_dir, _skopeo_cache_dir
):
    result = run_test_case_ok(
        test_case, tmp_path, shared_cache, _skopeo_wrapper_dir, _skopeo_cache_dir
    )
    expected = load_expected(test_case, result.port)
    assert result.lockfile == expected
    _check_output(test_case, result)


@pytest.mark.parametrize("test_case", FAILURE_CASES)
def test_expected_failure(
    test_case, tmp_path, shared_cache, _skopeo_wrapper_dir, _skopeo_cache_dir
):
    result = run_test_case(
        test_case, tmp_path, shared_cache, _skopeo_wrapper_dir, _skopeo_cache_dir
    )
    assert result.rc != 0
    _check_output(test_case, result)
