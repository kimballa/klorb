# © Copyright 2026 Aaron Kimball
"""`SessionPromptSetupMixin`: resolves the active turn's system prompt and reasoning/cache
request shape, and ensures the session's bookkeeping-only `system`/`tool_defs` messages are
inserted before the first turn is dispatched."""

import json
from datetime import datetime
from typing import Any

from klorb.message import Message
from klorb.models.model import CacheMgmtStyle
from klorb.session.mixins._base import SessionBase
from klorb.token_estimate import estimate_tokens


class SessionPromptSetupMixin(SessionBase):
    """System-prompt resolution, reasoning/cache-style parameters, and the one-time
    `system`/`tool_defs` bookkeeping messages `_dispatch_turn` ensures are present before
    sending a turn's first request."""

    def _resolve_system_prompt(self) -> str:
        """Resolve the system prompt for the active turn by delegating to the session's
        `SystemPrompt` (see `klorb.system_prompt.SystemPrompt.resolve`), which runs the
        default and role walks and concatenates their results. Re-derived fresh each place
        it's needed, so it always reflects the *current* active model — see
        docs/specs/roles-and-system-prompts.md."""
        return self._system_prompt.resolve()

    def _reasoning_params(self) -> dict[str, Any] | None:
        """Return the provider-shaped `reasoning` request body for the active turn, or
        `None` if thinking shouldn't be requested (disabled in config, or the active model
        isn't registered or doesn't report `thinking` support). Translates
        `config.thinking_effort` into whichever shape the active model's
        `thinking_budget_style` wants: an `"effort"` keyword, or a numeric `"max_tokens"`
        budget looked up from `THINKING_EFFORT_TOKEN_BUDGETS`.
        """
        if not self.config.thinking_enabled:
            return None
        model = self.active_model()
        if model is None or not model.capabilities().get("thinking", False):
            return None
        budget_style = model.capabilities().get("thinking_budget_style", "effort")
        if budget_style == "tokens":
            return {"max_tokens": self._thinking_token_budgets[self.config.thinking_effort]}
        return {"effort": self.config.thinking_effort}

    def _drop_reasoning(self) -> bool:
        """Return whether the active model wants prior turns' thinking/reasoning content
        stripped from the outgoing request rather than resent as-is (see
        `Model.drop_reasoning`) -- `False`, matching that method's own default, if the active
        model isn't registered."""
        model = self.active_model()
        return model.drop_reasoning() if model is not None else False

    def _cache_mgmt_style(self) -> CacheMgmtStyle:
        """Return the cache management style for the active model (see
        `Model.cache_mgmt_style`)."""
        model = self.active_model()
        return model.cache_mgmt_style() if model is not None else "AUTOMATIC"

    def _ensure_system_message(self) -> None:
        """Insert a `role="system"` `Message` at the very front of `self._messages`,
        recording the session's resolved system prompt (see `_resolve_system_prompt` —
        resolution always produces a prompt, so one is always inserted), the first time a
        turn is dispatched. A no-op if one is already present (so retries and later turns
        don't insert a duplicate). This message is bookkeeping only: it's never replayed to
        the model as a chat message (see `OpenRouterApiProvider._build_api_messages`) — the
        live system prompt is sent via `send_prompt(system_prompt=...)` on every turn
        instead (re-resolved fresh via `_resolve_system_prompt()` each time, so it reflects
        the *current* active model even if the session's `config.model` changes after this
        message was inserted), so this doesn't need to be kept in sync with such changes
        after it's first inserted.
        """
        if any(message.role == "system" for message in self._messages):
            return
        system_prompt = self._resolve_system_prompt()
        self._messages.insert(0, Message(
            content=system_prompt,
            role="system",
            num_tokens=estimate_tokens(system_prompt),
            processing_state="complete",
            timestamp=datetime.now(),
        ))

    def _ensure_tool_defs_message(self) -> None:
        """Insert a `role="tool_defs"` `Message` recording the tool definitions offered for
        this session — right after the `role="system"` message if `_ensure_system_message()`
        inserted one, otherwise at the very front of `self._messages` — the first time a turn
        is dispatched with a non-empty `tool_registry`. A no-op if `tool_registry` is
        unset/empty, or if one is already present (so retries and later turns don't insert a
        duplicate). This message is bookkeeping only: it's never sent to the model as a chat
        message (see `OpenRouterApiProvider._build_api_messages`) — the live tool definitions
        are sent via `send_prompt(tools=...)` on every turn instead, so this doesn't need to
        be kept in sync with config changes after it's first inserted.
        """
        if self._tool_registry is None:
            return
        if any(message.role == "tool_defs" for message in self._messages):
            return
        definitions = self._tool_registry.tool_definitions()
        if not definitions:
            return
        definitions_json = json.dumps(definitions)
        insert_index = 1 if self._messages and self._messages[0].role == "system" else 0
        self._messages.insert(insert_index, Message(
            content=definitions_json,
            role="tool_defs",
            num_tokens=estimate_tokens(definitions_json),
            processing_state="complete",
            timestamp=datetime.now(),
        ))
