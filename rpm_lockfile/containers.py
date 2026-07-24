import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from . import utils

logger = logging.getLogger(__name__)

# Known locations for rpmdb inside the image; files/lib is for
# Flatpak runtime images.
RPMDB_PATHS = [
    "usr/lib/sysimage/rpm",
    "var/lib/rpm",
    "files/lib/sysimage/rpm",
    "usr/share/rpm",
]

# Storage usage limit. If the filesystem with the cache fills up over this
# limit, nothing new will be added into the cache.
# Value in percent.
USAGE_THRESHOLD = 80


def _copy_image(baseimage, arch, destdir):
    """Download image into given location."""
    if not utils.check_image_spec(baseimage):
        logger.warning(
            """
            Image specification is missing registry. Skopeo will use some
            registry as a default. If the build system uses a different one,
            you will see strange errors during the prefetch and build steps.
            """
        )

    cmd = [
        "skopeo",
        f"--override-arch={arch}",
        "copy",
        f"docker://{baseimage}",
        f"dir:{destdir}",
    ]
    utils.logged_run(cmd, check=True)


def setup_rpmdb(dest_dir, baseimage, arch):
    """
    Extract rpmdb from `baseimage` for `arch` to `dest_dir`.
    """
    image, _, digest = utils.split_image(baseimage)

    if not digest:
        # We don't have a digest yet, so find the correct one from the
        # registry.
        digest = utils.inspect_image(baseimage, arch)["Digest"]

    # Construct a new image pull spec with the digest (we no longer need the
    # tag). We need to pull the image by the digest used in the cache.
    # Otherwise we would risk a race condition if the image got updated between
    # calls to `skopeo inspect` and `skopeo copy`.
    image = utils.make_image_spec(image, None, digest)

    # The images need to be cached per-architecture. The same digest is used
    # reference the same image.
    cache = utils.CACHE_PATH / "rpmdbs" / arch / digest
    if not cache.exists():
        # If we don't have anything cached, extract the rpmdb from the image
        # into the cache.
        _online_setup_rpmdb(cache, image, arch)
    else:
        logger.info("Using already downloaded rpmdb")

    # Copy the cache to the correct destination directory. Use
    # symlinks=True to preserve symlinks as-is. The cache may contain
    # symlinks from the image (e.g. usr/lib/sysimage/rpm pointing to
    # ../../share/rpm in ostree-based images).
    shutil.copytree(cache, dest_dir, symlinks=True, dirs_exist_ok=True)

    _maybe_cleanup(cache)


def _online_setup_rpmdb(dest_dir, baseimage, arch):
    arch = utils.translate_arch(arch)

    # Ensure the top destination directory exists. The base image may not
    # contain any rpmdb, in which case we would repeatedly have to download the
    # image instead of caching the empty data.
    dest_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        _copy_image(baseimage, arch, tmpdir)

        # The manifest is always in the same location, and contains information
        # about individual layers.
        with open(tmpdir / "manifest.json") as f:
            manifest = json.load(f)

        # In ostree-based images (e.g. bootc), rpmdb entries can be
        # hardlinks to content-addressed objects stored elsewhere in
        # the tar (under sysroot/ostree/repo/objects/). When extractall
        # can not create a hardlink because the target was filtered
        # out, it falls back to calling the filter on the target
        # member. Pre-scan all layers to collect these targets so the
        # filter can accept them.
        _hardlink_targets = set()
        for layer in manifest["layers"]:
            digest = layer["digest"].split(":", 1)[1]
            with tarfile.open(tmpdir / digest) as archive:
                for member in archive:
                    if not member.islnk():
                        continue
                    for candidate_path in RPMDB_PATHS:
                        if Path(member.name).is_relative_to(candidate_path):
                            _hardlink_targets.add(member.linkname)
                            break

        def filter_rpmdb(member, path):
            for candidate_path in RPMDB_PATHS:
                if Path(member.name).is_relative_to(candidate_path):
                    return tarfile.data_filter(member, path)
            if member.name in _hardlink_targets:
                return tarfile.data_filter(member, path)

        # One layer at a time...
        for layer in manifest["layers"]:
            logger.info("Extracting rpmdb from layer %s", layer["digest"])
            digest = layer["digest"].split(":", 1)[1]
            # ...find all files in interesting locations and extract them to
            # the destination cache.
            with tarfile.open(tmpdir / digest) as archive:
                archive.extractall(path=dest_dir, filter=filter_rpmdb)

        # Determine which rpmdb paths actually have content on disk.
        # Tracking accepted members in the filter is unreliable:
        # extraction can silently skip members (e.g. hardlinks whose
        # targets were not extracted).
        dbpaths = {
            p
            for p in RPMDB_PATHS
            if (dest_dir / p).is_dir() and any((dest_dir / p).iterdir())
        }

        if dbpaths and utils.RPMDB_PATH not in dbpaths:
            # If we have at least one possible rpmdb location populated by the
            # image, and the local rpmdb is not in the set, we need to create a
            # symlink so that local dnf can find the database.
            #
            # When running DNF, it will use configuration from the local
            # system, and the database in wrong location will be silently
            # ignored, resulting in lock file that includes packages that are
            # already installed.
            dbpath = dbpaths.pop()
            logger.debug("Creating rpmdb symlink %s -> %s", utils.RPMDB_PATH, dbpath)
            os.makedirs(
                os.path.dirname(os.path.join(dest_dir, utils.RPMDB_PATH)),
                exist_ok=True,
            )
            link_path = os.path.join(dest_dir, utils.RPMDB_PATH)
            target_path = os.path.join(dest_dir, dbpath)
            os.symlink(
                os.path.relpath(target_path, os.path.dirname(link_path)),
                link_path,
            )


def _maybe_cleanup(directory):
    """Check if there's enough free space on the filesystem with given
    directory. If not, delete the directory.
    """
    usage = _get_storage_usage(directory)
    if usage and usage >= USAGE_THRESHOLD:
        logger.info("Storage is %d%% full. Cleaning up cached rpmdb.", usage)
        shutil.rmtree(directory)


def _get_storage_usage(directory):
    """Return disk usage of filesystem with given directory as an integer
    representing percentage. Returns None on failure.
    """
    cp = subprocess.run(
        ["df", "--output=pcent", directory],
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if cp.returncode != 0:
        logger.debug("Failed to check free storage size...")
    else:
        m = re.search(r"\b(\d+)%", cp.stdout)
        if m:
            return int(m.group(1))
    return None
