

# Bugs:

* KLORB_CONFIG_DIR/KLORB_STATE_DIR/KLORB_DATA_DIR are eager-computed from the environment
  on module load, before load_dotenv() runs, so they cannot be shadowed in a `.env` file.

# Feature backlog

* Tools
    * Test that ReadFile tool works.
      * And uses the max-lines from the settings
    * EditFileTool

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

* Permissions
    * what dirs can it access
    * what bash commands can it run (or not)
    * what web sites can it access? (... what kind of prompt injection could happen here?)
* BashTool
