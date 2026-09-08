"""The full log of every model session.

The user asked to be able to open up any extracted fact and see exactly what
happened: which prompt, which model, what it answered, what we kept and what we
threw away. This writes that log.

One gzipped JSONL file per run, append-only, next to the database. It is not
indexed and not queried by the application -- it exists to be read by a human
who does not trust a result, which is the correct attitude to have towards a
model's output.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from diairy.core.clock import to_iso, utc_now


class RunLog:
    """Append-only record of the model calls made during one run."""

    def __init__(self, runs_dir: Path, run_id: str) -> None:
        self.path = runs_dir / f"{run_id}.jsonl.gz"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # SIM115: the handle deliberately outlives __init__; the class is itself
        # a context manager and close() is called by the pipeline in a finally.
        self._handle = gzip.open(self.path, "at", encoding="utf-8")  # noqa: SIM115
        self._entries = 0

    def record(self, kind: str, **fields: Any) -> None:
        """Append one entry. ``kind`` says what it is; the rest is free-form."""
        payload = {"at": to_iso(utc_now()), "kind": kind, **fields}
        self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._entries += 1

    def record_extraction(
        self,
        *,
        chunk_id: str,
        system: str,
        prompt: str,
        response: str,
        model: str,
        duration_ms: int,
        accepted: int,
        rejected: list[tuple[str, str]],
    ) -> None:
        """Record one extraction call, verbatim on both sides."""
        self.record(
            "extraction",
            chunk_id=chunk_id,
            model=model,
            duration_ms=duration_ms,
            system=system,
            prompt=prompt,
            response=response,
            accepted=accepted,
            rejected=[{"reason": reason, "detail": detail} for reason, detail in rejected],
        )

    @property
    def entry_count(self) -> int:
        return self._entries

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> RunLog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def read_run_log(path: Path) -> Iterator[dict[str, Any]]:
    """Read back a run log, entry by entry."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
