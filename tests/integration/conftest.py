"""Test fixtures and helpers for integration tests."""

import dataclasses
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest
import yaml

from . import repogen

DATA_DIR = Path(__file__).parent / "data"
SUCCESS_DIR = DATA_DIR / "success"
FAILURE_DIR = DATA_DIR / "failure"
WRAPPER_DIR = Path(__file__).parent
PORT_PLACEHOLDER = "PORT"


@pytest.fixture(scope="session")
def shared_cache(tmp_path_factory):
    """Shared cache directory for the test session.

    All tests share this cache, so the first test using a given image
    exercises the cold (download) path and subsequent tests hit the warm
    (cached) path.
    """
    return str(tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="session")
def _skopeo_cache_dir(tmp_path_factory):
    """Skopeo download cache directory, shared across all workers.

    Uses SKOPEO_CACHE_DIR if set (persistent across runs), otherwise
    uses a fixed path under the system temp directory so all xdist
    workers share the same cache within a run.
    """
    persistent = os.environ.get("SKOPEO_CACHE_DIR")
    if persistent:
        os.makedirs(persistent, exist_ok=True)
        return persistent
    default = Path(tempfile.gettempdir()) / "rpm-lockfile-test-skopeo-cache"
    default.mkdir(exist_ok=True)
    return str(default)


@pytest.fixture(scope="session")
def _skopeo_wrapper_dir(tmp_path_factory):
    """Create a directory with a 'skopeo' symlink to the caching wrapper."""
    wrapper_dir = tmp_path_factory.mktemp("skopeo-wrapper")
    (wrapper_dir / "skopeo").symlink_to(WRAPPER_DIR / "skopeo-cache")
    return str(wrapper_dir)


