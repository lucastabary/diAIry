# 7. Network egress is blocked, and tested

Status: Accepted

## Context

The whole premise is that a lifetime of private thinking can be handed to a
system without any of it leaving the machine. A promise nobody can verify is
worth nothing, and the realistic threat is not malice but accident: a dependency
with usage analytics, a crash reporter, a helpful library that fetches
something.

## Decision

Three layers, all enforced rather than documented:

1. **Runtime.** `diairy.core.egress` patches `socket.socket.connect` and
   `connect_ex` at startup. Every address is refused except an explicit
   allowlist, which contains exactly one entry: the local model server.
   Hostnames are always refused, because resolving one is itself a network call.
2. **Tests.** An autouse fixture in the root `conftest.py` installs the same
   guard with loopback *also* forbidden. Any test that touches the network
   fails. The exception must be declared with `@pytest.mark.loopback`.
3. **Dependencies.** `scripts/check_dependencies.py` fails CI on any package not
   in a reviewed allowlist, so a new dependency is a decision rather than a side
   effect.

## Alternatives considered

**Documentation and good intentions.** Unverifiable, and it degrades silently as
dependencies change.

**OS-level firewalling.** Stronger, and it cannot be shipped in a repository or
asserted in CI. Complementary, not a substitute.

**Auditing dependencies once, by hand.** A snapshot. The allowlist is a ratchet.

## Consequences

- "No data leaves the machine" is a property CI checks on every pull request,
  not a claim in a README.
- A dependency that phones home fails the test suite loudly, at the moment it is
  introduced.
- Any future feature that genuinely needs the network -- fetching a URL to
  archive, for instance -- requires an explicit, user-initiated exception and its
  own ADR. It cannot be added quietly.
- Tests that need the real local model server must be marked, which makes the
  exceptions countable.
