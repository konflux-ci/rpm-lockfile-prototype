"""Generate DNF-compatible repodata from YAML package definitions.

Reads a YAML file describing packages and produces repodata (primary.xml.gz,
filelists.xml.gz, other.xml.gz, repomd.xml) that DNF can consume. No actual
.rpm files are created -- DNF only needs the metadata for dependency resolution.

The YAML format is:

    packages:
      - nvr: test-base-1.0-1
        arch: x86_64          # default: noarch
        requires:
          - some-dep
        provides:
          - some-capability
        recommends:
          - optional-dep
        sourcerpm: test-base-1.0-1.src.rpm
"""

import gzip
import hashlib
from pathlib import Path
from xml.sax.saxutils import escape

import yaml


def _parse_nvr(nvr):
    """Split NVR string into (name, epoch, version, release).

    The NVR is split on the last two hyphens, and the version may contain
    an epoch prefix (epoch:version):
      test-app-1.0-1       -> ("test-app", 0, "1.0", "1")
      foo-2.3.4-5          -> ("foo", 0, "2.3.4", "5")
      findutils-1:99.0-1   -> ("findutils", 1, "99.0", "1")
    """
    parts = nvr.rsplit("-", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid NVR (expected name-version-release): {nvr}")
    name, version, release = parts
    epoch = 0
    if ":" in version:
        epoch_str, version = version.split(":", 1)
        epoch = int(epoch_str)
    return name, epoch, version, release


def _pkg_size(nevra):
    """Derive a unique non-zero package size from the NEVRA string.

    Uses the hash to produce a size in the range 1000-99999, ensuring
    different packages get different sizes.
    """
    h = int(hashlib.sha256(nevra.encode()).hexdigest(), 16)
    return 1000 + (h % 99000)


def _pkg_checksum(nevra):
    """Derive a deterministic SHA-256 checksum from the NEVRA string."""
    return hashlib.sha256(nevra.encode()).hexdigest()


def _primary_xml(packages):
    """Generate primary.xml content from a list of package dicts."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<metadata xmlns="http://linux.duke.edu/metadata/common"'
        ' xmlns:rpm="http://linux.duke.edu/metadata/rpm"'
        f' packages="{len(packages)}">\n')
    ]

    for pkg in packages:
        name, epoch, version, release = _parse_nvr(pkg["nvr"])
        arch = pkg.get("arch", "noarch")
        nevra = f"{name}-{epoch}:{version}-{release}.{arch}"
        checksum = _pkg_checksum(nevra)
        size = _pkg_size(nevra)
        installed_size = size * 2
        archive_size = size + size // 2
        location = f"{name}-{version}-{release}.{arch}.rpm"
        # Default sourcerpm to <name>-<version>-<release>.src.rpm for binary
        # packages (non-src arch). Source RPMs have no sourcerpm.
        if arch == "src":
            sourcerpm = ""
        else:
            sourcerpm = pkg.get(
                "sourcerpm", f"{name}-{version}-{release}.src.rpm"
            )
        requires = pkg.get("requires", [])
        provides = pkg.get("provides", [])
        recommends = pkg.get("recommends", [])

        escaped_name = escape(name)

        parts.append(
            f'<package type="rpm">\n'
            f"  <name>{escaped_name}</name>\n"
            f"  <arch>{arch}</arch>\n"
            f'  <version epoch="{epoch}" ver="{escape(version)}"'
            f' rel="{escape(release)}"/>\n'
            f'  <checksum type="sha256" pkgid="YES">{checksum}</checksum>\n'
            f"  <summary>Test package {escaped_name}</summary>\n"
            f"  <description>Test package</description>\n"
            f"  <packager/>\n"
            f"  <url/>\n"
            f'  <time file="0" build="0"/>\n'
            f'  <size package="{size}" installed="{installed_size}"'
            f' archive="{archive_size}"/>\n'
            f'  <location href="{escape(location)}"/>\n'
            f"  <format>\n"
            f"    <rpm:license>MIT</rpm:license>\n"
            f"    <rpm:group>Unspecified</rpm:group>\n"
            f"    <rpm:buildhost>localhost</rpm:buildhost>\n"
            f"    <rpm:sourcerpm>{escape(sourcerpm)}</rpm:sourcerpm>\n"
        )

        # Self-provide is always added
        parts.append("    <rpm:provides>\n")
        parts.append(
            f'      <rpm:entry name="{escaped_name}" flags="EQ"'
            f' epoch="{epoch}" ver="{escape(version)}" rel="{escape(release)}"/>\n'
        )
        for prov in provides:
            parts.append(f'      <rpm:entry name="{escape(prov)}"/>\n')
        parts.append("    </rpm:provides>\n")

        if requires:
            parts.append("    <rpm:requires>\n")
            for req in requires:
                parts.append(f'      <rpm:entry name="{escape(req)}"/>\n')
            parts.append("    </rpm:requires>\n")

        if recommends:
            parts.append(
                "    <rpm:recommends>\n"
            )
            for rec in recommends:
                parts.append(f'      <rpm:entry name="{escape(rec)}"/>\n')
            parts.append("    </rpm:recommends>\n")

        parts.append("  </format>\n")
        parts.append("</package>\n")

    parts.append("</metadata>\n")
    return "".join(parts)


def _filelists_xml(packages):
    """Generate filelists.xml content."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<filelists xmlns="http://linux.duke.edu/metadata/filelists"'
        f' packages="{len(packages)}">\n')
    ]
    for pkg in packages:
        name, epoch, version, release = _parse_nvr(pkg["nvr"])
        arch = pkg.get("arch", "noarch")
        nevra = f"{name}-{epoch}:{version}-{release}.{arch}"
        checksum = _pkg_checksum(nevra)
        files = pkg.get("files", [])
        parts.append(
            f'<package pkgid="{checksum}" name="{escape(name)}"'
            f' arch="{arch}">\n'
            f'  <version epoch="{epoch}" ver="{escape(version)}"'
            f' rel="{escape(release)}"/>\n'
        )
        for filepath in files:
            parts.append(f"  <file>{escape(filepath)}</file>\n")
        parts.append("</package>\n")
    parts.append("</filelists>\n")
    return "".join(parts)


