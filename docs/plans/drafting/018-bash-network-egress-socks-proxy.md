# Plan: Domain-gated network egress for sandboxed Bash via a local SOCKS/HTTP proxy

> **Draft.** This plan describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented, and nothing else should depend on it,
> until it's promoted to `ready/` and then archived.

## Summary

Today `BashTool`'s `bwrap` sandbox (docs/specs/bash-tool-and-command-permissions.md's
"Sandboxing" section) passes `--unshare-net`, which denies a sandboxed command *all* network
access — TODO.md calls this out explicitly as the placeholder pending "network egress via a
domain-allowlist proxy." Meanwhile `WebFetchTool` already has a working domain-gated egress
model: a `deny`/`ask`/`allow` domain table evaluated by
`klorb.permissions.domain_access.DomainAccessTable`, enforced via the same
`PermissionAskItem`/`DomainResource` machinery every other resource kind uses.

This plan extends that same mechanism to cover Bash's network egress, but as a **second,
independent rule table**, not a shared one — see "Splitting `domains` into `bashDomains`/
`webDomains`" immediately below for why. It does so by:

1. Running a local proxy (SOCKS5 **and** HTTP CONNECT — see "Why both protocols") inside each
   sandboxed Bash invocation's own network namespace, backed by domain-screening logic that
   reuses `DomainAccessTable`/`evaluate_domain` verbatim, evaluated against the new `bashDomains`
   table.
2. Making that proxy the sandboxed command's *default* egress path — via `HTTP_PROXY`/
   `HTTPS_PROXY`/`ALL_PROXY` (and lowercase forms) injected into `build_bash_env()` — so `curl`,
   `wget`, `pip`, `uv`, `npm`, `git`, etc. reach it automatically with **zero flags the model has
   to remember to pass**.
3. Extending `BashTool`'s existing static command classification (`klorb.permissions.shell_parse`
   / `BashTool._classify`, docs/specs/bash-tool-and-command-permissions.md's "Combining verdicts")
   to recognize network-shaped commands and their target domain(s) *before* the sandboxed process
   ever runs, producing ordinary `DomainResource`-typed `PermissionAskItem`s — the same ask the
   user already sees for a `WebFetch` call, now surfaced for `curl`/`git clone`/`pip install`/etc.
   too, through the panel/ACP flow that already exists.
4. Failing closed, fast, and observably for anything the pre-flight pass didn't catch (a domain
   discovered only at runtime, e.g. by a script, not a literal CLI argument) — the connection is
   refused immediately rather than hung, and the refusal is surfaced back to the model in
   `BashTool`'s own response so it can explain the block and prompt the user, or retry once the
   domain is granted.

