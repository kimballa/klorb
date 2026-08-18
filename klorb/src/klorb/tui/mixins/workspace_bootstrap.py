# © Copyright 2026 Aaron Kimball
"""WorkspaceBootstrapMixin: workspace trust resolution, session restore, and the
startup announcement flow for ReplApp."""

import logging

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import Static

from klorb.agents.policy import compute_root_session_grants
from klorb.permissions.directory_access import concat_dir_rules
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    SESSION_DEFAULTS_KEY,
    ProcessConfig,
    load_process_config,
    project_config_path,
)
from klorb.schema_envelope import read_versioned_json
from klorb.session import Session
from klorb.session.restore import try_restore_session
from klorb.tools.util.secret_redaction import clear_cached_redactor
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.trust_commands import TRUST_WORKSPACE_LABEL
from klorb.tui.constants import HISTORY_ID, NEW_SESSION_LABEL, PROMPT_INPUT_ID, SESSION_NAME_ID
from klorb.tui.panels.confirm_screen import ConfirmScreen
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.workspace import Workspace
from klorb.workspace.input_history import project_history_path
from klorb.workspace.session_store import RecentSession, read_sessions_index
from klorb.workspace.workspace_init import (
    write_initial_project_config,
    write_session_defaults_to_project_config,
)

logger = logging.getLogger(__name__)


