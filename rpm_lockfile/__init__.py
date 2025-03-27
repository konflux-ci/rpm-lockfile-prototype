#!/usr/bin/env python3

import argparse
import contextlib
import logging
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from dataclasses import asdict, dataclass
import subprocess

import yaml

from . import containers, content_origin, schema, utils

CONTAINERFILE_HELP = """
Load installed packages from base image specified in Containerfile and make
them available during dependency resolution.
"""

LOCAL_SYSTEM_HELP = "Resolve dependencies for current system."

BARE_HELP = "Resolve dependencies as if nothing is installed in the target system."

FLATPAK_HELP = """
Determine the set of packages from the flatpak: section of container.yaml.
"""

ARCH_HELP = """
Run the resolution for this architecture. Can be specified multiple times.
"""

IMAGE_HELP = "Use rpmdb from the given image."

VALIDATE_HELP = "Run schema validation on the input file."
PRINT_SCHEMA_HELP = "Print schema for the input file to stdout."
ALLOWERASING_HELP = "Allow  erasing  of  installed  packages to resolve dependencies."


def copy_local_rpmdb(cache_dir):
    shutil.copytree("/" + utils.RPMDB_PATH, os.path.join(cache_dir, utils.RPMDB_PATH))


def filter_for_arch(arch, pkgs):
    """Given an iterator with packages, keep only those that should be included
    on the given architecture.
    """
    for pkg in pkgs:
        if isinstance(pkg, str):
            yield pkg
        else:
            if _arch_matches(pkg.get("arches", {}), arch):
                yield pkg["name"]


def resolver(
    arch: str,
    root_dir,
    repos,
    solvables,
    allow_erasing: bool,
    reinstall_packages: set[str],
    module_enable: set[str],
    module_disable: set[str],
    no_sources: bool,
    install_weak_deps: bool,
):
    input_file = {
        "contentOrigin": {
            "repos": [r.as_dict() for r in repos],
        },
        "packages": list(solvables),
        "reinstallPackages": list(reinstall_packages),
        "moduleEnable": list(module_enable),
        "moduleDisable": list(module_disable),
        "allowerasing": allow_erasing,
        "arches": [arch],
    }

    # Without the directory the plugin will fail on writing history database.
    os.makedirs(Path(root_dir) / "var/lib/dnf")

    with tempfile.NamedTemporaryFile("w+") as input_f:
        yaml.dump(input_file, input_f)
        input_f.flush()

        cmd = [
            "dnf4", "--verbose", f"--installroot={root_dir}", f"--forcearch={arch}",
        ]
        if install_weak_deps is not None:
            cmd.append(f"--setopt=install_weak_deps={install_weak_deps}")

        subprocess.run(
            cmd + [
                "manifest",
                "new",
                "--use-system",
                f"--input={input_f.name}",
                f"--manifest=packages.manifest.{arch}.yaml",
            ],
            check=True,
        )


def rpmdb_preparer(func=None):
    @contextlib.contextmanager
    def worker(arch):
        with tempfile.TemporaryDirectory() as root_dir:
            if func:
                func(root_dir, arch)
            yield root_dir

    return worker


def empty_rpmdb():
    return rpmdb_preparer()


def local_rpmdb():
    return rpmdb_preparer(lambda root_dir, _: copy_local_rpmdb(root_dir))


def image_rpmdb(baseimage):
    return rpmdb_preparer(
        lambda root_dir, arch: containers.setup_rpmdb(root_dir, baseimage, arch)
    )


def process_arch(
    arch,
    rpmdb,
    repos,
    packages,
    allow_erasing,
    reinstall_packages: set[str],
    module_enable: set[str],
    module_disable: set[str],
    no_sources: bool,
    install_weak_deps: bool,
):
    logging.info("Running solver for %s", arch)

    with rpmdb(arch) as root_dir:
        resolver(
            arch,
            root_dir,
            repos,
            packages,
            allow_erasing,
            reinstall_packages,
            module_enable,
            module_disable,
            no_sources,
            install_weak_deps,
        )


def collect_content_origins(config_dir, origins):
    loaders = content_origin.load()
    repos = []
    for source_type, source_data in origins.items():
        try:
            collector = loaders[source_type](config_dir)
        except KeyError:
            raise RuntimeError(f"Unknown content origin '{source_type}'")
        repos.extend(collector.collect(source_data))
    return repos


def read_packages_from_treefile(arch, treefile):
    # Reference: https://coreos.github.io/rpm-ostree/treefile/
    # TODO this should move to a separate module
    packages = set()
    with open(treefile) as f:
        data = yaml.safe_load(f)
        for path in data.get("include", []):
            packages.update(
                read_packages_from_treefile(
                    arch, os.path.join(os.path.dirname(treefile), path)
                )
            )

        if arch_include := data.get("arch-include", {}).get(arch):
            packages.update(
                read_packages_from_treefile(
                    arch, os.path.join(os.path.dirname(treefile), arch_include)
                )
            )

        for key in ("packages", f"packages-{arch}"):
            for entry in data.get(key, []):
                packages.update(entry.split())

        for entry in data.get("repo-packages", []):
            # The repo should not be needed, as the packages should be present
            # in only one place.
            for e in entry.get("packages", []):
                packages.update(e.split())

        # TODO conditional-include
        # TODO exclude-packages might be needed here
    return packages


