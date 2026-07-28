# © Copyright 2026 Aaron Kimball
"""`try_restore_session`: rebuild a `Session` from a previously saved `sessions/<subdir>/
session.json`, shared by every caller that resumes a past session -- the TUI's
restore-latest-on-startup flow and its "Load session" picker
(`klorb.tui.mixins.workspace_bootstrap`), and the ACP server's `session/load` support
(`klorb.server.klorb_agent.KlorbAcpAgent.load_session`). Not imported by `klorb.session`'s own
`__init__.py` -- unlike every mixin under `klorb.session.mixins`, this module needs the fully
assembled `Session` class itself, which would be circular if it were part of that assembly.
"""

import logging

from klorb.api_provider import ApiProvider
from klorb.lockfile import create_lockfile
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace
from klorb.workspace.session_store import RecentSession, read_session_state, session_lock_path

logger = logging.getLogger(__name__)


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
    session = Session(
        restored_config, provider=provider, model_registry=model_registry,
        process_config=process_config,
        session_id=state.session_id, root_id=state.root_id, session_name=state.session_name,
        tool_registry=ToolRegistry.discover_tools(process_config, restored_config))
    session.set_chainlink_task(state.cur_chainlink_task_id)
    session.load_messages(state.messages)
    if state.statistics is not None:
        session.load_statistics(state.statistics)
    session.adopt_claimed_session_directory(entry.subdir, lock)
    logger.debug("Restored session %s (subdir=%s) for workspace %s.",
                 session.id, entry.subdir, workspace.path)
    return session
