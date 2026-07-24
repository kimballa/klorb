# © Copyright 2026 Aaron Kimball
"""`TurnBridge`: the sync/async bridge that runs a blocking `Session.send_turn()` call on a
worker thread and converts its `TurnEventHandlers` callbacks into ordered ACP `session/update`
notifications -- see docs/specs/klorb-server.md's "Threading bridge" section."""

import asyncio
import logging
import threading
from typing import Any

import acp

from klorb.session import Session, TurnEventHandlers

logger = logging.getLogger(__name__)

_SENTINEL = object()
"""Enqueued by `run_turn()`'s `finally` to tell the pump task to stop, once the worker thread's
`Session.send_turn()` call has returned or raised -- distinct from `None`, which is never a
value a real session update could be."""


class TurnBridge:
    """Runs one `Session.send_turn()` call per `run_turn()` invocation, forwarding streamed
    text as ACP `session/update` notifications sent through `client`.

    Every `on_chunk`/`on_thinking_chunk` callback fires on `Session.send_turn()`'s worker
    thread (via `asyncio.to_thread`); each is enqueued onto one `asyncio.Queue` via
    `loop.call_soon_threadsafe`, and a single pump task awaits each `client.session_update()`
    call in the order the callbacks fired -- the ordering guarantee described in
    docs/specs/klorb-server.md. `run_turn()` always drains and stops the pump task in a
    `finally`, whether `send_turn()` succeeds, raises `ResponseAborted`, or raises anything
    else.
    """

    def __init__(self, session: Session, client: acp.Client, session_id: str) -> None:
        self._session = session
        self._client = client
        self._session_id = session_id

    async def run_turn(self, prompt_text: str) -> str:
        """Send `prompt_text` as one turn of `self._session`'s conversation, streaming the
        reply and any reasoning text out as ACP `session/update` notifications. Returns the
        model's final response text; propagates whatever `Session.send_turn()` raises
        (including `klorb.api_provider.ResponseAborted` on a cancelled turn), after the pump
        task has fully drained.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def enqueue(update: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, update)

        def on_chunk(delta_text: str) -> None:
            enqueue(acp.update_agent_message_text(delta_text))

        def on_thinking_chunk(delta_text: str) -> None:
            enqueue(acp.update_agent_thought_text(delta_text))

        cancel_event = threading.Event()
        handlers = TurnEventHandlers(
            on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk, cancel_event=cancel_event)

        async def pump() -> None:
            while True:
                update = await queue.get()
                if update is _SENTINEL:
                    return
                await self._client.session_update(session_id=self._session_id, update=update)

        pump_task = asyncio.create_task(pump())
        try:
            return await asyncio.to_thread(self._session.send_turn, prompt_text, handlers)
        finally:
            enqueue(_SENTINEL)
            await pump_task
