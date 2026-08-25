# © Copyright 2026 Aaron Kimball
"""`try_restore_session`: rebuild a `Session` from a previously saved session state."""

import base64
import logging

from klorb.agents.chat import Channel
from klorb.agents.policy import compute_root_session_grants
from klorb.api_provider import ApiProvider
from klorb.lockfile import create_lockfile
from klorb.message import Message
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.workspace import Workspace
from klorb.workspace.chat_store import read_chat_state
from klorb.workspace.session_store import (
    RecentSession,
    read_session_image,
    read_session_state,
    session_lock_path,
)

logger = logging.getLogger(__name__)


def _rehydrate_image_fragments(workspace: Workspace, subdir: str, messages: list[Message]) -> None:
    """Rebuild `image_url` (in place) for every persisted image fragment across `messages`,
    reading each one's bytes back from `image_path`. Doing this once here, right after load,
    keeps `Message.provider_content()` and `MessageFragment.to_wire_dict()` pure functions
    of in-memory state."""
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
    """Attempt to lock and rebuild the session recorded by `entry`. Returns `None` if
    `entry`'s directory is currently locked by another live process, or its `session.json`
    is missing or fails to validate.

    On success, the returned `Session` has already adopted `entry`'s `session.lock`.
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

    restored_config = state.config.model_copy()
    restored_config.apply_workspace_access(
        workspace=workspace, read_dirs=restored_config.read_dirs,
        write_dirs=restored_config.write_dirs)
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
    chat_snapshot = read_chat_state(workspace, entry.subdir)
    if chat_snapshot is not None:
        session.load_chat_channel(Channel.restore(
            chat_snapshot, max_history=process_config.chat_max_history,
            max_mention_wakes=process_config.chat_max_mention_wakes))
    session.adopt_claimed_session_directory(entry.subdir, lock)
    logger.debug("Restored session %s (subdir=%s) for workspace %s.",
                 session.id, entry.subdir, workspace.path)
    return session
