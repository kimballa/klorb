# © Copyright 2026 Aaron Kimball
"""`JsonlServer`: the read-dispatch-reply loop behind `klorb server` (see `klorb.cli.
run_server_cli`) -- see docs/specs/klorb-server.md for the wire protocol.
"""

import json
import logging
from typing import IO, Any

logger = logging.getLogger(__name__)

SHUTDOWN_ACTION = "shutdown"
GREET_KEY = "greet"


class JsonlServer:
    """Reads newline-delimited JSON (JSONL) command records from `stdin` one line at a time and
    writes a JSONL reply record to `stdout` for each, until a `{"action": "shutdown"}` command
    is received or `stdin` reaches EOF.

    Each input line is stripped of leading/trailing whitespace and parsed as one JSON value;
    a blank line is skipped rather than treated as an empty record. Every reply is written as
    a single JSON line (`json.dumps` never emits an embedded literal newline -- a `\\n` within
    a string value is escaped, not a line break) followed by `\\n`, and flushed immediately so
    a caller reading this process's stdout sees each reply as soon as it's produced.

    Installs no signal handling of its own: a `SIGINT` delivered while `run()` is blocked on
    `stdin` unwinds it via the interpreter's ordinary `KeyboardInterrupt`, left for the caller
    (`klorb.cli.run_server_cli`) to catch.
    """

    def __init__(self, *, stdin: IO[str], stdout: IO[str]) -> None:
        self._stdin = stdin
        self._stdout = stdout

    def run(self) -> int:
        """Read and dispatch records from `stdin` until a shutdown command or EOF. Always
        returns `0` -- there is currently no error condition here that warrants a distinct
        non-zero exit status; a malformed record gets an `{"error": ...}` reply instead of
        stopping the loop.
        """
        logger.debug("klorb server starting")
        for line in self._stdin:
            stripped = line.strip()
            if not stripped:
                continue
            if not self._handle_line(stripped):
                break
        logger.debug("klorb server stopping")
        return 0

    def _handle_line(self, line: str) -> bool:
        """Parse and dispatch one non-blank input line. Returns `False` if this record was a
        shutdown command -- `run()`'s loop should stop reading -- `True` otherwise.
        """
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.debug("klorb server discarding malformed JSONL record: %s", exc)
            self._write({"error": f"invalid JSON: {exc}"})
            return True

        if not isinstance(record, dict):
            self._write({"error": "record must be a JSON object"})
            return True

        if record.get("action") == SHUTDOWN_ACTION:
            logger.debug("klorb server received shutdown command")
            return False

        if GREET_KEY in record:
            self._handle_greet(record)
            return True

        self._write({"error": "unrecognized command"})
        return True

    def _handle_greet(self, record: dict[str, Any]) -> None:
        name = record.get(GREET_KEY)
        if not isinstance(name, str):
            self._write({"error": "'greet' must be a string"})
            return
        self._write({"message": f"hello, {name}!"})

    def _write(self, record: dict[str, Any]) -> None:
        self._stdout.write(json.dumps(record))
        self._stdout.write("\n")
        self._stdout.flush()
