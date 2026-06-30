# Use XDG-style config/data/state directories, overridable by KLORB_* env vars

* Question: Where should klorb store its configuration, persistent data, and runtime state
  (e.g. session logs) on disk?
* Answer: Follow the XDG base directory convention's shape — `~/.config/klorb` for config,
  `~/.local/share/klorb` for data, `~/.local/state/klorb` for state — but expose these as
  klorb-specific constants (`KLORB_CONFIG_DIR`, `KLORB_DATA_DIR`, `KLORB_STATE_DIR` in
  `klorb.paths`), each overridable by an environment variable of the same name, rather than
  reading the standard `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_STATE_HOME` variables directly.
* Reasoning: The XDG layout is a well-understood, conventional split between
  config/data/state that avoids inventing a bespoke directory scheme. Using klorb-specific
  env vars (rather than the shared XDG_* ones) lets a user or test harness override just
  klorb's directories without affecting every other XDG-aware application on the system, and
  keeps the override mechanism simple (one env var per directory, same name as the constant).
