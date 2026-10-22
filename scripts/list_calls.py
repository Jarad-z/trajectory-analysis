#!/usr/bin/env python3
"""List an agent trajectory's tool calls in issue order, with search and full display.

Usage:
  python3 -I list_calls.py TRAJ                # one line per call, plus [COMPACT] markers
  python3 -I list_calls.py TRAJ -p REGEX       # calls whose decoded arguments match
  python3 -I list_calls.py TRAJ -r REGEX       # calls whose results match
  python3 -I list_calls.py TRAJ -s 120-125     # original arguments, decoded view, results

Formats: Harbor ATIF trajectory.json (Claude Code or Codex), Claude platform normalized.json,
Codex exec JSONL (codex.txt). Calls are numbered consecutively in the order they were
issued; the native id (step_id / seq / item id) is printed next to each number.
Flags: exit=N, TRUNC (output truncated), ERR (traceback / build failure), BG (background).
Only reads the trajectory; never executes anything from it.
"""
import argparse
import ast
import json
import re
import signal
import sys

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TRUNC_RE = re.compile(r"truncated output|tokens truncated|chars truncated|\[truncated|Output too large|<persisted-output>", re.I)
ERR_RE = re.compile(r"Traceback \(most recent call last\)|BUILD FAILED|FAILURE: Build failed|^e: ", re.M)
BG_RE = re.compile(r"&\s*$|&\s*(?:echo|sleep|disown)|\bnohup\b|\bsetsid\b|run_in_background", re.M)
BG_RESULT_RE = re.compile(r"Process running with session ID|running in background", re.I)
COMPACT_PREFIX = "This session is being continued from a previous conversation"
JS_STRING_RE = re.compile(r'\b(cmd|chars)\s*:\s*("(?:[^"\\]|\\.)*")')


