# `Timer` events are best-effort, not a real persistent scheduler

* Date: 2026-08-08
* Question: `events.Timer` lets a user configure an `interval_minutes`/`cron` schedule. Should
  klorb guarantee a `Timer` entry fires at its scheduled time even if no klorb process for that
  workspace happens to be running at that moment — the way a real cron daemon or systemd timer
  would — or is a missed fire while nothing is running simply dropped?
* Answer: Best-effort only. A `Timer` entry fires while some klorb process for the workspace
  already happens to be running for other reasons (an open TUI session, a connected ACP client);
  a scheduled fire time that elapses while nothing is running is simply missed, never queued or
  caught up on restart.
* Reasoning: Nothing in klorb today stays running independent of a live TUI session or a connected
  ACP client — `klorb server` exits the moment its one client disconnects
  (docs/specs/klorb-server.md), and there is no persistent daemon mode. Building one just to make
  `Timer` a real scheduler would pull a process-lifecycle feature (detach, survive client
  disconnects, systemd-friendly startup) into a hooks/events plan where it doesn't naturally
  belong, and isn't scoped to a concrete need yet. `TimerScheduler` is implemented as a purely
  in-process `threading.Timer` loop instead, matching the lifetime constraint honestly rather than
  overselling reliability the process model can't back up. `docs/user/hooks.md` states this
  plainly so a user configuring `Timer` doesn't mistake it for real cron. A genuine persistent
  daemon mode remains open future work (docs/specs/hooks-and-events.md's "Out of scope" section)
  once there's a concrete need for it.
