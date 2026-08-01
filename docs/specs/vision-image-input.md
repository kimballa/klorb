# Vision / image input

## Summary

A user can attach an image (a screenshot, diagram, or photo) to a prompt -- via drag-and-drop,
clipboard paste, or the VS Code plugin's status-row file picker -- and have klorb send it to a
vision-capable model. The image travels raw from the client to the server; klorb resizes,
transcodes, and base64-encodes it server-side for whichever model is active, estimates its token
cost, and threads it through the conversation as an `image_url`-type `klorb.message.
MessageFragment` alongside the prompt text.

In the TUI and headless CLI, where there's no drag-drop/paste surface, `@mention`ing a workspace
image file reuses this same resize/transcode pipeline instead -- see docs/specs/at-mention-file-
inlining.md's "Image mentions" section.

Out of scope: remote image URLs (images are always inlined as base64), server-side file-upload
preflighting, and any image-retention/pruning policy for long sessions (see "Out of scope"
below).

## Data model

`klorb.message.MessageFragmentType` is `Literal["text", "image_url"]` -- `"image_url"` matches
the OpenAI/OpenRouter content-part `"type"` value literally. `MessageFragment` carries:

* `image_url: dict[str, str] | None` -- `{"url": "data:image/webp;base64,..."}` when populated
  in memory (a fragment created fresh this turn, or rehydrated from disk on restore).
* `image_path: str | None` -- path relative to the session's own directory, once this
  fragment's bytes have been spilled to disk (see docs/specs/session-persistence.md's
  "Image-fragment storage" section and
  docs/adrs/store-image-fragments-on-disk-not-inline-in-session-json.md).
* `mime_type: str | None` -- set alongside `image_path`, so a restored fragment's `image_url`
  data URI can be rebuilt without re-parsing a (by-then-cleared) prior value.
* `source_filename: str | None`, `original_width`/`original_height`/`resized_width`/
  `resized_height: int | None`, `estimated_tokens: int | None` -- klorb-only bookkeeping,
  excluded from any wire dump.

`MessageFragment.to_wire_dict()` produces the exact wire shape (`{"type": "text", "text": ...}`
or `{"type": "image_url", "image_url": {...}}`) `Message.provider_content()` sends to a
provider, regardless of which bookkeeping fields are also set.

## Model capability data

Every packaged `klorb-model` JSON with `capabilities.vision: true` (`claude-sonnet-5`,
`gpt-5-nano`, `kimi-k2.7-code`, `kimi-k3`, `mimo-v2.5`, `qwen-3.7-plus`) additionally declares a
`vision_details` sub-dict, an open extension of `Model.capabilities()`'s already-open
`dict[str, Any]`:

```json
"vision_details": {
  "supported_mime_types": ["image/jpeg", "image/png", "image/webp", "image/gif"],
  "max_width_px": null,
  "max_height_px": null,
  "max_megapixels": 1.15,
  "token_formula": "anthropic_tiles",
  "max_images_per_request": null
}
```

`token_formula` is one of `"anthropic_tiles"` ((w×h)/750 -- also the fallback for a model with
no `vision_details`, or an unrecognized/absent `token_formula`, e.g. `moonshotai/kimi-*`, which
has no published formula), `"qwen_pixel_ratio"` ((w×h)/(32×32)+2 -- also used by `xiaomi/
mimo-v2.5`, whose `patch_size=16`/`merge_size=2` preprocessor config yields the same effective
32×32 token-patch), or `"openai_patch_budget"` (`gpt-5-nano`: `patches = min(patch_budget,
ceil(w/32)×ceil(h/32))`, `tokens = round(patches × token_multiplier)`, with `patch_budget`/
`token_multiplier` living in that model's own `vision_details` rather than hardcoded).

## Resize/transcode pipeline (`klorb.images.prepare`)

`prepare_image_for_model(raw_bytes, model, config) -> PreparedImage` is the module's one entry
point, where `config` is an `ImagePipelineConfig` (`default_max_dimension_px`, `max_bytes_raw`,
`preferred_formats` -- the three `tools.images.*` values the caller extracts from `ProcessConfig`
before calling in; `klorb.images.prepare` can't import `ProcessConfig` directly without a
circular import, since it sits underneath `klorb.session` in the import graph, which
`ProcessConfig` itself imports from):

1. Opens the image with Pillow; `ImageOps.exif_transpose()` bakes in EXIF orientation, and the
   subsequent `save()` call is never passed `exif=`, so whatever EXIF block (GPS/device metadata
   included) the source carried never reaches the output bytes -- an intentional privacy
   behavior, not just an orientation fix.
2. Computes a downscale-only target box from `model`'s `vision_details.max_width_px`/
   `max_height_px`/`max_megapixels` (whichever are set), or `config.default_max_dimension_px` as
   a long-edge cap for a model with no `vision_details` at all.
3. Picks the first of `config.preferred_formats` (default `["image/webp", "image/png"]`) that
   `model`'s `vision_details.supported_mime_types` lists (or the first preferred format outright
   for a model with no declared list), falling through to `image/jpeg` (quality 90) only if
   neither lossless format is supported.
