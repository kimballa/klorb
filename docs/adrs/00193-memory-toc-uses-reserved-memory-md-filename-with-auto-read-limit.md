2026-08-15

## Question

A model can accumulate many memory files per namespace, but the Memories interjection only ever
shows filenames and one-line topics — the model needs an extra `ReadMemory` round trip to learn
which memories matter for a given task, or how they relate to each other. How should klorb give
it a durable, low-cost index over each namespace's memories, without growing the interjection
unboundedly or adding a new tool/schema?

## Answer

Reserve the filename `MEMORY.md`, in each namespace, as a freeform table of contents — an
ordinary memory file, created/edited with the existing `CreateMemory`/`EditMemory` tools, with
no schema of its own. `SessionMemoryMixin` reads its leading 50 lines automatically into the
`Memories` interjection every session, alongside the filename/topic catalog. Once `MEMORY.md`
reaches 45 lines, `CreateMemory`/`EditMemory` attach a warning to their result urging the model
to compact it down or move detail into a separately-named memory file that `MEMORY.md` points to.

## Reasoning

Reusing the existing memory tools and file-format conventions (bare filename within a namespace,
first line as topic) means no new tool, permission rule, or JSON schema is needed — `MEMORY.md`
is special only in the two places that read/warn about it. The 45-line warning threshold sits
five lines below the 50-line auto-read cap so a model that keeps writing after the warning still
has a turn or two of margin before content silently drops out of the interjection, rather than
warning only after the overflow has already happened.
