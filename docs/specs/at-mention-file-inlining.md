# @mention file attachments

When a user's prompt contains `@<filename>`, the session reads the referenced file and
attaches its contents to the user turn as a separate `MessageFragment`, alongside the
prompt text itself. This lets the user provide file context without a separate `ReadFile`
tool call round trip, and without the file's contents being spliced into the prompt text a
user sees echoed back.

## Syntax

| Form | Example | Unescapes to |
| ------ | --------- | -------------- |
| Simple | `@foo.txt` | `foo.txt` |
| With path | `@src/main.py` | `src/main.py` |
| Escaped space | `@foo\ bar.txt` | `foo bar.txt` |
| Quoted | `@"foo bar.txt"` | `foo bar.txt` |
| Escaped backslash | `@foo\\bar.txt` | `foo\bar.txt` |
| Escaped quote | `@foo\"bar.txt` | `foo"bar.txt` |
| Literal backslash | `@abc\def` | `abc\def` (backslash + non-special char kept as-is) |

The `@` must be preceded by whitespace or appear at the start of the prompt; an `@`
embedded in a word (e.g. `user@example.com`) is not treated as a mention.

Quoted filenames (`@"..."`) support the same escape sequences inside the quotes.

## Interactive fuzzy finder (TUI)

Typing `@` in the terminal REPL's prompt box opens an inline fuzzy-finder popup, an in-flow
panel shown directly above the prompt input -- mirroring `klorb.tui.widgets.palette`'s `>`
command palette (see [[command-palette-from-prompt]] for that mechanism). It narrows to
matching workspace files as more of the query is typed; inserting a selection always produces
exactly the escaped syntax the "Syntax" table above describes, so anything the finder inserts
is resolvable by `resolve_at_mentions()` later.

* **Trigger.** The popup activates once the cursor sits inside an `@query`: an `@` preceded by
  the start of the line or whitespace, with no whitespace yet typed between it and the cursor --
  mirroring `_AT_MENTION_RE`'s own unquoted-mention boundary rule
  (`klorb.tui.widgets.file_finder.detect_mention_query`). Only the unquoted form is
  interactively completed; the quoted form (`@"..."`) is still accepted when typed by hand, but
  the finder never offers it.
