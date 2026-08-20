# Secret redaction

## Summary

`ReadFileTool`, `EditFileTool`, and `CreateFileTool` pass file content through
`klorb.tools.util.SecretRedactor`, which detects likely credentials (AWS keys, private keys,
vendor API tokens, and other credential-shaped strings, via the third-party `detect-secrets`
library) and replaces each one with a `[[SECRET:<type>:<hash>]]` token before the content
reaches a model — so a model can read and edit a file containing real secrets without those
secrets ever appearing in its context. `EditFileTool` and `CreateFileTool` resolve the same
tokens back to their real plaintext when a model echoes one back in the call's arguments, so
the read-then-edit and read-then-create loops work even though the model itself never sees the
underlying secret. `GrepTool` redacts the same way: matching still runs against a file's real
content, but every line returned in a result is masked before it reaches the model.

This is scoped to `ReadFileTool`/`EditFileTool`/`CreateFileTool`/`GrepTool` — the tools that
operate on real, model-named filesystem paths, where a genuine credential (a `.env` file, a
config file with an API key) is most likely to live. See "Out of scope" for what this
deliberately doesn't cover (most notably `Bash`).

`klorb.search_index.indexer.WorkspaceIndexer` also uses a `SecretRedactor`, but for a different
purpose: redacting each file's text before chunking/embedding so no detected credential is ever
persisted in the search index's on-disk store. See docs/specs/local-search-index.md's "Indexing:
`WorkspaceIndexer`" section.

`SecretRedactionFilter` (`logging_config.py`) applies the same detection to every log handler
installed by `configure_logging()`, so credentials that pass through `logger.log()` are replaced
with `[REDACTED]` before they reach stderr, the TUI conversation history, or the session log file.

## How it works

* `klorb.tools.util.secret_redaction.SecretRedactor` (`klorb/src/klorb/tools/util/
  secret_redaction.py`) holds no state of its own — the same shape as
  `klorb.tools.util.spill.SpillDir` — and exposes two methods:
  * `redact(session, text)` scans `text` line-by-line with a curated set of `detect-secrets`
    plugins and replaces each detected secret's plaintext with a token, recording the
    token-to-plaintext mapping in `session.tool_state["SecretRedaction"]["token_to_secret"]`.
  * `detokenize(session, text)` substitutes every known token in `text` back to its real
    plaintext, looking up the same `session.tool_state` map. A token with no known mapping (a
    different session, or one a model fabricated) is left as literal text rather than raising.
  * Both methods accept `session: Session | None`; a `None` session (e.g. a `ToolSetupContext`
    built directly in a unit test) still redacts, just without a map that survives past that one
    call — mirroring how `BashTool._maybe_sandbox_notice()` degrades when there's no `Session` to
    dedupe against.
* The token is derived from a SHA-256 hash of the secret's own plaintext (truncated to 12 hex
  characters), not a counter — so the same secret value always resolves to the same token,
  whether it's re-read in a later `ReadFile` call or appears at a second location in the same
  read. This is what makes the token stable enough for a model to reuse verbatim across separate
  tool calls.
