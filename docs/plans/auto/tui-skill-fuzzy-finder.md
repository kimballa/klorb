# TUI: fuzzy-finder for skill search

Context: klorb TUI (Python, Textual-based), feature request.

When the user types `/` at the start of the prompt input, or after whitespace, the TUI should
show a fuzzy-finder pop-up to help find the skill they want to invoke — mirroring the existing
`@`-mention fuzzy file finder (`klorb/src/klorb/tui/widgets/file_finder.py`, `FileFinderPanel`),
which already renders an inline fuzzy-match panel above the prompt input driven by an active
`@mention` at the cursor. Build the analogous panel for `/`-triggered skill search, reusing the
same layout/style (an `OptionList` panel positioned over the prompt input area).

Behavior:

* ESC dismisses the fuzzy-finder panel.
* Continuing to type after the query rules out all remaining skill matches also dismisses it.
* Skill names/descriptions to match against are available via klorb's skill catalog
  (`klorb/src/klorb/tools/skill/catalog.py`).

See docs/specs/at-mention-file-inlining.md for how the existing `@`-mention finder is specified,
as a model for how to spec this skill-finder feature.
