# WakeUpTimer: one-shot timer tool for agents

Harness — `klorb/src/klorb/tools/`, `klorb/src/klorb/hooks/timer_events.py`, `klorb/src/klorb/session/mixins/core.py`

## Task

Add a `WakeUpTimer(delay_seconds, self_prompt)` tool that lets an agent schedule a one-shot
timer event during its session. When the delay expires, the session receives the `self_prompt`
text as an injected message (event delivery), waking the agent with a self-chosen prompt.

Constraints:
* `delay_seconds` must be bounded: 60 ≤ delay_seconds ≤ 3600.
* The timer is one-shot: it fires exactly once, unlike the existing recurring
  `TimerEventConfig`/`TimerScheduler` infrastructure.
* The tool should integrate with the existing `Timer` event dispatch path in
  `Session._dispatch_timer_event` (`klorb/src/klorb/session/mixins/core.py`) and the
  `TimerScheduler` (`klorb/src/klorb/hooks/timer_events.py`) rather than building a
  parallel scheduling mechanism.
* Each tool call creates a dynamically-added `TimerEventConfig` entry (interval set to
  `delay_seconds`; the entry is removed or disabled after it fires once so it doesn't
  recur). The existing `TimerEventConfig` model in `klorb/src/klorb/hooks/config.py` may
  need a one-shot field (e.g. `one_shot: bool = False`) to signal that the entry should
  not reschedule itself after firing.
* The `self_prompt` becomes the event's `action.prompt` value (action type `"chat"`).
* Unit tests should cover: the delay bounds validation, the one-shot behavior (fires once
  then stops), correct prompt delivery to the session, and that recurring `Timer` entries
  still reschedule normally.

## Context

* Existing timer infrastructure: `TimerEventConfig` (config model, supports
  `interval_minutes`/`cron`), `TimerScheduler` (background-thread scheduler that
  re-arms after each fire via `_schedule_next`), `Session._dispatch_timer_event`
  (delivery into session conversation).
* `MIN_EVENT_DEBOUNCE_SECONDS` (10.0) is the existing floor for timer intervals;
  the WakeUpTimer's minimum delay (60s) is well above it, so no clamping should be needed.
* Tests live in `klorb/tests/klorb/hooks/test_timer_events.py` and
  `klorb/tests/klorb/session/test_hooks_timer_events.py`.
* TODO.md bullet: `(#agent) One-shot timer events` in section
  `Agent / Harness > Feature backlog`.
