import logging
import os
import shlex
import subprocess
import tempfile


# Path to where local dnf expects to find rpmdb. This is relative to /.
RPMDB_PATH = subprocess.run(
    ["rpm", "--eval", "%_dbpath"], stdout=subprocess.PIPE, check=True, encoding="utf-8"
).stdout.strip()[1:]


def relative_to(directory, path):
    """os.path.join() that gracefully handles None"""
    if path:
        return os.path.join(directory, path)
    return None


def find_containerfile(dir):
    """Look for a containerfile in the given directory.

    Returns a path to the found file or None.
    """
    for candidate in (dir / "Containerfile", dir / "Dockerfile"):
        if candidate.exists():
            return candidate
    return None


def logged_run(cmd, *args, **kwargs):
    logging.info("$ %s", shlex.join(cmd))
    return subprocess.run(cmd, *args, **kwargs)


def extract_image(containerfile):
    """Find image mentioned in the first FROM statement in the containerfile."""
    logging.debug("Looking for base image in %s", containerfile)
    baseimg = ""
    with open(containerfile) as f:
        for line in f:
            if line.startswith("FROM "):
                baseimg = line.split()[1]
    if baseimg == "":
        raise RuntimeError("Base image could not be identified.")
    return baseimg


def get_file_from_git(repo, ref, file):
    tmp_dir = tempfile.mkdtemp(prefix="rpm-lockfile-checkout-")
    logging.info("Extracting commit %s from repo %s to %s", ref, repo, tmp_dir)
    cmds = [
        ["git", "init"],
        ["git", "remote", "add", "origin", os.path.expandvars(repo)],
        ["git", "fetch", "--depth=1", "origin", ref],
        ["git", "checkout", "FETCH_HEAD"],
    ]
    for cmd in cmds:
        # The commands can possibly contain a secret token. They can not be
        # logged.
        subprocess.run(cmd, cwd=tmp_dir, check=True)
    return os.path.join(tmp_dir, file)
