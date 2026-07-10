
# Memories

A way for the agent to make permanent notes to itself about things in the current workspace/project,
or the broader user / homedir environment.

## Tools

This introduces the following tools

* ListMemories - enumerate the various memories available.
* SearchMemories - find memories that recall various keywords
* ReadMemory - read a memory file
* EditMemory - update a memory file
* CreateMemory - create a new memory file.
* DeleteMemory - remove a memory file.

## Memories are files

Memories are stored in markdown files in well-defined directories:

* `global` memories are stored in `$HOME/.local/share/klorb/memories/`
* `workspace` memories are stored in `${workspaceRoot}/.klorb/memories/`

The agent does not think of these as specific paths, though. The agent thinks
about two _flat_ namespaces of files.

* The tools each take a `namespace` argument of either `global` or `workspace`
* The tools take a `filename` argument for a `name-of-a-memory.md` file within
  the specified namespace.
  * The path separator character is forbidden, as are any attempts to escape the
    relevant `..../memories/` dir through `..`, etc.

The Create, Read, and Edit tools operate through the same common basis as 
CreateFile, ReadFile, EditFile already do with the shared componentry for these
tools already shared with e.g. ReadScratchpad and EditScratchpad.

The 'create' file machinery has not been abstracted away from CreateFile yet,
in the same way that ReadFileCore and EditFileCore have been abstracted out
from ReadFile and EditFile, but that is a task needed as part of this plan.

### File format

* In general memories are considered ordinary markdown files and the agent is free
  to record in them as it sees fit.
* The first line of the file is the _topic_ of the memory file.
* The first line must not be empty. A `CreateFile` or `EditFile` tool call
  that would cause the first line to be blank must fail, and provide an error
  back to the agent that informs it about this restriction.


## Listing memories

The ListMemories tool returns an object of the following format:

```json
{
    "global": [
        {
            "filename": "memory-filename-within-global-namespace.md",
            "topic": "First line of the file."
        },
        ... /* List all such files in the namespace. */
    ],
    "workspace": [
        /* Same format as `global` above. */
    ]
}
```

* If a memory file is empty, the `topic` should be the empty string `""`.
* If there are no global (or workspace) memories, then they are reported like `"global": []`
  or `"workspace": []`.


## Searching memories

The search process should operate similar to other Grep and SearchScratchpad
tools, accepting a list of `queries`. It should return structured json objects identifying all the hits, 
with both `namespace` and `filename` attributes so the agent knows where they
belong. The `filename` attr should just be the filename _within_ the namespace dir, not the 
complete path to the file. We **never** report the full `/path/to/memories/filename.md`.

The `filename` should also be the subject of the search. That is, the line-by-line `re.match` search
algorithm used e.g. by SearchScratchpad should also apply `re.match` to the filename itself, as this
is semantically meaningful.  In the case when the filename is the match (e.g. "you-like-birds.md"
should match the search string `bird`), report the first non-blank line of the file as the matching
line, regardless of whether the contents of that line actually matches the query. This search tool
therefore also has a bit of "find file"-like behavior to it.

## Tool permissions

Default permission for operations are controlled by the following config flags,
at the process level:

* `tools.memory.readPermission` - also governs ability to list and search memories.
* `tools.memory.editPermission`
* `tools.memory.createPermission`
* `tools.memory.deletePermission`

The value for each is one of `deny`, `ask`, `allow`. 

System defaults:
* read: allow
* edit: allow
* create: ask
* delete: ask

### Workspace memories are only accessible in trusted workspaces

The `.klorb/memories/` dir of **untrusted** workspaces are not accessible to the tools. 

* ListMemories returns an empty list for the `workspace` namespace.
* SearchMemories does not iterate over any of the files for searching.
* ReadMemory, CreateMemory, and EditMemory for a workspace memory are all instantly 
  permission-denied without appeal to the user.

## System prompt

The primary system prompt is altered to have a section about memories, explaining briefly 
what its purpose is (to remember important details of how to work within the workspace, or
user preferences or directives that span across all workspaces, in a way that is durable
across sessions).

## Specific test cases to implement

(TODO - to be populated by klorb)

## Specific TODO Items for Tasks to Implement Memories

(TODO - to be populated by klorb)
