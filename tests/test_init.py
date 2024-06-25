import pytest

import rpm_lockfile

@pytest.mark.parametrize(
    "image_spec,expected",
    [
        ("example.com/image:latest", "example.com/image:latest"),
        ("example.com/image@sha256:abcdef", "example.com/image@sha256:abcdef"),
        ("example.com/image:latest@sha256:0123456", "example.com/image@sha256:0123456"),
    ]
)
def test_strip_tag(image_spec, expected):
    assert rpm_lockfile._strip_tag(image_spec) == expected