4. If the encoded result still exceeds `config.max_bytes_raw`, shrinks the target box by 0.75x
   and re-encodes, up to three times, before raising `ImageTooLargeError` -- surfaced to the
   user as "image too large for `<model>`", never silently truncated.
5. Base64-encodes the result into a `PreparedImage` (`mime_type`, `data_b64`, `width`/`height`,
   `original_width`/`original_height`, `byte_size`).

## Token estimation

`klorb.token_estimate.estimate_image_tokens(width, height, model)` dispatches on `model.
capabilities()["vision_details"]["token_formula"]` per the formulas above.
`estimate_message_tokens(message, model)` is `Message.num_tokens`'s image-aware replacement for
plain `estimate_tokens(message.body())` (see docs/adrs/count-every-message-tokens-client-side-
with-tiktoken.md): each text fragment's `.text` (or `content`/`streaming_content` when there are
no fragments) goes through `estimate_tokens()`; each `image_url` fragment's `resized_width`/
`resized_height` goes through `estimate_image_tokens()`. This avoids `body()`'s JSON-dumped
`fragments` (including a multi-KB base64 string) ever reaching `tiktoken`, which would produce a
token count with no relationship to a provider's actual multimodal billing.

## ACP wire protocol

* `ServerStreams.from_stdio()` (`klorb.server.acp_server`) raises the stdin `asyncio.
  StreamReader`'s buffer limit to `STDIN_STREAM_LIMIT_BYTES` (256MB), well past `asyncio.
  StreamReader`'s own 64KiB default. ACP frames one JSON-RPC message per line, and an image
  `session/prompt` request's raw (not-yet-resized) attachment bytes -- base64-inflated, and
  capped client-side at 25MB per image with no cap on attachment count -- routinely exceed the
  default, which fails closed as an unrecoverable `LimitOverrunError` that tears down the whole
  ACP connection rather than erroring just that one request.
* `klorb_agent._extract_prompt_content(blocks, active_model, image_pipeline_config) -> (text,
  image_fragments)` concatenates every `text` block and builds one `MessageFragment` per `image`
  block via `prepare_image_for_model`. `image_pipeline_config` is the calling `Session`'s own
  `image_pipeline_config` property (see docs/specs/at-mention-file-inlining.md) rather than a
  fresh `ImagePipelineConfig` built from `ProcessConfig` per call, so a drag-drop/paste
  attachment and an @mentioned image are resized/transcoded under identical settings. Raises
  `invalid_params` for an `image` block if `active_model` is `None` or its
  `capabilities()["vision"]` is falsy (`{"reason": "the active model does not support image
  input"}`), or if `prepare_image_for_model` raises `ImageTooLargeError`; audio/resource blocks
  keep raising `invalid_params` unconditionally.
* `agentCapabilities._meta.klorb.imageInput = true` at `initialize()` -- static (the server
  understands `ImageContentBlock` at the protocol level at all). `_klorb/getSessionConfig`'s
  result additionally carries `activeModelVision: boolean` (the *current* model's own
  `capabilities()["vision"]`), since the active model can change mid-session
  (`_klorb/setSessionConfig`) independent of the server's own protocol-level support.
* `TurnBridge.run_turn(prompt_text, image_fragments=None)` passes `image_fragments` to `Session.
  send_turn()` on the *first* `send_turn()` call only -- never to a later iteration of the
  turn-end redelivery loop, which resends plain drained-queued-message text, not a fresh
  `session/prompt` with its own content blocks.
* `Session.send_turn(prompt, callbacks=None, image_fragments=None)` appends image fragments
  *after* the prompt's own text fragment (vendor guidance: send the text prompt first, then
  images), each preceded by its own text-fragment header naming its 1-indexed position and
  origin (`filename='<name>'` for a drag-drop/file-picker attachment with a known name, or "user
  pasted from clipboard" otherwise -- see `_image_header_text`), and spills each to disk via
  `_spill_image_fragment_to_disk` if the session's directory is claimed.
* `update_mapping.build_session_replay()` (`_klorb/sessionReplay`, docs/specs/klorb-server.md)
  attaches an `images` key -- a list of `AttachedImageMeta`-shaped dicts (metadata only, no
  bytes: `name`/`width`/`height`, each key present only when the corresponding fragment field
  survived persistence) -- to a `"prompt"`-kind entry for a user message with `image_url`
  fragments (`_replay_image_meta`). The webview renders these as a paper-clip placeholder rather
  than the actual picture; see "VS Code plugin" below and docs/specs/session-persistence.md's
  "Image-fragment storage" section.

## VS Code plugin

`shared/webviewMessages.ts` defines `ImageAttachment` (`{mimeType, dataBase64, name?, width?,
height?}`), added as an optional `images?` field on `SubmitPromptMessage`/
`EnqueueMessageMessage`, plus `AttachImageFileMessage` (webview → host: open a native file
picker) and `ImageAttachedMessage` (host → webview: the picked file's bytes, ready to add to the
pending tray). `StatusUpdateMessage`/`SessionControls.StatusSnapshot` carry
`activeModelVision?: boolean`, fetched alongside `model`/`thinking` via
`_klorb/getSessionConfig`. `shared/imageDimensions.ts`'s `readImageDimensions(bytes)` reads
width/height directly from an image's header bytes (PNG/GIF/BMP/JPEG; `undefined` for WebP/
HEIC/HEIF or malformed bytes) -- plain byte parsing rather than an async `<img>` decode, since
neither the webview's own jsdom test environment nor the (DOM-less) extension host can decode an
image synchronously; both sides call this same function on whatever raw bytes they already have
before an attachment is ever added to a tray.

