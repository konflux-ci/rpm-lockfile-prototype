import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock, mock_open, call

import pytest

from rpm_lockfile import caching_wrapper


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        input_file = tmp_path / "rpms.in.yaml"
        output_file = tmp_path / "rpms.lock.yaml"
        cache_dir = tmp_path / "cache"

        input_file.write_text("packages:\n  - bash\n", encoding="utf-8")

        yield {
            "input_file": str(input_file),
            "output_file": str(output_file),
            "cache_dir": cache_dir,
            "tmp_path": tmp_path,
        }


def test_main_with_cache_hit(temp_dirs):
    """Test that when cache exists, it's copied without running resolver."""
    input_file = temp_dirs["input_file"]
    output_file = temp_dirs["output_file"]
    cache_dir = temp_dirs["cache_dir"]

    # Create expected cache file content
    cached_content = "lockfileVersion: 1\npackages: []"

    # Calculate expected hash
    input_hash = hashlib.sha256()
    input_hash.update(b"\0")  # rest args
    input_hash.update(Path(input_file).read_bytes())
    expected_digest = input_hash.hexdigest()

    cache_file = cache_dir / "results" / f"{expected_digest}.yaml"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(cached_content, encoding="utf-8")

    with (
        patch("rpm_lockfile.utils.CACHE_PATH", cache_dir),
        patch("rpm_lockfile.utils.logged_run") as mock_run,
        patch("sys.argv", ["caching-wrapper", input_file, "--outfile", output_file]),
    ):
        caching_wrapper.main()

    # Should not call the resolver since cache hit
    mock_run.assert_not_called()

    # Output file should contain cached content
    assert Path(output_file).read_text() == cached_content


def test_main_with_cache_miss(temp_dirs):
    """Test that when cache doesn't exist, resolver is called and results cached."""
    input_file = temp_dirs["input_file"]
    output_file = temp_dirs["output_file"]
    cache_dir = temp_dirs["cache_dir"]

    resolver_output = "lockfileVersion: 1\npackages:\n  - name: bash"

    def fake_resolver(cmd, **kwargs):
        # Write output to the cache file that would be created
        cache_file_path = cmd[3]  # --outfile argument
        Path(cache_file_path).write_text(resolver_output, encoding="utf-8")

    with (
        patch("rpm_lockfile.utils.CACHE_PATH", cache_dir),
        patch("rpm_lockfile.utils.logged_run", side_effect=fake_resolver) as mock_run,
        patch("sys.argv", ["caching-wrapper", input_file, "--outfile", output_file]),
    ):
        caching_wrapper.main()

    # Should call the resolver once
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "rpm-lockfile-prototype"
    assert call_args[1] == input_file
    assert call_args[2] == "--outfile"
    assert call_args[4:] == []  # no additional args

    # Output file should contain resolver output
    assert Path(output_file).read_text() == resolver_output


def test_main_with_extra_args(temp_dirs):
    """Test that extra arguments are passed to resolver and included in cache key."""
    input_file = temp_dirs["input_file"]
    output_file = temp_dirs["output_file"]
    cache_dir = temp_dirs["cache_dir"]

    extra_args = ["--arch", "x86_64", "--debug"]

    def fake_resolver(cmd, **kwargs):
        cache_file_path = cmd[3]  # --outfile argument
        Path(cache_file_path).write_text("result", encoding="utf-8")

    with (
        patch("rpm_lockfile.utils.CACHE_PATH", cache_dir),
        patch("rpm_lockfile.utils.logged_run", side_effect=fake_resolver) as mock_run,
        patch(
            "sys.argv",
            ["caching-wrapper", input_file, "--outfile", output_file] + extra_args,
        ),
    ):
        caching_wrapper.main()

    # Should call resolver with extra args
    call_args = mock_run.call_args[0][0]
    assert call_args[4:] == extra_args


def test_main_with_custom_cmd():
    """Test that custom command can be specified via environment variable."""
    input_content = "packages: []"

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as inp,
        tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as out,
        tempfile.TemporaryDirectory() as cache_dir,
    ):
        inp.write(input_content)
        inp.flush()

        custom_cmd = "/custom/path/to/resolver"

        def fake_resolver(cmd, **kwargs):
            Path(cmd[3]).write_text("result", encoding="utf-8")

        with (
            patch("rpm_lockfile.utils.CACHE_PATH", Path(cache_dir)),
            patch(
                "rpm_lockfile.utils.logged_run", side_effect=fake_resolver
            ) as mock_run,
            patch.dict(os.environ, {"RPM_LOCKFILE_PROTOTYPE_CMD": custom_cmd}),
            patch("sys.argv", ["caching-wrapper", inp.name, "--outfile", out.name]),
        ):
            caching_wrapper.main()

        # Should use custom command
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == custom_cmd

        os.unlink(inp.name)
        os.unlink(out.name)


def test_cache_key_calculation():
    """Test that cache key includes both input file content and extra args."""
    input_content1 = "packages:\n  - bash"
    input_content2 = "packages:\n  - zsh"

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as inp1,
        tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as inp2,
        tempfile.TemporaryDirectory() as cache_dir,
    ):
        inp1.write(input_content1)
        inp1.flush()
        inp2.write(input_content2)
        inp2.flush()

        def fake_resolver(cmd, **kwargs):
            Path(cmd[3]).write_text("result", encoding="utf-8")

        with (
            patch("rpm_lockfile.utils.CACHE_PATH", Path(cache_dir)),
            patch("rpm_lockfile.utils.logged_run", side_effect=fake_resolver),
        ):
            # First call with file 1
            with patch("sys.argv", ["caching-wrapper", inp1.name]):
                caching_wrapper.main()

            # Second call with file 2
            with patch("sys.argv", ["caching-wrapper", inp2.name]):
                caching_wrapper.main()

        # Should have created different cache files
        cache_results_dir = Path(cache_dir) / "results"
        cache_files = list(cache_results_dir.glob("*.yaml"))
        assert len(cache_files) == 2

        os.unlink(inp1.name)
        os.unlink(inp2.name)


def test_cache_directory_creation():
    """Test that cache directory is created if it doesn't exist."""
    input_content = "packages: []"

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as inp,
        tempfile.TemporaryDirectory() as tmp_dir,
    ):
        inp.write(input_content)
        inp.flush()

        cache_dir = Path(tmp_dir) / "new_cache"
        assert not cache_dir.exists()

        def fake_resolver(cmd, **kwargs):
            Path(cmd[3]).write_text("result", encoding="utf-8")

        with (
            patch("rpm_lockfile.utils.CACHE_PATH", cache_dir),
            patch("rpm_lockfile.utils.logged_run", side_effect=fake_resolver),
            patch("sys.argv", ["caching-wrapper", inp.name]),
        ):
            caching_wrapper.main()

        # Cache directory should be created
        assert (cache_dir / "results").exists()

        os.unlink(inp.name)
