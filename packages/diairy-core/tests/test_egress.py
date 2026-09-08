"""The network guard is the one test that must never be deleted."""

from __future__ import annotations

import socket

import pytest

from diairy.core.egress import egress_guard
from diairy.core.errors import EgressBlockedError


def _connect(host: str, port: int = 443) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.1)
        sock.connect((host, port))


def test_external_ip_is_refused() -> None:
    with pytest.raises(EgressBlockedError):
        _connect("1.1.1.1")


def test_hostname_is_refused_because_dns_is_already_egress() -> None:
    with pytest.raises(EgressBlockedError):
        _connect("example.com")


def test_loopback_is_refused_by_default_in_tests() -> None:
    # The autouse fixture installs the guard with allow_loopback=False.
    with pytest.raises(EgressBlockedError):
        _connect("127.0.0.1", 11434)


def test_loopback_can_be_allowed_explicitly() -> None:
    with egress_guard(allow_loopback=True), pytest.raises(OSError) as excinfo:
        _connect("127.0.0.1", 9)
    assert not isinstance(excinfo.value, EgressBlockedError)


def test_allowlisted_address_passes_the_policy() -> None:
    with egress_guard(allow_loopback=False, allowlist=[("127.0.0.1", 11434)]):
        with pytest.raises(OSError) as excinfo:
            _connect("127.0.0.1", 11434)
        assert not isinstance(excinfo.value, EgressBlockedError)

        with pytest.raises(EgressBlockedError):
            _connect("127.0.0.1", 11435)


def test_connect_ex_is_guarded_too() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(EgressBlockedError),
    ):
        sock.connect_ex(("8.8.8.8", 53))


def test_guard_restores_previous_policy_on_exit() -> None:
    with egress_guard(allow_loopback=True):
        pass
    # Back to the suite-wide policy: loopback forbidden again.
    with pytest.raises(EgressBlockedError):
        _connect("127.0.0.1", 11434)
