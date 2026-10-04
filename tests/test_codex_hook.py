import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "references/codex-examples/.codex/hooks/attach_rules.py"


def load_hook(name: str):
    spec = importlib.util.spec_from_file_location(name, HOOK)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {HOOK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def block_rule(directory: Path) -> Path:
    path = directory / "provider-proxy.mdc"
    path.write_text(
        "---\n"
        "description: Provider proxy\n"
        "globs:\n"
        "  - \"internal/llm/**/*.go\" # provider core\n"
        "  # provider commands\n"
        "\n"
        "  - cmd/coddy/providers.go # sign-in commands\n"
        "alwaysApply: false\n"
        "---\n\n"
        "Every provider request follows its proxy.\n",
        encoding="utf-8",
    )
    return path


def flow_rule(directory: Path) -> Path:
    path = directory / "flow.mdc"
    path.write_text(
        "---\n"
        "description: \"Flow rule\" # display text\n"
        "globs: [\"fixtures/foo,bar.go\", \"internal/**/*.go\"] # scoped paths\n"
        "alwaysApply: true # required\n"
        "---\n\n"
        "Flow rule body.\n",
        encoding="utf-8",
    )
    return path


def always_rule(directory: Path) -> Path:
    path = directory / "always.mdc"
    path.write_text(
        "---\ndescription: Always\nalwaysApply: true\n---\n\nAlways follow the workflow.\n",
        encoding="utf-8",
    )
    return path


def run_hook_optional(module, rules_dir: Path, state_dir: Path, payload: dict):
    module.RULES_DIR = rules_dir
    module.STATE_DIR = state_dir
    payload.setdefault("session_id", "hook-case")
    old_stdin = sys.stdin
    output = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(output):
            assert module.main() == 0
    finally:
        sys.stdin = old_stdin
    if not output.getvalue():
        return None
    return json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]


def run_pretool(module, rules_dir: Path, state_dir: Path, tool_input: dict) -> str:
    context = run_hook_optional(
        module,
        rules_dir,
        state_dir,
        {"hook_event_name": "PreToolUse", "tool_input": tool_input},
    )
    if context is None:
        raise AssertionError("hook emitted no context")
    return context


def run_concurrent_deliveries() -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        script = root / ".codex/hooks/attach_rules.py"
        script.parent.mkdir(parents=True)
        shutil.copy2(HOOK, script)
        rules_dir = root / ".cursor/rules"
        rules_dir.mkdir(parents=True)
        block_rule(rules_dir)
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "shared-session",
                "tool_input": {"file_path": "internal/llm/openai.go"},
            }
        )
        processes = [
            subprocess.Popen(
                [sys.executable, str(script)],
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(12)
        ]
        for process in processes:
            process.stdin.write(payload)
            process.stdin.close()
        outputs = []
        errors = []
        for process in processes:
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            returncode = process.wait(timeout=15)
            process.stdout.close()
            process.stderr.close()
            if returncode != 0:
                errors.append(stderr)
            elif stdout:
                outputs.append(stdout)
        if errors:
            raise AssertionError("\n".join(errors))
        return outputs


class CodexHookTest(unittest.TestCase):
    def test_valid_yaml_forms_are_preserved(self):
        hook = load_hook("yaml_forms")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            block = hook.parse_rule(block_rule(root))
            self.assertEqual(["internal/llm/**/*.go", "cmd/coddy/providers.go"], block.globs)
            flow = hook.parse_rule(flow_rule(root))
            self.assertEqual("Flow rule", flow.description)
            self.assertEqual(["fixtures/foo,bar.go", "internal/**/*.go"], flow.globs)
            self.assertTrue(flow.always)

    def test_session_sources_control_scoped_dedupe(self):
        hook = load_hook("lifecycle")
        with tempfile.TemporaryDirectory(dir=hook.REPO_ROOT) as tmp:
            root = Path(tmp)
            rules = root / "rules"
            rules.mkdir()
            always_rule(rules)
            block_rule(rules)
            state = root / "state"
            tool_input = {"file_path": "internal/llm/openai.go"}
            self.assertIn("Every provider request", run_pretool(hook, rules, state, tool_input))
            run_hook_optional(hook, rules, state, {"hook_event_name": "SessionStart", "source": "resume"})
            self.assertIsNone(
                run_hook_optional(
                    hook,
                    rules,
                    state,
                    {"hook_event_name": "PreToolUse", "tool_input": tool_input},
                )
            )
            for source in ("compact", "clear", "startup"):
                run_hook_optional(hook, rules, state, {"hook_event_name": "SessionStart", "source": source})
                self.assertIn("Every provider request", run_pretool(hook, rules, state, tool_input))

    def test_paths_are_contained_in_repository(self):
        hook = load_hook("containment")
        inside = str(hook.REPO_ROOT / "internal/llm/openai.go")
        escaped = str(hook.REPO_ROOT / "../outside.go")
        self.assertEqual(
            ["internal/llm/openai.go"],
            hook.patched_paths(
                {
                    "file_path": inside,
                    "destination": escaped,
                    "source": "internal/llm/\x00bad.go",
                }
            ),
        )

    def test_session_start_preamble_is_grammatical(self):
        hook = load_hook("preamble")
        with tempfile.TemporaryDirectory(dir=hook.REPO_ROOT) as tmp:
            root = Path(tmp)
            rules = root / "rules"
            rules.mkdir()
            always_rule(rules)
            context = run_hook_optional(
                hook,
                rules,
                root / "state",
                {"hook_event_name": "SessionStart", "source": "startup"},
            )
            self.assertNotIn("They are The", context)
            self.assertIn("The Codex project hook attached them", context)

    def test_rule_delivery_is_interprocess_safe(self):
        outputs = run_concurrent_deliveries()
        self.assertEqual(1, len(outputs))
        self.assertIn("Every provider request follows its proxy.", outputs[0])


if __name__ == "__main__":
    unittest.main()
