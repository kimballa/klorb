
# klorb

```
  ,_/\_
   .--.
  ( () )
 (      )
(   ~~   )
 (      )
  `----'
   )  (
```

klorb is your friendly neighborhood agent.

## Setup

This repository is organized as a collection of subprojects (see `CLAUDE.md`); each has its
own provisioning steps. For the Python harness and CLI, see
[`klorb/README.md`](klorb/README.md#setup).

The top-level `make cloud_setup` target performs the installation steps described there
(`make venv` and `make install_dev_deps` in `klorb/`) in one step. It's used to provision
ephemeral cloud development environments; see `bin/claude-session-start.sh`.

