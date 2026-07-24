from __future__ import annotations

import argparse
import contextlib
import logging
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import dnf
    import hawkey
    import libdnf.conf
except ImportError:
    print(
        "Python bindings for DNF are missing.",
        "Please install python3-dnf (or equivalent) with system package manager.",
        sep="\n",
        file=sys.stderr,
    )
    sys.exit(127)
import yaml

from . import assumed_provides, containers, content_origin, schema, utils
from .containerfile_packages import (
    analyze_containerfile_stages,
    resolve_builddep_packages,
    select_stage,
)

logger = logging.getLogger(__name__)

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


def mkdir(dir):
    os.mkdir(dir)
    return dir


class MissingFilelists(Exception):
    pass


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
    upgrade_packages: set[str],
    download_filelists: bool = False,
    zchunk: bool | None = None,
    assume_provides: list[str] | None = None,
    match_context_versions: list[str] | None = None,
):
    packages = set()
    sources = set()
    module_metadata = []

    with tempfile.TemporaryDirectory() as cache_dir, dnf.Base() as base:
        # Configure base
        conf = base.conf

        if install_weak_deps is not None:
            conf.install_weak_deps = install_weak_deps

        if zchunk is not None:
            conf.zchunk = zchunk

        if download_filelists:
            conf.optional_metadata_types = ["filelists"]
        conf.installroot = str(root_dir)
        conf.cachedir = os.getenv(
            "RPM_LOCKFILE_PROTOTYPE_DNF_CACHE", os.path.join(cache_dir, "cache")
        )
        conf.logdir = mkdir(os.path.join(cache_dir, "log"))
        conf.persistdir = mkdir(os.path.join(cache_dir, "dnf"))
        conf.substitutions["arch"] = arch
        conf.substitutions["basearch"] = dnf.rpm.basearch(arch)
        conf.substitutions["releasever"] = "unknown"
        try:
            releasever = dnf.rpm.detect_releasever(root_dir)
            if releasever:
                logger.debug("Setting releasever to %s", releasever)
                conf.substitutions["releasever"] = releasever
            else:
                logger.warning("Failed to detect $releasever")
        except dnf.exceptions.Error as exc:
            logger.warning("Failed to detect $releasever: %s", exc)
        # Configure repos
        for repo in repos:
            base.repos.add_new_repo(
                libdnf.conf.ConfigParser.substitute(repo.repoid, conf.substitutions),
                conf,
                **repo.kwargs,
            )
        if assume_provides:
            logger.info(
                "Adding assumed provides repo: %s",
                ", ".join(assume_provides),
            )
            repo_path = assumed_provides.create_repo(cache_dir, assume_provides)
            base.repos.add_new_repo(
                assumed_provides.REPO_ID,
                conf,
                baseurl=[f"file://{repo_path}"],
            )
        base.fill_sack(load_system_repo=True)

        if match_context_versions:
            solvables = utils.pin_context_versions(
                base.sack.query().installed(),
                solvables,
                match_context_versions,
            )

        module_base = dnf.module.module_base.ModuleBase(base)

        # Enable and disable modules as requested
        module_base.disable(module_disable)
        module_base.enable(module_enable)

        # Mark packages to upgrade
        for pkg in upgrade_packages:
            base.upgrade(pkg)
        # Mark packages to remove
        for pkg in reinstall_packages:
            try:
                base.reinstall(pkg)
            except dnf.exceptions.PackagesNotInstalledError:
                logger.warning("Can not reinstall %s: it is not installed", pkg)
            except dnf.exceptions.PackageNotFoundError:
                raise RuntimeError(
                    f"Can not reinstall {pkg}: no package matched in configured repo"
                )
            except dnf.exceptions.PackagesNotAvailableError:
                # The package is not available in the same version as in
                # base image. If we are supposed to update it, it's
                # probably okay and we don't need to reinstall as a new
                # copy will be used for the upgrade. Otherwise report an
                # error.
                if pkg not in upgrade_packages:
                    raise
        # Mark packages for installation
        try:
            base.install_specs(solvables)
        except dnf.exceptions.MarkingErrors as exc:
            if any(spec.startswith("/") for spec in exc.no_match_pkg_specs):
                # User specified a package by absolute path, and we did not
                # download filelists. Let's try again.
                raise MissingFilelists()
            logger.error(exc.value)
            raise RuntimeError(f"DNF error: {exc}")
        # And resolve the transaction
        try:
            base.resolve(allow_erasing=allow_erasing)
        except dnf.exceptions.DepsolveError:
            if not download_filelists:
                # Retry with filelists — they may provide file-level
                # dependencies needed to validate the transaction.
                raise MissingFilelists()
            raise

        modular_packages = {
            nevra
            for module in module_base.get_modules("*")[0]
            for nevra in module.getArtifacts()
        }
        modular_repos = set()

        # These packages would be installed
        for pkg in base.transaction.install_set:
            if pkg.name.startswith(assumed_provides.PACKAGE_PREFIX):
                continue
            if f"{pkg.name}-{pkg.e}:{pkg.v}-{pkg.r}.{pkg.a}" in modular_packages:
                modular_repos.add(pkg.repoid)
            packages.add(PackageItem.from_dnf(pkg))
            # Find the corresponding source package
            if not no_sources:
                n, v, r = strip_suffix(pkg.sourcerpm, ".src.rpm").rsplit("-", 2)
                results = base.sack.query().filter(
                    name=n, version=v, release=r, arch="src"
                )
                if len(results) == 0:
                    logger.warning("No sources found for %s", pkg)
                else:
                    src = results[0]
                    sources.add(PackageItem.from_dnf(src))

        for repoid in modular_repos:
            repo = base.repos[repoid]
            modulemd_path = repo.get_metadata_path("modules")
            if not modulemd_path:
                raise RuntimeError(
                    "Modular package is coming from a repo with no modular metadata"
                )
            module_metadata.append(
                {
                    "url": repo.remote_location(
                        "repodata/" + os.path.basename(modulemd_path)
                    ),
                    "repoid": repo.id,
                    "size": os.stat(modulemd_path).st_size,
                    "checksum": f"sha256:{utils.hash_file(modulemd_path)}",
                }
            )

    return packages, sources, module_metadata


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
    upgrade_packages: set[str],
    zchunk: bool | None = None,
    assume_provides: list[str] | None = None,
    match_context_versions: list[str] | None = None,
):
    logger.info("Running solver for %s", arch)

    with rpmdb(arch) as root_dir:
        for download_filelists in [False, True]:
            try:
                packages, sources, module_metadata = resolver(
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
                    upgrade_packages,
                    download_filelists=download_filelists,
                    zchunk=zchunk,
                    assume_provides=assume_provides,
                    match_context_versions=match_context_versions,
                )
                break
            except MissingFilelists:
                logger.error(
                    "Dependency error indicates we may be missing filelists. Let's try "
                    "again with filelists."
                )

    return {
        "arch": arch,
        "packages": [p.as_dict() for p in sorted(packages)],
        "source": [s.as_dict() for s in sorted(sources)],
        "module_metadata": sorted(module_metadata, key=lambda x: x["url"]),
    }


