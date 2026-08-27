# Project skills

Skills committed here are auto-discovered by Claude Code for anyone working in this
repo — no install step. Invoke one with `/<skill-name>`, or let it load automatically
when relevant.

## First-party (this project)

- `new-agent` — scaffold a new AI trader agent (persona + strategies + universe).
- `new-strategy` — scaffold and validate a new deterministic `indicator_dsl` strategy.

## Vendored: Matt Pocock's engineering & productivity skills

The remaining skill folders are vendored from **[mattpocock/skills](https://github.com/mattpocock/skills)**
(the `mattpocock-skills` plugin in Anthropic's official marketplace), pinned at **v1.2.3**.
They cover the dev workflow: `triage`, `to-tickets`, `to-spec`, `tdd`, `code-review`,
`diagnosing-bugs`, `domain-modeling`, `codebase-design`, `wayfinder`, and more.

These are vendored (copied in) so every contributor gets the same skills with zero
setup. The per-runtime `agents/` adapter files were omitted; each skill's `SKILL.md`
and its sibling docs are intact. To pull upstream updates, re-copy from a newer
plugin release. Configuration for the workflow (issue tracker, triage labels, domain
docs) lives in `docs/agents/` and is referenced from `CLAUDE.md`.

Vendored under the MIT License — see `LICENSE-mattpocock-skills` in this directory.
