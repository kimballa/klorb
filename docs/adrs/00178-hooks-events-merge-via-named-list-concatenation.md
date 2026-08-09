# `hooks`/`events` config layers merge via named-list concatenation, a new fifth variant

* Date: 2026-08-08
* Question: `hooks`/`events` are each an object keyed by hook/event name, holding a list of
  handler configs per name. `SessionConfig`'s existing merge behaviors
  (docs/specs/process-and-session-config.md) cover scalar override, list concatenation on a fixed
  set of keys (`readDirs`'s `deny`/`ask`/`allow`), key-by-key merge (`setEnv`), and
  always-fresh-object replacement (`ThinkingEffort`). None of those fit an object whose *set of
  keys* isn't fixed in advance (any `HOOK_NAMES`/`EVENT_NAMES` entry can appear) while each key's
  *value* should still concatenate across layers the way `readDirs` does. How should a later
  config layer's `hooks`/`events` combine with an earlier layer's?
* Answer: A new, fifth cross-layer merge variant: named-list concatenate. For each hook/event name
  a layer's `hooks`/`events` object mentions, that layer's list is appended to whatever list
  earlier layers already built for that same name. Implemented once
  (`klorb.hooks.merge.concatenate_named_handler_lists`) and reused for both keys, rather than two
  near-identical merge loops.
* Reasoning: `readDirs`'s three-subkey concatenation already established that "later layers add
  policy, they don't replace it" is the right default for permission-adjacent lists — a user
  config shouldn't have to repeat a system config's hooks just to add one of its own. Hooks/events
  differ from `readDirs` only in that the key set is open-ended (any hook/event name) instead of
  three fixed subkeys, so the same concatenation idea generalizes directly rather than needing a
  different shape. Fixed linear order within a name (layer order, then authoring order) falls out
  of implementing this as a straightforward per-layer append, and is deliberately *not* promised
  as part of the contract — see docs/specs/hooks-and-events.md's "Merge behavior" section.