def load(path):
    text = open(path, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def plain_args(args):
    """Unescaped argument text for searching: string values joined by newlines."""
    if isinstance(args, dict):
        return "\n".join(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in args.values())
    return str(args)


def decode_codex_exec(args):
    """A Codex ATIF `exec` wraps JS such as tools.exec_command({cmd:"..."}); pull out the commands."""
    src = args.get("input", "") if isinstance(args, dict) else str(args)
    parts = []
    for key, literal in JS_STRING_RE.findall(src):
        try:
            value = json.loads(literal)
        except json.JSONDecodeError:
            value = literal
        parts.append(value if key == "cmd" else f"[stdin] {value}")
    return "\n".join(parts) if parts else src


def result_text(content):
    """Normalise a result payload; return (text, exit_code)."""
    texts, exit_code = [], None
    if isinstance(content, str) and content.startswith("[{"):
        try:
            content = ast.literal_eval(content)  # Codex ATIF stores a Python repr of a list
        except (ValueError, SyntaxError):
            pass
    items = content if isinstance(content, list) else [content]
    for item in items:
        t = item.get("text", "") if isinstance(item, dict) else str(item if item is not None else "")
        if t.lstrip().startswith("{"):
            try:
                j = json.loads(t)
            except json.JSONDecodeError:
                j = None
            if isinstance(j, dict) and ("output" in j or "stdout" in j):
                exit_code = j.get("exit_code", exit_code)
                t = j.get("output") or (j.get("stdout", "") + (j.get("stderr") or ""))
                if j.get("original_token_count"):
                    t += f"\n[original_token_count={j['original_token_count']}]"
        texts.append(t)
    return "\n".join(texts), exit_code


def from_atif(data):
    for s in data["steps"]:
        msg = s.get("message") or ""
        if s.get("source") == "user" and isinstance(msg, str) and msg.startswith(COMPACT_PREFIX):
            yield {"compact": True, "native": f"step_id={s['step_id']}", "info": "context summary follows"}
        results = {r.get("source_call_id"): r.get("content") for r in (s.get("observation") or {}).get("results", [])}
        for c in s.get("tool_calls") or []:
            args = c.get("arguments")
            decoded = decode_codex_exec(args) if c["function_name"] == "exec" else plain_args(args)
            text, code = result_text(results.get(c["tool_call_id"], ""))
            yield {"native": f"step_id={s['step_id']}", "tool": c["function_name"], "args": decoded,
                   "raw_args": args, "result": text, "exit": code}


def from_normalized(data):
    calls, order = {}, []
    for e in data:
        if e.get("role") != "event":
            continue
        j = json.loads(e["content"])
        if j.get("type") == "system" and j.get("subtype") == "compact_boundary":
            meta = j.get("compact_metadata") or {}
            order.append({"compact": True, "native": f"seq={e['seq']}",
                          "info": f"{meta.get('trigger')} {meta.get('pre_tokens')}->{meta.get('post_tokens')} tokens"})
            continue
        for p in (j.get("message") or {}).get("content") or []:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "tool_use":
                rec = {"native": f"seq={e['seq']}", "tool": p["name"], "args": plain_args(p.get("input")),
                       "raw_args": p.get("input"), "result": "", "exit": None}
                calls[p["id"]] = rec
                order.append(rec)
            elif p.get("type") == "tool_result" and p.get("tool_use_id") in calls:
                rec = calls[p["tool_use_id"]]
                rec["result"], rec["exit"] = result_text(p.get("content"))
                rec["native"] += f"→{e['seq']}"
                if p.get("is_error"):
                    rec["exit"] = rec["exit"] if rec["exit"] is not None else "is_error"
    if not any(not rec.get("compact") for rec in order):
        for event in data:
            if event.get("role") == "event":
                payload = json.loads(event["content"])
                if payload.get("item"):
                    raise ValueError("Codex events inside normalized.json are not supported; use ATIF trajectory.json or native codex.txt")
    return order


def from_codex_jsonl(data):
    items, order = {}, []
    for j in data:
        it = j.get("item") or {}
        if it.get("type") in (None, "agent_message", "reasoning"):
            continue
        if it["id"] not in items:  # number by issue order (item.started), not completion order
            rec = {"native": it["id"], "tool": it["type"], "args": "", "result": "", "exit": None}
            items[it["id"]] = rec
            order.append(rec)
        rec = items[it["id"]]
        rec["raw_args"] = it.get("command") or it.get("changes") or it.get("query") or it
        rec["args"] = it.get("command") or json.dumps(rec["raw_args"], ensure_ascii=False)
        if j.get("type") == "item.completed":
            rec["result"], rec["exit"] = str(it.get("aggregated_output", "")), it.get("exit_code")
    return order


def records(data):
    if isinstance(data, dict) and "steps" in data:
        return list(from_atif(data))
    if isinstance(data, list) and data and "seq" in data[0] and "role" in data[0]:
        return from_normalized(data)
    return from_codex_jsonl(data)


def flags(rec):
    out = [] if rec["exit"] is None else [f"exit={rec['exit']}"]
    if TRUNC_RE.search(rec["result"]):
        out.append("TRUNC")
    if ERR_RE.search(rec["result"]):
        out.append("ERR")
    if BG_RE.search(rec["args"]) or BG_RESULT_RE.search(rec["result"][:400]):
        out.append("BG")
    return ",".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trajectory")
    ap.add_argument("-p", "--args-regex", help="only calls whose decoded arguments match")
    ap.add_argument("-r", "--result-regex", help="only calls whose results match")
    ap.add_argument("-s", "--show", help="print full calls, e.g. 42 or 40-45")
    opt = ap.parse_args()

    rows, n = [], 0
    try:
        parsed = records(load(opt.trajectory))
    except (ValueError, KeyError, TypeError, OSError) as error:
        ap.error(str(error))
    for rec in parsed:
        if rec.get("compact"):
            rows.append((None, rec))
        else:
            n += 1
            rows.append((n, rec))

    if opt.show:
        lo, _, hi = opt.show.partition("-")
        lo, hi = int(lo), int(hi or lo)
        for num, rec in rows:
            if num is not None and lo <= num <= hi:
                original = json.dumps(rec.get("raw_args", rec["args"]), ensure_ascii=False, indent=2)
                print(f"===== #{num} {rec['native']} {rec['tool']} {flags(rec)}\n--- args (original JSON)\n{original}\n--- decoded args (navigation only)\n{rec['args']}\n--- result\n{rec['result']}\n")
        return

    one = lambda s, k: s.replace("\n", "⏎")[:k]
    for num, rec in rows:
        if num is None:
            if not (opt.args_regex or opt.result_regex):
                print(f"--\t{rec['native']}\t[COMPACT]\t{rec['info']}")
            continue
        searchable_args = rec["args"] + "\n" + plain_args(rec.get("raw_args", ""))
        if opt.args_regex and not re.search(opt.args_regex, searchable_args):
            continue
        if opt.result_regex and not re.search(opt.result_regex, rec["result"], re.M):
            continue
        print(f"{num}\t{rec['native']}\t{rec['tool']}\t{flags(rec)}\t{one(rec['args'], 200)}\t{one(rec['result'], 140)}")


if __name__ == "__main__":
    main()
