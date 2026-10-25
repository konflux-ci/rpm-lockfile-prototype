import os
from unittest.mock import patch, Mock

import pytest

from rpm_lockfile.content_origin import Repo
from rpm_lockfile.content_origin.composes import ComposeOrigin


@pytest.fixture
def mock_compose():
    """Create a mock productmd.Compose object."""
    compose = Mock()
    compose.compose_path = "https://example.com/compose"
    compose.info.compose.id = "RHEL-9.5.0-20241201.0"

    # Create mock variants
    variant1 = Mock()
    variant1.uid = "BaseOS"
    variant1.paths.repository = {
        "aarch64": "BaseOS/aarch64/os",
        "x86_64": "BaseOS/x86_64/os",
    }

    variant2 = Mock()
    variant2.uid = "AppStream"
    variant2.paths.repository = {
        "aarch64": "AppStream/aarch64/os",
        "x86_64": "AppStream/x86_64/os",
    }

    compose.info.variants.variants = {
        "BaseOS": variant1,
        "AppStream": variant2,
    }

    return compose


class TestComposeOrigin:
    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Set up environment for tests."""
        with patch.dict(os.environ, {"CTS_URL": "https://cts.example.com"}):
            yield

    def test_init_with_cts_url(self):
        """Test initialization when CTS_URL is set."""
        origin = ComposeOrigin()
        assert origin.cts_url == "https://cts.example.com"
        assert origin.session is not None

    def test_init_without_cts_url(self):
        """Test initialization fails when CTS_URL is not set."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Env var 'CTS_URL' is not defined"):
                ComposeOrigin()

    def test_init_strips_trailing_slash(self):
        """Test that trailing slashes are stripped from CTS_URL."""
        with patch.dict(os.environ, {"CTS_URL": "https://cts.example.com/"}):
            origin = ComposeOrigin()
            assert origin.cts_url == "https://cts.example.com"

    def test_collect_from_url(self, mock_compose):
        """Test collecting repos from a compose URL."""
        origin = ComposeOrigin()

        with patch(
            "rpm_lockfile.content_origin.composes.productmd.Compose"
        ) as mock_productmd:
            mock_productmd.return_value = mock_compose

            repos = list(origin.collect_from_url("https://example.com/compose"))

        assert len(repos) == 2

        # Check BaseOS repo
        assert repos[0].repoid == "RHEL-9.5.0-20241201.0-BaseOS-rpms"
        assert repos[0].kwargs == {
            "baseurl": ["https://example.com/compose/BaseOS/$basearch/os"]
        }

        # Check AppStream repo
        assert repos[1].repoid == "RHEL-9.5.0-20241201.0-AppStream-rpms"
        assert repos[1].kwargs == {
            "baseurl": ["https://example.com/compose/AppStream/$basearch/os"]
        }

    def test_collect_from_url_inconsistent_paths(self):
        """Test error when variant has inconsistent arch paths."""
        origin = ComposeOrigin()

        # Create a compose with inconsistent paths
        compose = Mock()
        compose.compose_path = "https://example.com/compose"
        compose.info.compose.id = "TEST-1.0"

        variant = Mock()
        variant.uid = "BaseOS"
        # Different paths for different arches - should cause error
        variant.paths.repository = {
            "x86_64": "BaseOS/x86_64/os",
            "aarch64": "Different/aarch64/path",
        }
        compose.info.variants.variants = {"BaseOS": variant}

        with patch(
            "rpm_lockfile.content_origin.composes.productmd.Compose"
        ) as mock_productmd:
            mock_productmd.return_value = compose

            with pytest.raises(RuntimeError, match="Unexpected compose metadata"):
                list(origin.collect_from_url("https://example.com/compose"))

    def test_collect_by_id(self, mock_compose):
        """Test collecting repos by compose ID."""
        origin = ComposeOrigin()
        compose_id = "RHEL-9.5.0-20241201.0"

        # Mock the CTS API response
        mock_response = Mock()
        mock_response.json.return_value = {"compose_url": "https://example.com/compose"}
        origin.session.get = Mock(return_value=mock_response)

        with patch(
            "rpm_lockfile.content_origin.composes.productmd.Compose"
        ) as mock_productmd:
            mock_productmd.return_value = mock_compose

            repos = list(origin.collect_by_id(compose_id))

        # Should call CTS API
        origin.session.get.assert_called_once_with(
            f"https://cts.example.com/api/1/composes/{compose_id}"
        )
        mock_response.raise_for_status.assert_called_once()

        # Should return expected repos
        assert len(repos) == 2
        assert repos[0].repoid == "RHEL-9.5.0-20241201.0-BaseOS-rpms"

    def test_collect_by_latest(self, mock_compose):
        """Test collecting repos by latest compose filters."""
        origin = ComposeOrigin()
        filters = {
            "release_short": "RHEL",
            "release_version": "9.5",
            "release_type": "ga",
        }

        # Mock the CTS API response
        mock_response = Mock()
        mock_response.json.return_value = {
            "items": [{"compose_url": "https://example.com/compose"}]
        }
        origin.session.get = Mock(return_value=mock_response)

        with patch(
            "rpm_lockfile.content_origin.composes.productmd.Compose"
        ) as mock_productmd:
            mock_productmd.return_value = mock_compose

            repos = list(origin.collect_by_latest(filters))

        # Should call CTS API with filters
        origin.session.get.assert_called_once_with(
            "https://cts.example.com/api/1/composes/", params=filters
        )
        mock_response.raise_for_status.assert_called_once()

        # Should return expected repos
        assert len(repos) == 2

    def test_collect_dispatches_correctly(self, mock_compose):
        """Test that collect method dispatches to correct collector methods."""
        origin = ComposeOrigin()

        # Mock the collector methods
        origin.collect_by_id = Mock(return_value=[Mock(spec=Repo)])
        origin.collect_by_latest = Mock(return_value=[Mock(spec=Repo)])

        # Test with ID spec
        id_spec = {"id": "RHEL-9.5.0-20241201.0"}
        list(origin.collect([id_spec]))
        origin.collect_by_id.assert_called_once_with("RHEL-9.5.0-20241201.0")

        # Test with latest spec
        latest_spec = {"latest": {"release_short": "RHEL"}}
        list(origin.collect([latest_spec]))
        origin.collect_by_latest.assert_called_once_with({"release_short": "RHEL"})

    def test_http_error_handling(self):
        """Test that HTTP errors are properly raised."""
        origin = ComposeOrigin()

        # Mock a failed response
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 404")
        origin.session.get = Mock(return_value=mock_response)

        with pytest.raises(Exception, match="HTTP 404"):
            list(origin.collect_by_id("invalid-id"))

    def test_empty_compose_variants(self):
        """Test handling of compose with no variants."""
        origin = ComposeOrigin()

        compose = Mock()
        compose.compose_path = "https://example.com/compose"
        compose.info.compose.id = "EMPTY-1.0"
        compose.info.variants.variants = {}

        with patch(
            "rpm_lockfile.content_origin.composes.productmd.Compose"
        ) as mock_productmd:
            mock_productmd.return_value = compose

            repos = list(origin.collect_from_url("https://example.com/compose"))

        assert len(repos) == 0
