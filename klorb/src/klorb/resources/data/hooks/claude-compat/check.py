# © Copyright 2026 Aaron Kimball
"""`onSessionStart` hook handler: on a newly-bootstrapped, trusted workspace, suggests enabling
`compatibility.claudeMarkdown`/`compatibility.claudeSkills` when a `CLAUDE.md` file or
`.claude/skills/` directory is present.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    hook_input = json.load(sys.stdin)
    if not hook_input.get("workspace_just_bootstrapped") or not hook_input.get("workspace_trusted"):
        print(json.dumps({}))
        return 0

    workspace_root = Path(hook_input["workspace_root"])
    has_claude_markdown = (workspace_root / "CLAUDE.md").is_file()
    has_claude_skills = (workspace_root / ".claude" / "skills").is_dir()
    if not has_claude_markdown and not has_claude_skills:
        print(json.dumps({}))
        return 0

    flags = []
    if has_claude_markdown:
        flags.append("compatibility.claudeMarkdown")
    if has_claude_skills:
        flags.append("compatibility.claudeSkills")
    log = (
        f"This workspace looks like it uses Claude Code ({' and '.join(flags)} may apply). "
        "Run /claude-compatibility to set the matching klorb-config.json flags."
    )
    print(json.dumps({"log": log}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
