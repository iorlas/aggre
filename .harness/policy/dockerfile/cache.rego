package dockerfile.cache

# Dockerfile cache mount policy.
# Ensures dependency install commands use --mount=type=cache.
#
# Input: flat array of Dockerfile instructions [{Cmd, Flags, Value, Stage}, ...]

import rego.v1

dep_install_pattern := `(uv sync|pip install|pip3 install|poetry install|pdm install|npm ci|npm install|yarn install|pnpm install|bun install|go mod download|cargo build|cargo install|bundle install|gem install)`

# ── Policy: dep install must use --mount=type=cache ──

deny contains msg if {
	some instr in input
	instr.Cmd == "run"

	some val in instr.Value
	regex.match(dep_install_pattern, val)

	not _has_cache_mount(instr)

	cmd := regex.find_n(dep_install_pattern, val, 1)[0]
	msg := sprintf("`%s` without --mount=type=cache — dependency cache is discarded between builds, slowing rebuilds", [cmd])
}

_has_cache_mount(instr) if {
	some flag in instr.Flags
	contains(flag, "mount=type=cache")
}
