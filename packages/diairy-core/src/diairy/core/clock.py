"""Time helpers.

diAIry is bitemporal: every fact carries both when the thing happened
(``event_time``) and when we learned it (``knowledge_time``). Mixing naive and
aware datetimes silently corrupts that, so every timestamp in the system is
timezone-aware and stored in UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(tz=UTC)


def ensure_aware(value: datetime) -> datetime:
    """Return ``value`` as UTC, assuming UTC when it carries no timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso(value: datetime) -> str:
    """Serialise a datetime to a sortable ISO-8601 UTC string."""
    return ensure_aware(value).isoformat()


def from_iso(value: str) -> datetime:
    """Parse an ISO-8601 string produced by :func:`to_iso`."""
    return ensure_aware(datetime.fromisoformat(value))
