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

try:
    import dnf
    import hawkey
except ImportError:
    print(
        "Python bindings for DNF are missing.",
        "Please install python3-dnf (or equivalent) with system package manager.",
        sep="\n",
        file=sys.stderr
    )
    sys.exit(127)
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


def strip_suffix(s, suf):
    if s.endswith(suf):
        return s[: -len(suf)]
    return s


@dataclass(frozen=True, order=True)
class PackageItem:
    url: str
    repoid: str
    size: int
    checksum: str = None
    name: str = None
    evr: str = None
    sourcerpm: str = None

    @classmethod
    def from_dnf(cls, pkg):
        return cls(
            pkg.remote_location(),
            pkg.repoid,
            pkg.downloadsize,
            f"{hawkey.chksum_name(pkg.chksum[0])}:{pkg.chksum[1].hex()}",
            pkg.name,
            pkg.evr,
            pkg.sourcerpm,
        )

    def as_dict(self):
        d = asdict(self)
        if not self.sourcerpm:
            del d["sourcerpm"]
        return d


def mkdir(dir):
    os.mkdir(dir)
    return dir


def resolver(
    arch: str,
    root_dir,
    repos,
    solvables,
    allow_erasing: bool,
    reinstall_packages: set[str],
):
    packages = set()
    sources = set()

    with tempfile.TemporaryDirectory() as cache_dir:
        with dnf.Base() as base:
            # Configure base
            conf = base.conf
            conf.installroot = str(root_dir)
            conf.cachedir = os.path.join(cache_dir, "cache")
            conf.logdir = mkdir(os.path.join(cache_dir, "log"))
            conf.persistdir = mkdir(os.path.join(cache_dir, "dnf"))
            conf.substitutions["arch"] = conf.substitutions["basearch"] = arch
            # Configure repos
            for repo in repos:
                base.repos.add_new_repo(repo.repoid, conf, baseurl=[repo.baseurl], **repo.kwargs)
            base.fill_sack(load_system_repo=True)
            # Mark packages to remove
            for pkg in reinstall_packages:
                try:
                    base.reinstall(pkg)
                except dnf.exceptions.PackagesNotInstalledError:
                    raise RuntimeError(f"Can not reinstall {pkg}: it is not installed")
                except dnf.exceptions.PackageNotFoundError:
                    raise RuntimeError(
                        f"Can not reinstall {pkg}: no package matched in configured repo"
                    )
            # Mark packages for installation
            for solvable in solvables:
                try:
                    base.install(solvable)
                except dnf.exceptions.PackageNotFoundError:
                    raise RuntimeError(f"No match found for {solvable}")
            # And resolve the transaction
            base.resolve(allow_erasing=allow_erasing)
            # These packages would be installed
            for pkg in base.transaction.install_set:
                packages.add(PackageItem.from_dnf(pkg))
                # Find the corresponding source package
                n, v, r = strip_suffix(pkg.sourcerpm, ".src.rpm").rsplit("-", 2)
                results = base.sack.query().filter(
                    name=n, version=v, release=r, arch="src"
                )
                if len(results) == 0:
                    logging.warning("No sources found for %s", pkg)
                else:
                    src = results[0]
                    sources.add(PackageItem.from_dnf(src))

    return packages, sources


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
    arch, rpmdb, repos, packages, allow_erasing, reinstall_packages: set[str]
):
    logging.info("Running solver for %s", arch)

    with rpmdb(arch) as root_dir:
        packages, sources = resolver(
            arch, root_dir, repos, packages, allow_erasing, reinstall_packages
        )

    return {
        "arch": arch,
        "packages": [p.as_dict() for p in sorted(packages)],
        "source": [s.as_dict() for s in sorted(sources)],
    }


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


def read_packages_from_container_yaml(arch):
    packages = set()

    with open("container.yaml") as f:
        data = yaml.safe_load(f)
        for package in data.get("flatpak", {}).get("packages", []):
            if isinstance(package, str):
                packages.add(package)
            else:
                platforms = package.get('platforms', {})
                only = platforms.get('only', [])
                if isinstance(only, str):
                    only = [only]
                not_ = platforms.get('not', [])
                if isinstance(not_, str):
                    not_ = [not_]

                if (
                    (not only or arch in only) and
                    (not not_ or arch not in not_)
                ):
                    packages.add(package['name'])

    return packages


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

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    config_dir = os.path.dirname(os.path.realpath(args.infile))
    with open(args.infile) as f:
        config = yaml.safe_load(f)

    schema.validate(config)

    data = {"lockfileVersion": 1, "lockfileVendor": "redhat", "arches": []}
    arches = args.arch or config.get("arches") or [platform.machine()]

    context = config.get("context", {})

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
            or utils.relative_to(config_dir, context.get("containerfile"))
            or utils.find_containerfile(Path.cwd())
        )
        rpmdb = image_rpmdb(image or utils.extract_image(containerfile))

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
        data["arches"].append(
            process_arch(
                arch,
                rpmdb,
                repos,
                set(config.get("packages", [])) | packages,
                allow_erasing=args.allowerasing,
                reinstall_packages=set(config.get("reinstallPackages", [])),
            )
        )

    with open(args.outfile, "w") as f:
        # Sorting by keys would put the version info at the end...
        yaml.dump(data, f, sort_keys=False, explicit_start=True)


if __name__ == "__main__":
    main()
