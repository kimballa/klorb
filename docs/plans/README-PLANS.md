
# Plans

This directory contains planned projects that can be performed by an agent. They may
reference or rely upon specs and ADRs that describe the existing state of the repo,
but text in this directory generally describes features that do not *yet* exist, but
will, with your help.

## Basic Rules

* Do not implement plans without permission.
* Do not implement plans in the `drafting` folder. They are not yet ready.
  * Do not read anything in the `drafting` folder unless explicitly asked. Do not
    use these documents to inform the context on how to impement anything else, as
    none of this is locked in to be depended upon.
* Only implement plans (with permission) that you find in the `ready/` folder.
* Plans have id numbers which should be unique. If duplicate numbers exist, alert
  the user for reconciliation.
* After planning docs are moved to the archive dir, they are no longer modified, even if
  later decisions cause the plan to be out-of-date with respect to the source code. That's
  OK -- the plan was a planning doc for a task, not a specification to be maintained in
  sync with the codebase.

## Top-level Workflow

* Plans that are not yet ready for execution are in the `drafting` folder.
* Plans are moved by the user into the `ready` folder when drafting is complete.
* Agents implement plans one at a time, at human direction.
  * Each plan is numbered, and may depend on earlier plans. Get confirmation if not asked
    to execute on the lowest-numbered plan in the `ready/` folder.
* As a plan is implemented, the relevant and durable aspects of the plan can be moved
  into a spec. The plan file itself should be moved to the `archive/` subdir with `git mv`.
* If the plan has a "Future work" section (or similar) describing follow-up ideas that are
  out of scope for the current implementation, log each of those items in `TODO.md`,
  referencing the plan by number (e.g. "plan 013") so a reader can trace the item back to
  its source plan.

## How to Implement

* Plans describe a mix of higher-level features / user stories / user journeys that should
  be accomplished, as well as specific technical mechanisms of implementing the features,
  and/or technical enhancements to the system as a whole. (i.e. "platform features" that
  will be taken advantage of by later user-facing features.)
* Read the whole plan before implementing it.
* Make a thorough search of the codebase as well as specs and ADRs before implementing.
* Take a step back and perform an architecture review of the plan.
  * It is important that the implementation of the plan "fit in" with the codebase. If
      the plan implies an approach that would look out-of-step with the rest of the
      codebase, say so. It may be that this is the first stage of a refactor which will be
      upgrading the codebase to a new style or architecture or pattern. Or it may also be
      that the user was confused when writing the plan, and you could point out a better
      way to do this.
  * Consider implementation patterns, as well as data / domain model.
  * Consider modularity and encapsulation.
  * Consider python packages and modules and hierarchical navigability.
  * For example, PLAN-003 proposed putting a `Workspace` object on the ProcessConfig.
      This includes a `workspace.path` element describing where the workspace lives.
      There was already a `workspace_root` field on the SessionConfig, though. These
      were redundant, and we wound up removing workspace\_root and moving the workspace
      over as a field of SessionConfig. An architecture review could have caught that
      data modeling error before the implementation.
* After the architecture review, raise any points of concern or clarification with the
  user.
* Break the work down into a collection of discrete todo-list items for yourself to perform.
* Execute the todo-list.
