# © Copyright 2026 Aaron Kimball
"""A reusable Yes/No confirmation modal, shared by every workspace-trust prompt
(`klorb.tui.ReplApp`'s workspace-bootstrap flow and its "Trust workspace" palette
command — see docs/specs/projects-and-trust.md) so each one isn't its own near-identical
`ModalScreen` subclass. Distinct from `klorb.tui.widgets.tool_call_widgets.ToolCallLimitScreen`,
an earlier, narrower yes/no modal this doesn't replace — see
docs/adrs/reuse-a-generic-confirmscreen-for-workspace-trust-prompts.md.
"""

from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

CONFIRM_YES_ID = "confirm-yes"
CONFIRM_NO_ID = "confirm-no"

QUIT_SAVE_ID = "quit-confirm-save"
QUIT_DISCARD_ID = "quit-confirm-discard"
QUIT_CANCEL_ID = "quit-confirm-cancel"

SaveOnQuitChoice = Literal["save", "discard", "cancel"]


class ConfirmScreen(ModalScreen[bool]):
    """Shows `message` with Yes/No buttons; dismisses `True`/`False` accordingly. Escape (or
    the "No" button) always dismisses `False`. `yes_label`/`no_label` default to "Yes"/"No"
    but can be overridden for a more specific affirmative/negative phrasing. Left/Right arrow
    keys move focus between the two buttons, same as Tab/Shift+Tab. Press `y` to accept or
    `n` to decline without moving focus.
    """

    CSS = """
    ConfirmScreen {
        align: center middle;
    }

    ConfirmScreen Vertical {
        width: auto;
        max-width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #confirm-message {
        margin: 0 0 1 0;
    }

    #confirm-buttons {
        align: center middle;
        height: auto;
    }

    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "decline", "No"),
        ("y", "accept", "Yes"),
        ("n", "decline", "No"),
        Binding("left", "app.focus_previous", "Focus Previous", show=False),
        Binding("right", "app.focus_next", "Focus Next", show=False),
    ]

    def __init__(self, message: str, *, yes_label: str = "Yes", no_label: str = "No") -> None:
        super().__init__()
        self._message = message
        self._yes_label = yes_label
        self._no_label = no_label

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._message, id="confirm-message"),
            Horizontal(
                Button(self._yes_label, id=CONFIRM_YES_ID, variant="primary"),
                Button(self._no_label, id=CONFIRM_NO_ID),
                id="confirm-buttons",
            ),
        )

    def on_mount(self) -> None:
        self.query_one(f"#{CONFIRM_YES_ID}", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == CONFIRM_YES_ID)

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class SaveOnQuitScreen(ModalScreen[SaveOnQuitChoice]):
    """Asks whether to save session state before quitting, with three outcomes instead of
    `ConfirmScreen`'s two: `"save"` (write session state, then quit), `"discard"` (quit without
    saving), or `"cancel"` (don't quit at all — dismisses the modal and leaves the session
    running exactly as if it had never been opened). Escape (or the "Cancel" button) dismisses
    `"cancel"`. Left/Right arrow keys move focus between the three buttons, same as Tab/Shift+Tab.
    Press `y` to save, `n` to discard without moving focus.

    A separate class from `ConfirmScreen` rather than a third option bolted onto it: every other
    `ConfirmScreen` call site (workspace-trust prompts) is a genuine yes/no question with no
    sensible "cancel" — see `klorb.tui.ReplApp._quit_after_maybe_saving`, the only caller of
    this screen.
    """

    CSS = """
    SaveOnQuitScreen {
        align: center middle;
    }

    SaveOnQuitScreen Vertical {
        width: auto;
        max-width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #quit-confirm-message {
        margin: 0 0 1 0;
    }

    #quit-confirm-buttons {
        align: center middle;
        height: auto;
    }

    #quit-confirm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("y", "save", "Save"),
        ("n", "discard", "Discard"),
        Binding("left", "app.focus_previous", "Focus Previous", show=False),
        Binding("right", "app.focus_next", "Focus Next", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._message, id="quit-confirm-message"),
            Horizontal(
                Button("Yes", id=QUIT_SAVE_ID, variant="primary"),
                Button("No", id=QUIT_DISCARD_ID),
                Button("Cancel", id=QUIT_CANCEL_ID),
                id="quit-confirm-buttons",
            ),
        )

    def on_mount(self) -> None:
        self.query_one(f"#{QUIT_SAVE_ID}", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == QUIT_SAVE_ID:
            self.dismiss("save")
        elif event.button.id == QUIT_DISCARD_ID:
            self.dismiss("discard")
        else:
            self.dismiss("cancel")

    def action_save(self) -> None:
        self.dismiss("save")

    def action_discard(self) -> None:
        self.dismiss("discard")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
