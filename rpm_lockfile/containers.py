import json
import logging
import os
import re
import tarfile
import tempfile
from pathlib import Path

from . import utils

# Known locations for rpmdb inside the image.
RPMDB_PATHS = ["usr/lib/sysimage/rpm", "var/lib/rpm"]


def _translate_arch(arch):
    # This is a horrible hack. Skopeo will reject x86_64, but is happy with
    # amd64. The same goes for aarch64 -> arm64.
    ARCHES = {"aarch64": "arm64", "x86_64": "amd64"}
    return ARCHES.get(arch, arch)


def _strip_tag(image_spec):
    """
    If the image specification contains both a tag and a digest, remove the
    tag. Skopeo rejects such images. The behaviour is chosen to match podman
    4.9.4, which silently ignores the tag if digest is available.

    https://github.com/containers/image/issues/1736
    """
    # De don't want to validate the digest here in any way, so even wrong
    # length should be accepted.
    m = re.match(r'([^:]+)(:[^@]+)(@sha\d+:[a-f0-9]+)$', image_spec)
    if m:
        logging.info("Digest was provided, ignoring tag %s", m.group(2)[1:])
        return f"{m.group(1)}{m.group(3)}"
    return image_spec


def _copy_image(baseimage, arch, destdir):
    """Download image into given location."""
    cmd = [
        "skopeo",
        f"--override-arch={arch}",
        "copy",
        f"docker://{_strip_tag(baseimage)}",
        f"dir:{destdir}",
    ]
    utils.logged_run(cmd, check=True)


def setup_rpmdb(cache_dir, baseimage, arch):
    arch = _translate_arch(arch)

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
            archive.extractall(path=cache_dir, filter=filter_rpmdb)

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
                os.path.dirname(os.path.join(cache_dir, utils.RPMDB_PATH)),
                exist_ok=True,
            )
            os.symlink(
                os.path.join(cache_dir, dbpath),
                os.path.join(cache_dir, utils.RPMDB_PATH),
            )
