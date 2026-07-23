# © Copyright 2026 Aaron Kimball
#
# vscode-plugin Makefile
#
# * `make help` for all commands
# * `make lint typecheck test` to run all CI checks.
# * `make install_dev_deps` to install local deps, `install_deps` for production
#   * `make sync_deps` to resolve and update the package-lock.json from package.json
# * `make install` to package the extension as a .vsix and install it into this VS Code

COMMANDS=help lint typecheck clean distclean sync_deps \
	install_deps install_dev_deps test compile install all

NPM=npm
NODE_BIN=node_modules/.bin
ESLINT=$(NODE_BIN)/eslint
TSC=$(NODE_BIN)/tsc
VITEST=$(NODE_BIN)/vitest
VSCE=$(NODE_BIN)/vsce
ESBUILD=$(NODE_BIN)/esbuild
# The VS Code CLI used to install the packaged .vsix into the local editor. Overridable
# (e.g. `CODE=codium make install`) for editors that ship a differently-named CLI.
CODE ?= code

VSIX=klorb-vscode.vsix

all: lint typecheck test

help:
	@echo "Available commands:"
	@echo "${COMMANDS}"

lint:
	"$(ESLINT)" src test

# Type-checks both the extension host code and the webview code without bundling -- see
# `compile`, which builds on this to also produce the webview's runtime bundle.
typecheck:
	"$(TSC)" -p ./
	"$(TSC)" -p tsconfig.webview.json

compile: typecheck
	"$(ESBUILD)" src/webview/main.tsx --tsconfig=tsconfig.webview.json --bundle \
		--sourcemap --define:process.env.NODE_ENV=\"development\" \
		--outfile=out/webview/main.js --format=iife --platform=browser --target=es2022

test:
	"$(VITEST)" run

# Resolves dependency versions from package.json and rewrites package-lock.json. Analogous to
# klorb/Makefile's `uv pip compile` step for the Python side.
sync_deps:
	"$(NPM)" install
	@echo ""
	@echo "package-lock.json updated from package.json."
	@echo "Use 'make install_deps' to install runtime dependencies."
	@echo "Use 'make install_dev_deps' to install runtime and dev dependencies."

# Installs exactly what's pinned in package-lock.json, production dependencies only.
install_deps:
	"$(NPM)" ci --omit=dev

# Installs exactly what's pinned in package-lock.json, including devDependencies.
install_dev_deps:
	"$(NPM)" ci

# Packages the extension into a .vsix and installs it into the local VS Code, so a change to
# the source can be tried out in the actual editor rather than only via `test`/`lint`. --force
# is required or `code` silently no-ops when the installed .vsix is already at this version
# (see package.json's "version"), which otherwise makes repeated `make install` runs during
# development look like they never picked up a change.
install: compile
	"$(VSCE)" package --out "$(VSIX)"
	"$(CODE)" --force --install-extension "$(VSIX)"

clean:
	-rm -rf out
	-rm -rf coverage
	-rm -f "$(VSIX)"
	-rm -f tsconfig.tsbuildinfo

distclean: clean
	-rm -rf node_modules

.PHONY: ${COMMANDS}