class WorkspaceBootstrapMixin(ReplAppBase):
    """Workspace trust resolution/bootstrapping, saved-session restore/load, and the
    "Trust workspace" palette command flow."""

    def list_recent_sessions(self) -> list[RecentSession]:
        """Return the live workspace's saved sessions, most recently touched first. `[]` when
        this app has no `TrustManager` at all."""
        if self._trust_manager is None:
            return []
        return read_sessions_index(self._session.config.workspace).recent_sessions

    def load_recent_session(self, entry: RecentSession) -> None:
        """Replace the active session with the one recorded by `entry`. A no-op if `entry` is
        already the live session. If the saved session can't be loaded, reports why via
        `show_notice` and starts a fresh session."""
        if entry.session_id == self._session.id:
            return
        workspace = self._session.config.workspace
        provider = self._session.provider
        model_registry = self._session.model_registry
        self._session.close()
        restored = try_restore_session(
            workspace, entry, provider=provider, model_registry=model_registry,
            process_config=self._process_config)
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()
        if restored is None:
            new_config = self._process_config.session.model_copy(update={"workspace": workspace})
            grants = compute_root_session_grants(self._process_config, new_config, new_config.role_name)
            new_config.skill_rules = grants.skill_rules
            self._session = Session(
                new_config, provider=provider, model_registry=model_registry,
                process_config=self._process_config,
                tool_registry=grants.tool_registry,
                effective_subagent_roles=grants.effective_subagent_roles)
            self._selected_session = self._session
            self._selected_handle = None
            self._wire_session_notice_handler(self._session)
            self._wire_session_wake_handler(self._session)
            self.show_notice(
                f"Could not load session {entry.title or entry.session_id!r}: it is locked by "
                "another process or no longer available. Started a new session instead.",
                error=True)
            self._update_status_bar()
            session_name_widget = self.query_one(f"#{SESSION_NAME_ID}", Static)
            session_name_widget.update(NEW_SESSION_LABEL)
            return
        self._adopt_restored_session(restored)

    @work()
    async def _run_startup_workspace_and_initial_message(self) -> None:
        """Runs as a Textual worker because `_resolve_workspace_trust()` may push a
        `ConfirmScreen` and await its dismissal. Submits `self._initial_message` (if any)
        only once workspace trust is resolved."""
        await self._resolve_workspace_trust()
        if self._initial_message:
            self._submit_prompt(self._initial_message)

    def workspace_trust_management_enabled(self) -> bool:
        """Whether this app was constructed with a `TrustManager`."""
        return self._trust_manager is not None

    def is_workspace_trusted(self) -> bool:
        """Whether the current workspace is currently trusted."""
        return self._session.config.workspace.trusted

    @staticmethod
    def _workspace_auto_allowed_skills(workspace: Workspace) -> list[str]:
        """Workspace skill names from the workspace's own `.klorb/klorb-config.json`
        `skillRules.allow`. Reads the config directly and returns names whose
        fully-qualified entry starts with `workspace:`."""
        config_path = project_config_path(workspace.path)
        raw = read_versioned_json(config_path, expected_schema_name=CONFIG_SCHEMA_NAME)
        session_defaults = raw.get(SESSION_DEFAULTS_KEY, {})
        skill_rules = session_defaults.get("skillRules", {})
        results: list[str] = list(filter(
            lambda name: bool(name),
            (entry[len("workspace:"):] for entry in skill_rules.get("allow", [])
             if isinstance(entry, str) and entry.startswith("workspace:")),
        ))
        return sorted(results)

    def _trust_prompt_message(self, workspace: Workspace) -> str:
        """Build the trust confirmation prompt, listing auto-allowed workspace skills if any."""
        base = f"Do you trust the workspace at {workspace.path}?"
        skills = self._workspace_auto_allowed_skills(workspace)
        if not skills:
            return base
        skill_list = "\n".join(f"  - {name}" for name in skills)
        return f"{base}\n\nWorkspace skills auto-allowed by config:\n{skill_list}"

    async def _resolve_workspace_trust(self) -> None:
        """A no-op unless this app was given a `TrustManager`. If the workspace has no
        `projects.json` record yet, interactively bootstraps it and applies the user's
        decision. Finishes by announcing the resulting trust state and restoring the most
        recent saved session. See docs/specs/projects-and-trust.md."""
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        just_bootstrapped = workspace.id is None
        if just_bootstrapped:
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
        # Same gating rationale as the history store above: start the `@`-mention file finder's
        # background index only for a real `cli.main()` run, against the now-resolved workspace
        # root.
        self._start_file_finder_index(workspace)
        session_before_restore = self._session
        if workspace.trusted and not self._skip_session_restore:
            self._maybe_restore_latest_session(workspace)
        resumed = self._session is not session_before_restore
        self._session.fire_session_start_hook(
            "ResumeSession" if resumed else "NewSession",
            workspace_just_bootstrapped=just_bootstrapped)

    def _maybe_restore_latest_session(self, workspace: Workspace) -> None:
        """If `workspace`'s `sessions.json` has a most-recently-touched entry, replace the
        freshly-constructed `Session` with one built from that saved state and re-render the
        history scroll. A no-op if `sessions.json` has no entries, or if the entry is locked
        or corrupt."""
        index = read_sessions_index(workspace)
        if not index.recent_sessions:
            return
        restored = try_restore_session(
            workspace, index.recent_sessions[0], provider=self._session.provider,
            model_registry=self._session.model_registry, process_config=self._process_config)
        if restored is None:
            return
        self._session.close()
        self._adopt_restored_session(restored)

    def _adopt_restored_session(self, restored: Session) -> None:
        """Replace the live `Session` with `restored`, updating the header/status-line/
        session-name widgets and re-rendering the history scroll. The caller is responsible
        for closing the outgoing `Session` first."""
        self._session = restored
        self._selected_session = restored
        self._selected_handle = None
        self._wire_session_notice_handler(restored)
        self._wire_session_wake_handler(restored)
        self.sub_title = restored.config.model
        self._update_status_bar()
        session_name_widget = self.query_one(f"#{SESSION_NAME_ID}", Static)
        if restored.name is not None:
            # A previously-named session was restored; `Session.__init__` seeds
            # `session_naming_pending = False` from the `session_name` passed to it, so the
            # classifier won't re-trigger on the next prompt.
            session_name_widget.update(f"Session: {restored.name}")
        else:
            session_name_widget.update(NEW_SESSION_LABEL)
        self._mount_restored_history(restored.messages)

    async def _bootstrap_new_workspace(self, workspace: Workspace) -> Workspace:
        """Ask the two workspace-bootstrap questions for a workspace with no `projects.json`
        record yet: whether to open it as a project, and whether to trust it. If opened as a
        project, registers it and writes its starter config file unless the workspace already
        has its own `.klorb/klorb-config.json`."""
        assert self._trust_manager is not None
        open_as_project = await self.push_screen_wait(ConfirmScreen(
            f"You are working in {workspace.path}. Open as a project?\n\n"
            "Projects have persistent settings files and permissions.",
            yes_label="Open as project", no_label="Not now"))
        trusted = await self.push_screen_wait(
            ConfirmScreen(self._trust_prompt_message(workspace)))
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
        have just changed, and apply it to the live process/session config in place.
        `read_dirs`/`write_dirs` are concatenated rather than replaced. New config warnings
        are posted via `show_notice()`. Also forces a fresh skill-catalog scan."""
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

        clear_cached_redactor(self._session)

        self._session.config.read_dirs = concat_dir_rules(
            self._session.config.read_dirs, reloaded.session.read_dirs)
        self._session.config.write_dirs = concat_dir_rules(
            self._session.config.write_dirs, reloaded.session.write_dirs)
        self._process_config.session.read_dirs = concat_dir_rules(
            self._process_config.session.read_dirs, reloaded.session.read_dirs)
        self._process_config.session.write_dirs = concat_dir_rules(
            self._process_config.session.write_dirs, reloaded.session.write_dirs)

        self._session.reload_skills()

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
        """Confirm with the user, then trust the current workspace and apply the now-unlocked
        config. If the workspace is a registered project with no `.klorb/klorb-config.json`
        yet, offers to write one from the live session's current settings. A no-op if the
        user declines or if this app has no `TrustManager`."""
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        confirmed = await self.push_screen_wait(
            ConfirmScreen(self._trust_prompt_message(workspace)))
        if not confirmed:
            return

        trusted_workspace = workspace.model_copy(update={"trusted": True})
        if trusted_workspace.is_project:
            assert trusted_workspace.id is not None
            self._trust_manager.set_trusted(trusted_workspace.id, True)
        self._apply_workspace_config(trusted_workspace)
        self._session.fire_workspace_trust_changed_hook("TrustCommand")

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