def _other_xml(packages):
    """Generate other.xml content."""
    parts = [
        ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<otherdata xmlns="http://linux.duke.edu/metadata/other"'
        f' packages="{len(packages)}">\n')
    ]
    for pkg in packages:
        name, epoch, version, release = _parse_nvr(pkg["nvr"])
        arch = pkg.get("arch", "noarch")
        nevra = f"{name}-{epoch}:{version}-{release}.{arch}"
        checksum = _pkg_checksum(nevra)
        parts.append(
            f'<package pkgid="{checksum}" name="{escape(name)}"'
            f' arch="{arch}">\n'
            f'  <version epoch="{epoch}" ver="{escape(version)}"'
            f' rel="{escape(release)}"/>\n'
            f"</package>\n"
        )
    parts.append("</otherdata>\n")
    return "".join(parts)


def _write_gz(path, content):
    """Write content as a gzip-compressed file. Returns (sha256, size)."""
    data = content.encode("utf-8")
    with gzip.GzipFile(path, "wb", mtime=0) as f:
        f.write(data)
    with open(path, "rb") as f:
        compressed = f.read()
    return hashlib.sha256(compressed).hexdigest(), len(compressed)


def _repomd_xml(records):
    """Generate repomd.xml content.

    records is a list of (md_type, sha256, size, filename) tuples.
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


def create_repo(repo_dir, packages):
    """Create repodata in repo_dir from a list of package dicts.

    Each package dict should have at minimum an 'nvr' key.
    Returns the repo_dir path.
    """
    repo_dir = Path(repo_dir)
    repodata_dir = repo_dir / "repodata"
    repodata_dir.mkdir(parents=True, exist_ok=True)

    # Collect source packages from sourcerpm fields
    source_packages = _collect_source_packages(packages)
    all_packages = list(packages) + source_packages

    records = []
    for md_type, generator in [
        ("primary", _primary_xml),
        ("filelists", _filelists_xml),
        ("other", _other_xml),
    ]:
        filename = f"{md_type}.xml.gz"
        path = repodata_dir / filename
        content = generator(all_packages)
        checksum, size = _write_gz(str(path), content)
        records.append((md_type, checksum, size, filename))

    with open(repodata_dir / "repomd.xml", "w") as f:
        f.write(_repomd_xml(records))

    return str(repo_dir)


def _get_sourcerpm(pkg):
    """Get the sourcerpm for a package, applying the default if needed."""
    arch = pkg.get("arch", "noarch")
    if arch == "src":
        return ""
    name, _epoch, version, release = _parse_nvr(pkg["nvr"])
    return pkg.get("sourcerpm", f"{name}-{version}-{release}.src.rpm")


def _collect_source_packages(packages):
    """Create source package entries from sourcerpm fields.

    For each binary package that has a sourcerpm, create a corresponding
    source package entry (arch=src) if one doesn't already exist.
    """
    seen = set()
    source_packages = []
    for pkg in packages:
        sourcerpm = _get_sourcerpm(pkg)
        if not sourcerpm or sourcerpm in seen:
            continue
        seen.add(sourcerpm)
        if not sourcerpm.endswith(".src.rpm"):
            continue
        nvr = sourcerpm[: -len(".src.rpm")]
        source_packages.append({"nvr": nvr, "arch": "src"})
    return source_packages


def create_repo_from_yaml(yaml_path, repo_dir):
    """Read package definitions from a YAML file and generate repodata.

    Returns the repo_dir path.
    """
    yaml_path = Path(yaml_path)
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    packages = data.get("packages", [])
    return create_repo(repo_dir, packages)
