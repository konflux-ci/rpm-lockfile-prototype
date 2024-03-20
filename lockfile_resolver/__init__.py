#!/usr/bin/env python3

import argparse
import contextlib
import logging
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass

import dnf
import hawkey
import yaml

from . import content_origin, schema

CONTAINERFILE_HELP = """
Load installed packages from base image specified in Containerfile and make
them available during dependency resolution.
"""

LOCAL_SYSTEM_HELP = "Resolve dependencies for current system."

BARE_HELP = "Resolve dependencies as if nothing is installed in the target system."

ARCH_HELP = """
Run the resolution for this architecture. Can be specified multiple times.
"""

PULL_HELP = """
Pull policy for the base image. See `podman-run --pull` for more details. Only
makes sense if Containerfile is used.
"""

IMAGE_HELP = "Use rpmdb from the given image."

VALIDATE_HELP = "Run schema validation on the input file."
PRINT_SCHEMA_HELP = "Print schema for the input file to stdout."


RPMDB_PATH = subprocess.run(
    ["rpm", "--eval", "%_dbpath"], stdout=subprocess.PIPE, check=True, encoding="utf-8"
).stdout.strip()[1:]


def logged_run(cmd, *args, **kwargs):
    logging.info("$ %s", shlex.join(cmd))
    return subprocess.run(cmd, *args, **kwargs)


def setup_rpmdb(cache_dir, baseimage, arch, pull):
    # This may be better done by running `rpm --exportdb` in the container and
    # then `rpm --importdb --root={cache_dir}` on localhost. But selinux blocks
    # it on Fedora 38 and it doesn't seem to work even in Permissive mode.
    dest_dir = os.path.join(cache_dir, RPMDB_PATH)
    os.makedirs(dest_dir)
    cmd = [
        "podman",
        "run",
        "--rm",
        "-ti",
        f"--arch={arch}",
        f"--pull={pull}",
        f"--volume={dest_dir}:/dest:z",
        baseimage,
        "sh",
        "-c",
        "cp -r $(rpm --eval %_dbpath)/* /dest/",
    ]
    logging.info("Copying rpmdb from base image")
    logged_run(cmd, check=True)


def copy_local_rpmdb(cache_dir):
    shutil.copytree("/" + RPMDB_PATH, os.path.join(cache_dir, RPMDB_PATH))


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


def resolver(arch: str, root_dir, repos, solvables):
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
                # TODO we may need to support excluding packages
                base.repos.add_new_repo(repo["repoid"], conf, baseurl=[repo["baseurl"]])
            base.fill_sack(load_system_repo=True)
            # Mark packages for installation
            for solvable in solvables:
                try:
                    base.install(solvable)
                except dnf.exceptions.PackageNotFoundError:
                    raise RuntimeError(f"No match found for {solvable}")
            # And resolve the transaction
            base.resolve()
            # These packages would be installed
            for pkg in base.transaction.install_set:
                packages.add(PackageItem.from_dnf(pkg))
                # Find the corresponding source package
                n, v, r = strip_suffix(pkg.sourcerpm, ".src.rpm").rsplit("-", 2)
                results = base.sack.query().filter(
                    name=n, version=v, release=r, arch="src"
                )
                if len(results) == 0:
                    logging.error("No sources found for %s", pkg)
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


def image_rpmdb(baseimage, pull):
    return rpmdb_preparer(
        lambda root_dir, arch: setup_rpmdb(root_dir, baseimage, arch, pull)
    )


def extract_image(containerfile):
    """Find image mentioned in the first FROM statement in the containerfile."""
    with open(containerfile) as f:
        for line in f:
            if line.startswith("FROM "):
                return line.split()[1]
    raise RuntimeError("Base image could not be identified.")


def process_arch(arch, rpmdb, pull, repos, packages):
    logging.info("Running solver for %s", arch)

    with rpmdb(arch) as root_dir:
        packages, sources = resolver(arch, root_dir, repos, packages)

    return {
        "arch": arch,
        "packages": [p.as_dict() for p in sorted(packages)],
        "sources": [s.as_dict() for s in sorted(sources)],
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

        for key in ("packages", f"packages-{arch}"):
            for entry in data.get(key, []):
                packages.update(entry.split())

        for entry in data.get("repo-packages", []):
            # The repo should not be needed, as the packages should be present
            # in only one place.
            for e in entry.get("packages", []):
                packages.update(e.split())

        # TODO arch-include, conditional-include
        # TODO exclude-packages might be needed here
    return packages


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-f", "--containerfile", default="Containerfile", help=CONTAINERFILE_HELP
    )
    group.add_argument("--image", help=IMAGE_HELP)
    group.add_argument("--local-system", action="store_true", help=LOCAL_SYSTEM_HELP)
    group.add_argument("--bare", action="store_true", help=BARE_HELP)
    group.add_argument("--rpm-ostree-treefile")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--arch", action="append", help=ARCH_HELP)
    parser.add_argument(
        "--pull",
        choices=["always", "missing", "never", "newer"],
        default="newer",
        help=PULL_HELP,
    )
    parser.add_argument("infile", metavar="INPUT_FILE", default="rpms.in.yaml")
    parser.add_argument("--outfile", default="rpms.lock.yaml")
    parser.add_argument("--validate", action="store_true", help=VALIDATE_HELP)
    parser.add_argument(
        "--print-schema", action=schema.HelpAction, help=PRINT_SCHEMA_HELP
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    config_dir = os.path.dirname(os.path.realpath(args.infile))
    with open(args.infile) as f:
        config = yaml.safe_load(f)

    if args.validate:
        schema.validate(config)
        return

    data = {"lockfileVersion": 1, "arches": []}
    arches = args.arch or [platform.machine()]

    if args.local_system and arches != [platform.machine()]:
        parser.error(
            f"Only current architecture ({platform.machine()}) can be resolved against local system.",
        )

    repos = collect_content_origins(config_dir, config["contentOrigin"])

    if args.local_system:
        rpmdb = local_rpmdb()
    elif args.bare or args.rpm_ostree_treefile:
        rpmdb = empty_rpmdb()
    else:
        rpmdb = image_rpmdb(args.image or extract_image(args.containerfile), args.pull)

    # TODO maybe try extracting packages from Containerfile?
    for arch in sorted(arches):
        packages = set()
        if args.rpm_ostree_treefile:
            packages = read_packages_from_treefile(arch, args.rpm_ostree_treefile)
        data["arches"].append(
            process_arch(
                arch,
                rpmdb,
                args.pull,
                repos,
                set(config.get("packages", [])) | packages,
            )
        )

    with open(args.outfile, "w") as f:
        # Sorting by keys would put the version info at the end...
        yaml.dump(data, f, sort_keys=False, explicit_start=True)


if __name__ == "__main__":
    main()