`PromptInput.tsx` owns the pending-attachment tray:

* `onDragOver`/`onDrop` on the input row's wrapper, and `onPaste` on the textarea, each filtered
  to `ACCEPTED_IMAGE_MIME_TYPES` (the union of every packaged vision model's own supported MIME
  types, not any one vendor's list -- the server re-validates and transcodes for the actual
  active model regardless) and gated on the `imagesCapable` prop (`activeModelVision`); `false`
  or not-yet-known both suppress the handlers entirely.
* Each accepted `Blob` is read via `FileReader.readAsDataURL` (for the base64 payload) and
  `Blob.arrayBuffer()` (fed to `readImageDimensions`) in parallel, and the result added to local
  `attachments` state, rendered via the shared `AttachmentThumbnail` component (`webview/
  components/AttachmentThumbnail.tsx`) above the textarea.
* `MAX_ATTACHMENT_RAW_BYTES` (25MB) rejects an oversized attachment with an inline error before
  it's ever base64-encoded and posted through `vscode.postMessage` -- independent of the
  server-side `tools.images.maxBytesRaw` ceiling.
* An imperative `addAttachment()` (alongside the existing `focus()`) on `PromptInputHandle` lets
  `App`'s `imageAttached` handler feed in a status-row-picked file the same way a drop/paste does
  -- the host has already computed `width`/`height` before sending it, so this stays synchronous.
