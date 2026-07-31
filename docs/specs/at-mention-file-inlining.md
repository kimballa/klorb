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
dumped to its `{"type": "text", "text": ...}` wire shape) when set, else the plain
`content` string. `Message.body()` is a separate, more general "give me a reasonable
text representation of this message" helper (JSON-stringified `fragments` if set, else
joined `streaming_content` if still streaming, else `content`) used for things like
token-count estimation that want to account for attachment content too.

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
