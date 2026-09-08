"""Enforced network isolation.

diAIry's central promise is that no journal content ever leaves the machine.
A promise nobody can check is worth nothing, so we make it a runtime property:
this module patches ``socket.socket.connect`` and refuses any address that is
not explicitly allowed.

It is installed in three places, with different settings:

* the application, allowing loopback only (the local Ollama server);
* the test suite, allowing *nothing* -- a test that touches the network fails;
* CI, which runs that same suite.

A hostname is always refused, because resolving it is itself a network call.
Only literal IP addresses can be allowed.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from diairy.core.errors import EgressBlockedError

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex

_MIN_ADDRESS_PARTS = 2
"""An AF_INET address is (host, port); shorter tuples are not IP sockets."""

_allowlist: set[tuple[str, int]] = set()
_allow_loopback = True
_installed = False


def _address_is_allowed(address: object) -> bool:
    """Return whether ``address`` may be connected to under the current policy."""
    if not isinstance(address, tuple) or len(address) < _MIN_ADDRESS_PARTS:
        # AF_UNIX and friends never leave the machine.
        return True
    host, port = address[0], address[1]
    if not isinstance(host, str) or not isinstance(port, int):
        return True
    if (host, port) in _allowlist:
        return True
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        # A hostname means a DNS lookup, which is already egress.
        return False
    return _allow_loopback and parsed.is_loopback


def _refuse(address: object) -> EgressBlockedError:
    return EgressBlockedError(
        f"Refused an outbound connection to {address!r}. diAIry is offline by "
        f"construction; if this came from a dependency, that dependency has to go."
    )


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if not _address_is_allowed(address):
        raise _refuse(address)
    _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if not _address_is_allowed(address):
        raise _refuse(address)
    return _original_connect_ex(self, address)


def install_egress_guard(
    *,
    allow_loopback: bool = True,
    allowlist: Iterable[tuple[str, int]] = (),
) -> None:
    """Install the guard process-wide.

    Args:
        allow_loopback: allow connections to 127.0.0.0/8 and ``::1``. True for
            the application (local model server), False for the test suite.
        allowlist: exact ``(ip, port)`` pairs to permit regardless of the above.
    """
    global _installed, _allow_loopback  # noqa: PLW0603 -- process-wide policy
    _allow_loopback = allow_loopback
    _allowlist.clear()
    _allowlist.update(allowlist)
    if _installed:
        return
    socket.socket.connect = _guarded_connect  # type: ignore[assignment]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[assignment]
    _installed = True


def uninstall_egress_guard() -> None:
    """Restore the unpatched socket methods. Intended for tests only."""
    global _installed  # noqa: PLW0603 -- process-wide policy
    if not _installed:
        return
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    _allowlist.clear()
    _installed = False


@contextmanager
def egress_guard(
    *,
    allow_loopback: bool = True,
    allowlist: Iterable[tuple[str, int]] = (),
) -> Iterator[None]:
    """Scope the guard to a block, restoring the previous state on exit."""
    was_installed = _installed
    previous_loopback = _allow_loopback
    previous_allowlist = set(_allowlist)
    install_egress_guard(allow_loopback=allow_loopback, allowlist=allowlist)
    try:
        yield
    finally:
        if was_installed:
            # The guard stays installed; only the policy is restored.
            install_egress_guard(allow_loopback=previous_loopback, allowlist=previous_allowlist)
        else:
            uninstall_egress_guard()
