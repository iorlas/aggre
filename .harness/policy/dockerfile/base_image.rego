package dockerfile.base_image

# Dockerfile base image policy.
# Warns when Alpine base is used with stacks that have musl libc issues.
#
# Input: flat array of Dockerfile instructions [{Cmd, Flags, Value, Stage}, ...]

import rego.v1

# Stacks where musl causes native extension breakage
musl_problem_stacks := {"python", "python3", "uv", "node", "ruby"}

# ── Policy: no Alpine for musl-sensitive stacks ──

deny contains msg if {
	some instr in input
	instr.Cmd == "from"
	image := instr.Value[0]

	contains(lower(image), "alpine")

	some stack in musl_problem_stacks
	contains(lower(image), stack)

	msg := sprintf("Alpine base `%s` with %s — musl libc causes compatibility issues with native extensions. Use -slim (Debian) variant instead.", [image, stack])
}
