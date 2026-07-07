# © Copyright 2026 Aaron Kimball
"""Detects whether `bwrap` (bubblewrap, https://github.com/containers/bubblewrap) can actually
sandbox `BashTool`'s subprocesses on this host, and will eventually build the `bwrap` argv that
does so. See docs/plans/ready/004-bash-permissions-and-bash-tool.md's "Layer 2: bubblewrap
sandbox (execution boundary)" section.

Building the actual `bwrap` argv (mount list, namespace unshares, env plumbing) is a stub for
now: developing and testing it requires a host where unprivileged user namespaces work, which
this project's own dev/cloud-agent environments do not provide (`bwrap_available()` reports
`False` there — confirmed directly; see the plan doc's "Known critical risk" note). `BashTool`
runs unsandboxed everywhere until that work lands; see `klorb.tools.bash`.
"""

import logging
import shutil
import subprocess
from typing import Literal

logger = logging.getLogger(__name__)

BWRAP_BINARY_NAME = "bwrap"

BwrapUnavailableReason = Literal["missing_binary", "no_userns"]
"""Why `bwrap_available()` returned `False`, for `BashTool`'s one-time fallback notice
(`detect_bwrap_unavailable_reason()`): `"missing_binary"` (not installed) needs a different fix
(`apt-get install bubblewrap`) than `"no_userns"` (installed, but this kernel/container policy
refuses unprivileged user namespaces) does (reconfigure the host or outer container) — see the
plan doc's "process outcome" section for the two distinct messages these map to."""

_SMOKE_TEST_ARGV = [
    BWRAP_BINARY_NAME, "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--", "true"]
"""A minimal `bwrap` invocation (no unshares at all) that only succeeds if this host permits
`bwrap` to create the implicit user namespace it always needs, regardless of which namespaces a
real invocation would go on to `--unshare-*`. Deliberately not a real sandbox shape (no
`--unshare-*` flags) — this is a capability probe, not something to reuse as-is for an actual
`BashTool` invocation once `build_bwrap_argv()` exists."""

_availability_cache: bool | None = None
"""Cached result of `bwrap_available()`'s smoke test, for the life of this process — the plan
calls for running it once (at session start, or lazily on the first `BashTool` call) and reusing
the boolean rather than re-probing on every command. `None` means not yet probed."""


def reset_availability_cache() -> None:
    """Clear the cached `bwrap_available()` result, so the next call re-runs the smoke test.
    Used by tests that need to simulate both an available and an unavailable host in the same
    process; production code never needs to call this."""
    global _availability_cache
    _availability_cache = None


def bwrap_available() -> bool:
    """Return whether `bwrap` can actually create a sandbox on this host right now: the binary
    exists on `PATH` and a minimal smoke-test invocation (`_SMOKE_TEST_ARGV`) succeeds. Cached
    for the life of this process after the first call — see `_availability_cache`.

    This is the single source of truth for "can `BashTool` sandbox its subprocess," per the
    plan's "Detection" section: no `/.dockerenv`/`cgroup` fingerprinting drives this decision,
    only an actual attempt to do the thing. Those heuristics are still useful for tailoring the
    unavailable-reason message (see `detect_bwrap_unavailable_reason()`), just never for the
    go/no-go decision itself.
    """
    global _availability_cache
    if _availability_cache is not None:
        return _availability_cache
    if shutil.which(BWRAP_BINARY_NAME) is None:
        _availability_cache = False
        return False
    try:
        result = subprocess.run(_SMOKE_TEST_ARGV, capture_output=True, timeout=10)
        _availability_cache = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("bwrap smoke test failed to run: %s", exc)
        _availability_cache = False
    return _availability_cache


def detect_bwrap_unavailable_reason() -> BwrapUnavailableReason | None:
    """Return why `bwrap_available()` is `False`, or `None` if it's actually available (or
    hasn't been probed yet — callers should check `bwrap_available()` first). Only used to
    tailor `BashTool`'s one-time fallback notice; never consulted for the go/no-go decision
    itself (see `bwrap_available()`).
    """
    if shutil.which(BWRAP_BINARY_NAME) is None:
        return "missing_binary"
    if not bwrap_available():
        return "no_userns"
    return None


def build_bwrap_argv() -> list[str]:
    """Build the full `bwrap` argv for one `BashTool` invocation (mount list, namespace
    unshares, hostname, env, workspace/homedir binds with denyholes over privileged paths) per
    the plan's "bubblewrap args to use" section.

    Not implemented yet: developing this requires iterating against a host where unprivileged
    user namespaces actually work (this repo's own dev and cloud-agent environments do not
    provide one — `bwrap_available()` reports `False` there), so it can't be built and verified
    here. `BashTool` never calls this today; it always runs unsandboxed (see
    `klorb.tools.bash`), gated on `bwrap_available()` only to decide the wording of its one-time
    fallback notice, not on this function.
    """
    raise NotImplementedError(
        "build_bwrap_argv() is a stub: bubblewrap sandboxing isn't implemented yet. "
        "BashTool always falls back to unsandboxed execution; see klorb.tools.bash.")