class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that suppresses request logging."""

    def log_message(self, format, *args):
        pass


def _make_handler(directory):
    """Create an HTTP handler class that serves from the given directory."""
    def handler(*args, **kwargs):
        return _QuietHTTPHandler(*args, directory=str(directory), **kwargs)
    return handler


def _start_http_server(directory):
    """Start an HTTP server on a random port. Returns (server, port)."""
    server = http.server.HTTPServer(("localhost", 0), _make_handler(directory))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _replace_port(filepath, actual_port):
    """Replace the PORT placeholder with the actual port in a file."""
    text = filepath.read_text()
    text = text.replace(PORT_PLACEHOLDER, str(actual_port))
    filepath.write_text(text)


def _find_test_case_dir(test_case_name):
    """Find the test case directory in success/ or failure/ subdirectories."""
    for parent in (SUCCESS_DIR, FAILURE_DIR):
        candidate = parent / test_case_name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Test case directory not found: {test_case_name} "
        f"(looked in {SUCCESS_DIR} and {FAILURE_DIR})"
    )


def prepare_test_case(test_case_name, tmp_path, port):
    """Set up a test case: generate repos and copy input files.

    Returns the path to the prepared rpms.in.yaml in tmp_path.
    """
    test_case_dir = _find_test_case_dir(test_case_name)

    rpms_in = test_case_dir / "rpms.in.yaml"

    # Generate repos from YAML definitions. The path of each .yaml file
    # relative to repos/ (minus extension) becomes the repo directory.
    # Examples:
    #   repos/test-repo.yaml          -> <tmpdir>/test-repo/repodata/
    #   repos/test-repo/x86_64.yaml   -> <tmpdir>/test-repo/x86_64/repodata/
    #   repos/test-repo/9.yaml        -> <tmpdir>/test-repo/9/repodata/
    repos_dir = test_case_dir / "repos"
    if repos_dir.exists():
        for repo_yaml in sorted(repos_dir.rglob("*.yaml")):
            rel = repo_yaml.relative_to(repos_dir).with_suffix("")
            repo_dir = tmp_path / rel
            repogen.create_repo_from_yaml(repo_yaml, repo_dir)

    # Copy rpms.in.yaml and replace PORT placeholder with actual port
    dest_rpms_in = tmp_path / "rpms.in.yaml"
    shutil.copy2(rpms_in, dest_rpms_in)
    _replace_port(dest_rpms_in, port)

    # Copy Containerfile if present
    for cf_name in ["Containerfile", "Dockerfile"]:
        cf = test_case_dir / cf_name
        if cf.exists():
            shutil.copy2(cf, tmp_path / cf_name)

    # Copy any extra files (.repo files etc.) and replace PORT placeholder
    for extra in test_case_dir.glob("*"):
        if extra.is_dir():
            continue
        if extra.name in ("rpms.in.yaml", "expected.lock.yaml",
                          "Containerfile", "Dockerfile"):
            continue
        dest = tmp_path / extra.name
        shutil.copy2(extra, dest)
        if extra.suffix in (".repo",):
            _replace_port(dest, port)

    return dest_rpms_in


def load_expected(test_case_name, actual_port):
    """Load the expected lockfile, replacing PORT placeholder with actual."""
    expected_path = _find_test_case_dir(test_case_name) / "expected.lock.yaml"
    text = expected_path.read_text()
    text = text.replace(PORT_PLACEHOLDER, str(actual_port))
    return yaml.safe_load(text)


def _load_expected_patterns(test_case_name, filename):
    """Load expected output patterns from a file, if it exists.

    Returns a list of substring patterns that must each appear somewhere
    in the corresponding output stream, or None if the file doesn't exist.
    Lines starting with # and blank lines are ignored.
    """
    expected_path = _find_test_case_dir(test_case_name) / filename
    if not expected_path.exists():
        return None
    return [
        line for line in expected_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def load_expected_stdout(test_case_name):
    """Load expected stdout patterns, if defined."""
    return _load_expected_patterns(test_case_name, "expected.stdout")


def load_expected_stderr(test_case_name):
    """Load expected stderr patterns, if defined."""
    return _load_expected_patterns(test_case_name, "expected.stderr")


@dataclasses.dataclass
class RunResult:
    rc: int
    lockfile: object
    stdout: str
    stderr: str
    port: int


def run_test_case_ok(
    test_case_name, tmp_path, cache_dir,
    _skopeo_wrapper_dir=None, _skopeo_cache_dir=None,
    extra_args=None,
):
    """Run a test case, assert it succeeds, and return RunResult."""
    result = run_test_case(
        test_case_name, tmp_path, cache_dir,
        _skopeo_wrapper_dir, _skopeo_cache_dir, extra_args,
    )
    assert result.rc == 0, f"Tool failed with exit code {result.rc}:\n{result.stderr}"
    assert result.lockfile is not None, "Lockfile was not produced"
    return result


def run_test_case(
    test_case_name, tmp_path, cache_dir,
    _skopeo_wrapper_dir=None, _skopeo_cache_dir=None,
    extra_args=None,
):
    """Prepare and run a test case.

    Returns RunResult.
    """
    server, port = _start_http_server(tmp_path)
    try:
        rpms_in = prepare_test_case(test_case_name, tmp_path, port)
        outfile = tmp_path / "rpms.lock.yaml"

        cmd = ["rpm-lockfile-prototype"]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend([str(rpms_in), "--outfile", str(outfile)])

        # Point XDG_CACHE_HOME at a shared session-scoped directory so the
        # tool doesn't use the user's real cache. The first test to use a
        # given image exercises the cold path; subsequent tests hit the
        # warm path.
        env = {**os.environ, "XDG_CACHE_HOME": cache_dir}

        # Always use the skopeo caching wrapper to avoid redundant downloads.
        if _skopeo_wrapper_dir and _skopeo_cache_dir:
            env["PATH"] = f"{_skopeo_wrapper_dir}:{env.get('PATH', '')}"
            env["SKOPEO_CACHE_DIR"] = _skopeo_cache_dir

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
        )

        lockfile = None
        if outfile.exists():
            with open(outfile) as f:
                lockfile = yaml.safe_load(f)

        return RunResult(
            rc=result.returncode,
            lockfile=lockfile,
            stdout=result.stdout,
            stderr=result.stderr,
            port=port,
        )
    finally:
        server.shutdown()
