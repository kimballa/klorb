# © Copyright 2026 Aaron Kimball
"""`SubagentsPanel`: a docked, togglable right-hand panel listing every session in the current
subagent tree -- see `klorb.tui.mixins.subagents_panel.SubagentsPanelMixin` (Ctrl+G) and
docs/specs/subagents.md's "Subagents panel" section.
"""

from dataclasses import dataclass
from typing import Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from klorb.tui.widgets.task_sidebar import TASK_SIDEBAR_WIDTH

SUBAGENTS_PANEL_WIDTH = TASK_SIDEBAR_WIDTH
"""Same width as `TaskSidebar` -- the two panels occupy the same docked RHS slot, one at a time
(see `SubagentsPanelMixin.action_toggle_subagents_panel`)."""

SUBAGENTS_LIST_ID = "subagents-panel-list"
_HEADER_ID = "subagents-panel-header"
_FOOTER_ID = "subagents-panel-footer"

_HEADER_TEXT = "Agents"
_ATTENTION_MARKER = "!"
_RUNNING_MARKER = "*"
_IDLE_MARKER = " "


def _friendly_role_name(role_name: str) -> str:
    """
    Convert a role name like "operator" or "project_manager" into a friendlier name for
    display to user like "Operator" or "Project manager".
    """
    return role_name.capitalize().replace('_', ' ', -1)


@dataclass(frozen=True)
class SubagentRowData:
    """One row `SubagentsPanel.show_rows` renders -- a display-layer projection of one
    `klorb.agents.runtime.SessionTreeNode`, decoupled from `Session`/threading internals the way
    `TaskSidebar.show_tasks`'s plain dicts are decoupled from `ChainlinkClient`. No `depth` field:
    the dotted-decimal address (`"1.1.1"`) already reads as nested without needing extra
    indentation stacked on top of it."""

    session_id: str
    address: str
    title: str
    role: str
    state: Literal["running", "finished"] | None
    """`None` only for the tree's own root row -- a root session has no `SubagentHandle` describing
    it, so "running"/"finished" doesn't apply."""


class SubagentPanelOption(Option):
    """An `OptionList` row carrying the `session_id` it represents, so selecting it can recover
    which `Session` to switch the history view to -- mirrors `klorb.tui.widgets.palette.
    PaletteOption`."""

    def __init__(self, row: SubagentRowData, label: Text) -> None:
        super().__init__(label, id=row.session_id)
        self.session_id = row.session_id
        self.role = row.role


class SubagentsPanel(Vertical, can_focus=False):
    """Lists every session in the live subagent tree rooted at this process's top-level session,
    docked to the right edge of the screen and hidden until `Ctrl+G`
    (`SubagentsPanelMixin.action_toggle_subagents_panel`) first shows it. Unlike `TaskSidebar`,
    rows are selectable: the inner `OptionList` (`can_focus=True`, Textual's default) handles
    click and up/down-arrow navigation on its own, firing `OptionList.OptionHighlighted` for
    `SubagentsPanelMixin` to switch the displayed transcript. The footer row shows the currently
    selected agent's role.
    """

    DEFAULT_CSS = f"""
    SubagentsPanel {{
        dock: right;
        width: {SUBAGENTS_PANEL_WIDTH};
        border-left: solid $accent;
        display: none;
    }}
    #{_HEADER_ID} {{
        background: $panel;
        color: $foreground;
        width: 1fr;
        padding: 0 1;
    }}
    /* Textual's OptionList default CSS draws its own border (and a different one on focus) and
       background -- cleared here so rows sit flush with the header/footer's own `padding: 0 1`
       inset, and so the panel's own `border-left` above is the only border drawn. */
    #{SUBAGENTS_LIST_ID} {{
        height: 1fr;
        width: 1fr;
        border: none;
        background: transparent;
    }}
    #{SUBAGENTS_LIST_ID}:focus {{
        border: none;
    }}
    #{_FOOTER_ID} {{
        background: $panel;
        color: $text-muted;
        width: 1fr;
        padding: 0 1;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(_HEADER_TEXT, id=_HEADER_ID, markup=False)
        yield OptionList(id=SUBAGENTS_LIST_ID)
        yield Static("", id=_FOOTER_ID, markup=False)

    def show_rows(
        self, rows: list[SubagentRowData], selected_session_id: str,
        attention_ids: frozenset[str], blink_on: bool,
    ) -> None:
        """Replace the displayed rows with `rows` (expected in `klorb.agents.runtime.
        walk_session_tree` order), highlighting whichever one's `session_id` matches
        `selected_session_id` and updating the footer with its role. `attention_ids` -- sessions
        with an ask waiting for the user to select them (see `klorb.tui.mixins.interactions.
        InteractionsMixin._await_session_selected`) -- get a `(!)` marker, shown only while
        `blink_on` so repeated calls from `SubagentsPanelMixin._tick_subagents_panel` make it
        blink rather than stay lit solid.
        """
        option_list = self.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList)
        option_list.clear_options()
        selected_index = 0
        selected_role = ""
        for index, row in enumerate(rows):
            needs_attention = row.session_id in attention_ids and blink_on
            label = self._render_row_label(row, needs_attention)
            option_list.add_option(SubagentPanelOption(row, label))
            if row.session_id == selected_session_id:
                selected_index = index
                selected_role = row.role
        if rows:
            option_list.highlighted = selected_index
        footer = self.query_one(f"#{_FOOTER_ID}", Static)
        footer.update(f"Role: {_friendly_role_name(selected_role)}" if selected_role else "")

    @staticmethod
    def _render_row_label(row: SubagentRowData, needs_attention: bool) -> Text:
        """One marker character -- `!` if `needs_attention`, else `*` while `row.state ==
        "running"`, else a blank -- followed by the address and title. No depth-based indent: a
        subagent's dotted-decimal address (`"1.1.1"`) already reads as nested without needing
        extra padding stacked in front of it."""
        if needs_attention:
            marker = _ATTENTION_MARKER
        elif row.state == "running":
            marker = _RUNNING_MARKER
        else:
            marker = _IDLE_MARKER
        return Text(f"{marker} {row.address} {row.title}")
