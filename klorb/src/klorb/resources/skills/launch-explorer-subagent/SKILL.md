---
name: launch-explorer-subagent
description: 'Launch an Explorer subagent to answer a bounded research question without consuming
  your own context window. Use when a question requires reading many files, searching the codebase,
  or fetching web pages, and you want the answer back without those tool calls filling up your
  context.'
---

# Launching an Explorer subagent

An Explorer subagent is a read-only research assistant that you can launch to answer a bounded
question. It gets its own context window and its own tool calls, so the work it does doesn't
eat into yours. Its report will be relayed back to you.

## When to launch one

Use an Explorer when you need to:

* Understand how a piece of the codebase works before writing code, and the answer requires
  reading many files.
* Search for a pattern, usage, or definition across the project without filling your own
  context with search results.
* Fetch and summarize a web page or documentation without bloating your conversation.
* Delegate a self-contained research subtask so you can work on something else in parallel.

Do **not** launch an Explorer when:

* The question can be answered by reading one or two files — just read them yourself.
* You need the subagent to modify files — Explorer is read-only.
* You're already near the concurrency or depth limits — check before launching.

## How to launch one

Call `CreateSubagent` with:

* `role`: `"explorer"`
* `session_title`: a short, descriptive title (e.g. "Find how config merging works")
* `initial_message`: a clear, self-contained question or research task. Include the specific
  files, directories, urls, or search terms you want investigated. The more precise the prompt, the
  better the report.

The Explorer will work asynchronously. You can:

* Continue working and let the result arrive as a `SystemInterjection` when it's done.
* Call `WaitForSubagent` if you need the answer before continuing. This tool will pause your session so use it only if you do not have anything else to do in the meantime.
* Call `MessageSubagent` to send a follow-up question after the Explorer finishes.

## Composing the initial message

The Explorer only sees what you tell it — it has no memory of your conversation. Write the
`initial_message` as a standalone request:

* State the question clearly.
* If you have multiple / compound questions, number them and direct the subagent to use corresponding numbered headings in its response.
* Point the explorer at specific files, directories, or web urls if you already know where you want it to look.
* Tell it what format you want the answer in (e.g. "list the files and their roles", "explain
  the data flow", "summarize the differences between X and Y").
* If there are constraints (e.g. "only look at Python files", "ignore tests"), say so.
* Be specific about the level of detail you require. If you only need a filename and line number where some piece of primary information can be found, say so. If you want greater exposition, state what kind of extra content is helpful. If you need a report, state a word or token limit budget for the Explorer to fill.

## Example

```python
CreateSubagent(
    role="explorer",
    session_title="Map config loading chain",
    initial_message="Trace how klorb-config.json is loaded and merged into ProcessConfig. "
        "Start from klorb/config.py and follow the chain. Report: (1) which files are involved, "
        "(2) the merge order, (3) which keys override which. "
        "Use no more than 500 words in your report."
)
```