* **Matching.** `klorb.tui.widgets.file_finder.filter_workspace_files` fuzzy-matches the query
  with `textual.fuzzy.Matcher` -- the same matcher `klorb.tui.commands.model_commands.
  filter_model_names` uses for model selection -- against one blended candidate list: every
  workspace file plus every ancestor directory of one (`_ancestor_directories`), since the
  workspace index itself only ever lists files. A directory candidate's score gets
  `_DIRECTORY_SCORE_BUMP` added on top of its own fuzzy score, so it ranks above an
  equally-relevant file and the query can be used to drill toward a subtree, not just a leaf
  file. Up to `MAX_FILE_FINDER_MATCHES` (25) ranked results are kept, scrollable within the
  popup's fixed on-screen height, in a `FileFinderPanel` (`klorb.tui.widgets.file_finder`), an
  `OptionList` mounted directly above the prompt input and never focused: the prompt input keeps
  focus throughout and drives the panel's highlight programmatically. An empty query lists
  entries alphabetically with directories sorted ahead of files rather than by fuzzy score
  (there's nothing to rank). Up/Down move the highlight; Enter or Tab applies the highlighted
  match (see "Insertion" below); Escape closes the popup without changing the text, and typing
  further within the same `@query` (its start position unchanged) does not reopen it until the
  cursor moves to a different mention. A query matching nothing closes the popup outright, so
  Enter/Tab/Up/Down keep their ordinary meaning (submit, navigate) instead of being claimed by
  an empty finder.
* **Row display.** Each match is shown as its workspace-relative path split into a directory
  part (muted color) and a file part (normal color, with its own leading `/`) --
  `klorb.tui.widgets.file_finder.split_finder_row` -- with a trailing `/` appended when the row
  is itself a directory. When the full path doesn't fit the panel's width, the directory part's
  *front* is truncated with a leading `...`, keeping the segment immediately before the file
  part -- the most differentiating context when several rows share a long, generic leading
  prefix -- always fully visible: `.../deep/path/someFile.txt`.
* **Insertion.** Selecting a file match replaces the `@query` span with `@` followed by the
  path, escaped exactly per the "Syntax" table above (backslash, then double quote, then space,
  via `klorb.tui.widgets.file_finder.escape_mention_path` -- the precise inverse of
  `unescape_mention_filename`), plus a trailing space, landing the cursor right after it and
  closing the popup so typing continues immediately as plain text. Selecting a directory match
  instead replaces the `@query` span with `@` followed by the escaped directory path plus a
  trailing `/`, and leaves the mention (and the popup) open so the query keeps narrowing into
  that subtree -- a directory is never a resolvable `@mention` target on its own
  (`resolve_at_mentions()` only ever reads files).
* **Workspace index.** `klorb.tui.workspace_file_index.WorkspaceFileIndex` maintains the
  candidate file list: a gitignore-aware recursive scan (reusing
  `klorb.tools.util.gitignore.GitignoreFilter`, the mechanism [[gitignore-aware-tree-walk]]
  documents, minus that walk's permission gating -- selecting a file only inserts a path, the
  same "no permission check" reasoning this doc's own "Security" section gives for reading an
  `@mention`ed file) kept current via the `watchdog` PyPI package's filesystem push
  notifications (`watchdog.observers.Observer` -- unrelated to `klorb.watchdog.
  LivenessWatchdog`, klorb's own hang-detection heartbeat) instead of periodic polling, the same
  mechanism WSGI dev-server reloaders use. A plain file's creation or deletion is applied as an
  incremental add/remove; a directory create/delete or any `.gitignore` change forces a full
  rescan, since either can affect more paths than the one the filesystem event names. A rescan
  can run on any thread and take a while against a large tree; events that arrive while one is
  already running are queued rather than lost, and applied immediately (no debounce) once that
  rescan completes, instead of racing it. `close()` signals a shutdown event a running rescan
  polls periodically so it aborts its walk promptly rather than running to completion (or
  restarting) after the index should already be dead. See
  `docs/adrs/use-watchdog-for-tui-file-finder-index.md`.
* **Startup.** The index starts only once workspace trust is resolved for a real `klorb`
  process (`ReplApp._start_file_finder_index`, gated on `trust_manager` the same way
  `PromptInput.set_history_store` is), so a `ReplApp` built without one -- every existing test
  that doesn't explicitly opt in -- never spawns a background filesystem watcher against a real
  directory.

## Output format

`resolve_at_mentions()` never modifies the prompt text -- an `@mention` stays exactly as
typed wherever it appears in the prompt. Instead, each resolved mention becomes its own
`MessageFragment` (`klorb.message.MessageFragment`, `type="text"`) carrying an
`AttachedFile` block:

```text
Filename: <filename>
Attachment Id: <ordinal>
Total lines: <n>
Truncated: true/false

<line-numbered content, same format as ReadFile>
```

Ordinals are assigned in first-seen order. Duplicate mentions of the same filename are
resolved only once (one read, same ordinal reused, one fragment).

Files larger than the configured line cap are truncated (only the first N lines are
included), and `Truncated: true` is set in the header.

Files that fail to read (not found, permission error, etc.) produce a fragment whose text
is an error note instead of content: `(error reading file: <exception>)`.

## Message shape

`Message.fragments` (`list[MessageFragment] | None`) carries this turn's attachments plus
one final fragment wrapping the fully-embellished prompt text (after every other
interjection -- skill activation, permission-framework change, standing interjections, etc.
-- has already been prepended to it). `Message.content` still holds that same
plain-text prompt string on its own, unconditionally, so any caller that only wants "the
text of this turn" (TUI rendering, char-count logging, `retry_last_turn()`) keeps working
whether or not the turn had any `@mention`s -- `fragments` is `None` when it didn't.

`Message.provider_content()` is what actually goes out over the wire: `fragments` (each
dumped via `MessageFragment.to_wire_dict()` -- `{"type": "text", "text": ...}` for a text
fragment; see docs/specs/vision-image-input.md for the `"image_url"` fragment type images
use) when set, else the plain `content` string. `Message.body()` is a separate, more
general "give me a reasonable text representation of this message" helper
(JSON-stringified `fragments` if set, else joined `streaming_content` if still streaming,
else `content`) used for things like debug char-count totals.

## Configuration

| Key                       | Type | Default | Description                  |
|---------------------------|------|---------|------------------------------|
| `tools.@mention.maxLines` | int  | 500     | Per-file line cap for inline |

This is a process-level config key (in `ProcessConfig`, not `SessionConfig`), following the
same `tools.*` namespace as `tools.readFile.maxLines` and similar keys.

## Implementation

The core logic lives in `klorb.session.mixins.mentions`:

* `_AT_MENTION_RE` -- the regex that finds `@mentions` in prompt text.
* `unescape_mention_filename()` -- resolves `\`, `\\`, `\"` escape sequences.
* `has_at_mention()` -- fast check for whether a prompt contains any mention.
* `resolve_at_mentions()` -- the main entry point: finds all mentions, reads each unique
  file via `ReadFileCore`, and returns one `MessageFragment` per unique mention (in
  first-seen order), or `None` if the prompt has no mentions. Never modifies the prompt
  text it's given.
* `_resolve_and_read()` -- resolves a filename to an absolute path within the workspace and
  reads it; no permission check is performed since the user implicitly authorized the read
  by @mentioning the file.

`Session.send_turn()` calls `resolve_at_mentions()` first, against the raw (pre-interjection)
prompt, before skill mention detection and interjection assembly. Every other interjection is
then prepended onto `prompt` exactly as it would be without any mentions -- mention
resolution doesn't affect that assembly at all. Only once `prompt` is fully embellished, right
before constructing the turn's `Message`, is a final `MessageFragment` wrapping that `prompt`
text appended onto the list `resolve_at_mentions()` returned (if it returned one); that
combined list becomes `Message.fragments`, while `Message.content` is set to `prompt` either
way. See docs/specs/permissions.md and docs/specs/skills.md for what those other interjections
look like.

`SessionCoreMixin.__init__` constructs a `ReadFileCore` instance configured with
`process_config.mention_max_lines`, stored as `_mention_read_file_core`. The `ReadFileCore`
import is deferred (inside a static method) to avoid a circular import through
`klorb.tools.util` → `klorb.tools.setup_context` → `klorb.process_config` → `klorb.session`.

`klorb.openrouter.OpenRouterApiProvider._build_api_messages` sends `message.provider_content()`
(not `message.content`) as each API message's `content` field, so a message carrying
`fragments` sends its content-part array to the provider.

## Security

No `readDirs`/`readFiles` permission check is performed -- the user explicitly chose to
mention the file, which constitutes implicit authorization. The file is still resolved
within the workspace path (relative mentions are joined to `workspace.path`), but the
`trusted` flag and directory rules are not consulted.

## Test coverage

Tests live in `tests/klorb/session/test_mentions.py`:

* `TestUnescapeMentionFilename` -- escape sequence resolution edge cases.
* `TestAtMentionRegex` -- regex matching (simple, paths, escapes, quotes, emails, boundaries).
* `TestHasAtMention` -- fast detection helper.
* `TestResolveAtMentions` -- integration with real files (simple, relative, absolute paths,
  errors, deduplication, escapes, truncation, and confirming the prompt itself is never
  modified).

`tests/klorb/test_message.py` covers `Message.body()`/`Message.provider_content()` directly.
`tests/klorb/test_openrouter.py::test_build_api_messages_sends_fragments_when_message_has_them`
covers wire serialization. `tests/klorb/session/test_session.py`'s
`test_send_turn_attaches_at_mention_fragments_without_altering_prompt_content` and
`test_send_turn_leaves_fragments_none_without_at_mentions` cover the end-to-end
`Session.send_turn()` path.

The interactive fuzzy finder is covered by `tests/klorb/tui/widgets/test_file_finder.py`
(mention detection, fuzzy matching, escaping, insertion, and row-truncation helpers, all as
pure functions), `tests/klorb/tui/test_workspace_file_index.py` (gitignore-aware scanning plus
the `watchdog`-driven incremental/rescan update paths), and the "@-mention file finder" section
of `tests/klorb/tui/widgets/test_prompt_input.py` (end-to-end keyboard-driven flows: opening,
narrowing, up/down, Enter/Tab selection, Escape dismissal, and the finder correctly staying out
of the way of a non-matching query or a cursor that moves out of the mention).
