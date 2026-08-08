# Plan 020: Vision / image input

> **Draft.** This plan describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented, and nothing else should depend on it,
> until it's promoted to `ready/` and then archived.

## Summary

Let a user attach an image (a screenshot, diagram, or photo) to a prompt and have klorb send it
to a vision-capable model. In scope:

* Drag-and-drop and clipboard-paste of an image file onto the VS Code plugin's prompt box.
* A server-side resize/downsample + transcode pipeline that fits an image within the active
  model's supported resolution and picks the smallest wire-compatible format, before
  base64-encoding it.
* Per-vendor image token estimation, since `klorb.token_estimate.estimate_tokens` is text-only
  (`tiktoken`) and has no notion of pixels.
* Threading an image all the way from the webview, through the ACP wire protocol (which already
  defines `ImageContentBlock` but klorb currently rejects it), into `Message`/`ApiProvider`, and
  out to OpenRouter.

Out of scope (see "Future work"): TUI/CLI image attachment, remote image URLs, OCR-based secret
redaction inside image content, and cross-turn image-retention/pruning policy.

## Where this plugs into the existing design

Three places in the codebase already anticipate this feature almost exactly:

1. **`klorb.message.MessageFragment`** (`klorb/src/klorb/message.py:20-35`) — built for
   `@mention` file attachments (docs/specs/at-mention-file-inlining.md) — has a `type` field
   whose docstring says outright: *"Only `"text"` exists today; a future image/file-attachment
   fragment type extends this literal rather than replacing it."* This is the sanctioned
   extension point; there is no reason to invent a second multi-part-content mechanism.
2. **Every packaged `klorb-model` JSON already declares a `vision: bool` capability**
   (docs/specs/model-framework.md) — `claude-sonnet-5`, `gpt-5-nano`, `kimi-k2.7-code`,
   `kimi-k3`, `mimo-v2.5`, and `qwen-3.7-plus` are `true`; `glm-5.2`, `gpt-oss-120b:nitro`, and
   `mimo-v2.5-pro` are `false`. Nothing reads this flag today beyond display (`klorb models`,
   "Show model info") — this plan is the first consumer that actually gates behavior on it.
3. **The ACP wire protocol already carries image content**, and klorb already knows it doesn't
   support it yet. `klorb/src/klorb/server/klorb_agent.py`'s `_PromptContentBlock` union already
   includes the `agent-client-protocol` SDK's `ImageContentBlock` (fields: `data: str` (base64),
   `mime_type: str`, optional `uri`/`annotations`/`_meta`), but `_extract_prompt_text()`
   explicitly raises `invalid_params` on any non-text block:

   ```python
   # klorb_agent.py:544-554
   def _extract_prompt_text(blocks: list[_PromptContentBlock]) -> str:
       """... Raises a JSON-RPC `invalid params` error on the first non-text block --
       images/audio/resources aren't supported until a later increment."""
   ```

   docs/specs/klorb-server.md says the same thing in its own words: *"Only `TextContentBlock`
   prompt content is supported at this checkpoint."* This plan is that later increment, for
   images specifically (audio/resource blocks stay unsupported).

Also relevant: `OpenRouterApiProvider._build_api_messages()` (`klorb/src/klorb/openrouter.py`)
already sends `message.provider_content()`, not `message.content`, as the wire `content` field
for every role — so once an image `MessageFragment` exists and `provider_content()` includes it,
**no change is needed in `_build_api_messages` itself** to get image bytes to OpenRouter. The
work is concentrated in the data model, the resize pipeline, the ACP boundary, and the webview.

## Data model: extending `MessageFragment`

```python
MessageFragmentType = Literal["text", "image_url"]
"""`"image_url"` matches the OpenAI/OpenRouter content-part "type" value literally, the same
way "text" already does, so `MessageFragment.model_dump()` stays a verbatim wire dict."""

class MessageFragment(BaseModel):
    type: MessageFragmentType
    text: str = ""
    image_url: dict[str, str] | None = None
    """`{"url": "data:image/webp;base64,..."}` when `type == "image_url"` -- the exact
    OpenAI/OpenRouter content-part shape, so `provider_content()` needs no per-type branching."""

    source_filename: str | None = Field(default=None, exclude=True)
    original_width: int | None = Field(default=None, exclude=True)
    original_height: int | None = Field(default=None, exclude=True)
    resized_width: int | None = Field(default=None, exclude=True)
    resized_height: int | None = Field(default=None, exclude=True)
    estimated_tokens: int | None = Field(default=None, exclude=True)
```

