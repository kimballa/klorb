
# Plan 001: `klorb init`

## Context

* Packaging can't place `/etc/klorb/klorb-config.json` or `~/.config/klorb/klorb-config.json`
    at install time on its own — modern wheels don't reliably support `data_files`-style
    absolute install destinations, and neither `pip install` nor `uv install` has any notion of
    "was this a sudo, site-wide install vs. a per-user one" to branch on anyway. `etc/klorb-
    config.json` in the repo stays an uninstalled reference file for now.
* Add a `klorb init` CLI command instead: `klorb init --system` copies the reference config to
    `/etc/klorb/klorb-config.json`, refusing loudly if not running as root; `klorb init --user`
    copies it to `$KLORB_CONFIG_DIR/klorb-config.json` (default `~/.config/klorb`). Both should
    refuse to clobber an existing file without an explicit `--force`.
* Could double as (or share code with) the entry point for the per-project `.klorb/` bootstrap
    flow above — same "copy a starter config into place" idea, just a different destination and
    trust prompt.

## Flow

* You can run `klorb init [{--system | --user}]` directly from the command line at any time.
  * default is `--user` unless your username is `root`.
  * if a config file already exists in the target location, this does nothing; it says on
    stderr that the target config file (show the path) already exists, and exit w/ status 0.
    Do not overwrite it.
  * ... unless `--force` is provided.
    * If --force is provided and it does need to overwrite the config file,  it should
      warn on stderr that it is doing so, while proceeding to do so (don't wait for interactive user
      confirmation).
  * Parsing of the `--system`, `--user`, and `--force` args is only activated if `init` is
    a positional arg immediately after the process name (i.e., argv[1] = `init`).
  * it should make any necessary directories (/etc/klorb or path elements required for
    `$KLORB_CONFIG_DIR`)
  * Directory-wise, this uses the same defaults / env var mapping that normal invocation of `bin/klorb`
    uses.  That is to say: it should load the dotenv and respect environment variables as overrides
    of the internal defaults, using `paths.py` per the normal route of the interactive klorb TUI.
  * Then it writes out the reference config to this location.
    * This means the reference config needs to be bundled as a resource in the egg.
    * it's ok to move it somewhere deeper into the source tree for this.
  * State on stderr that you have written the config file and print its location.

## "executable" symlink

This should also create a symlink at `~/.local/bin/klorb` or `/usr/bin/klorb` (for user and system
install, respectively) to the `bin/klorb` that got installed from the python egg. (or more precisely,
whatever `...../bin/klorb` process got put on the argv for the klorb process running `klorb init`.)

Announce on stderr that you have done so.

If this already exists, don't overwrite it unless --force is specified, in which case remove the
existing symlink and create a new one.

When installing in --user mode, create `~/.local/bin/` if needed.

## exit status

0 if we created things correctly as-needed. 1 otherwise.

Whatever caused a problem should have put some diagnostic info on stderr to say why.
If there is a problem creating a dir, writing a file, a symlink, etc., we terminate
our operation at the *first* such problem.

## Running from within the app

* Create a new palette option "Init local klorb config" that does this for the current user when selected.
* When the interactive TUI is starting up, if `~/.config/klorb/klorb-config.json` does not exist,
  add a message to the terminal scroll that says:
  "Klorb configuration file not found. Run `>Init local klorb config` to set up."
