# Subagents: `starting_task_id` field on CreateSubagent

Context: klorb harness (Python), subagent tooling, chainlink task-tracking integration. Feature
request carried over from "Plan 021: Subagents" follow-up work.

Add a `starting_task_id` field to `CreateSubagentTool`'s parameters
(`klorb/src/klorb/tools/subagents/create.py`, `CreateSubagentParameters`) that lets the parent
agent start a new subagent with a specific chainlink todo item pre-claimed, instead of the
subagent having to call `TodoCreate`/`TodoNext` itself once it starts.

Chainlink task tracking (see docs/specs/chainlink-task-tracking.md, "Task assignment" section)
already supports per-agent task labels and `TodoCreate`'s `assign_to` field, letting a parent
delegate a task to a specific subagent id. This feature folds that into `CreateSubagent` itself
as a convenience:

* Incorporate the claimed task's summary into the new subagent's first user prompt.
* If the task was labeled `all` (meaning any subagent may claim it), claim it first by removing
  the `all` label before the new subagent starts, so no other subagent poaches it during
  subagent startup.
* Explicitly set the `agent:(id)` label for the new subagent onto the claimed task in chainlink
  (decide whether this is done by the parent's `CreateSubagent` handler or by the child on
  startup, and document the choice).
