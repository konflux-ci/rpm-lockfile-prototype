import hashlib
import json
import logging
import os
import subprocess
import tempfile
from abc import ABC
from pathlib import Path
from unittest import mock

import pytest

from rpm_lockfile import containers


@pytest.fixture
def baseimage():
    return "registry.example.com/image:latest"


@pytest.fixture
def disk_is_free():
    with mock.patch("rpm_lockfile.containers._get_storage_usage") as f:
        f.return_value = 10
        yield


@pytest.fixture
def disk_is_full():
    with mock.patch("rpm_lockfile.containers._get_storage_usage") as f:
        f.return_value = 95
        yield


class FakeImage:
    """
    This class allows creating container images on the local filesystem. Each
    layer is represented as a dict with keys being file paths and values being
    FSObject instances.
    """
    def __init__(self, layers):
        self.layers = layers

    def write_to(self, output_dir):
        self.manifest = {"layers": []}
        for layer in self.layers:
            digest = self._write_layer(output_dir, layer)
            self.manifest["layers"].append({"digest": f"sha256:{digest}"})

        with (output_dir / "manifest.json").open("w") as f:
            json.dump(self.manifest, f)

    def _write_layer(self, output_dir, layer):
        with tempfile.TemporaryDirectory() as temp_dir:
            layer_dir = Path(temp_dir) / "data"
            # Create file structure
            for fn, content in layer.items():
                (layer_dir / fn).parent.mkdir(parents=True, exist_ok=True)
                content.create(layer_dir / fn)
            # Pack it into a tarfile
            archive = Path(temp_dir) / "layer.tar.gz"
            subprocess.run(
                ["/usr/bin/tar", "cvfz", str(archive)]
                + [p.name for p in layer_dir.iterdir()],
                check=True,
                cwd=layer_dir,
            )
            # Compute digest. We are only ever working with small files,
            # reading in one go is fine here.
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            # Move file into correct location
            archive.rename(output_dir / digest)
            return digest


class FSObject(ABC):
    def create(self, name):
        return NotImplemented


class File(FSObject):
    def __init__(self, content):
        self.content = content

    def create(self, name):
        name.write_text(self.content, encoding="utf-8")


class Symlink(FSObject):
    def __init__(self, dest):
        self.dest = dest

    def create(self, name):
        name.symlink_to(self.dest)


@pytest.mark.parametrize("rpmdb", containers.RPMDB_PATHS)
@pytest.mark.parametrize(
    "image_spec,expected_content",
    [
        pytest.param(
            FakeImage(
                [{"var/lib/rpm/foo": File("foo")}]
            ),
            "foo",
            id="var-lib-rpm",
        ),
        pytest.param(
            FakeImage(
                [{"usr/lib/sysimage/rpm/foo": File("foo")}]
            ),
            "foo",
            id="usr-lib-sysimage-rpm",
        ),
        pytest.param(
            FakeImage(
                [
                    {
                        "usr/lib/sysimage/rpm/foo": File("foo"),
                        "var/lib/rpm": Symlink("../../usr/lib/sysimage/rpm"),
                    }
                ]
            ),
            "foo",
            id="both-locations",
        ),
        pytest.param(
            FakeImage(
                [
                    {"usr/lib/sysimage/rpm/foo": File("foo")},
                    {"usr/lib/sysimage/rpm/foo": File("bar")},
                ],
            ),
            "bar",
            id="two-layers",
        ),
    ],
)
def test_extraction(
    tmp_path, rpmdb, image_spec, expected_content, caplog, disk_is_free
):
    """
    The tests exercise different locations in the image, and different setting
    on the local system.
    """

    cache_dir = tmp_path / "cache"
    dest_dir = tmp_path / "dest"
    digest = f"sha256:{'a' * 64}"
    baseimage = "registry.example.com/image:latest"
    resolved_image = f"registry.example.com/image@{digest}"

    def fake_copy(image, arch, destdir):
        image_spec.write_to(destdir)
        assert image == resolved_image
        assert arch == "amd64"

    def fake_inspect(image, arch=None):
        return {"Digest": digest}

    with caplog.at_level(logging.DEBUG), \
            mock.patch("rpm_lockfile.utils.RPMDB_PATH", new=rpmdb), \
            mock.patch("rpm_lockfile.containers._copy_image", new=fake_copy), \
            mock.patch("rpm_lockfile.utils.CACHE_PATH", new=cache_dir), \
            mock.patch("rpm_lockfile.utils.inspect_image", new=fake_inspect):
        containers.setup_rpmdb(dest_dir, baseimage, "x86_64")

    # Test that rpmdb is in expected location with expected content
    assert (dest_dir / rpmdb / "foo").read_text().strip() == expected_content

    # Check that each layer was logged.
    for layer, msg in zip(image_spec.manifest["layers"], caplog.messages):
        assert f"Extracting rpmdb from layer {layer['digest']}" == msg


