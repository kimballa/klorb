# Drop a MouseDown on a detached widget to avoid Textual's text-selection crash

* Date: 2026-07-13 19:10
* Question: A click in the REPL sometimes crashed the whole app with
  `AttributeError: 'NoneType' object has no attribute 'region'` from deep inside Textual
  (`screen.py:_forward_event`). Textual begins an app-level text selection on `MouseDown` by
  hit-testing the coordinate, then anchoring the selection at `widget.parent.region.offset`.
  While a streaming `Markdown` response re-renders, its `MarkdownParagraph` children are
  detached and remounted rapidly; the compositor's spatial map can still report a paragraph
  at the clicked coordinate after it has been detached (`parent is None`), so Textual
  dereferences the `None` parent and the process dies (crash log
  `klorb-crash-klorb-20260713-190453.log`). klorb does not use Textual's app-level text
  selection (no `TextSelected`/clipboard handlers). How should we stop the crash without
  giving up mouse text selection entirely (which real users rely on to copy responses/code)?
* Answer: Harden the hit-test rather than disable selection. `ReplApp.get_default_screen()`
  (Textual's sanctioned hook for a custom default screen) now returns a `SelectionSafeScreen`
  subclass that overrides `get_widget_and_offset_at` to return `(None, None)` when the hit
  widget is detached — `widget.parent is None` and it is not itself a `Screen` (the screen
  legitimately has no parent). With no widget under the cursor, Textual's `MouseDown` branch
  skips starting a selection and never touches the `None` parent; the next render re-hit-tests
  cleanly. Rejected: setting `App.ALLOW_SELECT = False`, which kills the crash but also removes
  mouse text selection app-wide, and monkeypatching/overriding the large internal
  `Screen._forward_event`, which would copy version-specific Textual internals and rot on
  upgrade.
* Reasoning: `get_widget_and_offset_at` is a small, documented, single-purpose method that
  every selection-related caller in `_forward_event` funnels through, so guarding it is the
  minimal, upgrade-durable intervention that keeps selection working for the normal (attached)
  case. Returning `(None, None)` for a detached hit is semantically honest — the widget is on
  its way out of the tree, so "nothing selectable here" is the correct answer — and both
  callers (the `MouseDown` selection-start and the `MouseUp` clear-selection check) already
  handle a `None` widget. Pinned to Textual 8.2.8; if a later Textual fixes the underlying
  spatial-map/detach race, this override becomes a harmless no-op and can be removed.
