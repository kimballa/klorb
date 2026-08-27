# (c) Copyright 2026 Aaron Kimball
#
# Makefile to invoke builds in dependency repositories to vendor dependency binaries.

SHELL:=/bin/bash

commands=all help clean distclean

# ARCH is a short, typeable stand-in for the Rust target triple actually being built --
# `make ARCH=<arch>` to pick a target other than the host's own. Vendored output lands under the
# triple itself, not ARCH: `vnd/<target-triple>/chainlink`, matching cargo's own
# `target/<target-triple>/release/` layout and `klorb.tools.tasks.common`'s lookup, and leaving
# room for a future non-Linux triple (e.g. `aarch64-apple-darwin`) without renaming anything.
ARCH ?= $(shell uname -m)

CHAINLINK_REPO_DIR=../../chainlink
CARGO=source $$HOME/.cargo/env && cargo

# Rust target triple and cross-linker for each ARCH this Makefile knows how to vendor
# chainlink for. `x86_64-linux-gnu-gcc`/`aarch64-linux-gnu-gcc` come from the
# `gcc-x86-64-linux-gnu`/`gcc-aarch64-linux-gnu` apt packages -- installed natively via the
# `gcc` multiarch support on a matching host, or as a cross-toolchain otherwise -- and are
# needed because chainlink's `rusqlite` dependency compiles bundled C SQLite source for
# whichever target triple cargo is building.
RUST_TARGET_x86_64=x86_64-unknown-linux-gnu
RUST_TARGET_aarch64=aarch64-unknown-linux-gnu
RUST_TARGET=$(RUST_TARGET_$(ARCH))

RUST_CC_x86_64=x86_64-linux-gnu-gcc
RUST_CC_aarch64=aarch64-linux-gnu-gcc
RUST_CC=$(RUST_CC_$(ARCH))

all_vendor_files=$(RUST_TARGET)/chainlink

# `export ... &&`, not an env-var prefix: a prefix assignment only applies to the one command it
# precedes, and that command here is `source`, not the `cargo build` that actually needs it.
CARGO_TARGET_EXPORTS=export CC_$(subst -,_,$(RUST_TARGET))=$(RUST_CC) \
	CARGO_TARGET_$(shell echo $(RUST_TARGET) | tr a-z- A-Z_)_LINKER=$(RUST_CC) &&

$(RUST_TARGET)/chainlink:
	mkdir -p $(RUST_TARGET)
	cd $(CHAINLINK_REPO_DIR)/chainlink && $(CARGO_TARGET_EXPORTS) $(CARGO) build --release --target $(RUST_TARGET)
	cp $(CHAINLINK_REPO_DIR)/chainlink/target/$(RUST_TARGET)/release/chainlink $(RUST_TARGET)/chainlink


all: $(all_vendor_files)

help:
	@echo "Build architecture is $(ARCH) (target triple $(RUST_TARGET))"
	@echo 'Use make ARCH=<arch> to override'
	@echo ""
	@echo "Targets: $(commands)"

clean:
	cd $(CHAINLINK_REPO_DIR)/chainlink && $(CARGO) clean --release --target $(RUST_TARGET)

distclean: clean


.PHONY: $(commands)
