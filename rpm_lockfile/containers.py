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

# Known locations for rpmdb inside the image; files/lib is for
# Flatpak runtime images.
RPMDB_PATHS = ["usr/lib/sysimage/rpm", "var/lib/rpm", "files/lib/sysimage/rpm"]

# Storage usage limit. If the filesystem with the cache fills up over this
# limit, nothing new will be added into the cache.
# Value in percent.
USAGE_THRESHOLD = 80


def _copy_image(baseimage, arch, destdir):
    """Download image into given location."""
    if not utils.check_image_spec(baseimage):
        logging.warning(
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

    if image.lower() == "scratch":
        # Nothing to do for scratch image. It doesn't have any RPMs.
        logging.warning(
            "Image with rpmdb was expected, but got `scratch` instead. "
            "Did you want to enable context.bare or use a different base image?"
        )
        return

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
        logging.info("Using already downloaded rpmdb")

    # Copy the cache to the correct destination directory.
    shutil.copytree(cache, dest_dir, dirs_exist_ok=True)

    _maybe_cleanup(cache)


def _online_setup_rpmdb(dest_dir, baseimage, arch):
    arch = utils.translate_arch(arch)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        _copy_image(baseimage, arch, tmpdir)

        # The manifest is always in the same location, and contains information
        # about individual layers.
        with open(tmpdir / "manifest.json") as f:
            manifest = json.load(f)

        # This are all possible locations for rpmdb that are populated by the
        # image.
        dbpaths = set()

        def filter_rpmdb(member, path):
            for candidate_path in RPMDB_PATHS:
                if Path(member.name).is_relative_to(candidate_path):
                    dbpaths.add(candidate_path)
                    return tarfile.data_filter(member, path)

        # One layer at a time...
        for layer in manifest["layers"]:
            logging.info("Extracting rpmdb from layer %s", layer["digest"])
            digest = layer["digest"].split(":", 1)[1]
            # ...find all files in interesting locations and extract them to
            # the destination cache.
            archive = tarfile.open(tmpdir / digest)
            archive.extractall(path=dest_dir, filter=filter_rpmdb)

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
            logging.debug("Creating rpmdb symlink %s -> %s", utils.RPMDB_PATH, dbpath)
            os.makedirs(
                os.path.dirname(os.path.join(dest_dir, utils.RPMDB_PATH)),
                exist_ok=True,
            )
            os.symlink(
                os.path.join(dest_dir, dbpath),
                os.path.join(dest_dir, utils.RPMDB_PATH),
            )


def _maybe_cleanup(directory):
    """Check if there's enough free space on the filesystem with given
    directory. If not, delete the directory.
    """
    usage = _get_storage_usage(directory)
    if usage and usage >= USAGE_THRESHOLD:
        logging.info("Storage is %d%% full. Cleaning up cached rpmdb.", usage)
        shutil.rmtree(directory)


def _get_storage_usage(directory):
    """Return disk usage of filesystem with given directory as an integer
    representing percentage. Returns None on failure.
    """
    cp = subprocess.run(
        ["df", "--output=pcent", directory], stdout=subprocess.PIPE, text=True
    )
    if cp.returncode != 0:
        logging.debug("Failed to check free storage size...")
    else:
        m = re.search(r"\b(\d+)%", cp.stdout)
        if m:
            return int(m.group(1))
    return None