* `SECRET_DETECTION_PLUGINS` (`secret_redaction.py`) is every `detect-secrets` vendor/format-specific plugin
  (`AWSKeyDetector`, `PrivateKeyDetector`, `GitHubTokenDetector`, `SlackDetector`, `JwtTokenDetector`,
  `KeywordDetector`, ...), deliberately excluding `Base64HighEntropyString`/`HexHighEntropyString`
  (trip constantly on ordinary hashes, UUIDs, and base64 blobs found in everyday source) and
  `IPPublicDetector` (an IP address isn't a credential). `detect-secrets`' own default heuristic
  filters (`is_potential_uuid`, `is_sequential_string`, `is_templated_secret`, etc.) still apply
  underneath this plugin selection, further cutting down on false positives.
* `detect-secrets` keeps its plugin/filter configuration in a process-wide
  `functools.lru_cache` singleton (`detect_secrets.settings.get_settings()`). `SecretRedactor`
  serializes every scan through a module-level `threading.Lock` (`SECRET_DETECTION_SCAN_LOCK`) so two sessions
  redacting concurrently in the same process can't race on that shared global state.
* `ReadFileCore.apply()` (`klorb/src/klorb/tools/util/read_file_core.py`) takes optional
  `redactor: SecretRedactor | None` and `session: Session | None` keyword arguments. When
  `redactor` is given, each raw line is redacted before line-wrapping (`_wrap_line`), so a
  secret can't be split across two wrapped segments before it's masked. `total_lines`/
  `truncated`/`next_start_line` are still computed from the real, unredacted line count, so
  paging stays accurate regardless of redaction. `ReadFileTool` is the only caller that passes a
  redactor today; `ReadScratchpadTool`/`ReadMemoryTool`/`ReadSkillFileTool` (the other three
  tools sharing `ReadFileCore`) don't, since those are agent/system-authored content rather than
  arbitrary filesystem paths.
* `EditFileCore.apply()` (`edit_file_core.py`) takes the same optional `redactor`/`session`
  arguments. When `redactor` is given:
  * Immediately after `_normalize_edit_args()` resolves the call's arguments,
    `_detokenize_normalized_args()` runs `redactor.detokenize(session, ...)` over `old_text`,
    `old_text_start`, `old_text_end`, and `new_text` — so anchor matching and the eventual
    `path.write_text()` both operate on the file's real bytes, never on a literal
    `[[SECRET:...]]` string. This is what stops a redacted secret
    from being destroyed: without this step, an edit that carries a token in `new_text` (e.g.
    "move this line, keep its content as-is") would write the token's literal text to disk in
    place of the real secret.
  * After the edit is written, `post_edit_content` and each `DiffLine.text` in the result's
    `diff` are re-redacted (`redactor.redact(session, ...)`) before being returned — the disk
    write itself (`path.write_text(content, ...)`) always uses the real, detokenized bytes;
    only the tool result echoed back to the model is masked again. Re-redacting reuses the same
    `session`-scoped token map, so a secret that was already assigned a token earlier in the
    session gets that same token back in the diff, rather than a fresh one.
* `ReadFileTool`/`EditFileTool`/`CreateFileTool` each construct one
  `self._secret_redactor = SecretRedactor()` in `__init__` and pass it (with
  `self.context.session`) into their core's `apply()`. Since `SecretRedactor` holds no state
  of its own, holding one instance per `Tool` for its whole lifetime is just a convenience —
  the actual map lives in `session.tool_state`, so a fresh `SecretRedactor()` on the same
  session resolves the same tokens.
* `CreateFileCore.apply()` (`create_file_core.py`) takes the same optional `redactor`/`session`
  arguments. When `redactor` is given, `content` is passed through `redactor.detokenize(session,
  ...)` before writing — so a `[[SECRET:...]]` token echoed from an earlier `ReadFile` resolves
  to the file's real bytes rather than being written literally. After the file is created, the
  returned `diff` is re-redacted (`redactor.redact(session, ...)`) before being returned, so the
  result never carries a plaintext secret either.
* `ReadFileTool.description()`/`GrepTool.description()` tell the model directly that a line may
  come back with a `[[SECRET:<type>:<hash>]]` token in place of a credential, and that the token
  (not a guessed or invented replacement) is what to pass back into `EditFile`'s
  `old_text`/`old_text_start`/`old_text_end`/`new_text` to match or preserve that line.
* `GrepTool` (`klorb/src/klorb/tools/grep.py`) constructs its own `self._secret_redactor =
  SecretRedactor()` and applies it via a `_redact_strings()` helper, applied to every dense-
  format result line (see docs/specs/tool-framework.md for the `"*42|text"`/`" 41|text"` format)
  *before* `_truncate_line()` runs, right where each `files[i]["lines"]` entry is built —
  covering both `outputStyle` values that return line text (`"Matches"`, `"FullContext"`;
  `"ListFiles"` returns only filenames, so there's nothing to redact). Redacting before
  truncating (not after) matters: truncating a raw line to `grep_max_line_length` first could
  slice a credential in half, leaving a partial fragment that no longer matches any
  `detect-secrets` pattern and so passes through un-redacted. Unlike `ReadFileCore`/
  `EditFileCore`, matching itself (`match_line_indices`) always runs against the file's real,
  unredacted content, read earlier in `apply()` — only the rendered `lines` strings returned to
  the caller are masked, so a query for a secret's own literal value still finds it (search
  behavior is unchanged), and results written to `results_data_file` via `SpillDir` (large-result
  spilling) are already redacted, since spilling happens after `_redact_strings()` has run.
* `queries` (the `array` argument naming what to search for) round-trips through the same
  `SecretRedactor` in both directions. Before `compile_queries()` builds the search pattern, each
  query is passed through `redactor.detokenize(session, query)` into a separate
  `search_queries` list — so a `[[SECRET:...]]` token echoed back from an earlier `ReadFile`/
  `Grep` result resolves to the real plaintext for matching against a file's real content, the
  same way `EditFileCore` detokenizes `old_text`. The *original* `queries` (never
  `search_queries`) is what gets redacted via `_redact_strings()` and echoed back as
  `result["queries"]` — covering both a query that was already a token (round-trips unchanged,
  since `redact()` finds nothing secret-shaped in a `[[SECRET:...]]` string) and a query where the
  caller typed a real secret's plaintext directly rather than obtaining it via a token (masked
  before being echoed back, so it isn't handed to the model a second time in the result).

## Session-state storage

The token-to-plaintext map lives entirely in `session.tool_state["SecretRedaction"]
["token_to_secret"]` — an in-memory-only dict. `Session.tool_state` is documented (see
`klorb/src/klorb/session/mixins/core.py`) as "never read or written by `Session` itself, and
never persisted to disk," and `klorb.workspace.session_store.SessionState` (the pydantic model
actually serialized to `session.json`) has no field that could carry it — so plaintext secrets
captured during a session never reach disk through this mechanism. This is the same guarantee
other tools already rely on to hold sensitive or unserializable state (`BashTool`'s live
persistent-shell subprocess handle, `WebFetchTool`'s spill tmpdir).

## Suppressing false positives with a secrets baseline

`SecretRedactor` supports a baseline file for suppressing known false positives — strings that `detect-secrets` flags but the workspace owner has verified are not real credentials (documentation examples, test fixtures, etc.).

### File location and naming

The baseline file lives at `${workspace.path}/.klorb/secrets-baseline.json`, not a bare `.secrets.baseline` in the workspace root, for the same reasons `.klorb/klorb-config.json` lives there (see docs/specs/projects-and-trust.md): it's project-level, human-maintained, meant to be committed alongside the repository it applies to, and — simply by living under `.klorb/` — is automatically covered by the existing privileged-path deny (`klorb.permissions.directory_access.is_privileged_path`/`KLORB_PROJECT_DIR_NAME`). A model's default tools (`ReadFile`/`EditFile`/`Grep`/`Bash`) cannot access that path; reaching it requires `EscalatePrivileges`.

### Trust gate

The baseline file is only loaded when `workspace.trusted` is `True`. An untrusted workspace may be a hostile, downloaded-and-unzipped repository that ships a crafted `.klorb/secrets-baseline.json` to suppress detection of its own planted secrets. `load_secrets_baseline()` takes a `trusted` parameter and returns an empty frozenset when it is `False`.

### Format

The file is the literal output of `detect-secrets scan > .klorb/secrets-baseline.json` — the standard `detect-secrets` baseline format, with `version`, `plugins_used`, `filters_used`, and `results` keys. No klorb-specific parsing or schema envelope is applied. It already contains a `version` field tracking the `detect-secrets` library version that produced it.

```json
{
  "version": "1.5.0",
  "plugins_used": [{"name": "AWSKeyDetector"}, ...],
  "filters_used": [{"path": "detect_secrets.filters.heuristic.is_likely_id_string"}, ...],
  "results": {
    "path/to/fixture.env": [
      {"type": "AWS Access Key", "filename": "path/to/fixture.env", "hashed_secret": "abac545fc3bf803134bc8f78fb6160a5c6a87b26", "is_verified": false}
    ]
  }
}
```

The `hashed_secret` values are SHA-1 digests (the same hashing algorithm `detect-secrets` uses internally), so a baseline can suppress a secret without holding the plaintext: `load_secrets_baseline()` reads the file and returns only the hashes; during scanning, `SecretRedactor` computes the SHA-1 of each detected secret and skips it if that hash is in the set.

### How baseline filtering works

`load_secrets_baseline(workspace_path, trusted=...)` reads the file at `workspace_path/.klorb/secrets-baseline.json`, extracts all `hashed_secret` values, and returns them as a `frozenset[str]`.  `ReadFileTool`, `EditFileTool`, and `GrepTool` each call `load_secrets_baseline()` once in `__init__()` and pass the resulting frozenset to `SecretRedactor(baseline_hashes=...)`.  During `redact()`, for each potential secret the scan flags, `SecretRedactor` checks whether `PotentialSecret.hash_secret(secret_value)` is in the frozenset — if so, the secret is skipped rather than masked.  Secrets whose hash is not in the set are redacted as before.

The baseline is evaluated per secret value, not per filename or line number — if the same secret value appears somewhere the baseline doesn't name, it still passes through unredacted, because the hash is all that's checked.

## Out of scope

* **`Bash` bypasses this filter entirely.** `cat`, `grep`, and any other command run through
  `BashTool` return literal file content straight to the model with no redaction. This feature
  only closes the `ReadFile`/`EditFile`/`Grep` paths; it is not a guarantee that a secret in the
  workspace can never reach the model's context by some other route.
* **Multi-line secrets are only partially handled.** `PrivateKeyDetector` matches the line
  carrying a PEM boundary marker (e.g. `-----BEGIN PRIVATE KEY-----`), but the key body on
  subsequent lines is not itself flagged or redacted, since detection runs per line
  (`detect_secrets.core.scan.scan_line`) with no cross-line context.
* **The TUI's click-to-expand overlay is intentionally unredacted.** `ReadFileTool
  ._open_full_view()` re-reads the file directly for a human viewing their own local file in the
  terminal — that path never touches `SecretRedactor`, since redaction exists to protect what
  reaches the *model's* context, not what the user who already has filesystem access can see.
* **`ReadScratchpadTool`/`ReadMemoryTool`/`ReadSkillFileTool` and
  `EditScratchpadTool`/`EditMemoryTool` don't redact**, even though they share `ReadFileCore`/
  `EditFileCore` with `ReadFileTool`/`EditFileTool`. A future change could pass a `SecretRedactor`
  into these the same way, since the mechanic itself is generic.
