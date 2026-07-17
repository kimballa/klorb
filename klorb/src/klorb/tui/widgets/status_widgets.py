# © Copyright 2026 Aaron Kimball
"""Status-bar widgets: the palette-mode hint chip and the permission-framework badge."""

from typing import Literal

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static

from klorb.session import PermissionFramework
from klorb.tui.constants import PERMISSION_FRAMEWORK_CYCLE
from klorb.tui.widgets.palette import PALETTE_PREFIX

PALETTE_HINT_TEXT = f"{PALETTE_PREFIX} palette"

PERMISSION_BADGE_HORIZONTAL_PADDING = 2
"""Matches `PermissionBadge`'s own `padding: 0 1` (1 cell each side) -- folded into
`PERMISSION_BADGE_WIDTH` since Textual's CSS `width` is a border-box measurement that
already includes padding, so the content area available for text is `width` minus this."""

PERMISSION_BADGE_WIDTH = (
    max(len(f"[{value}]") for value in PERMISSION_FRAMEWORK_CYCLE) + 1
    + PERMISSION_BADGE_HORIZONTAL_PADDING
)
"""Fixed cell width (border-box, including padding) for `PermissionBadge`: the longest
bracketed value (`"[auto]"`/`"[deny]"`, 6 characters) plus 1 for breathing room plus
`PERMISSION_BADGE_HORIZONTAL_PADDING`. A fixed width sidesteps `width: auto` sizing a
`Static` once (from whatever value is showing at mount) and never re-measuring on a later
`refresh()` -- only `update()` does that -- which would otherwise clip a later, longer
value's trailing `]`."""


class PaletteHint(Static):
    """Renders `PALETTE_HINT_TEXT` styled like one of `Footer`'s own key-binding chips (e.g.
    `^q Quit`): the leading `>` in `$footer-key-foreground`/`-background` (bold, like a key),
    ` palette` in `$footer-description-foreground`/`-background` — rather than plain
    single-toned text — so it reads as one more binding in the status row instead of an
    unrelated label. Uses its own component classes (`palette-hint--key`/`-description`)
    rather than `Footer`'s private `footer-key--key`/`-description` ones: Textual resolves a
    component class through CSS scoped to the declaring widget type, so borrowing `Footer`'s
    directly wouldn't resolve on a different widget; declaring our own against the same
    theme-level `$footer-*` variables gets the identical look without that coupling. The
    widget's own base `background` is set to `$footer-background` — the same variable
    `Footer` itself uses — rather than left at its default: `$footer-key-background`/
    `$footer-item-background`/`$footer-description-background` all resolve to `transparent`
    in the built-in themes (`Footer`'s own blue comes entirely from its own top-level
    `background: $footer-background`, not from those component-class colors), so without
    this base the hint would composite over the app's own background instead.
    """

    COMPONENT_CLASSES = {"palette-hint--key", "palette-hint--description"}

    DEFAULT_CSS = """
    PaletteHint {
        width: auto;
        height: 1;
        background: $footer-background;
    }
    PaletteHint .palette-hint--key {
        color: $footer-key-foreground;
        background: $footer-key-background;
        text-style: bold;
        padding: 0 1;
    }
    PaletteHint .palette-hint--description {
        color: $footer-description-foreground;
        background: $footer-description-background;
        padding: 0 1 0 0;
    }
    """

    def on_mount(self) -> None:
        """Start hidden until `show_hint()` is first called."""
        self._shown = False

    def render(self) -> Text:
        """Render `PALETTE_PREFIX` and `"palette"` in the two component styles above (or
        nothing, while hidden), padded the same way `FooterKey.render()` pads its own
        key/description. Recomputing the styles here — rather than baking a `Text` once via
        `self.update(...)` in `show_hint()` — is what `FooterKey.render()` itself does too,
        and is why it works: Textual re-invokes `render()` whenever the active theme changes,
        so reading `get_component_rich_style()`/`get_component_styles()` fresh on every call
        (rather than caching their result in a stored renderable) is what keeps this hint's
        colors following a theme switch instead of freezing at whatever they were the moment
        `show_hint()` last ran.
        """
        if not self._shown:
            return Text("")
        key_style = self.get_component_rich_style("palette-hint--key")
        key_padding = self.get_component_styles("palette-hint--key").padding
        description_style = self.get_component_rich_style("palette-hint--description")
        description_padding = self.get_component_styles("palette-hint--description").padding
        return Text.assemble(
            (" " * key_padding.left + PALETTE_PREFIX + " " * key_padding.right, key_style),
            (" " * description_padding.left + "palette" + " " * description_padding.right,
             description_style),
        )

    def show_hint(self) -> None:
        """Show the hint, redrawing via `render()` above."""
        self._shown = True
        self.refresh()

    def hide_hint(self) -> None:
        """Hide the hint, redrawing via `render()` above."""
        self._shown = False
        self.refresh()


