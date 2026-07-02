

# Bugs:

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

* "Whether a relative readDirs/writeDirs rule path (none of this doc's examples
  use one) resolves against workspace_root or something else isn't specified". These should
  always be canonicalized against the workspace root. e.g. Allow("..") really means the same
  thing as Allow("<workspace_root>/../).
  (This is called out as a gap in the bottom of permissions.md)

# Feature backlog

* project bootstrapping
  * when you start klorb it attempts to identify the workspace root.
  * if it can't find one, then the cwd is the workspace.
  * ask the user if they want to open the cwd as a project.
    * if no, do nothing. we only get default permissions of stuff.
    * if yes, create cwd/.klorb/ and write a config json in there
    * by default allow file read access to the workspace root.
  * ask the user if they trust the dir and want to allow writes 
    * if yes then allow file write/create access to the workspace root.
    * if no then put the workspace root down as 'ask' for writes. 
    * If no then clamp down reads to inside workspace root too, not just trusting its config file.
  * ... this trust question actually needs to happen the first time a given
    workspace is opened even if it already contained a config file (e.g., you download it from
    the internet and unzip a tarball with a /.klorb/ in it). so trust needs to be tracked in
    ~/.share/klorb/ or ~/.config/klorb/ or something external to the workspace itself.
    ("Trust" is its own whole thing that will need a dedicated plan, basically.)

* Tools
  * Test that ReadFile tool works.
    * And uses the max-lines from the settings
  * Test that EditFileTool works
  * Add some tool evals; see https://platform.claude.com/cookbook/tool-evaluation-tool-evaluation

* Add a basic system prompt to make this actually do coding things.

* mouse-based select/copy/paste doesn't work. (ctrl-x/c/v does though, and shift-l/r does select...)

* If we are streaming a response back from the agent, the ESC key should abort the
  response generation, and also put the most recent user prompt back in the textbox for editing.

* When we quit, ask if we should save the session state.
  If yes, then write a file that goes in `cwd`/.klorb/last-session.json
  storing last Session config  and the message history for the session.

  When we next load klorb in that same directory, auto-load the session state info from last-session.json
  and reconstitute the session.

  This json file should include the schema info:
  ```
  {
    schema: {
        name: "klorb-session",
        version: "1.0.0"
    },

    /* actual data here. */
  }
  ```

* "Set Thinking Effort" palette option should say currently-selected value in parens: ("... Effort (High)")
* send_one_shot should actually run a whole session (non-interactively) until it gets a 'finished'
  state rather than just sending a single msg turn for a single response.
* Need a ProviderFactory
    * Produces ApiProviders from a string
    * Currently only openrouter api provider is supported from "openrouter" string.
    * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
    * Session can get the current Provider from the ProviderFactory.
    * /clear to create a new session keeps the same model name (and thus model, provider) as last session.
* More tools:
    * CreateFileTool
    * ListDirTool
    * AskUserQuestionsTool
    * GrepTool
    * WebSearchTool
    * WebFetchTool

* Skills in <built-in-skills-dir>, ~/.klorb/skills, cwd/.klorb/skills/
    * Add general skills/know-how for writing docs/specs and docs/adrs/ files.
* Integrate with chainlink for todo tracking
    * TodoList tool
    * TodoWrite tool
* Memories in ~/.klorb/memory, cwd/.klorb/memory/
    * UpdateMemory tool
    * Remember tool
* Subagent spawning
* Agent teams
* Need a Planning Tool or Planning Mode agent

* Permissions
  * Need to handle `PermissionAskRequired` exception with a user prompt.
  * Need to handle extra safeguards for writing into ${workspaceRoot}/.klorb/. This is
    implicitly denied; we will add a separate EscalatePrivileges tool that will unlock
    the dir (with a user prompt) for writes through the end of the *turn*.
  * what bash commands can it run (or not)
  * what web sites can it access? (... what kind of prompt injection could happen here?)
  * Use bubblewrap via https://github.com/anthropic-experimental/sandbox-runtime ?
  * TOCTOU: every permission check (klorb.permissions.workspace/directory_access) resolves a
    path string at check time; nothing holds an open OS-level directory handle across the gap
    between that check and the actual file I/O, so a rename/symlink swap in that window could
    redirect an approved operation. Closing this needs os.open()-based fd-relative I/O
    (O_NOFOLLOW/O_DIRECTORY), not path-string re-resolution. See docs/specs/permissions.md.
  * The global klorb-config.json should include r/w denylist entries for "~/.ssh", "~/.aws",
    ... and probably a few other well-known secrets-oriented places, too?
  * the well-defined config/state dirs (see paths.py) should also be hard-blocked without
    EscalatePrivileges.

* BashTool
* Metacognition tools -- read config; update (in-memory) config; update config file(s)