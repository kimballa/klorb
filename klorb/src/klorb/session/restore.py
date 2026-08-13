# © Copyright 2026 Aaron Kimball
"""`try_restore_session`: rebuild a `Session` from a previously saved `sessions/<subdir>/
session.json`, shared by every caller that resumes a past session -- the TUI's
restore-latest-on-startup flow and its "Load session" picker
(`klorb.tui.mixins.workspace_bootstrap`), and the ACP server's `session/load` support
(`klorb.server.klorb_agent.KlorbAcpAgent.load_session`). Not imported by `klorb.session`'s own
`__init__.py` -- unlike every mixin under `klorb.session.mixins`, this module needs the fully
assembled `Session` class itself, which would be circular if it were part of that assembly.
"""

import base64
import logging

from klorb.agents.policy import compute_root_session_grants
from klorb.api_provider import ApiProvider
from klorb.lockfile import create_lockfile
from klorb.message import Message
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.workspace import Workspace
from klorb.workspace.session_store import (
    RecentSession,
    read_session_image,
    read_session_state,
    session_lock_path,
)

logger = logging.getLogger(__name__)


def _rehydrate_image_fragments(workspace: Workspace, subdir: str, messages: list[Message]) -> None:
    """Rebuild `image_url` (in place) for every persisted image fragment across `messages`,
    reading each one's bytes back from `image_path` -- see `Message.for_persistence`, which
    cleared `image_url` for exactly these fragments before writing `session.json`. Doing this
    once here, right after load, keeps `Message.provider_content()`/`MessageFragment.
    to_wire_dict()` pure functions of in-memory state, needing no filesystem access of their
    own on every turn a restored session's history is resent."""
    for message in messages:
        if message.fragments is None:
            continue
        for fragment in message.fragments:
            if fragment.type == "image_url" and fragment.image_path and fragment.image_url is None:
                data = read_session_image(workspace, subdir, fragment.image_path)
                fragment.image_url = {
                    "url": f"data:{fragment.mime_type};base64,{base64.b64encode(data).decode('ascii')}"}


def try_restore_session(
    workspace: Workspace,
    entry: RecentSession,
    *,
    provider: ApiProvider,
    model_registry: ModelRegistry,
    process_config: ProcessConfig,
) -> Session | None:
    """Attempt to lock and rebuild the session recorded by `entry` (a `sessions.json` entry for
    `workspace`). Returns `None` -- with no lasting side effect beyond the failed lock probe --
    if `entry`'s `sessions/<subdir>/` directory is currently locked by another live process, or
    its `session.json` is missing or fails to validate (a hand-edited or otherwise corrupted
    file).

    On success, the returned `Session` has already adopted `entry`'s `session.lock`
    (`Session.adopt_claimed_session_directory`) -- the caller does not need to (and must not)
    call `Session.claim_session_directory()` itself.
    """
    lock = create_lockfile(session_lock_path(workspace, entry.subdir))
    if not lock.try_acquire():
        logger.debug(
            "Session %s (subdir=%s) is locked by another process; not restoring.",
            entry.session_id, entry.subdir)
        return None
    state = read_session_state(workspace, entry.subdir)
    if state is None:
        lock.release()
        return None

    restored_config = state.config.model_copy(update={"workspace": workspace})
    grants = compute_root_session_grants(process_config, restored_config, restored_config.role_name)
    restored_config.skill_rules = grants.skill_rules
    session = Session(
        restored_config, provider=provider, model_registry=model_registry,
        process_config=process_config,
        session_id=state.session_id, root_id=state.root_id, session_name=state.session_name,
        last_modified_at=state.last_modified_timestamp,
        tool_registry=grants.tool_registry,
        effective_subagent_roles=grants.effective_subagent_roles)
    session.set_chainlink_task(state.cur_chainlink_task_id)
    _rehydrate_image_fragments(workspace, entry.subdir, state.messages)
    session.load_messages(state.messages)
    if state.statistics is not None:
        session.load_statistics(state.statistics)
    session.adopt_claimed_session_directory(entry.subdir, lock)
    logger.debug("Restored session %s (subdir=%s) for workspace %s.",
                 session.id, entry.subdir, workspace.path)
    return session
