
# klorb

```
      o
     /
    ▄▄▄
   █████
  ███████
 █░███x███
███████████
▟█▙     ▟█▙
```

klorb is your friendly neighborhood agent.

## Setup

This repository is organized as a collection of subprojects (see `CLAUDE.md`); each has its
own provisioning steps. For the Python harness and CLI, see
[`klorb/README.md`](klorb/README.md#setup).

The top-level `make cloud_setup` target performs the installation steps described there
(`make venv` and `make install_dev_deps` in `klorb/`) in one step along with a few other
setup activities. It's used to provision ephemeral cloud development environments; see 
`bin/claude-session-start.sh`.

Create a top-level `.env` file (see `env.template` for a starter) and populate your
OpenRouter API key.

The `cloud_setup` process will have also created a file in `$HOME/.config/klorb` for your
local settings, which you can modify. If this file does not exist, run `bin/klorb init`.

## Running

Run `bin/klorb` to start the terminal UI.

There are some options to control the interface; see `bin/klorb --help` for a list.
There is further detail and examples in [usage.md](klorb/usage.md).