**v1 is pre-flight-only — confirmed.** A true mid-connection interactive prompt (the proxy
pausing an in-flight `connect()` to ask the user live) is answered in detail below ("Can the
proxy ask live, mid-connection?"): it is technically possible, but is a real architectural
departure from how every other klorb permission ask works today (ask-before-you-act-then-retry,
never pause-mid-act). **This plan builds only the pre-flight CLI-arg screening plus fail-fast
proxy denial for v1**; the live/mid-flight ask is explicitly Phase 2, not attempted here.

## Splitting `domains` into `bashDomains`/`webDomains`

`WebFetchTool` runs **unsandboxed**, inside klorb's own process and trust boundary — "fetch from
my own machine" or "fetch from my LAN" is exactly as privileged as klorb itself already is, which
is why `default-config.json`'s shipped `domains.allow` today includes `localhost`, `127.0.0.1`,
`::1`, `10.*`, `192.168.*`, `172.16.*` alongside `developer.mozilla.org`/`docs.python.org`.

A sandboxed Bash command is a fundamentally different trust level: the whole point of
`--unshare-net` is that it is *not* implicitly trusted with the host's own network position. A
naive shared `domains` table would mean the same `localhost`/RFC1918 entries that are harmless for
`WebFetch` silently hand a sandboxed command the ability to reach klorb's own host loopback and
the user's LAN by default — a real regression from today's total denial.

At the same time, an unconditional, hardcoded, non-overridable denial of loopback/link-local
addresses inside the proxy is *also* wrong: a real, legitimate workflow — e.g. an agent
co-developing a webapp with the user and needing Cypress (or Playwright, or a browser devtools
protocol client) to instrument a locally-running dev server — needs exactly this kind of access,
and the user is the right party to decide it's safe, not code that refuses to let them.

So this plan uses **two independent tables**, both `DomainRules`/`DomainAccessTable` instances,
sharing the same matching semantics and the same underlying classes, differing only in which
`SessionConfig` field/on-disk key/tool consults them:

* `webDomains` (renamed from today's `domains`) — `SessionConfig.web_domain_rules`, consulted
  only by `WebFetchTool`. Keeps today's shipped defaults (`localhost`, RFC1918 ranges, MDN/Python
  docs) unchanged.
* `bashDomains` (new) — `SessionConfig.bash_domain_rules`, consulted only by the Bash-egress
  proxy backend (Component 2 below) and the pre-flight scanner (Component 3). Ships with an
  **empty `allow`** for loopback/link-local/RFC1918 — nothing pre-approves reaching `localhost`
  or the LAN from inside a sandboxed command — but the user can add `localhost`, a specific LAN
  host, `169.254.169.254`, or anything else to `bashDomains.allow` (permanently via config, or
  transiently via a `scope="session"`/`"once"` ask decision) exactly the same way any other domain
  is granted — **no special-cased code path, no hardcoded exception list**. `bashDomains.allow`
  *does* ship with the specific registries `pip`/`uv`/`npm` need to function out of the box (see
  "Configuration" below) — the goal is "safe by default," not "useless by default."

Both tables are still governed by the ordinary deny-then-ask-then-allow evaluation every other
`PermissionsTable` uses — nothing about the split changes that evaluation order, only which table
a given tool consults.

**Renaming `domains` → `webDomains` in the shipped default config, and callers, is in scope for
this plan.** Per explicit instruction, migrating any config file the user personally maintains
outside this source repo is the user's own responsibility, not something this plan or its
implementation needs to account for (no back-compat shim, no dual-key reading).

## Current state (what this plan builds on)

* `klorb.sandbox.build_bwrap_argv()` passes `--unshare-net` unconditionally — see
  `docs/specs/bash-tool-and-command-permissions.md`'s "Sandboxing" section. No proxy exists today.
* `klorb.permissions.domain_access.DomainRules`/`DomainAccessTable`/`evaluate_domain` — the
  `deny`/`ask`/`allow` domain table, matched by exact string, `*.example.com` wildcard prefix, or
  `172.16.*` wildcard-suffix for IPs. Already the single source of truth for `WebFetchTool`; this
  plan adds a second *instance* of the same classes for `bashDomains`, not a new matching engine.
* `SessionConfig.domain_rules: DomainRules` (`klorb/src/klorb/session/config.py`) — merged across
  config layers by concatenation, exactly like `readDirs`/`writeDirs`/`commandRules`. Renamed to
  `web_domain_rules` by this plan; a new sibling `bash_domain_rules: DomainRules` field is added,
  merged the same way from the new `bashDomains` config key.
* `klorb.permissions.resource.DomainResource` — the `PermissionResource` `PermissionAskItem`
  already uses for a `WebFetch` ask (`url: str`, with `.domain` derived via `parse_domain()`).
  Needs a discriminator (e.g. `rule_set: Literal["web", "bash"]`, defaulting to `"web"` at
  existing `WebFetchTool` call sites) so `grant_preview()`/`apply_grant()` persist to the correct
  table. `PermissionOverride.domains: frozenset[str]` — the once-scoped bypass set, checked today
  only in `klorb.tools.web.fetch` — stays a single shared set (it's scoped to one retried tool
  call's own resources either way; no cross-tool ambiguity from keeping it unified).
* `klorb.permissions.domain_grant.apply_domain_permission_grant()` — persists a domain
  allow/deny decision to `session`/`workspace`/`homedir` scope, mirroring
  `command_grant.py`/`skill_grant.py`. Needs parameterizing (or splitting into two thin wrappers)
  over which table/config-key/`SessionConfig` field it writes to.
* TODO.md's "Permissions" section already anticipates this: "Once domains are available in the
  bubblewrapped bash, add allow-list entries for pypi, npm, maven-central" — this plan's
  `bashDomains` default allowlist directly fulfills that for pip/uv/npm (Maven's registry is left
  for a user/future addition — see "Configuration").

## Goals

* A sandboxed Bash command's HTTP(S) egress is denied by default, ask for an unrecognized domain,
  and allowed for a domain in `bashDomains.allow` — the same three-way policy `WebFetch` already
  enforces via `webDomains`, now covering `curl`/`wget`/`pip`/`uv`/`npm`/`git`/etc. too, under its
  own independently-configured table.
* No flags or proxy configuration the model has to remember: proxy env vars are always present in
  the sandboxed environment, pointing at the one proxy.
* A command whose CLI arguments name a literal, recognizable target domain is screened *before*
  the sandbox even starts, through the exact same ask panel / ACP `session/request_permission`
  flow already used for a directory or domain ask — no new UI.
* A command that reaches an un-granted domain anyway (dynamic discovery, redirects, a domain the
  static scanner couldn't parse out of the argv) is refused promptly and observably, never hung
  and never silently allowed.
* The user can grant a sandboxed command access to `localhost`, a LAN address, or any other
  address they judge safe and necessary (e.g. Cypress instrumenting a co-developed webapp's local
  dev server) — through the *ordinary* ask/grant flow, not a special escape hatch, and not blocked
  by any non-overridable code-level rule.
* `pip`/`uv`/`npm` work out of the box against their default public registries, without the user
  needing to pre-populate `bashDomains` themselves for the common case.

## Non-goals (v1)

* A live, mid-connection interactive ask (see dedicated section below) — Phase 2.
* MITM/TLS-terminating inspection of HTTPS payload content (Anthropic's `sandbox-runtime` has
  `mitm-ca.ts`/`mitm-leaf.ts`/`tls-terminate-proxy.ts` for this; klorb's proxy only ever sees a
  SOCKS5 `CONNECT`/HTTP `CONNECT` target `host:port`, never decrypts the tunnel — matching
  `WebFetchTool`'s own posture of trusting-but-labeling fetched content, not inspecting it in
  transit).
* Non-HTTP(S) protocols as a *recognized, pre-flight-screened* command shape (raw TCP, SSH, FTP,
  arbitrary UDP/DNS) — Phase 3. Note this is about what the static scanner recognizes and what the
  shipped env vars are meant for, not an absolute protocol block inside the proxy itself: the
  SOCKS5 listener's `CONNECT` relay is protocol-agnostic once a domain is approved (see "Why both
  protocols, not SOCKS alone").
* Third-party domain-reputation/malware-blocklist integration — same deferral `WebFetchTool`'s
  plan already carries in `TODO.md`'s "Plan 013: WebFetch" section.

## Architecture

### Why a namespace-crossing relay is required at all

`bwrap --unshare-net` creates a private network namespace whose *only* interface is its own
loopback — bubblewrap brings that `lo` up specifically so sandboxed apps that assume loopback
exists don't break outright. That loopback is **not** the host's loopback: two different `lo`
devices, isolated by the kernel, `127.0.0.1:PORT` inside the sandbox cannot reach anything klorb
itself has bound on the host's `127.0.0.1:PORT`. (This must be verified empirically against the
project's own bundled `bwrap`, the same way `klorb.tools.bash._decode_exit`'s docstring notes
verifying signal-exit encoding empirically rather than assuming it from documentation — if a given
`bwrap` build doesn't bring `lo` up automatically, the setup step below adds an explicit `ip link
set lo up` as insurance; the sandboxed process has `CAP_NET_ADMIN` inside its own user+net
namespace pair, from the same `--unshare-user` identity-map trick `build_bwrap_argv()` already
relies on, so it's capable of running that itself.)

So a proxy needs a foot in *both* namespaces: something listening where the sandboxed `curl` can
reach it (the sandbox's private loopback), and something dialing out where the real internet is
reachable (klorb's own host namespace, unrestricted). Three ways to bridge that gap were
considered:

| Approach | Verdict |
| --- | --- |
| `slirp4netns`/`pasta` (rootless-container-style tap device + userspace NAT) | Rejected for v1: gives the sandbox *generic* IP connectivity (a real route to anywhere), which then still needs `nft`/`iptables` transparent-redirect rules run *inside* the sandbox to force traffic through the proxy — two new moving parts (a new system binary dependency, plus in-namespace firewall rules) to reach the same end state the fd-passing approach below reaches with zero new dependencies. Worth reconsidering only if a future non-goal (raw TCP/UDP passthrough) is picked up. |
| `veth` pair, host end in klorb's real netns | Rejected: creating one end of a veth pair *in the host's actual root network namespace* needs `CAP_NET_ADMIN` there, which an unprivileged klorb process does not have (this is exactly why rootless container tooling invented `slirp4netns` instead of using veths). |
| **fd-passing over a pre-existing `socketpair()`** (chosen) | A `socket.socketpair()` klorb creates *before* invoking `bwrap` remains a live, working, connected channel after the child calls `unshare(CLONE_NEWNET)` — network-namespace changes only affect *new* socket creation, never already-open file descriptors. A tiny in-sandbox relay accepts local connections on the sandbox's own loopback and hands each accepted socket's fd to the host side via `SCM_RIGHTS` (`socket.send_fds`/`recv_fds`, stdlib since Python 3.9). A passed-fd socket keeps operating against the network namespace it was actually created in, regardless of which process/namespace now holds the fd — so the host-side proxy, once it receives the fd, just wraps it as a normal `socket.socket` and talks to it directly; no manual byte-relay loop needed for that leg. Zero new system binaries, zero elevated privileges beyond what the sandbox already has. |

### Component 1 — in-sandbox relay stub

A small, dependency-free script that:

1. Listens on TCP `127.0.0.1:<port>` (a fixed, well-known port inside the sandbox's own private
   loopback — collisions with a host port are impossible since it's a different network stack) —
   two listeners, one behaving as a SOCKS5 server's *front door* and one as an HTTP CONNECT
   proxy's front door (see "Why both protocols" — v1 keeps these on two separate ports rather than
   sniffing the first byte to multiplex one port, matching Anthropic's own `mux-proxy.ts` idea in
   spirit but deferring the sniffing complexity).
2. On `accept()`, sends the accepted connection's fd across the inherited control-channel fd
   (`KLORB_SANDBOX_CTRL_FD`, passed via `subprocess.Popen(..., pass_fds=(...,))`) using
   `socket.send_fds()`, then closes its own copy and loops back to `accept()` the next one. It
   never parses SOCKS5/HTTP CONNECT itself — that logic lives once, host-side (see Component 2),
   so the in-sandbox surface trusted with raw bytes is as small and dumb as possible.
3. Is launched as the very first background step of the sandboxed shell's life — for a
   `shell_lifetime="command"` invocation, once per `bwrap` launch (the same cost model as today's
   sandbox setup); for `"session"`/`"new"`, once per persistent shell, torn down alongside it. This
   slots into the exact same bootstrap-script mechanism `PersistentShell` already uses to source
   `~/.bashrc` before the first real command (docs/specs/bash-tool-and-command-permissions.md's
   "Session-scoped terminals" section) — no new process-lifecycle machinery.

### Component 2 — host-side proxy backend

A per-Session (or, simplest v1 shape, per-live-sandbox) Python component,
`klorb.sandbox.network` (new module — not folded into `klorb.sandbox`, since that module is
purely `bwrap` argv/dir-set construction with no I/O of its own; this one owns live sockets and
threads):

1. Owns the host end of each live sandbox's control-channel `socketpair()`; on a received fd,
   wraps it as a `socket.socket` and hands it to a small thread-per-connection handler — matching
   this codebase's existing preference for plain OS threads over `asyncio` in the Bash/proxy
   surface (`PersistentShell`'s reader threads, `BashTool`'s cancel-poll loop), rather than
   introducing an async runtime into a part of the codebase that's synchronous throughout.
2. Speaks SOCKS5 (RFC 1928, `CONNECT` command only — `BIND`/`UDP ASSOCIATE` unsupported) or HTTP
   `CONNECT`, depending on which listener the fd came from, purely to extract the target
   `host:port` — **critically, resolution-deferred (`socks5h`, never plain `socks5`) so DNS
   happens host-side**: the sandbox namespace has no DNS resolver of its own (only loopback), so
   this isn't optional — a sandboxed client attempting local resolution would simply fail. This
   also means domain policy is evaluated against the literal hostname string the client asked for,
   exactly like `WebFetchTool.apply()`'s `parse_domain(url)` today.
3. Evaluates the target hostname through `evaluate_domain(session_config.bash_domain_rules,
   domain)` — the same function `WebFetchTool` calls, against the Bash-specific table. `"allow"`
   → resolve + `connect()` out for real and pump bytes bidirectionally between the passed-in
   sandbox-side fd and the new outbound socket until either side closes. `"deny"`/unresolved-`"ask"`
   → send the protocol-appropriate failure reply immediately (SOCKS5 `0x02` "connection not
   allowed by ruleset" / HTTP `403`) and close — see "Fail closed, fast, and observably" below for
   why `"ask"` is *not* a live pause in v1.
4. Every refusal is appended to a small per-invocation list (`session.tool_state["Bash"]
   ["proxy_blocked"]`, mirroring the existing `tool_state["Bash"]["persistent_shell"]`/
   `["sandbox_warned"]` bookkeeping) so `BashTool._execute`/`PersistentShell._run_raw` can read it
   back once the command finishes and fold it into the response (see below) — the proxy itself
   never talks to the model or the permission-ask UI directly.

### Component 3 — pre-flight domain screening in `BashTool._classify`

The static walker that already turns `simple_commands`/`redirects`/`forced_ask_reasons` into
`PermissionAskItem`s (docs/specs/bash-tool-and-command-permissions.md's "Combining verdicts")
gains one more contributor: for each `SimpleCommand` whose `argv0` is a recognized network client
— `curl`, `wget`, `git` (for `clone`/`pull`/`push`/`fetch`/`ls-remote`), `pip`/`pip3`, `uv`, `npm`,
`yarn`, `pnpm`, `go` (`get`/`install`/`mod download`), `cargo` (`add`/`install`/`build` — crates.io
fetches), `mvn`, `nc`/`ncat`/`telnet`, `http`/`https` (HTTPie) — an explicit, extensible allowlist
of "this argv0 is known to make outbound network calls," not a guess — extract every literal
argument that parses as a URL or a bare `host[:port]` token (reusing `parse_domain()`, tolerantly
wrapping a schemeless bare host as `https://<host>` purely so `parse_domain`'s `urlparse`-based
extraction has a netloc to find — the synthesized scheme is never itself meaningful, only the
extracted hostname is), and evaluate each via the same `evaluate_domain()` call Component 2 makes
against `bash_domain_rules`. An `"ask"` verdict contributes a
`PermissionAskItem(resource=DomainResource(url=..., rule_set="bash"), bash_context=...)` — the
*same* `DomainResource` type `WebFetchTool`'s ask already uses (now carrying the `rule_set`
discriminator described above), so `PermissionAskPanel`/ACP's `session/request_permission` render
it identically, no new UI code. `"deny"` short-circuits the whole command exactly like a denied
directory/command rule does today. A non-literal argument (a shell variable, `$(...)`, etc.) on a
recognized network command already escalates to `"ask"` today via the existing "non-literal token"
`ForcedAskReason` — this plan doesn't change that path, it only adds a *more specific*,
domain-typed ask for the common case where the target genuinely is a literal in the command text.

**`ssh`/`scp`/`rsync` are deliberately excluded from the recognized-clients list.** `ssh`/`scp`
speak the SSH protocol, not HTTP(S); `rsync`'s own native transport is either its own daemon
protocol (TCP 873) or tunneled over `ssh` — it has no built-in HTTP(S) mode either. None of the
three has a literal HTTP(S) URL argument for the scanner to usefully extract in the first place,
and — per the HTTP(S)-only non-goal — none of their traffic is expected to reach the network at
all under this plan (no SSH/rsync-daemon egress path is provided; only the two HTTP-shaped
listeners exist). If a user configures `ssh`'s `ProxyCommand` to tunnel through the SOCKS5
listener manually, the relay will carry it like any other approved `CONNECT` (see "Why both
protocols, not SOCKS alone"), but that's an explicit, user-driven configuration, not something
this plan's pre-flight scanner recognizes or screens for.

This mirrors exactly how `IMPLICIT_READ_COMMANDS`/`_maybe_add_implicit_reads` already gives
`cat file.txt` the same `readDirs` protection a real `ReadFile` call gets, on top of (never
instead of) `CommandRules`'s own check on the invocation — same shape, new resource kind.

**A `BashTool` retry after the ask resolves** re-runs `_classify` from scratch; the now-granted
domain evaluates to `"allow"` (a persisted grant in `bashDomains`) or is covered by
`PermissionOverride.domains` (a `scope="once"` decision) — `BashTool._classify` needs to check
`self.context.permission_override.domains` for its extracted target domain(s), the same way it
already checks `.commands`/`.reasons` for its own resource kinds — so the retried command actually
proceeds instead of asking again.

### Component 4 — default egress wiring (`build_bash_env`)

`klorb.tools.bash.build_bash_env()` unconditionally sets (alongside the existing `HOME`/`USER`/
`WORKSPACE_ROOT`/`SHELL`/`BASH` entries, before `shareEnv`/`setEnv` so a user override still wins):

```text
http_proxy=http://127.0.0.1:<http-connect-port>
HTTP_PROXY=http://127.0.0.1:<http-connect-port>
https_proxy=http://127.0.0.1:<http-connect-port>
HTTPS_PROXY=http://127.0.0.1:<http-connect-port>
all_proxy=socks5h://127.0.0.1:<socks-port>
ALL_PROXY=socks5h://127.0.0.1:<socks-port>
no_proxy=
NO_PROXY=
```

`NO_PROXY` is forced empty (not merely left unset) so a stray inherited value can't punch a
bypass hole. `ALL_PROXY` points at the SOCKS5 listener; `HTTP_PROXY`/`HTTPS_PROXY` point at the
HTTP-CONNECT listener — see "Why both protocols" immediately below for why both are needed, not
just the SOCKS one the task description names.

### Why both protocols, not SOCKS alone

The task description asks for "a SOCKS proxy to handle HTTP/HTTPS outbound traffic" as the
default egress path for `curl`/`wget`/`pip`/`npm`/etc. In practice this needs two front-ends, not
one:

* `curl`, `wget`, `git` all speak SOCKS5 natively at the C-library level — `ALL_PROXY=socks5h://
  ...` genuinely works for them today, no extra dependency. So does anything a user manually
  configures to tunnel through a SOCKS5 `CONNECT` (e.g. `ssh`'s `ProxyCommand` via `nc -x`) — the
  relay itself doesn't care what protocol rides inside an approved tunnel, only the domain named
  in the `CONNECT` request.
* `pip` (via `requests`/`urllib3`) only gains SOCKS support through the optional `PySocks`
  (`requests[socks]`) package — **not guaranteed to be installed** in an arbitrary sandboxed
  Python environment. Without it, `pip install` against a SOCKS-only proxy fails outright with
  "Missing dependencies for SOCKS support," which would make this feature actively break a common
  workflow rather than transparently enable it.
* `HTTP_PROXY`/`HTTPS_PROXY` pointing at a plain HTTP CONNECT proxy, by contrast, is understood
  natively and universally — `curl`, `wget`, `git`, `pip`, `npm`/`yarn`/`pnpm`, `uv`, `go`,
  `cargo`, and virtually every other HTTP client — with no optional dependency required.

So v1 stands up **both**: an HTTP CONNECT listener (`HTTP_PROXY`/`HTTPS_PROXY`, maximum
compatibility, the practical default for the overwhelming majority of tool invocations) and a
SOCKS5 listener (`ALL_PROXY`, for the minority of tools/uses that specifically want or need it).
Both share the exact same domain-screening/dial/relay backend (Component 2) — only the front-door
protocol parsing differs.

### Fail closed, fast, and observably

For anything the static scan (Component 3) didn't catch — a domain read from a config file, a
redirect chain landing somewhere unexpected, a domain assembled at runtime by a script — the
proxy itself is the last line of defense, and it fails *closed*: `"deny"` and unresolved `"ask"`
both refuse the connection immediately (no multi-second hang waiting for anything), matching this
plan's "never hung, never silently allowed" goal. `BashTool`'s response gains a
`blocked_domains: list[str]` field, populated from `session.tool_state["Bash"]["proxy_blocked"]`
after the command exits (cleared at the start of each call) — the model sees, alongside whatever
`curl: (7) Failed to connect` noise landed in `stderr`, a structured
`blocked_domains: ["sneaky-cdn.example"]` telling it *why*, so it can explain the block to the
user and suggest approving the domain (which then reaches it through the ordinary ask flow on the
next attempt) rather than the model guessing at a generic connection failure. This mirrors the
existing `sandbox_notice` field's role — an inline, always-visible observability signal on the
tool response, not a live interactive pause — rather than inventing a new synchronous cross-thread
rendezvous for this common case.

### Reconcile-on-grow, extended to domains

A persistent shell (`shell_lifetime="session"`/`"new"`) already rebuilds its `bwrap` sandbox when
the session's directory grants grow mid-session (docs/specs/bash-tool-and-command-permissions.md's
"Sandbox reconcile-on-grow"). The proxy doesn't actually need a sandbox *rebuild* for a grown
domain grant — the proxy backend (Component 2) reads `session_config.bash_domain_rules` live, on
every new connection, so a domain approved after the persistent shell started is honored on the
very next `curl` inside it with no rebuild at all. This is strictly simpler than the directory case
(where the *mount* namespace is genuinely fixed at launch) — worth calling out explicitly so a
future reader doesn't assume domain grants need the same rebuild-on-grow machinery directory
grants do.

## Can the proxy ask live, mid-connection?

Yes, mechanically — but it doesn't fit the shape of any ask this codebase does today, and this
plan recommends **not** building it for v1.

Every existing klorb permission ask is **pre-flight**: `PermissionAskRequired`/
`MultiPermissionAskRequired` is raised *before* the risky action runs, `Session` blocks on
`on_permission_ask`, and only once approved does `Session` *retry* the whole tool call from
scratch (docs/specs/permissions.md's "Interactive `"ask"` confirmation" section; ACP's own spec
explicitly documents this as an ordering guarantee — every ask callback fires synchronously within
one tool call's dispatch, never overlapping a second in-flight call). `BashTool`'s own redirect/
command/forced-ask items are already evaluated by parsing the command text *before* the sandboxed
process is spawned, for exactly this reason.

A live, mid-connection ask breaks that invariant in a specific way: by the time the proxy sees the
unrecognized domain, `BashTool.apply()` is already blocked inside `subprocess.communicate()` (or
`PersistentShell._run_raw`'s sentinel wait) on the Session's own worker thread — the tool call
cannot itself raise `PermissionAskRequired` anymore; the exception-then-retry pattern has nowhere
to attach. Making this work needs a **second, independent ask channel** that exists concurrently
with an in-flight tool call:

* A `ProxyAskBroker` (one per `Session`) the proxy backend calls synchronously, with some bounded
  timeout, *before* replying to the SOCKS5/CONNECT handshake — blocking that one connection's
  thread, not the whole sandboxed process (other already-open connections in the same command
  keep running).
* The broker's request would reuse `DomainResource`/`PermissionAskPanel` for display, but needs a
  transport that doesn't already exist: the TUI would need `call_from_thread` to show the panel
  while the Session worker thread is still inside the original `Bash` call; ACP would need to fire
  a *second*, concurrent `session/request_permission` while the first tool call's own response is
  still pending — something the ACP spec's "every callback fires... out on the wire" ordering
  guarantee doesn't cover today, since it was written for the strictly-sequential pre-flight case.
* Cancellation gets a new edge case: if the user `^C`s the whole `Bash` call while a live domain
  ask is pending, the pending ask itself must resolve (deny) rather than leave the broker's
  worker thread and the TUI panel stranded — `BashTool`'s existing cancel-event polling
  (docs/specs/bash-tool-and-command-permissions.md's "Cancellation" section) would need to also
  reach into `ProxyAskBroker` for the connections belonging to its own invocation.
* Timeout semantics need a real answer: what does `curl` see while the user hasn't yet responded?
  (Recommend: nothing yet — hold the SOCKS5/CONNECT handshake open, unresolved, up to the timeout,
  since both protocols tolerate a slow-but-eventually-answering proxy far better than a fast wrong
  answer; `curl`'s own `--connect-timeout` becomes the practical ceiling from the model's side.)

None of this is impossible, but it's a genuine second permission-ask architecture living alongside
the first, not a small extension of it. **Recommendation: ship v1 (pre-flight CLI-arg screening +
fail-fast proxy denial + inline `blocked_domains` reporting) first, and only revisit a live ask as
a Phase 2 once v1's static-scan coverage is measured against real usage** — the `blocked_domains`
field this plan already adds gives exactly the data needed to judge how often the static scanner
actually misses a domain a live ask would have caught, before investing in the second ask channel.
No config surface for this (e.g. a timeout value) is added in v1 — it's deferred in full to
whenever Phase 2 is actually designed, not stubbed out ahead of time.

## Security

* **`bashDomains` starts conservative, but every rule in it is ordinary, user-editable
  configuration — nothing is hardcoded or non-overridable.** Unlike `webDomains` (safe to default
  broad, since `WebFetch` runs inside klorb's own trust boundary), `bashDomains.allow` ships
  without `localhost`/`127.0.0.1`/`::1`/RFC1918 entries, and `bashDomains.deny` ships with the
  well-known link-local/cloud-metadata range (`169.254.0.0/16`, including `169.254.169.254`
  specifically) as a sensible **default**, not a rule the proxy enforces independent of
  `bashDomains`. A user who has a real, informed reason to grant a sandboxed command loopback or
  LAN access — e.g. Cypress/Playwright instrumenting a webapp dev server the user and agent are
  co-developing — adds `localhost`, `127.0.0.1`, or the specific host to `bashDomains.allow`
  through the *same* ask/grant flow (once, session-scoped, or persistent) every other domain uses.
  There is deliberately no separate, non-configurable code path for this — the whole point of the
  split from `webDomains` is that the *default* is conservative while the *ceiling* is exactly as
  flexible as every other permission table in this codebase.
* **Granting a bare hostname/IP grants all ports on it, not just the one the workflow needs** —
  `DomainSpec` matching today (`DomainAccessTable`/`_domain_matches`) has no port granularity, so
  approving `localhost` for a Cypress workflow on `:3000` also permits the sandbox to reach
  anything else locally bound (a debugger, another dev server, etc.). This is an honest,
  documented limitation to surface in the ask panel/grant-preview copy for a loopback/LAN grant
  specifically, not a blocker — **port-scoped `DomainSpec` matching (`localhost:3000`) is
  reasonable future work**, not required for v1, since the user is explicitly the one deciding
  this trade-off is acceptable for their workflow.
* **The proxy is the only egress path, by construction, not by convention.** `--unshare-net`
  still denies every other network path — there's no `iptables`/`nft` rule to bypass because
  there's no other path to be redirected *from*: raw sockets, DNS, anything other than the two
  loopback listeners the relay stub itself opens, simply have nowhere to go. A command that
  ignores `HTTP_PROXY`/`ALL_PROXY` and tries to connect directly gets the same `ENETUNREACH`/
  connection-refused it gets today. Setting the proxy env vars is what makes the proxy
  *convenient* to use correctly, not what makes it *mandatory* — the mandatory part is that
  nothing else is reachable at all.
* **The relay stub is deliberately protocol-blind.** It only ever passes raw fds via `SCM_RIGHTS`
  — it never parses SOCKS5/HTTP bytes itself, so a bug in protocol parsing is confined to the
  host-side backend (Component 2), which is easier to test and reason about in isolation, and
  which never runs with the sandboxed command's own reduced-but-still-somewhat-adversarial
  environment.
* **No SOCKS/proxy auth is added.** The control channel (a `socketpair()` fd that only ever
  existed as an already-open, unguessable file descriptor, never a discoverable network address)
  is the actual trust boundary — anything that can reach the relay stub's loopback listeners is,
  by construction, already inside this specific sandboxed command's own process tree, so username/
  password SOCKS auth (which Anthropic's own `socks-proxy.ts` uses, since *its* proxy is reachable
  by anything on a shared host port) would add friction without adding a real boundary here.

## Configuration

New `ProcessConfig` fields/on-disk keys, following the `tools.bash.*`/`tools.webFetch.*`
convention (`klorb.process_config.PROCESS_KEY_MAP`):

```json
{
  "tools.bash.network.enabled": true,
  "tools.bash.network.recognizedClients": [
    "curl", "wget", "git", "pip", "pip3", "uv", "npm", "yarn", "pnpm",
    "go", "cargo", "mvn", "nc", "ncat", "telnet", "http", "https"
  ]
}
```

* `tools.bash.network.enabled` (default `true`) — full escape hatch, mirroring
  `tools.bash.riskClassifier.enabled`: `false` skips standing up the proxy/relay entirely and
  Bash keeps today's `--unshare-net`-denies-everything behavior, for a user who doesn't want this
  surface at all.
* `tools.bash.network.recognizedClients` — the argv0 allowlist Component 3's static scanner
  treats as "this command makes outbound network calls, so scan its literal arguments for a
  target domain." User-extensible (a locally-installed CLI klorb doesn't know about by default),
  concatenated across config layers like `shareEnv`.

`default-config.json` changes:

```json
{
  "sessionDefaults": {
    "webDomains": {
      "deny": [],
      "ask": [],
      "allow": [
        "localhost", "127.0.0.1", "::1",
        "10.*", "192.168.*", "172.16.*",
        "developer.mozilla.org", "docs.python.org"
      ]
    },
    "bashDomains": {
      "deny": ["169.254.*"],
      "ask": [],
      "allow": [
        "pypi.org", "files.pythonhosted.org",
        "registry.npmjs.org"
      ]
    }
  }
}
```

`webDomains` is a straight rename of today's `domains` key/defaults — no behavior change for
`WebFetch`. `bashDomains.allow`'s three entries are what `pip`/`uv` (both against the standard
PyPI index) and `npm` need to install a package with no further configuration; `yarn`/`pnpm`
default to the same npm registry in most configurations, but a project pinned to a different
registry (a private Yarn/pnpm mirror, `registry.yarnpkg.com`, Maven Central for `mvn`, `crates.io`
for `cargo`, `proxy.golang.org` for `go`) will hit an `"ask"` the first time, exactly as intended
— these weren't named in the "necessary for pip/uv/npm out of the box" requirement, so they start
unlisted rather than guessed at. `bashDomains.deny`'s `169.254.*` is a default, not a hardcoded
rule (see "Security") — an `ask`, matching every other unrecognized domain, if the user removes
it.

No changes to `commandRules`/`readDirs`/`writeDirs` semantics — a sandboxed command still needs
its underlying `CommandRules` verdict to be `allow`/granted `ask` before it runs at all; domain
screening is an *additional* check on top for commands the static scanner recognizes as
network-shaped, exactly like `IMPLICIT_READ_COMMANDS`' `readDirs` check is additional on top of
`CommandRules`.

## New / changed modules

| Module | Change |
| --- | --- |
| `klorb/sandbox/network.py` (new) | `ProxyBackend` — owns the host-side control-channel socket, SOCKS5/HTTP-CONNECT parsing, `evaluate_domain()` calls against `bash_domain_rules`, connect+relay, and the `proxy_blocked` bookkeeping. |
| `klorb/sandbox/relay_stub.py` (new, or a bundled resource script) | The in-sandbox fd-passing relay (Component 1) — minimal, dependency-free, launched as the sandboxed shell's first background step. |
| `klorb/sandbox.py` | `build_bwrap_argv()`/callers pass the pre-created `socketpair()`'s sandbox-side fd through (`pass_fds`), and no longer needs `--unshare-net` to mean "no network at all" in its own docstring — update to describe the loopback-only relay path. |
| `klorb/tools/bash.py` | `build_bash_env()` adds the proxy env vars (Component 4); `BashTool._execute`/`PersistentShell` launch/track the relay stub and `ProxyBackend` alongside the existing sandbox lifecycle; response gains `blocked_domains`; `_classify` gains the network-command scanner (Component 3) and the `permission_override.domains` check. |
| `klorb/tools/web/fetch.py` | Update `session_config.domain_rules` references to `web_domain_rules`. |
| `klorb/permissions/resource.py` | `DomainResource` gains the `rule_set: Literal["web", "bash"]` discriminator; `grant_preview()`/`apply_grant()` dispatch to the matching table/writer. |
| `klorb/permissions/domain_grant.py` | Parameterize (or split into `apply_web_domain_permission_grant`/`apply_bash_domain_permission_grant`) over which config key/`SessionConfig` field to write. |
| `klorb/session/config.py` | Rename `domain_rules` → `web_domain_rules`; add `bash_domain_rules: DomainRules`. |
| `klorb/permissions/shell_parse.py` | No change to `BashCommandAnalysis`'s shape is required — Component 3 re-uses `simple_commands` as already extracted; the domain-scanning logic itself can live in `klorb.tools.bash` alongside `_classify` rather than in the parser, since it's a permissions concern, not a shell-grammar concern. |
| `klorb/process_config.py` | New `tools.bash.network.*` keys/fields; rename/split the `domains`-layer-merging logic in `load_process_config` into `webDomains`/`bashDomains` merges. |
| `klorb/resources/default-config.json` | Rename `domains` → `webDomains` (defaults unchanged); add `bashDomains` per "Configuration" above. |

## Testing strategy

* **`ProxyBackend` unit tests**: SOCKS5 handshake parsing (`CONNECT` only, malformed/short reads,
  a `BIND`/`UDP ASSOCIATE` request rejected cleanly), HTTP `CONNECT` parsing, domain evaluation
  delegating to `evaluate_domain()` against a mocked `bash_domain_rules`, `"deny"`/unresolved-
  `"ask"` producing an immediate protocol-correct failure reply with no hang, `proxy_blocked`
  bookkeeping.
* **Relay-stub integration test**: a real `socketpair()` + a subprocess that calls
  `unshare(CLONE_NEWNET)` (or, if that needs privileges the test environment lacks, a fake stand-
  in that only exercises the fd-passing contract without an actual namespace change) confirms a
  passed fd is usable end-to-end from the receiving process.
* **`BashTool._classify` unit tests**: `curl https://pypi.org/...` (default-allowed domain)
  proceeds; `curl https://unknown.example` produces a `DomainResource`-typed (`rule_set="bash"`)
  `PermissionAskItem`; `pip install --index-url https://denied.example/simple pkg` denies
  outright; a non-literal target (`curl "$URL"`) still escalates via the existing non-literal-
  token path, unchanged; a retried call with `permission_override.domains` containing the target
  proceeds without re-asking; `curl http://localhost:3000/` asks (not allowed, not hardcoded-
  denied) against the default `bashDomains`, and proceeds once the user grants `localhost`.
* **`webDomains`/`bashDomains` split regression tests**: a domain granted in `bashDomains` has no
  effect on `WebFetchTool`'s evaluation and vice versa — the two tables are genuinely independent.
* **End-to-end** (requires a real `bwrap`, likely gated the same way existing sandbox tests are):
  a sandboxed `curl` to an allowed domain succeeds; to a denied one fails fast (bounded wall-clock
  time, not the full `tools.bash.timeout`); `blocked_domains` is populated on the response;
  `pip install` against the default PyPI index succeeds without `PySocks` installed (proving the
  HTTP-CONNECT front-end, not just SOCKS, is actually in the loop); `curl http://169.254.169.254/`
  fails against the shipped default `bashDomains`, then succeeds once the user explicitly removes
  it from `bashDomains.deny`/adds it to `.allow` — proving the grant path, not a hardcoded rule, is
  what's actually governing this.
* `make lint`, `make typecheck`, `make test` (per this repo's standing rule) — plus
  `make lint_docs` after editing this plan and any spec it updates.

## Documentation

* Fold the durable parts of this plan into `docs/specs/bash-tool-and-command-permissions.md`'s
  "Sandboxing" section once implemented (its "Out of scope" bullet on network egress is exactly
  what this plan resolves) — per this repo's own convention, update the existing spec rather than
  create a new one, since this is squarely an extension of Bash's existing sandboxing story, not a
  new feature area.
* Document the `webDomains`/`bashDomains` split — why two tables, which tool consults which, and
  that `bashDomains`'s conservative defaults are ordinary config, not a code-level restriction — in
  whichever doc ends up describing domain-permission semantics generally (likely this same spec,
  plus wherever `WebFetch`'s own domain screening is documented).
* New ADR: the fd-passing-over-`socketpair()` choice over `slirp4netns`/veth (a real, non-obvious
  architecture decision with a real trade-off table, exactly what an ADR is for).

## Future work (Phase 2+, log to `TODO.md` under this plan's own section once archived)

* The live, mid-connection interactive ask described above (`ProxyAskBroker` + the new concurrent-
  ask transport for both TUI and ACP), gated on measuring how often v1's static CLI-arg scan
  actually misses a domain a live ask would have caught.
* Port-scoped `DomainSpec` matching (`localhost:3000`), so a loopback/LAN grant for one workflow
  (e.g. Cypress against a dev server) doesn't implicitly cover every other locally-bound service.
* Single-port SOCKS/HTTP-CONNECT protocol sniffing (`mux-proxy.ts`-style) instead of two fixed
  ports, if managing two listeners per sandbox turns out to be operationally annoying.
* Non-HTTP(S) protocols as a first-class, pre-flight-recognized command shape (`ssh`/`scp`/`rsync`
  and friends) — explicitly deferred by the task description; today these simply have no egress
  path at all (they're not among `tools.bash.network.recognizedClients`, and the sandbox provides
  no non-HTTP(S) listener for them to reach even if they were).
* Third-party domain-reputation/malware-blocklist integration (already deferred for `WebFetch`,
  same deferral applies here).
* TLS-terminating inspection, if a future need for content-level (not just domain-level) request
  filtering emerges (Anthropic's `sandbox-runtime` has this today via `mitm-ca.ts`/
  `tls-terminate-proxy.ts`/`body-substitution.ts`/`credential-*.ts` for credential redaction in
  transit — out of scope here, but worth knowing prior art exists if this is ever revisited).

## TODO list

1. Rename `domains` → `webDomains` throughout (`default-config.json`, `SessionConfig.domain_rules`
   → `web_domain_rules`, `klorb.tools.web.fetch`, `process_config.py`'s layer-merge logic); add the
   new `bashDomains`/`bash_domain_rules` sibling per "Configuration"/"Current state" above.
2. Add the `rule_set` discriminator to `DomainResource`; split/parameterize
   `klorb.permissions.domain_grant` over the two tables.
3. Implement `klorb/sandbox/network.py`'s `ProxyBackend`: SOCKS5 + HTTP CONNECT parsing,
   `evaluate_domain()` integration against `bash_domain_rules`, connect+relay, unit tests.
4. Implement the in-sandbox relay stub, and the `socketpair()`/`pass_fds` plumbing in
   `klorb.sandbox`/`klorb.tools.bash` that launches it alongside each sandboxed shell.
5. Wire proxy env vars into `build_bash_env()`.
6. Extend `BashTool._classify` with the network-command scanner (Component 3) and the
   `permission_override.domains` check; add `blocked_domains` to the response shape.
7. Add `tools.bash.network.*` config keys/fields/defaults, and the `bashDomains` defaults above.
8. Write the full testing-strategy suite above; run `make lint typecheck test`.
9. Fold the durable design into `docs/specs/bash-tool-and-command-permissions.md`; write the ADR
   noted in "Documentation"; move this plan file to `docs/plans/archive/` per
   `docs/plans/README-PLANS.md`; log Phase-2+ items to `TODO.md` under a new "Plan 018: ..."
   subsection.
