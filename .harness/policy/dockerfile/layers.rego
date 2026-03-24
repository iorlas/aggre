package dockerfile.layers

# Dockerfile layer ordering policy.
# Ensures dependency manifests are copied before source code,
# and dependency install happens between manifest copy and source copy.
#
# Input: flat array of Dockerfile instructions [{Cmd, Flags, Value, Stage}, ...]

import rego.v1

# ── Dependency manifest files (copying these before install is correct) ──
dep_manifests := {
	"pyproject.toml", "uv.lock", "requirements.txt", "Pipfile", "Pipfile.lock",
	"poetry.lock", "setup.py", "setup.cfg", "pdm.lock",
	"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
	"go.mod", "go.sum",
	"Cargo.toml", "Cargo.lock",
	"Gemfile", "Gemfile.lock",
	"README.md", "LICENSE",
}

# Broad source directories
source_dirs := {"src", "app", "lib", "cmd", "internal", "pkg", "server", "client"}

# Dependency install commands
dep_install_pattern := `(uv sync|pip install|pip3 install|poetry install|pdm install|npm ci|npm install|yarn install|pnpm install|bun install|go mod download|go build|cargo build|cargo install|bundle install|gem install)`

# ── Policy: no broad COPY before dependency install ──

deny contains msg if {
	# Find a broad COPY instruction
	some i, copy_instr in input
	copy_instr.Cmd == "copy"
	_is_broad_copy(copy_instr)

	# Find a dep install AFTER this copy in the same stage
	some j, install_instr in input
	j > i
	install_instr.Cmd == "run"
	install_instr.Stage == copy_instr.Stage
	some val in install_instr.Value
	regex.match(dep_install_pattern, val)

	# And no dep install BEFORE this copy in the same stage
	not _has_dep_install_before(i, copy_instr.Stage)

	sources := concat(" ", array.slice(copy_instr.Value, 0, count(copy_instr.Value) - 1))
	msg := sprintf("COPY %s appears before dependency install — busts cache on every code change. Copy dependency manifests first, install deps, then copy source.", [sources])
}

_has_dep_install_before(idx, stage) if {
	some k, instr in input
	k < idx
	instr.Cmd == "run"
	instr.Stage == stage
	some val in instr.Value
	regex.match(dep_install_pattern, val)
}

# ── Broad copy detection ──

_is_broad_copy(instr) if {
	# Not a multi-stage copy (--from=...)
	not _has_from_flag(instr)

	sources := array.slice(instr.Value, 0, count(instr.Value) - 1)
	some src in sources
	_is_broad_source(src)
}

_has_from_flag(instr) if {
	some flag in instr.Flags
	startswith(flag, "--from")
}

_is_broad_source(src) if {
	src in {".", "./"}
}

_is_broad_source(src) if {
	trim_right(src, "/") in source_dirs
}
