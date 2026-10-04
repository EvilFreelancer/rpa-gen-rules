import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "1.3.3"
POLICY_FILES = (
    ROOT / "SKILL.md",
    ROOT / "README.md",
    ROOT / "references" / "bdd-and-agents.md",
    ROOT / "references" / "cursor-examples" / ".cursor" / "rules" / "workflow.mdc",
    ROOT / "references" / "claude-examples" / ".claude" / "rules" / "workflow.md",
)
MANIFESTS = (
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".cursor-plugin" / "plugin.json",
    ROOT / ".codex-plugin" / "plugin.json",
)
CODEX_EXAMPLE = ROOT / "references" / "codex-examples" / ".codex"
HOOK_PATH = CODEX_EXAMPLE / "hooks" / "attach_rules.py"


def skill_frontmatter() -> str:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise AssertionError("SKILL.md has no YAML frontmatter")
    return match.group(1)


def folded_description(frontmatter: str) -> str:
    lines = frontmatter.splitlines()
    start = next(i for i, line in enumerate(lines) if line == "description: >")
    parts = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        if line.strip():
            parts.append(line.strip())
    return " ".join(parts)


def load_hook_module():
    spec = importlib.util.spec_from_file_location("rpa_gen_rules_codex_hook", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load Codex hook template")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillContractTest(unittest.TestCase):
    def test_release_metadata_is_consistent_and_trigger_only(self) -> None:
        frontmatter = skill_frontmatter()
        version_match = re.search(r"^\s+version:\s*([^\s]+)$", frontmatter, re.MULTILINE)
        self.assertIsNotNone(version_match)
        self.assertEqual(EXPECTED_VERSION, version_match.group(1))

        description = folded_description(frontmatter)
        self.assertTrue(description.startswith("Use when "), description)
        self.assertIn("create, refresh, audit, or synchronize project instructions", description)
        self.assertIn("AGENTS.md", description)
        for workflow_word in ("layered-cake", "Rules Sync", "installs a hook"):
            self.assertNotIn(workflow_word, description)

        for manifest_path in MANIFESTS:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(EXPECTED_VERSION, manifest["version"], manifest_path)
            self.assertEqual(description, manifest["description"], manifest_path)

    def test_skill_keeps_codex_hook_but_drops_the_noop_index(self) -> None:
        self.assertTrue((CODEX_EXAMPLE / "hooks.json").is_file())
        self.assertTrue(HOOK_PATH.is_file())
        self.assertFalse((CODEX_EXAMPLE / "rules.md").exists())

        skill = POLICY_FILES[0].read_text(encoding="utf-8")
        self.assertIn("Do not generate `.codex/rules.md`", skill)

        hook = HOOK_PATH.read_text(encoding="utf-8")
        self.assertNotIn("single source of truth", hook)

        for path in POLICY_FILES[1:]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".codex/rules.md", text, path)
            self.assertNotIn("rules.md index", text, path)

    def test_codex_guidance_uses_root_agents_and_cursor_rule_hook(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Codex (`AGENTS.md` + project hook)", skill)
        self.assertIn("Do not generate `.codex/rules.md`", skill)
        self.assertIn("reads `.cursor/rules/*.mdc`", skill)
        self.assertIn("path-scoped", skill)

    def test_codex_hook_parses_scalar_and_yaml_list_globs(self) -> None:
        hook = load_hook_module()
        cases = (
            ("globs: internal/**/*.go, cmd/**/*.go", ["internal/**/*.go", "cmd/**/*.go"]),
            (
                "globs:\n  - internal/**/*.go\n  - cmd/**/*.go",
                ["internal/**/*.go", "cmd/**/*.go"],
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, (globs, expected) in enumerate(cases):
                path = Path(tmp) / f"rule-{index}.mdc"
                path.write_text(
                    f"---\ndescription: test\n{globs}\nalwaysApply: false\n---\n\nBody\n",
                    encoding="utf-8",
                )
                rule = hook.parse_rule(path)
                self.assertIsNotNone(rule)
                self.assertEqual(expected, rule.globs)

    def test_codex_hook_extracts_structured_edit_paths(self) -> None:
        hook = load_hook_module()
        tool_input = {
            "file_path": "internal/llm/openai.go",
            "destination": "internal/llm/openai_new.go",
            "nested": {"path": "cmd/coddy/providers.go"},
        }
        self.assertEqual(
            [
                "internal/llm/openai.go",
                "internal/llm/openai_new.go",
                "cmd/coddy/providers.go",
            ],
            hook.patched_paths(tool_input),
        )

    def test_scoped_cursor_rules_are_not_mapped_to_always_apply(self) -> None:
        for path in (ROOT / "SKILL.md", ROOT / "references" / "bdd-and-agents.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "Cursor `globs` with `alwaysApply: false`",
                text,
                f"{path}: missing scoped mapping",
            )


if __name__ == "__main__":
    unittest.main()
