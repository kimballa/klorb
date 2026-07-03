# © Copyright 2026 Aaron Kimball
"""Modal shown when a tool call hits an `"ask"` permission verdict (see
`klorb.session.PermissionAskContext`/`PermissionDecision` and
docs/specs/permissions.md's "Interactive ask confirmation" section)."""

from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input
from textual.widgets import OptionList
from textual.widgets import Static

from klorb.session import PermissionAskContext
from klorb.session import PermissionDecision

_PermissionChoice = Literal["once", "session", "workspace", "homedir", "deny", "other"]
"""Mirrors `PermissionDecision.choice`'s Literal exactly, so indexing `_OPTION_CHOICES` below
type-checks without a cast at the one call site that does it."""

_OPTION_CHOICES: tuple[_PermissionChoice, ...] = (
    "once", "session", "workspace", "homedir", "deny", "other")
_OPTION_LABELS: tuple[str, ...] = (
    "Allow (once)",
    "Allow (this session)",
    "Allow (always, in this workspace)",
    "Allow (always, for me)",
    "Deny",
    "Other...",
)

_OTHER_CHOICE_INDEX = _OPTION_CHOICES.index("other")

PERMISSION_ASK_OPTIONS_ID = "permission-ask-options"
PERMISSION_ASK_INPUT_ID = "permission-ask-other-input"


class PermissionAskScreen(ModalScreen[PermissionDecision]):
    """Presents the six choices from `TODO.md`'s "ask" permission action item (Allow once /
    this session / always in this workspace / always for me, Deny, Other-with-free-text).

    `granted_paths` is `klorb.permissions.grant.compute_grant_paths()`'s pre-computed result —
    the directory (or directories) a persistent Allow would actually be recorded at — so the
    modal's copy can name the real scope of the grant up front: per the design decision behind
    this feature, an unmentioned path is always granted at its *containing directory*, not the
    single file the model happens to be touching right now, and the user needs to see that
    before picking a scope.

    Selecting "Other..." swaps the `OptionList` out for a single-line `Input`; submitting it
    (Enter) dismisses with `PermissionDecision(choice="other", other_text=...)`. Escape (or the
    `OptionList`'s "Deny" entry) dismisses as `PermissionDecision(choice="deny")`.
    """

    CSS = """
    PermissionAskScreen {
        align: center middle;
    }

    PermissionAskScreen Vertical {
        width: auto;
        max-width: 80;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #permission-ask-message {
        margin: 0 0 1 0;
    }

    PermissionAskScreen OptionList {
        border: none;
    }
    """

    BINDINGS = [("escape", "decline", "Deny")]

    def __init__(self, ask_ctx: PermissionAskContext, granted_paths: list[Path]) -> None:
        super().__init__()
        self._ask_ctx = ask_ctx
        self._granted_paths = granted_paths

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._message_text(), id="permission-ask-message"),
            OptionList(*_OPTION_LABELS, id=PERMISSION_ASK_OPTIONS_ID),
            id="permission-ask-body",
        )

    def _message_text(self) -> str:
        action = "write to" if self._ask_ctx.is_write else "read"
        directories = ", ".join(str(path) for path in self._granted_paths)
        return (
            f"Permission requested: {action} {self._ask_ctx.path}\n\n"
            f"Any persistent Allow choice below grants access to the whole directory:\n"
            f"{directories}"
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index == _OTHER_CHOICE_INDEX:
            self._reveal_other_input()
            return
        self.dismiss(PermissionDecision(choice=_OPTION_CHOICES[event.option_index]))

    def _reveal_other_input(self) -> None:
        self.query_one(f"#{PERMISSION_ASK_OPTIONS_ID}", OptionList).remove()
        other_input = Input(
            placeholder="Explain why, or what to allow instead...", id=PERMISSION_ASK_INPUT_ID)
        self.query_one("#permission-ask-body", Vertical).mount(other_input)
        other_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(PermissionDecision(choice="other", other_text=event.value))

    def action_decline(self) -> None:
        self.dismiss(PermissionDecision(choice="deny"))