def _arch_matches(spec, arch):
    only = spec.get("only", [])
    if isinstance(only, str):
        only = [only]
    not_ = spec.get("not", [])
    if isinstance(not_, str):
        not_ = [not_]

    return (
        (not only or arch in only) and
        (not not_ or arch not in not_)
    )


def read_packages_from_container_yaml(arch):
    packages = set()

    with open("container.yaml") as f:
        data = yaml.safe_load(f)
        for package in data.get("flatpak", {}).get("packages", []):
            if isinstance(package, str):
                packages.add(package)
            else:
                if _arch_matches(package.get("platforms", {}), arch):
                    packages.add(package['name'])

    return packages


def _get_containerfile_path(config_dir, context):
    cf = context.get("containerfile")
    if isinstance(cf, dict):
        return utils.relative_to(config_dir, cf["file"])
    if isinstance(cf, str):
        return utils.relative_to(config_dir, cf)
    return None


def _get_containerfile_filters(context):
    cf = context.get("containerfile")
    if isinstance(cf, dict):
        return {
            "stage_num": cf.get("stageNum"),
            "stage_name": cf.get("stageName"),
            "image_pattern": cf.get("imagePattern"),
        }
    return {}


def logging_setup(debug=False):

    class ExcludeErrorsFilter(logging.Filter):
        def filter(self, record):
            """Only lets through log messages with log level below ERROR."""
            return record.levelno < logging.ERROR

    console_stdout = logging.StreamHandler(stream=sys.stdout)
    console_stdout.addFilter(ExcludeErrorsFilter())
    console_stdout.setLevel(logging.DEBUG if debug else logging.INFO)

    console_stderr = logging.StreamHandler(stream=sys.stderr)
    console_stderr.setLevel(logging.ERROR)

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        handlers=[console_stdout, console_stderr],
    )


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-f", "--containerfile", help=CONTAINERFILE_HELP)
    group.add_argument("--image", help=IMAGE_HELP)
    group.add_argument("--local-system", action="store_true", help=LOCAL_SYSTEM_HELP)
    group.add_argument("--bare", action="store_true", help=BARE_HELP)
    group.add_argument("--rpm-ostree-treefile")
    parser.add_argument("--flatpak", action="store_true", help=FLATPAK_HELP)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--arch", action="append", help=ARCH_HELP)
    parser.add_argument(
        "--pull",
        choices=["always", "missing", "never", "newer"],
        default="newer",
        help="DEPRECATED",
    )
    parser.add_argument("infile", metavar="INPUT_FILE", default="rpms.in.yaml")
    parser.add_argument("--outfile", default="rpms.lock.yaml")
    parser.add_argument(
        "--print-schema", action=schema.HelpAction, help=PRINT_SCHEMA_HELP
    )
    parser.add_argument(
        "--allowerasing", action="store_true", help=ALLOWERASING_HELP
    )
    args = parser.parse_args()

    logging_setup(args.debug)

    config_dir = os.path.dirname(os.path.realpath(args.infile))
    with open(args.infile) as f:
        config = yaml.safe_load(f)

    schema.validate(config)

    arches = args.arch or config.get("arches") or [platform.machine()]

    context = config.get("context", {})
    allowerasing = args.allowerasing or config.get("allowerasing", False)
    no_sources = config.get("noSources", False)

    local = args.local_system or context.get("localSystem")
    if local and arches != [platform.machine()]:
        parser.error(
            f"Only current architecture ({platform.machine()}) can be resolved against local system.",
        )

    repos = collect_content_origins(config_dir, config["contentOrigin"])

    if args.local_system or context.get("localSystem"):
        rpmdb = local_rpmdb()
    elif args.bare or context.get("bare") or args.rpm_ostree_treefile:
        rpmdb = empty_rpmdb()
    elif args.rpm_ostree_treefile or context.get("rpmOstreeTreefile"):
        rpmdb = empty_rpmdb()
    else:
        image = args.image or context.get("image")
        containerfile = (
            args.containerfile
            or _get_containerfile_path(config_dir, context)
            or utils.find_containerfile(Path.cwd())
        )
        rpmdb = image_rpmdb(
            image or utils.extract_image(
                containerfile, **_get_containerfile_filters(context)
            )
        )

    # TODO maybe try extracting packages from Containerfile?
    for arch in sorted(arches):
        packages = set()
        if args.rpm_ostree_treefile or context.get("rpmOstreeTreefile"):
            packages = read_packages_from_treefile(
                arch,
                args.rpm_ostree_treefile
                or utils.relative_to(config_dir, context.get("rpmOstreeTreefile")),
            )
        elif args.flatpak or context.get("flatpak"):
            packages = read_packages_from_container_yaml(arch)
        process_arch(
            arch,
            rpmdb,
            repos,
            set(filter_for_arch(arch, config.get("packages", []))) | packages,
            allow_erasing=allowerasing,
            reinstall_packages=set(
                filter_for_arch(arch, config.get("reinstallPackages", []))
            ),
            module_enable=set(
                filter_for_arch(arch, config.get("moduleEnable", []))
            ),
            module_disable=set(
                filter_for_arch(arch, config.get("moduleDisable", []))
            ),
            no_sources=no_sources,
            install_weak_deps=config.get("installWeakDeps"),
        )


if __name__ == "__main__":
    main()
