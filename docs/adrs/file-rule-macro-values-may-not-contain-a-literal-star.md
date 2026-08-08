# Expanding a config macro into a `readFiles`/`writeFiles` rule rejects a value containing `*`

* Date: 2026-08-07
* Question: `FileAccessTable`/`klorb.sandbox.compute_sandbox_dirs` both classify a
  `readFiles`/`writeFiles` rule as wildcard-vs-exact purely by checking whether its final
  string contains `*` (`is_wildcard_rule`, `klorb.permissions.file_access`) — there is no escape
  syntax (no `\*`) for a literal `*` in this grammar (see
  docs/adrs/file-rule-wildcard-star-matches-any-characters-including-slash.md). If `${home}` or
  `${workspaceRoot}` ever expanded to a value that itself contains a `*` character (an unusual
  but legal POSIX path component), macro expansion could silently flip a plain, exact-match rule
  into a wildcard one, or silently widen an already-wildcard rule's matched set — in either
  direction, changing what a `deny`/`allow` rule actually matches without the rule's author
  typing a `*` themselves.
* Answer: `klorb.config_macros.expand_macros` takes a `forbid_char` parameter; `readFiles`/
  `writeFiles` expansion passes `forbid_char="*"`, so substituting a macro whose resolved value
  contains `*` raises `MacroExpansionError` instead of expanding — which, per
  docs/adrs/malformed-config-macro-drops-the-whole-layer.md, drops the whole layer with an
  error. `readDirs`/`writeDirs` (no wildcard grammar at all) and `setEnv` (a plain string value,
  not matched against anything) pass no `forbid_char` and are unaffected.
* Reasoning: This closes the hole without touching `file_access.py`'s matching logic or
  inventing an escape syntax the on-disk grammar doesn't have: since `${home}`/`${workspaceRoot}`
  each resolve to one fixed value per process, refusing to substitute a value containing `*`
  guarantees a successful expansion never introduces a `*` the rule's author didn't write —
  `is_wildcard_rule`'s "does the final string contain `*`" check stays correct with zero
  downstream changes. The alternative (adding a `\*` literal-star escape to the file-rule
  grammar) would be a much bigger, user-facing change to an already-shipped wildcard grammar, to
  guard against a substitution value that will essentially never actually contain `*` in
  practice.
