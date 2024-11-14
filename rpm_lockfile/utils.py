import functools
import hashlib
import json
import logging
import os
import re
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


def extract_image(containerfile, stage_num=None, stage_name=None, image_pattern=None):
    """Find matching image mentioned in the containerfile.
    If no filters are specified, then the last image is returned.
    """
    logging.debug("Looking for base image in %s", containerfile)
    baseimg = ""
    stages = 0
    from_line_re = re.compile(
        r"^\s*FROM\s+(--platform=\S+\s+)?(?P<img>\S+)(\s+AS\s+(?P<name>\S+))?\s*$",
        re.IGNORECASE,
    )
    with open(containerfile) as f:
        for line in f:
            m = from_line_re.match(line.strip())
            if m:
                baseimg = m.group("img")
                if stage_name and stage_name == m.group("name"):
                    return baseimg
                stages += 1

                if stage_num == stages:
                    return baseimg

                if image_pattern and re.search(image_pattern, baseimg):
                    return baseimg

    if baseimg == "":
        raise RuntimeError("Base image could not be identified.")
    if stage_num or stage_name or image_pattern:
        raise RuntimeError("No stage matched.")
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


def subst_vars(template, vars):
    """Replace {var} placeholders in template with provided values."""
    for key, value in vars.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def translate_arch(arch):
    # This is a horrible hack. Skopeo will reject x86_64, but is happy with
    # amd64. The same goes for aarch64 -> arm64.
    ARCHES = {"aarch64": "arm64", "x86_64": "amd64"}
    return ARCHES.get(arch, arch)


@functools.lru_cache
def inspect_image(image_spec, arch=None):
    cmd = ["skopeo"]
    if arch:
        cmd.append(f"--override-arch={translate_arch(arch)}")
    cmd.extend(["inspect", f"docker://{strip_tag(image_spec)}"])
    cp = logged_run(cmd, stdout=subprocess.PIPE, check=True)
    return json.loads(cp.stdout)


def _get_image_labels(image_spec):
    """Given an image specification, return a dict with labels from the image."""
    return inspect_image(image_spec)["Labels"]


def _get_containerfile_labels(containerfile, config_dir):
    """Find labels of the last base image used in the given containerfile."""
    if isinstance(containerfile, dict):
        fp = containerfile["file"]
        filters = {
            "stage_num": containerfile.get("stageNum"),
            "stage_name": containerfile.get("stageName"),
            "image_pattern": containerfile.get("imagePattern"),
        }
    else:
        fp = containerfile
        filters = {}

    return _get_image_labels(extract_image(os.path.join(config_dir, fp), **filters))


def split_image(image_spec):
    # De don't want to validate the digest here in any way, so even wrong
    # length should be accepted.
    m = re.match(r'([^:@]+)(:[^@]+)?(@sha\d+:[a-f0-9]+)?$', image_spec)
    if m:
        repo = m.group(1)
        tag = m.group(2)
        digest_suffix = m.group(3)
        digest = digest_suffix[1:] if digest_suffix else None
        return repo, tag, digest
    raise RuntimeError(f"Unknown format for image specification: {image_spec}")


def make_image_spec(repo, tag, digest):
    spec = repo
    if tag:
        spec += f":{tag}"
    if digest:
        spec += f"@{digest}"
    return spec


def strip_tag(image_spec):
    """
    If the image specification contains both a tag and a digest, remove the
    tag. Skopeo rejects such images. The behaviour is chosen to match podman
    4.9.4, which silently ignores the tag if digest is available.

    https://github.com/containers/image/issues/1736
    """
    repo, tag, digest = split_image(image_spec)
    if tag and digest:
        logging.info(f"Digest was provided, ignoring tag {tag}")
    if digest:
        return f"{repo}@{digest}"
    return image_spec


def get_labels(obj, config_dir):
    """Find labels from an image or the base image used in the containerfile
    from given configuration object. The given configuration dict is modified
    in place to remove any keys relevant for this lookup.
    """
    vars = {}
    image = obj.pop("varsFromImage", None)
    if image:
        vars |= _get_image_labels(image)

    containerfile = obj.pop("varsFromContainerfile", None)
    if containerfile:
        vars |= _get_containerfile_labels(containerfile, config_dir)

    return vars


CONTAINERFILE_SCHEMA = {
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "stageNum": {"type": "number"},
                "stageName": {"type": "string"},
                "imagePattern": {"type": "string"},
            },
            "additionalProperties": False,
            "required": ["file"],
        },
    ],
}


def hash_file(path):
    with open(path, "rb") as f:
        h = hashlib.sha256()
        while chunk := f.read(65536):
            h.update(chunk)
        return h.hexdigest()