@pytest.mark.parametrize("rpmdb", containers.RPMDB_PATHS)
def test_caching(tmp_path, rpmdb, baseimage, caplog, disk_is_free):
    """
    Verify that extracting the same image twice only downloads once.
    """
    expected_content = "foo"
    image_spec = FakeImage([{"var/lib/rpm/foo": File(expected_content)}])
    digest = f"sha256:{'a' * 64}"
    baseimage = "registry.example.com/image:latest"
    resolved_image = f"registry.example.com/image@{digest}"

    cache_dir = tmp_path / "cache"

    def fake_copy(image, arch, destdir):
        image_spec.write_to(destdir)
        assert image == resolved_image
        assert arch == "amd64"

    def fake_inspect(image, arch=None):
        return {"Digest": digest}

    with mock.patch("rpm_lockfile.utils.RPMDB_PATH", new=rpmdb), \
            mock.patch("rpm_lockfile.containers._copy_image") as copy, \
            mock.patch("rpm_lockfile.utils.CACHE_PATH", new=cache_dir), \
            mock.patch("rpm_lockfile.utils.inspect_image", new=fake_inspect):
        copy.side_effect = fake_copy
        containers.setup_rpmdb(tmp_path / "dest1", baseimage, "x86_64")
        containers.setup_rpmdb(tmp_path / "dest2", baseimage, "x86_64")

    assert (tmp_path / "dest1" / rpmdb / "foo").read_text().strip() == expected_content
    assert (tmp_path / "dest2" / rpmdb / "foo").read_text().strip() == expected_content

    assert len(copy.mock_calls) == 1


@pytest.mark.parametrize(
    "input_image,digest,resolved_image",
    [
        (
            "registry.example.com/image:latest",
            None,
            "registry.example.com/image@sha256:abcdef",
        ),
        (
            "registry.example.com/image@sha256:12345",
            "sha256:12345",
            "registry.example.com/image@sha256:12345",
        ),
        (
            "registry.example.com/image:latest@sha256:12345",
            "sha256:12345",
            "registry.example.com/image@sha256:12345",
        ),
    ]
)
def test_resolving_image(tmp_path, input_image, digest, resolved_image, disk_is_free):
    cache_dir = tmp_path / "cache"
    arch = "x86_64"
    default_digest = "sha256:abcdef"

    def fake_setup(destdir, image, arch):
        destdir.mkdir(parents=True)

    def fake_inspect(image, f_arch=None):
        assert digest is None
        assert f_arch == arch
        assert image == input_image
        return {"Digest": default_digest}

    with mock.patch("rpm_lockfile.containers._online_setup_rpmdb") as _online_setup, \
            mock.patch("rpm_lockfile.utils.CACHE_PATH", new=cache_dir), \
            mock.patch("shutil.copytree") as copytree, \
            mock.patch("rpm_lockfile.utils.inspect_image", new=fake_inspect):
        _online_setup.side_effect = fake_setup
        containers.setup_rpmdb(tmp_path / "d1", input_image, arch)
        containers.setup_rpmdb(tmp_path / "d2", input_image, arch)

    img_cache = cache_dir / "rpmdbs" / arch / (digest or default_digest)

    assert _online_setup.mock_calls == [
        mock.call(img_cache.with_suffix(f".{os.getpid()}"), resolved_image, arch)
    ]

    assert copytree.mock_calls == [
        mock.call(img_cache, tmp_path / "d1", dirs_exist_ok=True),
        mock.call(img_cache, tmp_path / "d2", dirs_exist_ok=True),
    ]


@pytest.mark.parametrize("rpmdb", containers.RPMDB_PATHS)
def test_caching_on_full_disk(tmp_path, rpmdb, baseimage, caplog, disk_is_full):
    """
    Verify that extracting the same image twice only downloads once.
    """
    expected_content = "foo"
    image_spec = FakeImage([{"var/lib/rpm/foo": File(expected_content)}])
    digest = f"sha256:{'a' * 64}"
    baseimage = "registry.example.com/image:latest"
    resolved_image = f"registry.example.com/image@{digest}"

    cache_dir = tmp_path / "cache"

    def fake_copy(image, arch, destdir):
        image_spec.write_to(destdir)
        assert image == resolved_image
        assert arch == "amd64"

    def fake_inspect(image, arch=None):
        return {"Digest": digest}

    with mock.patch("rpm_lockfile.utils.RPMDB_PATH", new=rpmdb), \
            mock.patch("rpm_lockfile.containers._copy_image", new=fake_copy), \
            mock.patch("rpm_lockfile.utils.CACHE_PATH", new=cache_dir), \
            mock.patch("rpm_lockfile.utils.inspect_image", new=fake_inspect):
        containers.setup_rpmdb(tmp_path / "dest1", baseimage, "x86_64")

    assert (tmp_path / "dest1" / rpmdb / "foo").read_text().strip() == expected_content

    assert list((cache_dir / "rpmdbs" / "x86_64").iterdir()) == []
