# Cross a sandboxed command's `--unshare-net` boundary via `socketpair()` fd-passing, not slirp4netns/veth

* Date: 2026-07-30 01:20
* Question: docs/plans/archive/018-bash-network-egress-socks-proxy.md's domain-gated network-egress
  proxy needs a live channel between a process inside a sandboxed command's own
  `--unshare-net` network namespace and klorb's own process, which has the real network
  connectivity. `bwrap`'s private network namespace only brings up its own loopback (`lo`),
  isolated from the host's — a sandboxed process cannot reach anything klorb itself has bound on
  `127.0.0.1`. What bridges that gap?
* Answer: A `socket.socketpair()` klorb creates *before* invoking `bwrap`, with the sandbox-side
  fd passed through via `subprocess.Popen(..., pass_fds=(...,))`. A small in-sandbox relay stub
  accepts connections on the sandbox's own private loopback and hands each accepted connection's
  fd to the host side over that same `socketpair()`, via `socket.send_fds`/`socket.recv_fds`
  (stdlib since Python 3.9). No `slirp4netns`/`pasta`, no `veth` pair, no new system binary
  dependency, no elevated privileges beyond what the sandbox already has.
* Reasoning: Three approaches were considered, verified empirically against this project's own
  bundled `bwrap` rather than assumed from documentation (see `klorb.tools.bash._decode_exit`'s
  docstring for the same verify-empirically discipline elsewhere in this codebase):
  * **`slirp4netns`/`pasta`** (rootless-container-style tap device + userspace NAT) gives the
    sandbox *generic* IP connectivity — a real route to anywhere — which then still needs
    `nft`/`iptables` transparent-redirect rules run *inside* the sandbox to force traffic through
    a proxy. That's two new moving parts (a new system binary dependency, plus in-namespace
    firewall rules) to reach the same end state fd-passing reaches with zero new dependencies.
    Only worth reconsidering if a future non-goal (raw TCP/UDP passthrough, not just HTTP(S))
    were picked up.
  * **A `veth` pair**, with the host end created in klorb's real (root) network namespace, needs
    `CAP_NET_ADMIN` there — which an unprivileged klorb process does not have. This is exactly
    why rootless container tooling invented `slirp4netns` in the first place rather than using
    veths directly.
  * **fd-passing over a pre-existing `socketpair()`** works because network-namespace changes
    (`unshare(CLONE_NEWNET)`, which `bwrap` performs as part of sandbox setup) only affect *new*
    socket creation, never already-open file descriptors — confirmed directly: a `socketpair()`
    created before `bwrap` launches, with one end passed through via `pass_fds`, remains a live,
    working, bidirectional channel after the child's network namespace changes underneath it (see
    the empirical roundtrip test run against this project's own `bwrap` during implementation —
    data sent from inside a `--unshare-net` sandbox on the passed fd was received on the host-side
    fd, and vice versa, with no special `bwrap` flag needed beyond `pass_fds` itself). `bwrap`
    forwards an inherited, `pass_fds`-marked fd straight through to the final child with no
    `--*-fd` flag required — verified the same way (a plain socket fd survived from parent to
    sandboxed child unassisted).

  A received fd keeps operating against the network namespace it was actually created in,
  regardless of which process/namespace now holds it — so the host-side `ProxyBackend`
  (`klorb.sandbox.network`) just wraps a received fd as a normal `socket.socket` and dials out for
  real, on klorb's own unrestricted network position, with no manual byte-relay loop needed for
  that leg. This gives the domain-gating proxy its one necessary foot in both namespaces at zero
  additional privilege and zero new system dependencies — `--unshare-net`'s isolation is otherwise
  untouched: nothing else the sandboxed command does can reach the network at all.