* `submit()` passes `attachments` (if any) as `onSubmit(text, images)`'s second argument, then
  clears the tray.

`AttachmentThumbnail` (`webview/components/AttachmentThumbnail.tsx`) renders one attachment's
thumbnail plus a caption below it: its dimensions (`<width>x<height>`, omitted when unknown) and
then its display name -- `name` with any leading directory parts stripped, or `"(clipboard)"`
for a nameless (pasted) attachment. Shared verbatim by `PromptInput`'s pending tray (with a
remove button, only rendered when an `onRemove` callback is given) and `HistoryView`'s sent
prompt bubbles (read-only) -- the same metadata that captioned a pending attachment rides along
once it's sent, via `TextHistoryEntry.images` (see "Image attachments" in
docs/specs/vscode-plugin.md). The thumbnail `<img>` is `draggable={false}`: browsers natively
allow dragging an `<img>`, and dragging one back onto `PromptInput`'s own drop zone would
otherwise re-trigger the drop handler and add a duplicate attachment.

`AttachmentThumbnail`'s `image` prop accepts either a full `ImageAttachment` (bytes and all) or
an `AttachedImageMeta` (metadata only, no bytes) -- whichever it's given, `hasBytes()` (checking
for a `dataBase64` key) decides whether to render the real `<img>` or a paper-clip placeholder
icon (codicon `attach`) in its place. A `_klorb/sessionReplay`-restored prompt entry's `images`
are always `AttachedImageMeta` (see "ACP wire protocol" above and docs/specs/session-
persistence.md's "Image-fragment storage" section) -- the server never resends an
already-persisted image's bytes just to redraw a thumbnail after a window reload/session
restore, so the restored entry captions with whatever of `name`/`width`/`height` survived
persistence and shows the placeholder icon instead of the actual picture.

The status row's chevron menu (`StatusMenu.tsx`) includes an "Attach Image…" item only when
`activeModelVision` is true; picking it posts `attachImageFile`, and `KlorbSessionViewProvider.
_attachImageFile()` opens `vscode.window.showOpenDialog` (filtered to image extensions), reads
the chosen file, and posts back `imageAttached`.

`AcpConnection.prompt(text, images?)` builds the ACP content-block array as `[{type: 'text',
text}, ...images.map(img => ({type: 'image', data: img.dataBase64, mimeType: img.mimeType,
_meta: img.name ? {klorb: {filename: img.name}} : undefined}))]` -- `_meta.klorb.filename` is
how a drag-drop/file-picker attachment's original name reaches the server's
`_image_header_text` (a clipboard paste carries no `_meta.klorb.filename`, and the server reads
its absence as "(pasted from clipboard)").

`_klorb/enqueueMessage` (queuing a message into an already-in-flight turn) redelivers as plain
text with no content-block channel of its own (see docs/specs/klorb-server.md); attaching an
image while a turn is in flight surfaces a clear `turnError` ("Image attachments cannot be
queued into an in-progress turn") rather than silently dropping the attachment.

A submitted prompt's images are also carried onto its `TextHistoryEntry` (`images?:
ImageAttachment[]`) so `HistoryView` can render the same thumbnails in the scrolled history.

## Configuration

Process-level `tools.images.*` keys (`ProcessConfig`/`PROCESS_KEY_MAP`/`default-config.json`,
mirroring `tools.@mention.maxLines`'s shape):

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `tools.images.defaultMaxDimensionPx` | int | 1568 | Long-edge fallback cap when a model has no `vision_details.max_width_px`/`max_height_px`/`max_megapixels` |
| `tools.images.maxBytesRaw` | int | 26214400 (25MB) | Post-encode byte ceiling `prepare_image_for_model` enforces |
| `tools.images.preferredFormats` | list[str] | `["image/webp", "image/png"]` | Transcode preference order, filtered against the active model's `supported_mime_types` |

## Security

* No `readDirs`/domain-permission gating on the attach action itself -- a drag/paste/file-pick is
  a direct user gesture inside the webview, not a workspace-path reference the agent could be
  tricked into supplying, the same "implicit authorization" reasoning docs/specs/at-mention-
  file-inlining.md applies to `@mention`.
* EXIF stripping (a side effect of the resize pipeline's `exif_transpose()`) removes GPS/device
  metadata before any bytes leave the machine.
* No pixel-content secret scanning (would need OCR) -- an explicit gap, not attempted here.
* Once sent, an image is subject to the same OpenRouter/provider trust boundary any other prompt
  content already is.

## Out of scope

* A dedicated TUI/CLI attach surface (`--image path.png`, or a `>attach <workspace-file>` palette
  command) beyond `@mention`ing a workspace image file (docs/specs/at-mention-file-inlining.md's
  "Image mentions" section) -- the resize pipeline and `MessageFragment`/ACP plumbing this feature
  builds are UI-agnostic and already reused by that entry point.
* Remote image URLs (`{"image_url": {"url": "https://..."}}` instead of always inlining base64)
  and server-side file-upload preflighting -- images are always inlined as base64 today.
* Image retention/pruning policy: klorb's session history has no context-pruning mechanism at
  all yet, so an attached image's token cost is paid again on every subsequent turn for the rest
  of the session, the same as any other conversation-history content.
* Re-verifying `gpt-5-nano`/`mimo-v2.5`'s exact resolution/token-formula numbers against
  OpenRouter's live `/models` response is an ongoing spot-check, not a one-time gate; OpenRouter's
  generic `/models` endpoint confirms `vision: true`/image support but doesn't publish
  resolution/token-formula detail, which is sourced from each vendor's own docs instead.

## Test coverage

* `tests/klorb/images/test_prepare.py` -- downscale math (never upscales, max-width/height box,
  max-megapixels), format selection across `supported_mime_types` combinations, byte-ceiling
  enforcement, EXIF-orientation correction.
* `tests/klorb/test_token_estimate.py` -- one case per `token_formula`, including the
  unrecognized/absent-formula fallback, and `estimate_message_tokens`'s text+image mix.
* `tests/klorb/test_message.py` -- `to_wire_dict()`'s exact wire shape excluding bookkeeping
  fields, `for_persistence()` clearing `image_url` once `image_path` is set.
* `tests/klorb/server/test_klorb_agent_prompt_content.py` -- vision-capability rejection,
  audio-block rejection, filename metadata, `ImageTooLargeError` → `invalid_params`.
* `tests/klorb/session/test_session.py` -- image fragments ordered after the prompt's text
  fragment, header text for a named vs. clipboard-pasted attachment.
* `tests/klorb/session/test_restore.py` -- a persist/restore round trip proving a path-backed
  fragment's `image_url` is rehydrated correctly.
* `tests/klorb/workspace/test_session_store.py` -- `write_session_image`/`read_session_image`
  round-trips, `write_session_state` dropping `image_url` once `image_path` is set,
  `source_filename`/`original_width`/`original_height` surviving a session.json round trip
  (unlike `resized_width`/`resized_height`).
* `tests/klorb/server/test_update_mapping.py::TestBuildSessionReplay` -- a prompt entry's
  `images` metadata (name/dimensions present, fields omitted when unknown, key absent entirely
  for a message with no image fragments).
* `tests/klorb/server/test_acp_server_streams.py` -- `ServerStreams.from_stdio()` passes
  `STDIN_STREAM_LIMIT_BYTES` to `acp.stdio_streams()`.
* vscode-plugin: `test/shared/webviewMessages.test.ts` (parse/guard coverage for `images`/
  `activeModelVision`/`imageAttached`/`attachImageFile`/a replayed prompt entry's metadata-only
  `images`), `test/shared/imageDimensions.test.ts` (PNG/GIF/BMP/JPEG header parsing,
  unrecognized-format fallback), `test/webview/components/PromptInput.test.tsx` (drop/paste/
  size-guard/remove/imperative-attach), `test/webview/components/AttachmentThumbnail.test.tsx`
  (display-name/clipboard-fallback/dimensions-caption/remove-button/non-draggable/paper-clip
  placeholder for metadata-only images), `test/webview/features/history/historyModel.test.ts`
  (`appendPrompt` carrying `images`).
