
# klorb-vscode

VS Code extension that hosts the Klorb session panel in the editor.

## Setup

From this directory (`vscode-plugin/`):

```bash
make sync_deps
make install_dev_deps
```

* `make sync_deps` runs `npm install`, resolving `package.json` into `package-lock.json`.
* `make install_dev_deps` installs the locked dependencies (including lint/test/packaging
  tooling) via `npm ci`. For a runtime-only install, use `make install_deps` instead — today
  that installs nothing, since the extension has no runtime dependencies beyond the VS Code
  API itself.
* `make lint test` runs the local CI suite. See `make help` for all targets.

## Trying it out

```bash
make install
```

Packages the extension into a `.vsix` (via `compile` + `vsce package`) and installs it into
the local VS Code with `code --install-extension`. Reload the VS Code window afterward to pick
it up. Use the **Klorb: Restart Session** command from the command palette to reload the panel's
webview after recompiling, without reloading the whole window. Use **Klorb: Restart Server** to
kill and respawn the `klorb server` child process the panel talks to — needed after changing the
`klorb.serverPath`/`klorb.openRouterApiKey` settings, or if the server process wedges.

After reloading, open the **Secondary Side Bar** if it isn't already open — `Ctrl+Alt+B` (or `Cmd+Option+B` on Mac), or View → Appearance → Secondary Side Bar — and the Klorb icon should appear on its icon rail.

## Settings

* `klorb.serverPath` — path to the `klorb` command used to launch `klorb server` (default:
  `"klorb"`, i.e. whatever resolves on `PATH`).
* `klorb.openRouterApiKey` — OpenRouter API key, passed to the `klorb server` process as its
  `OPENROUTER_API_KEY` environment variable.

See `docs/specs/vscode-plugin.md` at the repo root for how the extension is put together.
