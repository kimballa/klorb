# `Session` grows a standing (level-triggered) interjection mechanism alongside the existing one-shot (edge-triggered) one

* Date: 2026-07-08 19:00
* Question: A live `shell_lifetime="session"` persistent terminal is a standing condition — it
  can stay open for many turns with no `Bash` call at all in between, and the model has no other
  way to remember it exists (a `cd` from three turns ago, an exported variable, a background job)
  once it scrolls out of context. `Session` already has
  `_pending_permission_framework_interjection` (`klorb.session`, from the `PermissionFramework`
  mid-conversation change feature) — a single field `set_permission_framework()` sets and
  `send_turn()` prepends-then-clears, firing exactly once for the very next turn. Should the
  persistent-terminal reminder reuse that same field/mechanism, or does it need something else?
* Answer: Something else. `Session` gains `register_standing_interjection(subject, provider)`,
  storing `provider: Callable[[], str | None]` in a new
  `self._standing_interjection_providers: dict[str, Callable[[], str | None]]` keyed by
  `subject`. At the top of `send_turn()`, after the existing one-shot check, every registered
  provider is polled (in a fixed, deterministic order — sorted by subject); each non-`None`
  result is wrapped via the same `_wrap_system_interjection()` helper and prepended the same way,
  but *not* cleared — a provider keeps being polled, and can keep contributing text, on every
  future turn for as long as it keeps returning non-`None`. `BashTool` registers (or
  re-registers — a fresh `BashTool` instance is built per call, so this must be idempotent) a
  `"SessionTerminal"` provider whenever it creates or reuses a persistent shell; the provider
  reads the `PersistentShell`'s own `alive`/`cwd` state and returns `None` once the shell has
  died, which is what makes the interjection stop appearing — no separate unregister call exists
  or is needed. `PR #25`'s one-shot field and logic are untouched by this addition.
* Reasoning: The one-shot field's shape — set once, prepend once, clear — is a poor fit for "keep
  reminding the model every turn while X is still true," and shoehorning that into the same field
  (or a hand-rolled duplicate of the same "set a flag, prepend, clear" logic) would either lose
  the "every turn" property or require a second near-identical field for every future standing
  condition. A `Callable[[], str | None]` registered under a `subject` key generalizes over any
  number of standing conditions without `Session` needing to know what any of them mean.

  A closely related question was whether `Session` should instead read
  `tool_state["Bash"]["persistent_shell"]` directly rather than go through a registered callback
  at all. `Session.tool_state`'s own docstring documents it as "never read or written by
  `Session` itself" — deliberately opaque, tool-private bookkeeping — and reaching into a
  specific tool's private key from `session.py` would both break that documented invariant and
  require `session.py` to import from `klorb.tools.bash`, which would be circular (`bash.py`
  already imports `SessionConfig` from `klorb.session`). The registered-callback design avoids
  both problems: `Session` stays generic (it doesn't know or care that `"SessionTerminal"` means
  "a bash shell," only that it's a subject with a provider), and the only thing crossing the
  module boundary is a bound method/closure, not a private data shape.

  `Session.register_teardown(subject, teardown)` — a small, symmetric addition landing alongside
  this one for `Session.close()` (killing a live persistent shell when the owning `Session` is
  discarded, e.g. `/clear`) — follows the exact same reasoning and the same re-register-to-
  overwrite idempotency contract, for the same circular-import/opaque-tool_state reasons, even
  though earlier drafting notes for this feature had sketched `Session.close()` as reaching into
  `tool_state["Bash"]["persistent_shell"]` directly. Keeping `Session` itself fully generic about
  what any registered subject *is* was judged worth the small extra indirection, rather than
  special-casing one tool's shutdown path while keeping every other cross-tool interaction
  generic.

  Prepending every standing interjection on *every* turn while its condition holds (rather than
  throttling or deduplicating repeats within a session) is a deliberate, documented simplification
  for v1: it's the least surprising behavior, needs no extra "have I already reminded the model
  recently" bookkeeping, and can be revisited if it proves noisy in a long session with many
  turns — see docs/plans/archive/005-session-scoped-bash-terminals.md's "Out of scope" section.

  The registry itself is unchanged by
  [the structured tool-response envelope ADR](wrap-tool-responses-in-a-structured-json-envelope.md):
  it now has two delivery mechanisms for the same polled providers (the XML
  `<SystemInterjection>` block onto a user-turn prompt described above, and a JSON
  `system_interjections` list attached to the first `tool_response` envelope of each
  `_run_tool_calls` round), not a design change to `register_standing_interjection` or how a
  provider is polled.
