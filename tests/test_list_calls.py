import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "list_calls.py"
SPEC = importlib.util.spec_from_file_location("list_calls", SCRIPT)
parser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parser)


def event(seq, payload):
    return {"seq": seq, "role": "event", "content": json.dumps(payload)}


class TrajectoryParsingTests(unittest.TestCase):
    def cli(self, data, *args):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trajectory.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), str(path), *args],
                text=True, capture_output=True, check=False,
            )

    def test_atif_calls_keep_result_pairing(self):
        data = {"steps": [{"step_id": 9, "tool_calls": [
            {"tool_call_id": "a", "function_name": "Read", "arguments": {"file_path": "a.py"}},
            {"tool_call_id": "b", "function_name": "Bash", "arguments": {"command": "pytest -q"}},
        ], "observation": {"results": [
            {"source_call_id": "b", "content": '{"exit_code": 1, "output": "BUILD FAILED"}'},
            {"source_call_id": "a", "content": "source text"},
        ]}}]}
        rows = parser.records(data)
        self.assertEqual([r["tool"] for r in rows], ["Read", "Bash"])
        self.assertEqual(rows[0]["result"], "source text")
        self.assertEqual(rows[1]["exit"], 1)
        self.assertIn("ERR", parser.flags(rows[1]))

    def test_codex_exec_patch_is_displayed_and_searchable(self):
        source = (
            'text(await tools.exec_command({cmd:"cat src/client.py"}));\n'
            'text(await tools.apply_patch("*** Begin Patch\\n*** Update File: src/client.py\\n'
            '+CORRECT_TIMEOUT = 30\\n*** End Patch"));'
        )
        data = {"steps": [{"step_id": 2, "tool_calls": [
            {"tool_call_id": "edit", "function_name": "exec", "arguments": {"input": source}},
        ], "observation": {"results": [{"source_call_id": "edit", "content": "Success"}]}}]}
        shown = self.cli(data, "-s", "1")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("CORRECT_TIMEOUT = 30", shown.stdout)
        self.assertIn("apply_patch", shown.stdout)
        searched = self.cli(data, "-p", "CORRECT_TIMEOUT")
        self.assertEqual(searched.returncode, 0, searched.stderr)
        self.assertTrue(searched.stdout.startswith("1\t"), searched.stdout)

    def test_normalized_issue_order_and_out_of_order_results(self):
        data = [
            event(1, {"message": {"content": [
                {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "a.py"}},
                {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "pytest"}},
            ]}}),
            event(2, {"message": {"content": [
                {"type": "tool_result", "tool_use_id": "b",
                 "content": '{"exit_code": 1, "stdout": "", "stderr": "Traceback (most recent call last)"}'},
            ]}}),
            event(3, {"message": {"content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "file text"},
            ]}}),
            event(4, {"type": "system", "subtype": "compact_boundary",
                      "compact_metadata": {"trigger": "auto", "pre_tokens": 100, "post_tokens": 20}}),
        ]
        rows = parser.records(data)
        self.assertEqual([r["tool"] for r in rows if not r.get("compact")], ["Read", "Bash"])
        self.assertEqual(rows[0]["native"], "seq=1→3")
        self.assertEqual(rows[1]["native"], "seq=1→2")
        self.assertEqual(rows[1]["exit"], 1)
        self.assertTrue(rows[2]["compact"])
        self.assertEqual(rows[0]["raw_args"], {"file_path": "a.py"})

    def test_native_codex_start_order_not_completion_order(self):
        data = [
            {"type": "item.started", "item": {"id": "a", "type": "command_execution", "command": "slow"}},
            {"type": "item.started", "item": {"id": "b", "type": "command_execution", "command": "fast"}},
            {"type": "item.completed", "item": {"id": "b", "type": "command_execution", "command": "fast",
                                                "aggregated_output": "second done first", "exit_code": 0}},
            {"type": "item.completed", "item": {"id": "a", "type": "command_execution", "command": "slow",
                                                "aggregated_output": "first done second", "exit_code": 0}},
        ]
        rows = parser.records(data)
        self.assertEqual([r["native"] for r in rows], ["a", "b"])
        self.assertEqual(rows[0]["result"], "first done second")

    def test_compaction_does_not_consume_call_number(self):
        data = {"steps": [
            {"step_id": 1, "source": "user", "message": parser.COMPACT_PREFIX + "..."},
            {"step_id": 2, "tool_calls": [{"tool_call_id": "a", "function_name": "Bash",
                                          "arguments": {"command": "echo done"}}]},
        ]}
        result = self.cli(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[COMPACT]", result.stdout)
        self.assertTrue(any(line.startswith("1\t") for line in result.stdout.splitlines()))

    def test_flags_and_result_search(self):
        row = {"args": "nohup build > build.log &", "result": "Output too large\nBUILD FAILED", "exit": 1}
        self.assertEqual(set(parser.flags(row).split(",")), {"exit=1", "TRUNC", "ERR", "BG"})
        data = {"steps": [{"step_id": 1, "tool_calls": [
            {"tool_call_id": "a", "function_name": "Bash", "arguments": {"command": "build"}},
        ], "observation": {"results": [{"source_call_id": "a", "content": "BUILD FAILED"}]}}]}
        result = self.cli(data, "-r", "BUILD FAILED")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("1\t"))

    def test_unsupported_codex_normalized_reports_error(self):
        data = [event(1, {"type": "item.started", "item": {
            "id": "a", "type": "command_execution", "command": "pwd",
        }})]
        result = self.cli(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("use ATIF", result.stderr)


if __name__ == "__main__":
    unittest.main()
