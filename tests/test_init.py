import pytest

from unittest.mock import patch, mock_open

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


@pytest.mark.parametrize(
    "file,expected",
    [
        ("""FROM registry.io/repository/base
RUN something
""", "registry.io/repository/base"),
        ("""FROM registry.io/repository/build as build
RUN build
FROM registry.io/repository/base
COPY --from=build /artifact /
""", "registry.io/repository/base"),
    ]
)
def test_extract_image(file, expected):
    with patch("builtins.open", mock_open(read_data=file)) as mock_file:
        assert rpm_lockfile.extract_image(file) == expected
