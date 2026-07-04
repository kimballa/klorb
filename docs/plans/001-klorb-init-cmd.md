
# Plan 001: `klorb init`

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