`exclude=True` keeps these klorb-only bookkeeping fields (webview thumbnail captions, per-image
token accounting) out of `model_dump()`, so `provider_content()`/`Message.body()` still hand a
provider exactly the OpenAI-compatible wire shape, unchanged from today's text-fragment
behavior. No `Message.provider_content()` code change is needed for this reason.

This is an additive change to an already-open `Literal` and an already-optional-field-bearing
model — no schema-envelope version bump (`Message`/`session.json` aren't independently
schema-versioned as a document type the way `klorb-config`/`klorb-model`/`klorb-session` are;
they ride along inside `SessionState`, whose own `klorb-session` envelope version is unaffected
by an additive field on a nested model, per docs/specs/persisted-json-schema-versioning.md's
"additive, no migration needed" precedent — `drop_reasoning`/`knowledge_cutoff` were added to
`klorb-model` the same way).

## Model capability data: `vision_details`

`Model.capabilities()` is an open `dict[str, Any]` ("implementations may add further
provider-specific keys" — docs/specs/model-framework.md). Add a `vision_details` sub-dict to
every vision-capable model's JSON:

```json
"capabilities": {
  "vision": true,
  "vision_details": {
    "supported_mime_types": ["image/jpeg", "image/png", "image/webp", "image/gif"],
    "max_width_px": 1568,
    "max_height_px": 1568,
    "max_megapixels": 1.15,
    "token_formula": "anthropic_tiles",
    "max_images_per_request": null
  }
}
```

Values sourced from the TODO.md notes and vendor docs, per model:

| Model | Formats | Max resolution | Token formula | Other limits |
| --- | --- | --- | --- | --- |
| `anthropic/claude-sonnet-5` | jpeg, png, webp, gif | downsampled to ~1.15MP (1092×1092 square-equivalent) | `tokens ≈ (w×h) / 750` | — |
| `moonshotai/kimi-k3`, `moonshotai/kimi-k2.7-code` | jpeg, png, gif, webp, bmp, heic, heif | 4096×2160 | no published formula — see below | 100MB overall conversation body limit, no per-file-count cap |
| `qwen/qwen3.7-plus` | (verify against Qwen docs at implementation time) | ~2,621,440 px (~2.6MP) | `tokens = (w×h) / (32×32) + 2` | max 2048 images/request |
| `openai/gpt-5-nano` | jpeg, png, webp, non-animated gif | not a fixed pixel cap — bounded by a 1,536-patch budget at 32×32px/patch (≈1,572,864px, ~1.57MP, before the model multiplier is applied) | patch-based: `patches = min(1536, ceil(w/32) × ceil(h/32))` (image is downscaled first if it would exceed the budget), `tokens = patches × 2.46` (gpt-5-nano's own published multiplier — mini-tier models use 1.62 instead) | up to 512MB total payload/request, up to 1500 images/request (API-wide limits, not per-image) |
| `xiaomi/mimo-v2.5` | jpeg, png, webp, gif (no vendor-published allow-list beyond this; inherited from the standard `Qwen2VLImageProcessor` it reuses) | `min_pixels`=3,136 (56×56) to `max_pixels`=12,845,056 (~12.85MP) — read directly from the model's published `preprocessor_config.json` on Hugging Face | `tokens ≈ (resized_w × resized_h) / 1024` (`patch_size`=16, `merge_size`=2 → an effective 32×32 token-patch, the same shape as Qwen's own formula above — MiMo-V2.5's `preprocessor_config.json` names `"processor_class": "Qwen2_5_VLProcessor"` outright, i.e. it reuses Qwen's vision preprocessing pipeline) | — |

`gpt-5-nano`'s numbers come from OpenAI's own current API docs
(`developers.openai.com/api/docs/guides/images-vision`, the page `platform.openai.com/docs/
guides/images-vision` redirects to as of this writing) — its per-model multiplier table lists
`gpt-5-nano: 2.46` and a shared 1,536-patch budget across the mini/nano tier.
`xiaomi/mimo-v2.5`'s numbers come from its own repository's `preprocessor_config.json`
(`huggingface.co/XiaomiMiMo/MiMo-V2.5/raw/main/preprocessor_config.json`) rather than prose
documentation, which doesn't otherwise publish resolution specifics. Both should still be
spot-checked against OpenRouter's live `/models` response at implementation time, per the
`add-openrouter-model` skill's convention of never trusting a secondary source over the live API
for a model actually being registered.

**Kimi has no published token formula.** Rather than fabricate one, use a documented generic
fallback for any model whose `token_formula` is absent/unrecognized: reuse Anthropic's
`(w×h)/750` formula as a conservative estimate, clearly commented as an approximation, not a
vendor-verified number. `estimate_image_tokens()` (below) dispatches on `token_formula` with this
fallback as its `else` branch.

## Resize / downsample / transcode pipeline

New module `klorb/src/klorb/images/prepare.py`:

```python
def prepare_image_for_model(raw_bytes: bytes, model: Model) -> PreparedImage: ...
```

`PreparedImage` (small pydantic model or dataclass): `mime_type`, `data_b64`, `width`, `height`,
`original_width`, `original_height`, `byte_size`.

Steps:

1. Open with Pillow (`PIL.Image.open`); `ImageOps.exif_transpose()` bakes in EXIF orientation
   and, as a side effect, drops the EXIF block entirely — a deliberate privacy plus (screenshot/
   photo EXIF can carry GPS coordinates).
2. Compute the target box from the model's `vision_details.max_width_px`/`max_height_px`/
   `max_megapixels` (whichever are set; a model missing `vision_details` falls back to a
   process-config default, `tools.images.defaultMaxDimensionPx`, default 1568px long edge — Anthropic's
   own general-purpose recommendation, a reasonable universal default). Downscale only (never
   upscale) preserving aspect ratio, `Bicubic Sharper` resampling.
3. Pick an output format from `tools.images.preferredFormats` (default `["image/webp",
   "image/png"]`), trying each in order, keeping the first the model's `supported_mime_types`
   actually lists; encode WebP lossless, falling back to PNG (also lossless) — both avoid
   introducing JPEG block artifacts that could blur small text in a code/terminal screenshot,
   the primary expected use case. Only fall through to JPEG (quality 90) if neither lossless
   format is in the model's supported list.
4. Enforce an absolute post-encode byte ceiling (`tools.images.maxBytesRaw`, default 4MB, catching the
   pathological "still huge even at max resolution" case, and relevant to Kimi's 100MB
   whole-conversation cap) — step down quality/resolution further, or raise a clear error surfaced
   to the user ("image too large for `<model>`; try a smaller image or a different model") rather
   than silently truncating.
5. Base64-encode; return `PreparedImage`.

**New dependency: Pillow** (`Pillow >= 11.0.0, < 12.0.0`), added via the `add-python-dependency`
skill/workflow.

## Token estimation

`klorb/src/klorb/token_estimate.py` gains:

```python
def estimate_image_tokens(width: int, height: int, model: Model) -> int: ...
def estimate_message_tokens(message: Message, model: Model) -> int: ...
```

`estimate_image_tokens` dispatches on `model.capabilities().get("vision_details", {}).get(
"token_formula")`:

* `"anthropic_tiles"` → `(w×h)/750`.
* `"qwen_pixel_ratio"` → `(w×h)/(32×32)+2`. `xiaomi/mimo-v2.5` uses this same key — its
  `patch_size=16`/`merge_size=2` preprocessor config yields an identical effective 32×32
  token-patch, and it explicitly reuses Qwen's own processor class (see the model table above) —
  rather than inventing a second formula that computes the same thing.
* `"openai_patch_budget"` (`gpt-5-nano`, and any other GPT-5.x-family model added later) →
  `patches = min(vision_details.patch_budget, ceil(w/32) × ceil(h/32))`, `tokens = patches ×
  vision_details.token_multiplier`. Both `patch_budget` (1536 for gpt-5-nano) and
  `token_multiplier` (2.46 for gpt-5-nano; other GPT-5.x tiers publish different multipliers, e.g.
  1.62 for the mini tier) live in that model's own `vision_details`, not hardcoded in
  `estimate_image_tokens` itself, so a future GPT-5.x model just needs its own JSON values, not a
  code change.
* Anything else (including `moonshotai/kimi-*`, which has no published formula) → the
  Anthropic-formula fallback described above.

`estimate_message_tokens` replaces the current `estimate_tokens(user_message.body())` call
(`klorb/src/klorb/session/mixins/turns.py:542`) for a message that may carry image fragments:
text portions (each text fragment's `.text`, or plain `content` when there are no fragments) go
through the existing `tiktoken`-based `estimate_tokens()`; each `image_url` fragment's
`resized_width`/`resized_height` (bookkeeping fields, set by `prepare_image_for_model` before the
fragment is built) go through `estimate_image_tokens()`. The sum becomes the message's
`num_tokens`, same "definitive cost" treatment every other message already gets (see
docs/adrs/00121-count-every-message-tokens-client-side-with-tiktoken.md) — no naive fallthrough of
`Message.body()`'s JSON-dumped fragments (including a multi-KB base64 string) into `tiktoken`,
which would produce a token count with no relationship to the model's actual multimodal billing.

`OpenRouterApiProvider`'s own `total_content_chars` debug-log line (openrouter.py, logging only)
`str()`-stringifies whatever `content` ends up being — worth a small fix alongside this work so a
base64 image payload doesn't blow up one log line; not otherwise consequential.

## ACP wire protocol

* `klorb_agent.py`: rename/extend `_extract_prompt_text()` to
  `_extract_prompt_content(blocks, active_model) -> tuple[str, list[MessageFragment]]`. Text
  blocks concatenate as today. An `ImageContentBlock` becomes an image `MessageFragment` after
  `prepare_image_for_model(base64.b64decode(block.data), active_model)`. Audio/resource/embedded-
  resource blocks keep raising `invalid_params` — unchanged, out of scope here. An image block
  against a model whose `capabilities().get("vision")` is falsy also raises `invalid_params`
  (`{"reason": "the active model does not support image input"}`), mirroring the existing
  unsupported-block-type error shape.
* `KlorbAcpAgent.prompt()` passes the extracted image fragments to `TurnBridge.run_turn(
  prompt_text, image_fragments=...)` → `Session.send_turn(prompt_text, ..., image_fragments=...)`.
* `Session.send_turn` fragment assembly (`session/mixins/turns.py:527-536`) appends image
  fragments **after** the final text fragment: `[*mention_fragments, MessageFragment(type="text",
  text=prompt), *image_fragments]`. This follows the vendor guidance already sitting in TODO.md's
  notes verbatim: *"we recommend sending the text prompt first, then the images."*
  * The image fragments themselves are each preceded by a text fragment "header", stating `"The following is image #<n> in the order provided by the user:`
  * If the image had a known filename, `"filename='foo.png'"` is also included.
  * If the image was pasted from the clipboard, `"user pasted from clipboard"` is included.
* Capability advertisement: `agentCapabilities._meta.klorb.imageInput = true` at `initialize()`
  (static — "this server understands `ImageContentBlock` at the protocol level at all," mirroring
  how `enqueueMessage`/`sessionConfig` are advertised). This is necessarily coarser than "the
  *current* model supports vision," since the active model can change mid-session
  (`klorb/setSessionConfig`); extend whatever already reports the active model to the client
  (`session_config_json` in `klorb/src/klorb/server/update_mapping.py`, the same channel the
  status row's model chip already reads) with an `activeModelVision: bool` field, so the webview
  can proactively hide/disable the attach affordance for a non-vision model without waiting on a
  rejected request.

## VS Code plugin

* **`src/shared/webviewMessages.ts`**: `SubmitPromptMessage`/`EnqueueMessageMessage` gain
  `images?: {mimeType: string; dataBase64: string; name?: string}[]`.
* **`PromptInput.tsx`**: add `onDrop`/`onDragOver` (filtering `DataTransfer.files` to recognized
  image MIME types — the union of every vision model's `supported_mime_types`, not one vendor's
  list) and `onPaste` (checking `ClipboardEvent.clipboardData.items` for an `image/*` entry,
  `item.getAsFile()` → `FileReader.readAsDataURL`). A lightweight attachment tray above the
  textarea shows a thumbnail (`<img>`, CSS-capped height) per pending attachment with a remove
  (×) button. **Raw bytes travel client → host → ACP as-is; the resize/transcode pipeline runs
  server-side (Python), not in the webview** — keeps the webview bundle free of an image-
  processing dependency and centralizes vendor-specific resize logic in the one place a future
  TUI/CLI attachment path (see "Future work") could reuse too. A generous client-side raw-size
  cap (e.g. 25MB) rejects an oversized drop/paste with an inline error before it's ever
  base64-encoded and posted through `vscode.postMessage`, independent of the server-side
  `tools.images.maxBytesRaw` ceiling.
* **`AcpConnection.prompt()`**: builds the ACP content-block array as `[{type: 'text', text},
  ...images.map(img => ({type: 'image', data: img.dataBase64, mimeType: img.mimeType}))]`.
* Status row: gate the attach affordance's visibility on the new `activeModelVision` status
  field described above.
* The StatusRow gains a menu item for "attach image file" that pops open a file picker that does the same thing as `onDragOver`.

## Session persistence

`session.json` dumps every `Message` (including `fragments`) wholesale on every turn boundary
(docs/specs/session-persistence.md). An image fragment's `image_url.url` is a full base64 data
URI — potentially hundreds of KB to a few MB per image. Two consequences worth flagging before
implementation, not deferring past it:

1. **Storage bloat.** A session with several screenshots could grow `session.json` from KB to
   tens of MB, slowing every `persist_state()` write (every turn) and every `session/list`/
   restore read.
2. **Ongoing resend cost.** Unlike WebFetch's spill-to-file pattern (which only has to keep a
   large *tool result* out of context once), an image fragment lives in conversation history and
   is resent to the provider on **every subsequent turn** — this architecture has no context
   pruning yet, so attaching one image early in a long session means paying its token cost again
   on every later turn for the rest of the session.

**Proposed for this plan:** store prepared image bytes on disk under the session's own directory
(`sessions/<subdir>/images/<fragment-id>.<ext>`) rather than inline base64 in `session.json`. A
*persisted* image fragment holds `image_path` (relative to the session directory) instead of a
populated `image_url`; the wire-format `image_url` is (re)constructed — read the file, base64-
encode — only at send time. This is a real architectural departure from today's `MessageFragment`
contract (`provider_content()` becomes filesystem-touching for image fragments, no longer a pure
function of in-memory state) and probably deserves its own ADR once the implementation settles.
The alternative — dumping images inline like `@mention` fragments do — is rejected here because
`@mention` fragments are capped, line-numbered text (hundreds of bytes to a few KB); an image is
categorically larger. Deleting a session's directory (`MAX_RECENT_SESSIONS` pruning's existing
`shutil.rmtree`) already cleans up `images/` for free as long as it lives inside the session's own
directory tree.

*This proposal has been signed off as approved.*

## Security

* **No `readDirs`/domain-permission gating on the attach action itself** — a drag/paste is a
  direct user gesture inside the webview, not a workspace-path reference the agent could be
  tricked into supplying, so it follows the same "implicit authorization" reasoning
  docs/specs/at-mention-file-inlining.md already applies to `@mention`.
* **EXIF stripping** (a side effect of the resize pipeline's `exif_transpose()`) removes GPS/
  device metadata before any bytes leave the machine — call this out as an intentional privacy
  behavior, not just an orientation fix.
* **No pixel-content secret scanning.** The TODO.md item about masking AWS-style keys before
  sending text to the model has no image-domain equivalent here (would need OCR) — flagged as an
  explicit gap, not attempted by this plan.
* Once sent, an image is subject to the same OpenRouter/provider trust boundary any other prompt
  content already is; nothing new there.

## Configuration

New `tools.images.*` process-level keys (`ProcessConfig`/`PROCESS_KEY_MAP`/
`default-config.json`, mirroring `tools.@mention.maxLines`'s existing shape):

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `tools.images.defaultMaxDimensionPx` | int | 1568 | Long-edge fallback cap when a model has no `vision_details.max_width_px`/`max_height_px` |
| `tools.images.maxBytesRaw` | int | 26214400 (25MB) | Post-encode byte ceiling; also mirrored as the webview's client-side pre-encode guard |
| `tools.images.preferredFormats` | list[str] | `["image/webp", "image/png"]` | Transcode preference order, filtered against the active model's `supported_mime_types` |

## Testing strategy

* `prepare_image_for_model()`: aspect-ratio-preserving downscale math (never upscales), format
  selection across different `supported_mime_types` combinations, byte-ceiling enforcement,
  EXIF-orientation correction against a rotated fixture, using small synthetically-generated
  fixture images rather than committing large binary files where avoidable.
* `estimate_image_tokens()`: one case per `token_formula` value, including the unknown-formula
  fallback.
* `MessageFragment`/`Message.provider_content()`: an image fragment's `model_dump()` produces the
  exact OpenAI/OpenRouter content-part shape, and excludes the bookkeeping fields.
* `klorb_agent.py`: `_extract_prompt_content` accepts an `ImageContentBlock` against a vision
  model, rejects one against a non-vision model, still rejects audio/resource blocks.
* `Session.send_turn`: image fragments ordered after the text fragment; a persist/restore
  round-trip proving a path-backed fragment's file reference resolves and re-encodes correctly.
* VS Code (`vitest`): drop/paste handler tests via jsdom `DataTransfer`/`ClipboardEvent` mocks,
  the client-side size-guard rejection path, `webviewMessages.ts` parse/guard coverage for the new
  `images` field, and a `historyModel` reducer test for a prompt entry carrying an attachment
  thumbnail.
* `make lint typecheck test` in both `klorb/` and `vscode-plugin/`; `make lint_docs` after editing
  this plan or any spec.

## Future work

* **TUI/CLI image attachment** (`--image path.png`, or a `>attach <workspace-file>` palette
  command) — out of scope here since a raw terminal has no native drag-drop/paste-image surface
  to build the UI on top of; the resize pipeline and `MessageFragment`/ACP plumbing this plan
  builds are UI-agnostic and directly reusable once a TUI-side entry point exists.
* **Remote image URLs** — sending `{"image_url": {"url": "https://..."}}` instead of always
  inlining base64, gated through WebFetch's existing `DomainAccessTable` for permission screening
  — smaller wire payload, at the cost of a new network trust boundary. This plan always inlines.
* **Remote file upload / preflighting** — sending image files to a dedicated file upload endpoint of the provider and using a reference to the uploaded file. (We currently send the base64-encoded data every turn.)
* **Image retention/pruning policy** — an analogue to the existing per-model `drop_reasoning`
  toggle (docs/adrs/00122-preserve-reasoning-across-turns-by-default.md's counterpart), e.g. dropping or
  summarizing image fragments from history after N turns, to cap the ongoing resend-token cost
  the "Session persistence" section above flags. Not attempted here.
* **Re-verify `gpt-5-nano`/`mimo-v2.5`'s numbers against OpenRouter's live `/models` response**
  at implementation time. This plan's table above is already sourced from each vendor's own docs
  (OpenAI's `images-vision` guide; MiMo-V2.5's own `preprocessor_config.json`), not guessed, but
  per the `add-openrouter-model` skill's convention the live API is still the tie-breaker if
  OpenRouter's routing imposes different effective limits than the base model's own spec.
* **Kimi's token formula** remains genuinely unpublished (unlike gpt-5-nano/mimo-v2.5, which
  turned out to have public specs) — still riding the generic Anthropic-formula fallback until
  Moonshot AI publishes one.
* **Client-side (webview canvas) pre-downscaling** before the postMessage hop, if raw-image
  transit over stdio/postMessage proves to be a practical bottleneck in real use — v1 keeps the
  webview simple and the resize pipeline server-authoritative.

## TODO list

1. Add Pillow dependency via `/add-python-dependency`.
2. Extend `MessageFragmentType`/`MessageFragment` (`klorb/src/klorb/message.py`) with
   `"image_url"` + `image_url` field + `exclude=True` bookkeeping fields.
3. `klorb/src/klorb/images/prepare.py`: `prepare_image_for_model()`, `PreparedImage`.
4. Add `vision_details` to every `vision: true` packaged model JSON, using the values in the
   table above (spot-checked against OpenRouter's live `/models` response, per the
   `add-openrouter-model` skill's convention, before committing them).
5. `klorb/src/klorb/token_estimate.py`: `estimate_image_tokens()`, `estimate_message_tokens()`;
   wire into `session/mixins/turns.py`'s `num_tokens` computation.
6. `klorb_agent.py`: `_extract_prompt_content()` (replacing `_extract_prompt_text()`), image-block
   handling, vision-capability rejection, `activeModelVision` status field.
7. `TurnBridge.run_turn()` / `Session.send_turn()`: thread image fragments through, appended after
   the text fragment.
8. Implement the session-persistence storage strategy (path-backed image fragments under
   `sessions/<subdir>/images/`) — get explicit sign-off on this design point first (see "Session
   persistence" above).
9. Advertise `agentCapabilities._meta.klorb.imageInput`.
10. VS Code: `webviewMessages.ts` `images` field; `PromptInput.tsx` drop/paste handlers +
    attachment tray + client-side size guard; `AcpConnection.prompt()` image passthrough; status
    row attach-affordance gating.
11. Config: `tools.images.*` keys in `process_config.py` + `default-config.json`.
12. Tests per "Testing strategy" (Python + vscode-plugin).
13. `make lint typecheck test` (both subprojects); `make lint_docs`.
14. Update docs/specs: extend `docs/specs/message-model.md` (fragment type),
    `docs/specs/klorb-server.md` (superseding its "not supported yet" line for images),
    `docs/specs/vscode-plugin.md` (attach UI); add a new `docs/specs/vision-image-input.md` for
    the resize/token-estimation pipeline once built.
