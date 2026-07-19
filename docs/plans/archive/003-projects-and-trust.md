
# Plan 003: Project-based Trust Settings

The main config for klorb at the project level lives in the `.klorb` folder of the project root.

However, we need to know if we trust it, first.

In KLORB_DATA_DIR, we need a file called `projects.json`:

* it has the schema `klorb-projects`, version 1.0.0.

It has the following format:

```json
{
    schema: /* the usual, per avbove */

    projects: [
        {
            id: "some uuid",
            path: "/actual/path/to/the/project/root",
            trusted: true|false
        }

    ]

}
```

You assign the uuid for a project using a random uuid4 or uuid7.
We may later store project-specific system config in `${KLORB_DATA_DIR}/projects/<uuid>`,
but not right now.

## When you first open a project

* when you start klorb it attempts to identify the workspace root.
  * Read the list of known project roots from the projects.json.
  * If we are in a dir identified as a project root therein, that's our project.
    * That tells us whether or not we trust the local project.
    * Keep the `trusted` boolean on the ProcessConfig. Under no circumstances can this be loaded from a config file.
    * If we do not trust the current project, do *not* load the project's `.klorb/klorb-config.json`.
  * If the cwd is not listed in the projects list, then see if any ancestor dir is the project root
    for a mentioned project. If so, use it.
  * If none of the ancestor dirs of the cwd are listed in the `projects` json, then scan the cwd
    and its ancestor dirs for a dir with a `.klorb/` child dir.
    * e.g., if you are in `/home/aaron/some/deep/path`, and `/home/aaron/some/.klorb/` is a dir that exists,
      then `/home/aaron/some` is the project root dir.
  * if none of those ancestors had a .klorb child dir, then the cwd is the de facto project root.
* Once a project root has been determined:
  * If the projects.json has an entry for it, read the `Workspace` object out of it
    and attach it to the process config.
    * If the project is not trusted (Workspace.trusted is false) then put a msg in the history for
      the user: "The workspace at `<path>` is not trusted. Run `>Trust workspace` to change this."
    * Otherwise say "Working in project: `<path>`" in the history.
  * If no json entry, then pop up a choice for the user: "You are working in `<project root path>`. Open as a project?"
    * Text underneath it explains "Projects have persistent settings files and permissions."
    * Yes/No modal
    * If yes, then we are going to write a config file and create a record in `projects.json`.
    * If no, then everythign is just going to be in-memory in the ProcessConfig.
    * Define a new `Workspace` obj and store at `ProcessConfig.workspace`.
    * The is-it-a-project bool is then stored in `ProcessConfig.workspace.is_project` boolean; don't let
      PC.workspace or its children be loaded from a config file.
    * Next, pop up a choice for the user: "Do you trust the workspace at `<project root path>`?"
      * The user must select Yes or No.
      * If is_project_workspace, Create a new `project` record in projects.json with a new uuid and store
        the `trusted` flag there, as well as in `ProcessConfig.workspace.trusted`.
      * If not, just store the trusted flag in the ProcessConfig.workspace.

Note that all of the above refers to activities taken directly by the harness code.

The user should see TUI prompts from the app that leads to the flags being set.

The hard gates on dir access should be denying the agent any access to the directory where projects.json
lives, so the above cannot be mediated by the agent itself.

The management of the project.json file and all access to it (reads and writes) should be managed
through the `Workspace` class in `workspace.py`.

* There should be a `Workspace` class with properties that track things like uuid, root path, etc. for a project / workspace,  
  as well as the `is_project` and `trusted` flags.
  * If is_project, then it'll have a permanent record in projects.json.
* There should be a `TrustManager` class that actually owns projects.json. All I/O to projects.json goes thru a singleton
  TrustManager instance. This ensures we don't have dual writers clobbering the file.
  * It can load data from the file and return a Workspace object that is attached to the ProcessConfig.
  * If the PC.Workspace state changes, we can use the TrustManager to write it back to the json file.

## Initializing a new project

### Config file initialization

If we are in a new workspace and `is_project_workspace`, we should create a default settings file.

mkdir `${projRoot}/.klorb/` and create klorb-config.json

* by default allow file read access to the workspace root.
* Also burn in the currently-active model name to the default session config in the file.
* Allow file write/create access to the workspace root only if `trusted` is true.
  * Leave the writeDirs blank if not trusted.

### In-memory initialization

#### untrusted defaults

If the workspace is *not* trusted:

* DO NOT read $projRoot/.klorb/klorb-config.json. We always skip this file. We do not trust it.
  The code that loads config layers and overlays them must not load the in-project config.
  * The code that loads system prompt layers also must not load system prompts from untrusted projects.
    We will rely only on the user-level overrides or our own default system prompt layers.

* In-memory session config defaults include:
  * Allow reads inside the project root
  * Ask regarding writes inside the project root. (ok to leave setting effectively empty; ask is default.)

#### trusted defaults

If the workspace *is* trusted:

* Config files and system prompts MAY be read from projRoot/.klorb/.
  * Try to load them. The previous 'Config file initialization' step should have added one, if
    we were allowed to do so.
* If there is no project-level config file, we will set up default dir permissions in memory:
  * We allow reads inside the project root
  * We allow writes inside the project root.

## Changing their mind

If the user does not trust the current workspace, then a palette command `Trust workspace` should be
available on the palette. When run it pops up an "are you sure" yes/no modal. If no, take no action.
If yes, change `PC.workspace.trusted` to true, and rewrite it into the projects.json file.

Then reload the config now that we are allowed to load config files from the project.

Put a msg in the history saying "Trusted workspace `<path>`."

If there is no .klorb/k-config and this is a project (Workspace.is_project) and it is now trusted,
then ask yes/no prompt modal whether to init the project config. If yes, then write the current
PC.sessionDefaults into the project's .klorb settings file, which may include some allow/ask/deny
prompts that the user built up over the course of the session before deciding to trust the project.
