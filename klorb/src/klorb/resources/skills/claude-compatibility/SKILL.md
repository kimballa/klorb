---
name: claude-compatibility
description:
  'Enable klorb''s Claude Code compatibility flags (compatibility.claudeMarkdown,
  compatibility.claudeSkills) in this workspace''s klorb-config.json, based on which Claude Code
  files the repo actually has. Use when the user runs /claude-compatibility, or after the
  built-in claude-compat onSessionStart hook suggests it.'
---

# Enabling Claude Code compatibility flags

## 1. Detect which flags apply

* `${workspaceRoot}/CLAUDE.md` exists (a file) → `compatibility.claudeMarkdown` should be `true`.
* `${workspaceRoot}/.claude/skills/` exists (a directory) → `compatibility.claudeSkills` should be
  `true`.

If neither is present, tell the user there's nothing to change and stop.

## 2. Escalate privileges

`${workspaceRoot}/.klorb/` is a privileged directory. Call `EscalatePrivileges(scope="workspace")`
before touching `${workspaceRoot}/.klorb/klorb-config.json`.

## 3. Read-modify-write the config file

`ReadFile` `${workspaceRoot}/.klorb/klorb-config.json` if it exists. It's a top-level JSON object
(see docs/specs/process-and-session-config.md's "On-disk key naming") — `compatibility.claudeMarkdown`/
`compatibility.claudeSkills` are flat top-level keys, not nested under `sessionDefaults`. Add or
update only the keys from step 1, preserving every other key already in the file untouched.

If the file doesn't exist yet, create it with just the schema envelope and the applicable flags:

```json
{
  "schema": {"name": "klorb-config", "version": "1.0.0"},
  "compatibility.claudeMarkdown": true
}
```

Write the result back with `CreateFile`/`EditFile`.

## 4. Confirm

Tell the user which flag(s) you set.
