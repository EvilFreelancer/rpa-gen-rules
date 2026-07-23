# Authoring & maintaining the `rpa-gen-rules` skill

Agent guide for working **inside this skill repository**. Read it before editing `SKILL.md`, the README,
or any plugin manifest.

> Naming (Russian): the adjective for "agent / agentic" is **«агентный»**, never «агентский».

## Repository layout

This skill is packaged for **Claude Code**, **Cursor**, and **OpenAI Codex** (and installs as a plain
skill folder for Kimi Code CLI and others).

```
rpa-gen-rules/
├─ .claude-plugin/
│  └─ plugin.json        # Claude Code plugin manifest
├─ .cursor-plugin/
│  └─ plugin.json        # Cursor plugin manifest
├─ .codex-plugin/
│  └─ plugin.json        # Codex plugin manifest (interface block + skills: "./")
├─ SKILL.md              # CANONICAL skill: YAML frontmatter + instructions (repo root)
├─ references/           # bundled reference material, linked from SKILL.md
├─ README.md             # human overview
├─ AGENTS.md             # this file
└─ LICENSE
```

## ⚠️ Golden rule: a change anywhere must be synced across the whole skill

The same facts (the skill text, its `version`, its `description`) are duplicated in several files so the
skill works across platforms. **Whenever you edit one, propagate the change to every place it is mirrored —
in the same commit.** Nothing here is allowed to drift.

### 1. `SKILL.md` lives at the repo root — single canonical copy

- `SKILL.md` (repo root) — **canonical**. The repo itself is the skill folder: copy or symlink the whole
  repository into `~/.claude/skills/rpa-gen-rules/`, `~/.cursor/skills/rpa-gen-rules/`, etc.
- Bundled assets (`references/`, `scripts/`, …) live next to it at the repo root and are referenced from
  `SKILL.md` by relative path.

### 2. `version` must match in every manifest

When you bump the version, update it in **all** of these:

- `SKILL.md` frontmatter (repo root).
- `.claude-plugin/plugin.json`
- `.cursor-plugin/plugin.json`
- `.codex-plugin/plugin.json`

### 3. `description` must stay consistent across manifests

The same description text appears in `SKILL.md`, `.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`,
and `.codex-plugin/plugin.json` (plus a shorter `interface.shortDescription` in the Codex manifest).
Keep them in step.

Quick check that the version is identical everywhere:

```bash
grep -RhoE '"version"\s*:\s*"[^"]+"' .claude-plugin .cursor-plugin .codex-plugin | sort -u
```

## `SKILL.md` frontmatter

```yaml
---
name: rpa-gen-rules
version: <semver>          # major.minor.patch
description: >
  When to use this skill and the triggers that should load it. Be specific — the agent uses this text
  to auto-select the skill.
---
```

- `name` **must** equal the directory name `rpa-gen-rules` and the slash command (`/rpa-gen-rules`).
- Bump `version` on every substantive change.

## README.md

Human-facing overview: purpose, when to use, usage examples, install (plugin + folder), and a
**Source & attribution** section. Keep it in step with `SKILL.md`.

## If the skill ships code

Put scripts under `scripts/` at the repo root. Add tests where it makes sense and make sure they pass
before committing.

## Commit checklist

- [ ] `SKILL.md` edited where needed.
- [ ] `version` bumped and identical in `SKILL.md`, `.claude-plugin/plugin.json`,
      `.cursor-plugin/plugin.json`, `.codex-plugin/plugin.json`.
- [ ] `description` consistent across all manifests (and `interface.shortDescription` in Codex).
- [ ] README updated.
- [ ] Conventional commit message, e.g. `feat(rpa-gen-rules): …` / `fix(rpa-gen-rules): …`.

---

Part of the **[rpa-skills](https://github.com/EvilFreelancer/rpa-skills)** collection — see its `AGENTS.md`
for the collection-wide authoring process, and [cursor-vibe-prompts](https://github.com/EvilFreelancer/cursor-vibe-prompts).
