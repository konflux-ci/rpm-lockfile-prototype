"""Generate a minimal DNF repository whose packages provide assumed capabilities.

The repodata (primary.xml.gz, filelists.xml.gz, other.xml.gz and repomd.xml)
is written using only the Python standard library so that no external
dependency on createrepo_c is needed.
"""

import gzip
import hashlib
import os
from xml.sax.saxutils import escape

REPO_ID = "_assumed-provides"
PACKAGE_PREFIX = "_assumed-provides-"


def _primary_xml(entries):
    """Return primary.xml content for the given provide entries."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<metadata xmlns="http://linux.duke.edu/metadata/common"'
        ' xmlns:rpm="http://linux.duke.edu/metadata/rpm"'
        f' packages="{len(entries)}">\n')
    ]
    for entry in entries:
        name = f"{PACKAGE_PREFIX}{entry}"
        pkg_id = hashlib.sha256(entry.encode()).hexdigest()
        escaped_entry = escape(entry)
        escaped_name = escape(name)
        parts.append(
            f'<package type="rpm">\n'
            f"  <name>{escaped_name}</name>\n"
            f"  <arch>noarch</arch>\n"
            f'  <version epoch="0" ver="0" rel="0"/>\n'
            f'  <checksum type="sha256" pkgid="YES">{pkg_id}</checksum>\n'
            f"  <summary>Assumed provides placeholder</summary>\n"
            f"  <description>Placeholder</description>\n"
            f"  <packager/>\n"
            f"  <url/>\n"
            f'  <time file="0" build="0"/>\n'
            f'  <size package="0" installed="0" archive="0"/>\n'
            f'  <location href="{escaped_name}-0-0.noarch.rpm"/>\n'
            f"  <format>\n"
            f"    <rpm:license>MIT</rpm:license>\n"
            f"    <rpm:group>Unspecified</rpm:group>\n"
            f"    <rpm:buildhost>localhost</rpm:buildhost>\n"
            f"    <rpm:sourcerpm/>\n"
            f"    <rpm:provides>\n"
            f'      <rpm:entry name="{escaped_entry}"/>\n'
            f"    </rpm:provides>\n"
            f"  </format>\n"
            f"</package>\n"
        )
    parts.append("</metadata>\n")
    return "".join(parts)


def _filelists_xml(entries):
    """Return filelists.xml content."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<filelists xmlns="http://linux.duke.edu/metadata/filelists"'
        f' packages="{len(entries)}">\n')
    ]
    for entry in entries:
        pkg_id = hashlib.sha256(entry.encode()).hexdigest()
        escaped_name = escape(f"{PACKAGE_PREFIX}{entry}")
        parts.append(
            f'<package pkgid="{pkg_id}" name="{escaped_name}"'
            f' arch="noarch">\n'
            f'  <version epoch="0" ver="0" rel="0"/>\n'
            f"</package>\n"
        )
    parts.append("</filelists>\n")
    return "".join(parts)


def _other_xml(entries):
    """Return other.xml content."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<otherdata xmlns="http://linux.duke.edu/metadata/other"'
        f' packages="{len(entries)}">\n')
    ]
    for entry in entries:
        pkg_id = hashlib.sha256(entry.encode()).hexdigest()
        escaped_name = escape(f"{PACKAGE_PREFIX}{entry}")
        parts.append(
            f'<package pkgid="{pkg_id}" name="{escaped_name}"'
            f' arch="noarch">\n'
            f'  <version epoch="0" ver="0" rel="0"/>\n'
            f"</package>\n"
        )
    parts.append("</otherdata>\n")
    return "".join(parts)


def _write_gz(path, content):
    """Write *content* as a gzip-compressed file and return (sha256, size)."""
    data = content.encode("utf-8")
    with gzip.open(path, "wb") as f:
        f.write(data)
    with open(path, "rb") as f:
        compressed = f.read()
    return hashlib.sha256(compressed).hexdigest(), len(compressed)


def _repomd_xml(records):
    """Return repomd.xml content.

    *records* is a list of (md_type, sha256, size, filename) tuples.
    """
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<repomd xmlns="http://linux.duke.edu/metadata/repo">\n')
    ]
    for md_type, checksum, size, filename in records:
        parts.append(
            f'  <data type="{md_type}">\n'
            f'    <checksum type="sha256">{checksum}</checksum>\n'
            f'    <location href="repodata/{filename}"/>\n'
            f"    <size>{size}</size>\n"
            f"  </data>\n"
        )
    parts.append("</repomd>\n")
    return "".join(parts)


def create_repo(tmpdir, assume_provides):
    """Create a temporary repo with dummy packages providing *assume_provides*.

    Returns the path to the repository directory.
    """
    repo_dir = os.path.join(tmpdir, REPO_ID)
    repodata_dir = os.path.join(repo_dir, "repodata")
    os.makedirs(repodata_dir, exist_ok=True)

    records = []
    for md_type, generator in [
        ("primary", _primary_xml),
        ("filelists", _filelists_xml),
        ("other", _other_xml),
    ]:
        filename = f"{md_type}.xml.gz"
        path = os.path.join(repodata_dir, filename)
        content = generator(assume_provides)
        checksum, size = _write_gz(path, content)
        records.append((md_type, checksum, size, filename))

    with open(os.path.join(repodata_dir, "repomd.xml"), "w") as f:
        f.write(_repomd_xml(records))

    return repo_dir