def collect_content_origins(config_dir, origins, variables=None):
    loaders = content_origin.load()
    repos = []
    for source_type, source_data in origins.items():
        try:
            collector = loaders[source_type](config_dir, variables=variables)
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

        include_raw_value = data.get("include", [])
        treefiles_to_include = (
            [include_raw_value]
            if isinstance(include_raw_value, str)
            else include_raw_value
        )

        for path in treefiles_to_include:
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

    return (not only or arch in only) and (not not_ or arch not in not_)


def read_packages_from_container_yaml(arch):
    packages = set()

    with open("container.yaml") as f:
        data = yaml.safe_load(f)
        for package in data.get("flatpak", {}).get("packages", []):
            if isinstance(package, str):
                packages.add(package)
            else:
                if _arch_matches(package.get("platforms", {}), arch):
                    packages.add(package["name"])

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


@dataclass
class ContainerfilePackages:
    common: set[str] = field(default_factory=set)
    arch_specific: dict[str, set[str]] = field(default_factory=dict)
    upgrade: set[str] = field(default_factory=set)
    reinstall: set[str] = field(default_factory=set)
    module_enable: set[str] = field(default_factory=set)
    builddep: list[str] = field(default_factory=list)


def _extract_containerfile_packages(
    containerfile: str,
    context: dict,
    arches: list[str] | None = None,
) -> ContainerfilePackages:
    result = ContainerfilePackages()

    cf_path = Path(containerfile)
    source_dir = cf_path.parent
    stages = analyze_containerfile_stages(cf_path, source_dir=source_dir, arches=arches)
    selected = select_stage(stages, **_get_containerfile_filters(context))
    if selected:
        result.common.update(selected.packages)
        for arch, pkgs in selected.arch_packages.items():
            result.arch_specific.setdefault(arch, set()).update(pkgs)
        result.upgrade.update(selected.update_targets)
        result.reinstall.update(selected.reinstall_targets)
        result.module_enable.update(selected.module_specs)
        result.builddep = list(selected.builddep_packages)
    if result.common or result.arch_specific:
        logger.info(
            "Extracted %d common and %d arch-specific packages from Containerfile",
            len(result.common),
            sum(len(v) for v in result.arch_specific.values()),
        )
        if result.common:
            logger.debug("Containerfile packages: %s", sorted(result.common))
        for arch, pkgs in sorted(result.arch_specific.items()):
            logger.debug("Containerfile packages [%s]: %s", arch, sorted(pkgs))
        if result.upgrade:
            logger.debug("Containerfile upgrade packages: %s", sorted(result.upgrade))
        if result.reinstall:
            logger.debug(
                "Containerfile reinstall packages: %s", sorted(result.reinstall)
            )
        if result.module_enable:
            logger.debug(
                "Containerfile module enable: %s", sorted(result.module_enable)
            )
        if result.builddep:
            logger.debug("Containerfile builddep patterns: %s", sorted(result.builddep))

    return result


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
    parser.add_argument("--allowerasing", action="store_true", help=ALLOWERASING_HELP)
    args = parser.parse_args()

    logging_setup(args.debug)

    config_dir = os.path.dirname(os.path.realpath(args.infile))
    with open(args.infile) as f:
        config = yaml.safe_load(f)

    schema.validate(config)

    variables = utils.load_variables(config.pop("variables", []), config_dir)

    if variables:
        logger.info("Substitution variables: %s", ", ".join(sorted(variables)))
        for key in (
            "packages",
            "reinstallPackages",
            "upgradePackages",
            "moduleEnable",
            "moduleDisable",
            "assumeProvides",
        ):
            if key in config:
                config[key] = utils.subst_vars_in_list(config[key], variables)
        ctx = config.get("context", {})
        if isinstance(ctx.get("image"), str):
            ctx["image"] = utils.subst_vars(ctx["image"], variables)
        pfc = config.get("packagesFromContainerfile")
        if isinstance(pfc, str):
            config["packagesFromContainerfile"] = utils.subst_vars(pfc, variables)
        elif isinstance(pfc, dict):
            pfc["file"] = utils.subst_vars(pfc["file"], variables)

    data = {"lockfileVersion": 1, "lockfileVendor": "redhat", "arches": []}
    arches = args.arch or config.get("arches") or [platform.machine()]

    context = config.get("context", {})
    allowerasing = args.allowerasing or config.get("allowerasing", False)
    no_sources = config.get("noSources", False)
    assume_provides = config.get("assumeProvides", [])

    local = args.local_system or context.get("localSystem")
    if local and arches != [platform.machine()]:
        parser.error(
            f"Only current architecture ({platform.machine()}) can be resolved against local system.",
        )

    repos = collect_content_origins(config_dir, config["contentOrigin"], variables)

    cf_pkgs = ContainerfilePackages()

    # Determine rpmdb source — independent of package extraction.
    containerfile = None
    is_image_context = True
    if args.local_system or context.get("localSystem"):
        rpmdb = local_rpmdb()
        is_image_context = False
    elif (
        args.bare
        or context.get("bare")
        or args.rpm_ostree_treefile
        or context.get("rpmOstreeTreefile")
    ):
        rpmdb = empty_rpmdb()
        is_image_context = False
    else:
        image = args.image or context.get("image")
        containerfile = (
            args.containerfile
            or _get_containerfile_path(config_dir, context)
            or utils.find_containerfile(Path.cwd())
        )
        if not image and not containerfile:
            parser.error(
                "No base image source found. Please provide one of:\n"
                "  --image <image-name>\n"
                "  --containerfile <path>\n"
                "  --local-system\n"
                "  --bare\n"
                "Or set 'context'in the configuration file,\n"
                "or ensure a Containerfile/Dockerfile exists in the current directory."
            )
        rpmdb = image_rpmdb(
            image
            or utils.extract_image(containerfile, **_get_containerfile_filters(context))
        )

    # Determine package extraction source — independent of rpmdb mode.
    pfc_spec = config.get("packagesFromContainerfile")

    if pfc_spec:
        pfc_context = {"containerfile": pfc_spec}
        pfc_path = _get_containerfile_path(config_dir, pfc_context)
        try:
            cf_pkgs = _extract_containerfile_packages(pfc_path, pfc_context, arches)
        except Exception:
            logger.warning(
                "Failed to extract packages from Containerfile %s; "
                "falling back to explicitly listed packages only.",
                pfc_path,
                exc_info=True,
            )

        if cf_pkgs.builddep:
            source_dir = Path(pfc_path).parent
            builddep_resolved = resolve_builddep_packages(cf_pkgs.builddep, source_dir)
            cf_pkgs.common |= builddep_resolved

    elif containerfile and not config.get("packages"):
        logger.warning(
            "Implicit package extraction from Containerfile is deprecated. "
            "Add 'packagesFromContainerfile' with the Containerfile path "
            "to your config file to preserve this behavior."
        )
        try:
            cf_pkgs = _extract_containerfile_packages(containerfile, context, arches)
        except Exception:
            logger.warning(
                "Failed to extract packages from Containerfile %s; "
                "falling back to explicitly listed packages only.",
                containerfile,
                exc_info=True,
            )

        if cf_pkgs.builddep:
            source_dir = Path(containerfile).parent
            builddep_resolved = resolve_builddep_packages(cf_pkgs.builddep, source_dir)
            cf_pkgs.common |= builddep_resolved

    if config.get("matchContextVersions") and not is_image_context:
        parser.error(
            "matchContextVersions requires a context image or containerfile.\n"
            "It cannot be used with --bare, --local-system, or --rpm-ostree-treefile."
        )

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

        # Merge Containerfile-extracted packages
        packages |= cf_pkgs.common
        packages |= cf_pkgs.arch_specific.get(arch, set())

        data["arches"].append(
            process_arch(
                arch,
                rpmdb,
                repos,
                set(filter_for_arch(arch, config.get("packages", []))) | packages,
                allow_erasing=allowerasing,
                reinstall_packages=set(
                    filter_for_arch(arch, config.get("reinstallPackages", []))
                )
                | cf_pkgs.reinstall,
                module_enable=set(filter_for_arch(arch, config.get("moduleEnable", [])))
                | cf_pkgs.module_enable,
                module_disable=set(
                    filter_for_arch(arch, config.get("moduleDisable", []))
                ),
                no_sources=no_sources,
                install_weak_deps=config.get("installWeakDeps"),
                upgrade_packages=set(
                    filter_for_arch(arch, config.get("upgradePackages", []))
                )
                | cf_pkgs.upgrade,
                zchunk=config.get("zchunk"),
                assume_provides=assume_provides,
                match_context_versions=config.get("matchContextVersions"),
            )
        )

    with open(args.outfile, "w") as f:
        # Sorting by keys would put the version info at the end...
        yaml.dump(data, f, sort_keys=False, explicit_start=True)


if __name__ == "__main__":
    main()
