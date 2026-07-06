# © Copyright 2026 Aaron Kimball
"""A reusable Yes/No confirmation modal, shared by every workspace-trust prompt
(`klorb.tui.repl.ReplApp`'s workspace-bootstrap flow and its "Trust workspace" palette
command — see docs/specs/projects-and-trust.md) so each one isn't its own near-identical
`ModalScreen` subclass. Distinct from `klorb.tui.repl.ToolCallLimitScreen`, an earlier, narrower
yes/no modal this doesn't replace — see
docs/adrs/reuse-a-generic-confirmscreen-for-workspace-trust-prompts.md.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

CONFIRM_YES_ID = "confirm-yes"
CONFIRM_NO_ID = "confirm-no"


class ConfirmScreen(ModalScreen[bool]):
    """Shows `message` with Yes/No buttons; dismisses `True`/`False` accordingly. Escape (or
    the "No" button) always dismisses `False`. `yes_label`/`no_label` default to "Yes"/"No"
    but can be overridden for a more specific affirmative/negative phrasing. Left/Right arrow
    keys move focus between the two buttons, same as Tab/Shift+Tab.
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

    def action_decline(self) -> None:
        self.dismiss(False)
