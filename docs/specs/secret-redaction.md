# Secret redaction

## Summary

`ReadFileTool` and `EditFileTool` pass file content through `klorb.tools.util.SecretRedactor`,
which detects likely credentials (AWS keys, private keys, vendor API tokens, and other
credential-shaped strings, via the third-party `detect-secrets` library) and replaces each one
with a `[[SECRET:<type>:<hash>]]` token before the content reaches a model — so a model can read
and edit a file containing real secrets without those secrets ever appearing in its context.
`EditFileTool` resolves the same tokens back to their real plaintext when a model echoes one back
in `start_text`/`end_text`/`old_text`/`context_before`/`context_after`/`new_text`, so the
read-then-edit loop works even though the model itself never sees the underlying secret.

This is scoped to `ReadFileTool`/`EditFileTool` only — the two tools that operate on real,
model-named filesystem paths, where a genuine credential (a `.env` file, a config file with an
API key) is most likely to live. See "Out of scope" for what this deliberately doesn't cover.

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
* `_PLUGINS` (`secret_redaction.py`) is every `detect-secrets` vendor/format-specific plugin
  (`AWSKeyDetector`, `PrivateKeyDetector`, `GitHubTokenDetector`, `SlackDetector`, `JwtTokenDetector`,
  `KeywordDetector`, ...), deliberately excluding `Base64HighEntropyString`/`HexHighEntropyString`
  (trip constantly on ordinary hashes, UUIDs, and base64 blobs found in everyday source) and
  `IPPublicDetector` (an IP address isn't a credential). `detect-secrets`' own default heuristic
  filters (`is_potential_uuid`, `is_sequential_string`, `is_templated_secret`, etc.) still apply
  underneath this plugin selection, further cutting down on false positives.
* `detect-secrets` keeps its plugin/filter configuration in a process-wide
  `functools.lru_cache` singleton (`detect_secrets.settings.get_settings()`). `SecretRedactor`
  serializes every scan through a module-level `threading.Lock` (`_scan_lock`) so two sessions
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
    `_detokenize_normalized_args()` runs `redactor.detokenize(session, ...)` over `start_text`,
    `end_text`, `new_text`, `context_before`, `context_after`, and each line of an `old_text`
    block — so anchor matching and the eventual `path.write_text()` both operate on the file's
    real bytes, never on a literal `[[SECRET:...]]` string. This is what stops a redacted secret
    from being destroyed: without this step, an edit that carries a token in `new_text` (e.g.
    "move this line, keep its content as-is") would write the token's literal text to disk in
    place of the real secret.
  * After the edit is written, `post_edit_content` and each `DiffLine.text` in the result's
    `diff` are re-redacted (`redactor.redact(session, ...)`) before being returned — the disk
    write itself (`path.write_text(content, ...)`) always uses the real, detokenized bytes;
    only the tool result echoed back to the model is masked again. Re-redacting reuses the same
    `session`-scoped token map, so a secret that was already assigned a token earlier in the
    session gets that same token back in the diff, rather than a fresh one.
* `ReadFileTool`/`EditFileTool` each construct one `self._secret_redactor = SecretRedactor()` in
  `__init__` and pass it (with `self.context.session`) into `self.read_file_core.apply()`/
  `self.edit_file_core.apply()`. Since `SecretRedactor` holds no state of its own, holding one
  instance per `Tool` for its whole lifetime is just a convenience — the actual map lives in
  `session.tool_state`, so a fresh `SecretRedactor()` on the same session resolves the same
  tokens.
* `ReadFileTool.description()` tells the model directly that a line may come back with a
  `[[SECRET:<type>:<hash>]]` token in place of a credential, and that the token (not a guessed
  or invented replacement) is what to pass back into `EditFile`'s `start_text`/`end_text`/
  `old_text`/`new_text` to match or preserve that line.

## Session-state storage

The token-to-plaintext map lives entirely in `session.tool_state["SecretRedaction"]
["token_to_secret"]` — an in-memory-only dict. `Session.tool_state` is documented (see
`klorb/src/klorb/session/mixins/core.py`) as "never read or written by `Session` itself, and
never persisted to disk," and `klorb.workspace.session_store.SessionState` (the pydantic model
actually serialized to `session.json`) has no field that could carry it — so plaintext secrets
captured during a session never reach disk through this mechanism. This is the same guarantee
other tools already rely on to hold sensitive or unserializable state (`BashTool`'s live
persistent-shell subprocess handle, `WebFetchTool`'s spill tmpdir).

## Out of scope

* **Other read paths bypass this filter entirely.** `Grep` and `Bash` (`cat`, `grep`, etc.) read
  the same files and return literal content straight to the model with no redaction — `Grep` in
  particular is a more likely real-world leak path than `ReadFile`, since searching for a name
  like `API_KEY` surfaces the matching value directly in the result. This feature only closes
  the `ReadFile`/`EditFile` path; it is not a guarantee that a secret in the workspace can never
  reach the model's context by some other route.
* **False positives on klorb's own repository are expected and unhandled.** Test fixtures or
  documentation that contain AWS-key-shaped example strings (including this feature's own tests)
  get redacted the same as a real secret would. `detect-secrets` has a baseline-file convention
  (`.secrets.baseline`) for suppressing known false positives; this feature doesn't wire one up.
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
