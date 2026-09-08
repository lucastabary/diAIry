"""Test-wide fixtures.

Two autouse fixtures apply to every test in the repository, and both exist to
make the suite independent of the machine it runs on.

``_forbid_network`` blocks any attempt to open a socket to anything, loopback
included. A test that legitimately needs the local model server must say so with
``@pytest.mark.loopback``, which makes the exception visible in the test file
rather than implicit. This is how "no data ever leaves the machine" stops being
a claim and becomes a property CI enforces on every pull request.

``_hermetic_configuration`` strips the ambient ``DIAIRY_*`` environment. Every
setting is configurable by environment variable, which means a developer who
follows .env.example -- or a CI job that exports a profile -- silently changes
what the tests observe. Tests must describe the code, not the machine.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from diairy.core.config import ENV_PREFIX, Settings
from diairy.core.egress import egress_guard


@pytest.fixture(autouse=True)
def _forbid_network(request: pytest.FixtureRequest) -> Iterator[None]:
    allow_loopback = request.node.get_closest_marker("loopback") is not None
    with egress_guard(allow_loopback=allow_loopback):
        yield


@pytest.fixture(autouse=True)
def _hermetic_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide the ambient configuration from every test.

    Without this, `DIAIRY_MODEL_PROFILE=fake` in the environment makes a test
    asserting the default profile fail -- which is exactly what happened the
    first time CI ran. Any test that wants an environment variable sets it
    itself, so what it observes is what it asked for.

    ``DIAIRY_CONFIG`` is pointed at a path that does not exist rather than
    merely unset, because unsetting it would fall back to the real config file
    in the developer's OS configuration directory.
    """
    for name in [key for key in os.environ if key.startswith(ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(f"{ENV_PREFIX}CONFIG", str(tmp_path / "no-such-config.toml"))


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
