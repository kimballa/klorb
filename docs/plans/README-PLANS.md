
# Plans

This directory contains planned projects that can be performed by an agent. They may
reference or rely upon specs and ADRs that describe the existing state of the repo,
but text in this directory generally describes features that do not *yet* exist, but
will, with your help.

Rules:

* Do not implement plans without permission. 
* Do not implement plans in the `drafting` folder. They are not yet ready.
  * Do not read anything in the `drafting` folder unless explicitly asked. Do not
    use these documents to inform the context on how to impement anything else, as
    none of this is locked in to be depended upon.
* Only implement plans (with permission) that you find in the `ready/` folder.
* Each plan is numbered, and may depend on earlier plans. Get confirmation if not executing
  on the lowest-numbered plan in the `ready/` folder.
* When a plan is implemented, the relevant and durable aspects of the plan can be moved 
  into a spec. The plan file itself should be moved to the `archive/` subdir with `git mv`.
* After planning docs are moved to the archive dir, they are no longer modified, even if
  later decisions cause the plan to be out-of-date with respect to the source code. That's
  OK -- the plan was a planning doc for a task, not a specification to be maintained in
  sync with the codebase.
