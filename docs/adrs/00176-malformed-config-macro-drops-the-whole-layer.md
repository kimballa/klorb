# A malformed or unrecognized `${...}` config macro drops its entire config layer

* Date: 2026-08-07
* Question: `readDirs`/`writeDirs`/`readFiles`/`writeFiles` rule paths and `setEnv` values
  support `${home}`/`${workspaceRoot}` macro expansion (`klorb.config_macros.expand_macros`,
  called from `klorb.process_config.load_process_config`). What should happen when a config
  layer contains a typo'd macro name, an unterminated `${`, or an empty `${}`?
* Answer: The entire layer that reference came from is dropped — exactly like a layer that
  isn't valid JSON at all (`klorb.schema_envelope.parse_versioned_json`) — with an error logged
  and collected into `ProcessConfig.config_warnings`, annotated with a line/column excerpt via
  `klorb.json_error_display.format_json_error_context`. Nothing about the malformed reference is
  silently left unexpanded or substituted with an empty string.
* Reasoning: `readFiles.deny`/`readDirs.deny` are security-relevant. A `deny` rule containing a
  macro that silently failed to expand — left as literal `${home}` text, or blanked to `""` —
  would silently stop matching anything it was meant to block, which fails open at exactly the
  moment a config author's typo is most dangerous. Failing to load the layer at all, loudly, is
  the only outcome that can't be mistaken for "it's working." An empty-string substitution is
  its own hazard even outside `deny` rules: `${workspaceRoot}` silently becoming `""` turns
  `${workspaceRoot}/secrets` into the absolute path `/secrets`, not a no-op. Dropping the whole
  layer (rather than only the one malformed key) matches how a JSON syntax error is already
  handled and keeps the failure mode consistent and easy to explain: "a layer is valid JSON with
  well-formed macros, or it doesn't load."
