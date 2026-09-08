"""Test-wide fixtures.

The important one is ``_forbid_network``. It is autouse and it applies to every
test in the repository: any attempt to open a socket to anything -- including
loopback -- fails the test. A test that legitimately needs the local model
server must say so with ``@pytest.mark.loopback``, which makes the exception
visible in the test file rather than implicit.

This is how "no data ever leaves the machine" stops being a claim and becomes a
property the CI enforces on every pull request.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from diairy.core.config import Settings
from diairy.core.egress import egress_guard


@pytest.fixture(autouse=True)
def _forbid_network(request: pytest.FixtureRequest) -> Iterator[None]:
    allow_loopback = request.node.get_closest_marker("loopback") is not None
    with egress_guard(allow_loopback=allow_loopback):
        yield


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """An empty vault directory."""
    path = tmp_path / "vault"
    path.mkdir()
    return path


@pytest.fixture
def settings(tmp_path: Path, vault_dir: Path) -> Settings:
    """Settings pointing entirely inside the temporary directory."""
    return Settings(
        vault_path=vault_dir,
        data_dir=tmp_path / "data",
        model_profile="fake",
        chunk_target_chars=400,
        chunk_overlap_chars=40,
    )
