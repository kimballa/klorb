# Store image-fragment bytes on disk under `sessions/<subdir>/images/`, not inline in `session.json`

* Date: 2026-08-01 00:00
* Question: Plan 020 (vision/image input) adds an `image_url`-type `klorb.message.
  MessageFragment` carrying a full base64 data URI -- potentially hundreds of KB to a few MB
  per image. `session.json` already dumps every `Message` (including `fragments`) wholesale on
  every turn boundary (docs/specs/session-persistence.md). Left as-is, a session with several
  screenshots would grow `session.json` from KB to tens of MB, slowing every `persist_state()`
  write and every restore/list read -- and unlike a `@mention` fragment (capped, line-numbered
  text, hundreds of bytes to a few KB), an image fragment is categorically larger. Where should
  a persisted image fragment's bytes actually live, and how does the rest of the message
  pipeline (`Message.provider_content()`, `OpenRouterApiProvider`) find them again?
* Answer: Prepared image bytes are written to `sessions/<subdir>/images/<uuid>.<ext>` (`klorb.
  workspace.session_store.write_session_image`) the moment a `MessageFragment` is built for a
  turn (`SessionTurnsMixin._spill_image_fragment_to_disk`, called from `send_turn()` right after
  `claim_session_directory()`), for any session whose directory is already claimed. The fragment
  keeps `image_path` (relative to `sessions/<subdir>/`) as the durable reference and `mime_type`
  alongside it. `Message.for_persistence()` -- called by `write_session_state` -- returns a copy
  with `image_url` cleared for any fragment that already has `image_path` set, so `session.json`
  stores the path reference only, never a second copy of the base64 payload. On restore,
  `klorb.session.restore.try_restore_session` calls `_rehydrate_image_fragments()` immediately
  after loading `session.json`: every fragment with `image_path` set and no `image_url` gets its
  `image_url` rebuilt in memory, once, from the on-disk bytes (`read_session_image`) plus the
  stored `mime_type`.

  `MessageFragment.to_wire_dict()` (used by `Message.provider_content()`) and `Message.body()`'s
  fallback JSON dump therefore never touch the filesystem themselves -- they only ever see an
  already-populated `image_url`, whether the fragment was created fresh this turn or rehydrated
  at restore. `ApiProvider.send_prompt()`'s signature is unchanged.

  A session whose directory is never claimed (an untrusted workspace) never spills to disk at
  all: `image_path` stays `None`, the fragment lives purely in memory for the life of the
  process, and `persist_state()` is already a no-op for that case -- consistent with how
  `@mention` fragments behave for an untrusted workspace today.

  `klorb.images.prepare` (the resize/transcode pipeline) takes a small `ImagePipelineConfig`
  (three plain fields: `default_max_dimension_px`, `max_bytes_raw`, `preferred_formats`) instead
  of `klorb.process_config.ProcessConfig` directly. `klorb.images.prepare` sits underneath
  `klorb.session` in the import graph (`klorb.session.mixins.turns` imports `klorb.images.
  prepare.extension_for_mime_type`), and `ProcessConfig` itself imports from `klorb.session`
  (for `SessionConfig`), so importing `ProcessConfig` from `klorb.images.prepare` would be
  circular. The caller (`klorb.server.klorb_agent._extract_prompt_content`, which already holds
  a `ProcessConfig`) builds the small config object instead -- the same pattern `klorb.tools.
  util.ReadFileCore` already uses to take `ProcessConfig.read_file_max_lines`/
  `read_file_max_line_length` as plain constructor args rather than the whole config object.
* Reasoning: Plan 020 itself flagged this as "a real architectural departure from today's
  `MessageFragment` contract... probably deserves its own ADR once the implementation settles,"
  anticipating that the exact mechanism might shift during implementation. It did, in one
  respect: the plan's own phrasing described `image_url` being "(re)constructed -- read the
  file, base64-encode -- only at send time," which implies rehydration happening inside
  `Message.provider_content()`/`ApiProvider.send_prompt()` itself, lazily, on every turn a
  restored session's history is resent. Implementing that literally would have required
  threading a session-directory path through `ApiProvider.send_prompt()`'s abstract interface
  (a public seam every provider implementation and every test double already depends on) down
  through `OpenRouterApiProvider._build_api_messages()` to each `Message.provider_content()`
  call -- a wide, invasive ripple for a single benefit (not holding decoded image bytes in
  memory for a restored session that hasn't sent its next turn yet).

  Rehydrating once, immediately on restore, keeps the invasive part of the plan's own goal (keep
  `session.json` small on disk) while keeping `Message.provider_content()` a pure function of
  in-memory state, exactly as it was before this feature and as `openrouter.py` already assumes
  everywhere it's called. The cost is memory residency: a restored session's image fragments are
  base64-decoded back into memory at restore time rather than only at the moment they're
  actually resent. Given klorb's session history has no context-pruning policy yet regardless
  (an image fragment's token cost is already paid on every subsequent turn for the rest of the
  session -- see Plan 020's "Session persistence" section and its "Future work" note on an
  eventual image retention/pruning policy), this trade-off doesn't meaningfully change the
  session's overall memory/cost profile; it only changes *when* the one-time disk read happens.
