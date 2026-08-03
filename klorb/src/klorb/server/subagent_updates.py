# © Copyright 2026 Aaron Kimball
"""Pure function mapping a session tree's live subagent state onto the `_klorb/subagentTree`
ext-method result payload -- see docs/specs/subagents.md's "Subagents panel (VSCode)" section.
Kept free of ACP/`KlorbAcpAgent` dependencies, mirroring `klorb.server.plan_updates`, so it can be
unit-tested directly against a `Session` tree.
"""

from typing import Any

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, walk_session_tree
from klorb.session import Session


def _is_aborted(handle_output: str | None) -> bool:
    return handle_output is not None and SUBAGENT_ABORTED_MARKER in handle_output


def build_subagent_tree_snapshot(root: Session, acp_session_id: str) -> dict[str, Any]:
    """Build the `{"nodes": [...]}` payload for a `_klorb/subagentTree` ext-method result: one
    entry per session in the tree rooted at `root` (`root` itself included, first, with
    `state`/`aborted` reporting no subagent lifecycle since nothing created it), in the same
    pre-order/creation-order `klorb.tui.widgets.subagents_panel.SubagentsPanel` renders. Each
    entry carries enough of that session's own `SessionConfig`/token tally for a client to render
    a header/status-row for the *selected* session without a second round trip, the same fields
    `klorb.tui.mixins.status_bar.StatusBarMixin._update_status_bar`/`ReplApp.format_title` read
    off `_selected_session` today.

    `acp_session_id` overrides `root.id` as the root node's own reported `"id"`: the ACP client
    addresses the root session by the id `session/new`/`session/load` handed back
    (`KlorbAcpAgent._acp_session_id`), which can differ from `root.id` once the session-naming
    classifier renames it in place (see `KlorbAcpAgent`'s own class docstring) -- every other
    node's `"id"` is its own `Session.id`, which is never renamed (a subagent's `session_name` is
    pre-set at creation, skipping the classifier).
    """
    node_id_overrides = {root.id: acp_session_id}
    nodes: list[dict[str, Any]] = []
    for tree_node in walk_session_tree(root):
        session = tree_node.session
        parent = session.parent
        handle = tree_node.handle
        nodes.append({
            "id": node_id_overrides.get(session.id, session.id),
            "parentId": (
                node_id_overrides.get(parent.id, parent.id) if parent is not None else None),
            "address": session.address(),
            "title": session.name,
            "role": session.config.role_name,
            "state": handle.state if handle is not None else None,
            "aborted": _is_aborted(handle.output) if handle is not None else False,
            "model": session.config.model,
            "thinkingEnabled": session.config.thinking_enabled,
            "thinkingEffort": session.config.thinking_effort,
            "usedTokens": session.total_tokens_used(),
            "maxTokens": session.max_context_window(),
            "outputTokens": session.total_output_tokens_used(),
        })
    return {"nodes": nodes}
