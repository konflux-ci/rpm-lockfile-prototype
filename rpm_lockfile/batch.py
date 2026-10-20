"""Resolve several RPM lockfiles with setup data shared for one invocation."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from . import (
    ALLOWERASING_HELP,
    ARCH_HELP,
    TLS_VERIFY_HELP,
    _load_config,
    _rpmdb_source,
    logging_setup,
)
from . import containers

logger = logging.getLogger(__name__)


class BatchContext:
    def __init__(self, directory: Path):
        self.directory = directory
        self.templates: dict[tuple[str, str, bool | None], Path] = {}
        self.stack = contextlib.ExitStack()

    def close(self):
        self.stack.close()

    def image_rpmdb(self, image: str, tls_verify: bool | None = None):
        @contextlib.contextmanager
        def worker(arch: str):
            key = (image, arch, tls_verify)
            if key not in self.templates:
                template = Path(
                    self.stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="rpmdb-", dir=self.directory)
                    )
                )
                containers.setup_rpmdb(template, image, arch, tls_verify=tls_verify)
                self.templates[key] = template
                logger.info("Prepared shared rpmdb for %s on %s", image, arch)
            else:
                logger.info("Reusing shared rpmdb for %s on %s", image, arch)

            # Give each DNF transaction a clean installroot, as in a normal run.
            with tempfile.TemporaryDirectory(prefix="rpmdb-job-") as root:
                shutil.copytree(
                    self.templates[key], root, symlinks=True, dirs_exist_ok=True
                )
                yield root

        return worker

    def dnf_cache_dir(self, image: str | None, arch: str, repos) -> str | None:
        # Respect an explicit cache path supplied by an existing caller.
        if os.getenv("RPM_LOCKFILE_PROTOTYPE_DNF_CACHE"):
            return None

        repo_config = [(repo.repoid, repo.kwargs) for repo in repos]
        key = json.dumps([image, arch, repo_config], sort_keys=True, default=str)
        digest = hashlib.sha256(key.encode()).hexdigest()
        cache = self.directory / "dnf" / digest
        cache.mkdir(parents=True, exist_ok=True)
        return str(cache)


def _read_jobs(parser: argparse.ArgumentParser, manifest: Path):
    try:
        with manifest.open() as stream:
            data = json.load(stream)
    except (OSError, ValueError) as exc:
        parser.error(f"could not read batch manifest: {exc}")

    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        parser.error('batch manifest must contain a "jobs" array')

    jobs = []
    outputs = set()
    for index, job in enumerate(data["jobs"]):
        if not isinstance(job, dict) or not all(
            isinstance(job.get(name), str) and job[name] for name in ("input", "output")
        ):
            parser.error(f"job {index} requires nonempty input and output paths")
        output = Path(job["output"])
        resolved_output = output.resolve()
        if resolved_output in outputs:
            parser.error(f"job {index} repeats output path {output}")
        outputs.add(resolved_output)
        jobs.append((job["input"], output))
    return jobs


def _resolution_options(args):
    options = []
    if args.debug:
        options.append("--debug")
    for arch in args.arch or []:
        options.extend(["--arch", arch])
    if args.allowerasing:
        options.append("--allowerasing")
    if args.tls_verify is not None:
        options.append("--tls-verify" if args.tls_verify else "--no-tls-verify")
    return options


def _run_job(input_file, output_file, options, context):
    try:
        with tempfile.TemporaryDirectory(
            prefix=".rpms-lock-", dir=output_file.parent
        ) as output_directory:
            temporary_output = Path(output_directory) / output_file.name
            # Create with the process umask applied; the resolver
            # reopens this path, so we can close the descriptor now.
            fd = os.open(
                temporary_output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o666,
            )
            os.close(fd)
            if output_file.exists():
                shutil.copymode(output_file, temporary_output)

            from . import main as resolve_one

            resolve_one(
                [input_file, "--outfile", str(temporary_output), *options],
                batch_context=context,
            )
            os.replace(temporary_output, output_file)
            logger.info("Updated %s", output_file)
        return True
    except (Exception, SystemExit):
        logger.exception("Failed to update %s", output_file)
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="batch-rpm-lockfile-prototype")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--arch", action="append", help=ARCH_HELP)
    parser.add_argument("--allowerasing", action="store_true", help=ALLOWERASING_HELP)
    parser.add_argument(
        "--tls-verify",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=TLS_VERIFY_HELP,
    )
    args = parser.parse_args(argv)
    jobs = _read_jobs(parser, args.manifest)
    logging_setup(args.debug)
    options = _resolution_options(args)

    groups = {}
    failed = False
    for input_file, output_file in jobs:
        try:
            config, config_dir, _ = _load_config(input_file)
            source, image, _ = _rpmdb_source(config_dir, config.get("context", {}))
            groups.setdefault((source, image), []).append((input_file, output_file))
        except (Exception, SystemExit):
            logger.exception("Failed to prepare %s", input_file)
            failed = True

    with tempfile.TemporaryDirectory(prefix="rpm-lockfile-batch-") as directory:
        for (source, image), group_jobs in groups.items():
            logger.info("Resolving %d lockfiles for %s", len(group_jobs), image or source)
            index = 0
            while index < len(group_jobs):
                with tempfile.TemporaryDirectory(
                    prefix="group-", dir=directory
                ) as group_directory:
                    context = BatchContext(Path(group_directory))
                    try:
                        while index < len(group_jobs):
                            input_file, output_file = group_jobs[index]
                            if not _run_job(input_file, output_file, options, context):
                                failed = True
                            index += 1
                            if index < len(group_jobs):
                                usage = containers._get_storage_usage(group_directory)
                                if usage is not None and usage >= containers.USAGE_THRESHOLD:
                                    logger.info(
                                        "Storage is %d%% full. Releasing batch caches.",
                                        usage,
                                    )
                                    break
                    finally:
                        context.close()

    return int(failed)
