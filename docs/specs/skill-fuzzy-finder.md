# Interactive skill fuzzy finder (TUI)

Typing `/` at the start of the prompt input, or after whitespace, opens an inline
fuzzy-finder popup for skill search -- mirroring the `@`-mention file finder (`FileFinderPanel`,
see `docs/specs/at-mention-file-inlining.md`). The popup narrows to matching skills as more of
the query is typed.

## Trigger

The popup activates once the cursor sits inside a `/query`: a `/` preceded by the start of the
line or whitespace, with no whitespace yet typed between it and the cursor. URLs like
`https://example.com` and paths like `path/to/file` don't trigger because the `/` is embedded in
a word.

`klorb.tui.widgets.skill_finder.detect_skill_query` detects the query. The `/` must be preceded
by start-of-line or whitespace (same boundary rule as `detect_mention_query` for `@`-mentions).

## Matching

Skills to match against come from the session's `discover_skills()` catalog (canonical names,
precedence-deduped, non-deny-verdicted). `ReplApp.discoverable_skill_matches()` converts them to
`SkillMatch` objects (name, namespace, description).

`klorb.tui.widgets.skill_finder.filter_skills` fuzzy-matches the query against each skill's name
and description using `textual.fuzzy.Matcher`. The higher of the two scores is used. An empty
query returns all skills (up to `MAX_SKILL_FINDER_MATCHES`, 25). Up to 25 ranked results are
kept.

## Row display

Each match shows the skill name in the normal foreground, namespace in muted color
(`(namespace)`), and description (truncated with `...` if it doesn't fit the available width)
also muted.

## Insertion

Selecting a skill match replaces the `/query` span with `/<skill_name>` (the canonical name
plus a trailing space), landing the cursor right after it and closing the popup.

## Key handling

Up/Down move the highlight; Enter or Tab applies the highlighted match; Escape closes the popup
without changing the text. A mouse click on a row applies that row directly (via
`OptionList`'s own click handling).

## Shared FinderPanel base

Both `FileFinderPanel` and `SkillFinderPanel` inherit from `FinderPanel` (in
`klorb.tui.widgets.file_finder`), which owns the shared chrome: `show_matches`, `hide`,
`current_match`, `move_highlight`, and click dispatch via `on_option_list_option_selected`.
Each subclass implements `_build_options` (to construct typed `FinderOption` objects) and
`_select_match` (to dispatch to the right `PromptInput` method).
