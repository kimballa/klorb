
* send_one_shot should actually run a whole session (non-interactively) until it gets a 'finished'
  state rather than just sending a single msg turn for a single response.
* Need a ProviderFactory
    * Produces ApiProviders from a string
    * Currently only openrouter api provider is supported from "openrouter" string.
    * model names now can be fully-qualified model name (fqmn): e.g.: "openrouter:gpt-4o-mini"
    * Session can get the current Provider from the ProviderFactory.
    * /clear to create a new session keeps the same model name (and thus model, provider) as last session.
* EditFileTool
* CreateFileTool
* ListDirTool
* AskUserQuestionsTool
* 