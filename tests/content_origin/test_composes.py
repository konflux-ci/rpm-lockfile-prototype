import os
from unittest.mock import Mock, patch

import pytest

from rpm_lockfile.content_origin import composes


def test_collect_by_latest_without_match():
    with patch.dict(os.environ, {"CTS_URL": "https://cts.example.com"}):
        origin = composes.ComposeOrigin()
    origin.session = Mock()
    origin.session.get.return_value = Mock(json=Mock(return_value={"items": []}))

    with pytest.raises(RuntimeError, match="No compose found"):
        list(origin.collect_by_latest({"release_short": "nope"}))
