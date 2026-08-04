---
name: debug-with-evidence
description: >
  How to debug a failing test, a bug report, or unexpected behavior fast: form a falsifiable
  hypothesis, then get evidence for or against it by running something (a narrowed test, a
  reproduction script) or instrumenting the code with a log line, rather than reasoning about
  the cause in the abstract. Use as soon as you notice you're theorizing about why something
  fails instead of checking. Also covers when to hand a step to the user because you cannot
  perform it yourself (a GUI, a browser, a device).
---

# Debugging: evidence over reasoning

A few `logger.debug()` calls and a test run tell you more than an extended chain of reasoning
about what the code "must" be doing. Reasoning about unobserved state is guessing with extra
steps — it feels like progress because it produces text, but it doesn't narrow anything down.
Running something does. Default to the cheapest action that produces a real observation, not
the longest chain of thought that produces a plausible-sounding one.

## The loop

1. **State one falsifiable hypothesis.** Not "something's wrong with the config merge" — pick
   the single specific claim you can check next: "`SessionConfig.workspace.trusted` is `False`
   at the point `build_catalogs()` runs, so the workspace tier is skipped." If you can't state
   what observation would prove it wrong, you don't have a hypothesis yet, you have a vibe —
   narrow it until you do.
2. **Pick the cheapest check that could falsify it.** In order of preference:
   * A test that already exists and exercises the path — run just that one.
   * A one-line `logger.debug()` (or `print()` in a throwaway script) at the exact point your
     hypothesis makes a claim, then run the smallest thing that reaches it.
   * A tiny reproduction script in the scratchpad, when no existing entry point reaches the
     code path in isolation.
   Don't reach for a debugger session or a wide instrumentation pass before trying the smallest
   thing that could settle the one claim you just made.
3. **Run it and read the actual output.** Not what you expect it to say — what it says. A
   result that confirms the hypothesis narrows the search; a result that contradicts it is
   equally valuable and cheaper to act on than another round of reasoning would have been.
4. **Update and repeat.** Each iteration should change what you believe or where you're
   looking. If two iterations in a row leave your belief unchanged, the instrumentation isn't
   at the right point — move it closer to the divergence, not further into speculation about
   where the divergence might be.

Stop reasoning and go to step 2 the moment you catch yourself building a theory instead of
checking one. That catch is the signal, the same way second-guessing a settled decision is the
signal to ask rather than think harder (see `default_sys.md`'s "Deciding vs. asking").

## Running things

* **Narrow the test first.** Don't run the whole suite to chase one failure. Check whether the
  project's test runner exposes a way to select one test and surface its captured
  log/print output (e.g. pytest's `-k <name> --show-capture=log`, or the equivalent for
  whatever test runner and Makefile/task-runner target the project actually uses) and use it —
  seeing the one failing test's own output, including the log lines you just added, is worth
  far more than a full-suite run's noise.
* **Bisect instead of scanning.** For "it broke somewhere in this range" (a git range, a call
  chain, a data pipeline stage), add one probe at the midpoint rather than eyeballing the whole
  span. Each probe should roughly halve the space you still have to explain.
* **Write throwaway reproduction scripts freely.** A five-line script that calls the one
  function you're suspicious of, with the exact input from the bug report, beats reasoning
  about what that function does with that input. Clean it up before declaring the task done —
  see `default_sys.md`'s "Ground in reality" on using throwaway scripts and temporary code
  augmentation to learn.
* **Prefer logging over stepping.** klorb has no interactive debugger tool. A `logger.debug()`
  (or a temporary `print()`) at a decision point, run once, gives you the same answer a
  breakpoint would — and the transcript of what you tried stays readable afterward, in the
  scratchpad or the run's own log output, instead of living only in a debugger session nobody
  can replay.
* **Remove throwaway instrumentation once it's answered its question**, the same as any other
  throwaway script — unless the line is worth keeping on its own merits (it marks a
  consequential action: a system-level side effect, a permission decision, a step in a loop
  over many items), in which case leave it as a real `logger.debug()` call instead of deleting
  it, matching the surrounding project's own logging conventions.

## When you can't run it yourself

Some evidence is outside what a headless agent can gather: a VS Code extension's actual
behavior inside the VS Code UI, a rendered webview, a browser-only repro, a physical device, or
anything that needs a human to click through a GUI and describe or screenshot what happened.
Don't spend tokens theorizing about what a UI probably looks like or does — ask.

Use `AskUserQuestions` with an empty `options` list (a free-text ask) to hand the step to the
user as your hands: tell them exactly what to run or click, and exactly what you need back (a
screenshot, a pasted error, a copy of a panel's content, whether a button was enabled). This is
not a fallback of last resort to feel bad about — it is the cheapest correct move once you've
identified that the next piece of evidence genuinely requires eyes or hands you don't have.
Keep the ask narrow and concrete ("Open the extension in the Extension Development Host, run
the failing command, and paste the Output panel's `klorb` channel here") rather than a vague
"can you check if this works."

## Recording what you learn

Use the scratchpad to track hypotheses tried and their outcomes on anything longer than a
couple of iterations — "confirmed: X is null at Y" or "ruled out: Z runs before W, not after"
is worth more than re-deriving the same fact from scratch two iterations later, and keeps a
long debugging session from silently repeating a check it already ran.
