import hashlib
import json
import logging
import subprocess
import tempfile
from abc import ABC
from pathlib import Path
from unittest import mock

import pytest

from rpm_lockfile import containers


@pytest.mark.parametrize(
    "image_spec,expected",
    [
        ("example.com/image:latest", "example.com/image:latest"),
        ("example.com/image@sha256:abcdef", "example.com/image@sha256:abcdef"),
        ("example.com/image:latest@sha256:0123456", "example.com/image@sha256:0123456"),
    ],
)
def test_strip_tag(image_spec, expected):
    assert containers._strip_tag(image_spec) == expected


@pytest.fixture
def baseimage():
    return "registry.example.com/image:latest"


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
def test_extraction(tmp_path, rpmdb, baseimage, image_spec, expected_content, caplog):
    """
    The tests exercise different locations in the image, and different setting
    on the local system.
    """

    def fake_copy(image, arch, destdir):
        image_spec.write_to(destdir)
        assert image == baseimage
        assert arch == "amd64"

    with caplog.at_level(logging.DEBUG):
        with mock.patch("rpm_lockfile.utils.RPMDB_PATH", new=rpmdb):
            with mock.patch("rpm_lockfile.containers._copy_image", new=fake_copy):
                containers.setup_rpmdb(tmp_path, baseimage, "x86_64")

    # Test that rpmdb is in expected location with expected content
    assert (tmp_path / rpmdb / "foo").read_text().strip() == expected_content

    # Check that each layer was logged.
    for layer, msg in zip(image_spec.manifest["layers"], caplog.messages):
        assert f"Extracting rpmdb from layer {layer['digest']}" == msg
