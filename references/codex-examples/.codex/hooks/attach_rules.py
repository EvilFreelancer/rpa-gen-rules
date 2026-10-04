#!/usr/bin/env python3
"""Attach `.cursor/rules/*.mdc` to Codex sessions deterministically.

Codex has no glob-based rule attachment. Its `AGENTS.md` chain is resolved once
per session, walking from the repository root down to the launch directory, so
nested instruction files never load when Codex starts at the root. Telling the
model to "read the relevant rule file" is advisory and gets skipped.

This hook restores Cursor's behaviour by reading the frontmatter of the rule
files directly:

  SessionStart -> inject every rule with `alwaysApply: true`
  PreToolUse   -> inject rules whose `globs` match the paths an `apply_patch`
                  call is about to touch, at most once per rule per session

The hook reads `.cursor/rules/` directly and creates no Codex copy of a rule body.
It fails open: any unexpected input exits 0 with no output, so a broken rule file
can never block an edit.

Wired up in `.codex/hooks.json`. Requires `/hooks` approval in Codex once, and
again after every change to this file (Codex tracks hooks by content hash).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / ".cursor" / "rules"
# State is keyed by repository path so two clones never share dedup state.
STATE_DIR = Path(tempfile.gettempdir()) / (
    "codex-attach-rules-" + hashlib.sha1(str(REPO_ROOT).encode("utf-8")).hexdigest()[:12]
)

# `*** Add File: path`, `*** Update File: path`, `*** Delete File: path`
PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$", re.M)
# `*** Move to: path` carries the destination of a rename
PATCH_MOVE_RE = re.compile(r"^\*\*\*\s+Move to:\s*(.+?)\s*$", re.M)
PATH_FIELDS = frozenset(
    {
        "destination",
        "destination_path",
        "file",
        "file_path",
        "fileName",
        "filePath",
        "new_path",
        "notebook_path",
        "old_path",
        "path",
        "paths",
        "source",
        "target",
    }
)


class Rule:
    def __init__(self, path: Path, description: str, globs: list[str], always: bool, body: str):
        self.path = path
        self.description = description
        self.globs = globs
        self.always = always
        self.body = body

    @property
    def rel(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a Cursor glob into a regex.

    `fnmatch` is unusable here because its `*` also crosses `/`, which makes
    `src/api/**/*.py` miss `src/api/server.py`.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_rule(path: Path) -> Rule | None:
    """Read one `.mdc` file. Frontmatter is flat, so no YAML dependency."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    head = text[3:end]
    body = text[end + 4 :].strip()

    description, globs, always = "", [], False
    lines = head.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        key, sep, value = line.partition(":")
        if not sep:
            index += 1
            continue
        key, value = key.strip(), value.strip()
        if key == "description":
            description = value
        elif key == "globs":
            if value:
                value = value.removeprefix("[").removesuffix("]")
                globs = [g.strip().strip("'\"") for g in value.split(",") if g.strip()]
            else:
                index += 1
                while index < len(lines):
                    item = lines[index].strip()
                    if not item.startswith("-"):
                        index -= 1
                        break
                    pattern = item[1:].strip().strip("'\"")
                    if pattern:
                        globs.append(pattern)
                    index += 1
        elif key == "alwaysApply":
            always = value.lower() == "true"
        index += 1
    return Rule(path, description, globs, always, body)


def load_rules() -> list[Rule]:
    rules = []
    for path in sorted(RULES_DIR.glob("*.mdc")):
        rule = parse_rule(path)
        if rule and rule.body:
            rules.append(rule)
    return rules


def state_file(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")[:120]
    return STATE_DIR / f"{safe}.json"


def load_sent(session_id: str) -> set[str]:
    try:
        return set(json.loads(state_file(session_id).read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_sent(session_id: str, sent: set[str]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file(session_id).write_text(json.dumps(sorted(sent)), encoding="utf-8")
    except Exception:
        pass


def patched_paths(tool_input: object) -> list[str]:
    """Collect repo-relative paths from patch text and structured tool fields."""
    blobs: list[str] = []
    structured: list[str] = []

    def walk(node: object, key: str = "") -> None:
        if isinstance(node, str):
            blobs.append(node)
            if key in PATH_FIELDS:
                structured.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, dict):
            for child_key, item in node.items():
                walk(item, child_key)

    def repo_relative(raw: str) -> str | None:
        path = Path(raw)
        if path.is_absolute():
            try:
                path = path.relative_to(REPO_ROOT)
            except ValueError:
                return None
        else:
            path = Path(os.path.normpath(path))
            if path.parts and path.parts[0] == "..":
                return None
        return path.as_posix()

    walk(tool_input)

    candidates = list(structured)
    for blob in blobs:
        candidates.extend(PATCH_FILE_RE.findall(blob))
        candidates.extend(PATCH_MOVE_RE.findall(blob))

    found: list[str] = []
    for raw in candidates:
        relative = repo_relative(raw.strip())
        if relative and relative not in found:
            found.append(relative)
    return found


def render(rules: list[Rule], preamble: str) -> str:
    chunks = [preamble]
    for rule in rules:
        chunks.append(f"<!-- from {rule.rel} -->\n\n{rule.body}")
    return "\n\n".join(chunks)


def emit(event: str, context: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    event = payload.get("hook_event_name") or os.environ.get("CODEX_HOOK_EVENT", "")
    session_id = payload.get("session_id", "")

    try:
        rules = load_rules()
    except Exception:
        return 0
    if not rules:
        return 0

    if event == "SessionStart":
        if payload.get("source") == "clear":
            state_file(session_id).unlink(missing_ok=True)
        always = [r for r in rules if r.always]
        if not always:
            return 0
        save_sent(session_id, {r.rel for r in always})
        emit(
            event,
            render(
                always,
                "Project rules for this repository, always in force. They are"
                " The Codex project hook attached them from `.cursor/rules/`;"
                " no separate Codex copy is maintained. More rules are attached"
                " automatically when you edit files they cover.",
            ),
        )
        return 0

    if event != "PreToolUse":
        return 0

    paths = patched_paths(payload.get("tool_input"))
    if not paths:
        return 0

    sent = load_sent(session_id)
    matched: list[Rule] = []
    for rule in rules:
        if rule.always or rule.rel in sent or not rule.globs:
            continue
        patterns = [glob_to_regex(g) for g in rule.globs]
        if any(p.match(path) for path in paths for p in patterns):
            matched.append(rule)

    if not matched:
        return 0

    save_sent(session_id, sent | {r.rel for r in matched})
    touched = ", ".join(sorted(set(paths))[:8])
    emit(
        event,
        render(
            matched,
            f"Project rules that cover the files you are editing ({touched})."
            " Apply them to this change before continuing.",
        ),
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail open: never block an edit because of this hook.
        sys.exit(0)