_PermissionBadgeFlashStage = Literal["normal", "yellow", "white"]


class PermissionBadge(Static):
    """Shows `Session.config.permission_framework` as a bracketed, right-justified footer
    chip (e.g. `[ask]`), styled like `PaletteHint`/`#status-bar` so it reads as one more item
    in the status row. Its width is fixed at `PERMISSION_BADGE_WIDTH` rather than `auto`,
    since a `Static`'s auto-width only re-measures on `update()`, not on the bare `refresh()`
    a custom `render()` uses — a fixed width sized for the longest value
    (`"[auto]"`/`"[deny]"`) plus one, with the text right-justified within it, means a
    shorter value like `"[ask]"` is left-padded rather than ever clipping a longer one's
    trailing `]`. `ReplApp.action_cycle_permission_framework()` (bound to Shift+Tab, and
    reached by clicking the badge — see `Clicked`) calls
    `flash_to()` whenever the value changes, which briefly flashes the chip bright yellow
    (the same `$footer-key-foreground` used for the footer's own key-binding chips) for
    `_FLASH_YELLOW_SECONDS`, then bright/bold white for the longer `_FLASH_WHITE_SECONDS`,
    before settling back to its normal color -- a quick spark followed by a lingering glow
    reads more like a natural attention flash than two equal-length steps would. `set_value()`
    sets the displayed value without flashing, used for the initial render at startup.

    Clicking the badge posts `Clicked`, which `ReplApp` handles by advancing the framework
    exactly as Shift+Tab does — so the chip is a live control, not just a readout.
    """

    class Clicked(Message):
        """Posted when the user clicks the badge, asking `ReplApp` to advance the permission
        framework to the next value — the same cycle Shift+Tab drives (see
        `StatusBarMixin.on_permission_badge_clicked` / `action_cycle_permission_framework`)."""

    COMPONENT_CLASSES = {"permission-badge--flash-yellow", "permission-badge--flash-white"}

    DEFAULT_CSS = f"""
    PermissionBadge {{
        width: {PERMISSION_BADGE_WIDTH};
        height: 1;
        color: $footer-description-foreground;
        background: $footer-background;
        padding: 0 1;
    }}
    PermissionBadge .permission-badge--flash-yellow {{
        color: $footer-key-foreground;
        text-style: bold;
    }}
    PermissionBadge .permission-badge--flash-white {{
        color: white;
        text-style: bold;
    }}
    """

    _FLASH_YELLOW_SECONDS = 0.15
    _FLASH_WHITE_SECONDS = 0.4

    def on_mount(self) -> None:
        """Start showing `"ask"` until `set_value()`/`flash_to()` says otherwise."""
        self._value: PermissionFramework = "ask"
        self._flash_stage: _PermissionBadgeFlashStage = "normal"

    def set_value(self, value: PermissionFramework) -> None:
        """Set the displayed value with no flash -- used for the initial startup render."""
        self._value = value
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        """Post `Clicked` (and stop the event) so a click on the badge cycles the permission
        framework — the mouse equivalent of the Shift+Tab binding. The value change and its
        flash happen in `ReplApp.action_cycle_permission_framework`, not here, so both entry
        points stay identical."""
        event.stop()
        self.post_message(self.Clicked())

    def flash_to(self, value: PermissionFramework) -> None:
        """Set the displayed value and flash it: a quick `_FLASH_YELLOW_SECONDS` spark of
        bright yellow, then a longer `_FLASH_WHITE_SECONDS` glow of bright white, then back
        to the normal footer-chip color.
        """
        self._value = value
        self._flash_stage = "yellow"
        self.refresh()
        self.set_timer(self._FLASH_YELLOW_SECONDS, self._advance_flash_to_white)

    def _advance_flash_to_white(self) -> None:
        self._flash_stage = "white"
        self.refresh()
        self.set_timer(self._FLASH_WHITE_SECONDS, self._advance_flash_to_normal)

    def _advance_flash_to_normal(self) -> None:
        self._flash_stage = "normal"
        self.refresh()

    def render(self) -> Text:
        text = f"[{self._value}]"
        if self._flash_stage == "normal":
            return Text(text, justify="right")
        component = (
            "permission-badge--flash-yellow" if self._flash_stage == "yellow"
            else "permission-badge--flash-white")
        return Text(text, style=self.get_component_rich_style(component), justify="right")
