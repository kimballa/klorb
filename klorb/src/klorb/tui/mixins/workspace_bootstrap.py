# © Copyright 2026 Aaron Kimball
"""WorkspaceBootstrapMixin: workspace trust resolution, session restore, and the
startup announcement flow for ReplApp."""

import logging

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import Static

from klorb.process_config import ProcessConfig, load_process_config, project_config_path
from klorb.session import Session
from klorb.tools.registry import ToolRegistry
from klorb.tools.skill.catalog import get_skill_catalog_registry
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.trust_commands import TRUST_WORKSPACE_LABEL
from klorb.tui.constants import HISTORY_ID, NEW_SESSION_LABEL, PROMPT_INPUT_ID, SESSION_NAME_ID
from klorb.tui.formatting import concat_dir_rules
from klorb.tui.panels.confirm_screen import ConfirmScreen
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.workspace import Workspace
from klorb.workspace.input_history import project_history_path
from klorb.workspace.last_session import read_last_session
from klorb.workspace.workspace_init import (
    write_initial_project_config,
    write_session_defaults_to_project_config,
)

logger = logging.getLogger(__name__)


class WorkspaceBootstrapMixin(ReplAppBase):
    """Workspace trust resolution/bootstrapping, last-session restore, and the
    "Trust workspace" palette command flow -- see `ReplApp` for how this mixes into the
    concrete app class."""

    @work()
    async def _run_startup_workspace_and_initial_message(self) -> None:
        """Runs as a proper (non-thread) Textual worker, not directly from `on_mount`, because
        `_resolve_workspace_trust()` may push a `ConfirmScreen` and await its dismissal
        (`push_screen_wait`) — which Textual only allows from within an active worker's
        context, not from a plain event handler. Submits `self._initial_message` (if any) only
        once workspace trust is resolved, so the very first turn already reflects any
        newly-granted permissions rather than racing the interactive bootstrap.
        """
        await self._resolve_workspace_trust()
        if self._initial_message:
            self._submit_prompt(self._initial_message)

    def workspace_trust_management_enabled(self) -> bool:
        """Whether this app was constructed with a `TrustManager` — see `TrustWorkspaceCommandProvider`."""
        return self._trust_manager is not None

    def is_workspace_trusted(self) -> bool:
        """Whether the current workspace is currently trusted — see `TrustWorkspaceCommandProvider`."""
        return self._session.config.workspace.trusted

    async def _resolve_workspace_trust(self) -> None:
        """A no-op unless this app was given a `TrustManager` (see `__init__`). Otherwise:
        if `SessionConfig.workspace` (already resolved by whichever
        `klorb.process_config.load_process_config()` call built the live session's config) has
        no `projects.json` record yet (`workspace.id is None`), interactively bootstraps it
        (`_bootstrap_new_workspace`) and applies whatever the user decided
        (`_apply_workspace_config`); either way, finishes by announcing the resulting trust
        state in the history (`_announce_workspace`). See docs/specs/projects-and-trust.md.
        """
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        if workspace.id is None:
            workspace = await self._bootstrap_new_workspace(workspace)
            self._apply_workspace_config(workspace)
        self._announce_workspace(workspace)
        # Now that the workspace is resolved (and, if it was brand-new, registered), attach
        # the file-backed input-history store so up/down-arrow recall reaches prior sessions
        # and new submissions persist. Done only here (gated on `trust_manager`, i.e. a real
        # `cli.main()` run) so a `ReplApp` constructed without one (every existing test) keeps
        # purely in-memory recall and never touches a real `$KLORB_DATA_DIR`.
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.set_history_store(project_history_path(workspace))
        if workspace.trusted:
            self._maybe_restore_last_session(workspace)

    def _maybe_restore_last_session(self, workspace: Workspace) -> None:
        """If a previous interactive session in `workspace` saved its state (see
        `_quit_after_maybe_saving`), replace the freshly-constructed `Session` with one built
        from that saved `SessionConfig` and message history, and re-render the history scroll
        to match — so a trusted workspace picks its conversation back up where the last klorb
        process left off, instead of starting blank. A no-op if no `last-session.json` exists
        for `workspace` yet (`read_last_session` returns `None`).

        Only called for a trusted `workspace` (see caller): an untrusted or unresolved
        workspace has no saved state of its own to restore, by the same reasoning
        `_quit_after_maybe_saving` uses to decide whether to write one.
        """
        state = read_last_session(workspace)
        if state is None:
            return
        restored_config = state.config.model_copy(update={"workspace": workspace})
        self._session.close()
        self._session = Session(
            restored_config, provider=self._session.provider,
            model_registry=self._session.model_registry, process_config=self._process_config,
            session_id=state.session_id,
            root_id=state.root_id,
            session_name=state.session_name,
            tool_registry=ToolRegistry.discover_tools(self._process_config, restored_config))
        self._session.set_chainlink_task(state.cur_chainlink_task_id)
        self._session.load_messages(state.messages)
        if state.statistics is not None:
            self._session.load_statistics(state.statistics)
        self.sub_title = restored_config.model
        self._update_status_bar()
        session_name_widget = self.query_one(f"#{SESSION_NAME_ID}", Static)
        if state.session_name is not None:
            # A previously-named session was restored; `Session.__init__` seeds
            # `session_naming_pending = False` from the `session_name` passed above, so the
            # classifier won't re-trigger on the next prompt.
            session_name_widget.update(f"Session: {state.session_name}")
        else:
            session_name_widget.update(NEW_SESSION_LABEL)
        self._mount_restored_history(state.messages)

    async def _bootstrap_new_workspace(self, workspace: Workspace) -> Workspace:
        """Ask the two workspace-bootstrap questions from docs/specs/projects-and-trust.md for
        a workspace with no `projects.json` record yet: whether to open it as a project (a
        persistent record plus a starter `.klorb/klorb-config.json`), and whether to trust it.
        If opened as a project, registers it (`TrustManager.register_project`) and writes its
        starter config file (`write_initial_project_config`, burning in the session's
        currently-active model) -- unless the workspace already ships its own
        `.klorb/klorb-config.json` (e.g. a downloaded repository that ships one), which is
        kept as-is; otherwise returns an unregistered `Workspace` carrying only the trust
        decision, kept in memory for the rest of this session's lifetime (see
        `SessionConfig.workspace`).
        """
        assert self._trust_manager is not None
        open_as_project = await self.push_screen_wait(ConfirmScreen(
            f"You are working in {workspace.path}. Open as a project?\n\n"
            "Projects have persistent settings files and permissions.",
            yes_label="Open as project", no_label="Not now"))
        trusted = await self.push_screen_wait(
            ConfirmScreen(f"Do you trust the workspace at {workspace.path}?"))
        if open_as_project:
            new_workspace = self._trust_manager.register_project(workspace.path, trusted)
            if project_config_path(workspace.path).is_file():
                logger.debug(
                    "Keeping existing project config at %s; skipping starter config write.",
                    project_config_path(workspace.path))
            else:
                write_initial_project_config(
                    workspace.path, self._process_config.session.model, trusted)
            return new_workspace
        return Workspace(path=workspace.path, is_project=False, trusted=trusted)

    def _apply_workspace_config(self, workspace: Workspace) -> None:
        """Recompute the layered config now that `workspace`'s trust/registration state may
        have just changed (a newly-trusted project's own `.klorb/klorb-config.json` becomes
        readable, or a freshly-registered project's just-written starter file does), and apply
        it to the live process/session config in place — mutating the existing
        `ProcessConfig`/`SessionConfig` objects rather than reconstructing `Session`/
        `ToolRegistry`, so any conversation history already in this session is left untouched
        and every tool sees the change on its very next call (both hold references to these
        same objects, not copies — see `klorb.tools.registry.ToolRegistry`).

        `read_dirs`/`write_dirs` are concatenated onto the live session's own via
        `concat_dir_rules` (never replaced), so an "Allow (this session)" grant made before
        the user decided to trust the workspace isn't discarded. Every process-only
        (`ProcessConfig`) field is overwritten from the reload outright; `session`'s other
        scalar fields (model, thinking, tool-call limits) are deliberately left alone here,
        since a config file's declared defaults shouldn't silently override a value the user
        may have already picked interactively earlier this same session.

        `workspace` itself is dual-written onto both the live `self._session.config` and the
        `self._process_config.session` template — the same pattern `select_model()`/
        `set_thinking_enabled()` already use for session-scoped settings — so a future `/clear`
        in this process inherits the resolved trust state instead of re-bootstrapping it.

        This reload can surface a `config_warnings` entry that `on_mount()`'s startup pass never
        saw — the project config layer is only read once `workspace.trusted` is `True`, which
        for a brand-new workspace is only resolved here — so any warning not already shown is
        posted to the history via `show_notice()` below.

        Also forces a fresh `SkillCatalogRegistry` scan (`klorb.tools.skill.catalog`): a newly
        trusted workspace's `.klorb/skills/` tier is invisible to the process-wide catalog until
        rebuilt, since `SkillCatalogRegistry.ensure()` is a no-op once a catalog already exists.
        """
        reloaded = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)
        new_warnings = [
            warning for warning in reloaded.config_warnings
            if warning not in self._process_config.config_warnings
        ]

        for field_name in ProcessConfig.model_fields:
            # `session` is folded in separately below; `argv`/`cli_flags` are set once
            # by `klorb.cli.main()` and never re-derived by `load_process_config()`, so a
            # reload would otherwise wipe them back to their empty defaults.
            if field_name in ("session", "argv", "cli_flags"):
                continue
            setattr(self._process_config, field_name, getattr(reloaded, field_name))

        self._session.config.workspace = workspace
        self._process_config.session.workspace = workspace

        self._session.config.read_dirs = concat_dir_rules(
            self._session.config.read_dirs, reloaded.session.read_dirs)
        self._session.config.write_dirs = concat_dir_rules(
            self._session.config.write_dirs, reloaded.session.write_dirs)
        self._process_config.session.read_dirs = concat_dir_rules(
            self._process_config.session.read_dirs, reloaded.session.read_dirs)
        self._process_config.session.write_dirs = concat_dir_rules(
            self._process_config.session.write_dirs, reloaded.session.write_dirs)

        get_skill_catalog_registry().reload(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=self._process_config.compatibility_claude_skills)

        for warning in new_warnings:
            self.show_notice(warning, error=True)

        self._refresh_header_title()

    def _announce_workspace(self, workspace: Workspace) -> None:
        """Mount the one-line history notice docs/specs/projects-and-trust.md specifies for the
        resulting workspace state: which directory, and whether it's trusted. Constructed with
        `markup=False` since `workspace.path` is a filesystem path that must render verbatim
        rather than be parsed as Textual console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        if workspace.trusted:
            history.mount(Static(
                f"Working in project: {workspace.path}", classes="notice", markup=False))
        else:
            history.mount(Static(
                f"The workspace at {workspace.path} is not trusted. "
                f"Run `{PALETTE_PREFIX}{TRUST_WORKSPACE_LABEL}` to change this.",
                classes="notice", markup=False))

    @work()
    async def trust_workspace(self) -> None:
        """`TrustWorkspaceCommandProvider`'s action: confirm with the user, then trust the
        current workspace — persisting the decision to `projects.json` if it's a registered
        project (`TrustManager.set_trusted`) — and apply the now-unlocked config
        (`_apply_workspace_config`). If the workspace is a registered project with no
        `.klorb/klorb-config.json` of its own yet, additionally offers to write one from the
        live session's current settings (`write_session_defaults_to_project_config`), so any
        `readDirs`/`writeDirs` grants built up earlier this session aren't lost the next time
        klorb opens this workspace. A no-op if the user declines the initial confirmation, or
        if this app has no `TrustManager` (see `workspace_trust_management_enabled`) — the
        palette command that calls this is hidden in that case, but this method still guards
        against being invoked some other way.

        `@work()` (a proper Textual worker, not a thread) rather than a plain `async def`: like
        `_run_startup_workspace_and_initial_message`, this pushes a `ConfirmScreen` and awaits
        its dismissal (`push_screen_wait`), which Textual only allows from within an active
        worker's context. Called directly as the palette command's callback (see
        `TrustWorkspaceCommandProvider`/`PromptInput._run_palette_command`) — invoking a
        `@work()`-decorated method starts the worker and returns a `Worker`, not a coroutine, so
        callers don't (and shouldn't) await this directly.
        """
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        confirmed = await self.push_screen_wait(
            ConfirmScreen(f"Do you trust the workspace at {workspace.path}?"))
        if not confirmed:
            return

        trusted_workspace = workspace.model_copy(update={"trusted": True})
        if trusted_workspace.is_project:
            assert trusted_workspace.id is not None
            self._trust_manager.set_trusted(trusted_workspace.id, True)
        self._apply_workspace_config(trusted_workspace)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(
            f"Trusted workspace {trusted_workspace.path}.", classes="notice", markup=False))

        config_path = project_config_path(trusted_workspace.path)
        if trusted_workspace.is_project and not config_path.is_file():
            init_confirmed = await self.push_screen_wait(ConfirmScreen(
                "Initialize the project config file with your current session settings?"))
            if init_confirmed:
                write_session_defaults_to_project_config(trusted_workspace.path, self._session.config)
                history.mount(Static(
                    f"Wrote project config to {config_path}.", classes="notice", markup=False))

