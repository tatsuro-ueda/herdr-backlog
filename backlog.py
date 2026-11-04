#!/usr/bin/env python3
"""Backlog: sort the herdr agents sidebar by the mark at the start of each tab name.

Order: 🔴 → 🟡 → 🟢 → 🚪 → unmarked → 🔧. Inside one group the current order is kept.
Tool tabs (🔧) stay out of the list while they are idle or working, and show up
at the bottom only when they need you (blocked, or done and not yet seen).

How it works (two herdr features, nothing else):
  1. Every agent pane gets a display token  `backlog = "<group>-<index>"`
     via `herdr pane report-metadata`. Tool panes also get `backlog_tool = "1"`.
  2. An agent view sorted by that token, and filtered to hide quiet tool panes,
     is set with the socket API (`agent.view.set`), which the CLI does not expose yet.

Commands:
  apply    write tokens that changed, make sure the view is set   (startup, events, action)
  off      clear the view and every token                          (action)
  status   print the current order                                 (popup pane)
  fork     fork the focused Claude Code conversation into a new tab (action)

Python 3.9+ standard library only.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path

SOURCE = "backlog"  # metadata source id and view source id
TOKEN = "backlog"  # token name on each pane
TOOL_TOKEN = "backlog_tool"  # set to "1" on tool panes, cleared on the others
DEFAULT_MARKS = ["🔴", "🟡", "🟢", "🚪"]
DEFAULT_TOOLS = ["🔧"]

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
SOCKET = os.environ.get("HERDR_SOCKET_PATH") or str(
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "herdr" / "herdr.sock"
)
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR", Path.home() / ".config" / "herdr" / "plugins" / "config" / SOURCE))


def die(msg: str) -> None:
    print(f"backlog: {msg}", file=sys.stderr)
    raise SystemExit(1)


# --- herdr access -------------------------------------------------------------


def cli_json(*args: str) -> dict:
    """Run `herdr <args>` and return the `result` object of its JSON output."""
    proc = subprocess.run([HERDR, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"`herdr {' '.join(args)}` failed: {(proc.stderr or proc.stdout).strip()[:200]}")
    try:
        return json.loads(proc.stdout).get("result", {})
    except json.JSONDecodeError:
        die(f"`herdr {' '.join(args)}` did not return JSON")
    return {}


def api(method: str, params: dict | None = None) -> dict:
    """One request on the herdr socket (newline-delimited JSON)."""
    req = {"id": f"backlog:{uuid.uuid4().hex[:8]}", "method": method, "params": params or {}}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(SOCKET)
        sock.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as exc:
        die(f"cannot reach the herdr socket {SOCKET}: {exc}")
    finally:
        sock.close()
    try:
        res = json.loads(buf.split(b"\n")[0].decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        die(f"unreadable reply from the socket: {exc}")
    if "error" in res:
        die(f"{method} failed: {json.dumps(res['error'])}")
    return res.get("result", {})


def write_tokens(pane_id: str, tokens: dict[str, str | None]) -> None:
    """Set (string) or clear (None) several tokens on one pane in one report."""
    args = ["pane", "report-metadata", pane_id, "--source", SOURCE]
    for name, value in tokens.items():
        args += ["--clear-token", name] if value is None else ["--token", f"{name}={value}"]
    proc = subprocess.run([HERDR, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"cannot write to {pane_id}: {(proc.stderr or proc.stdout).strip()[:200]}")


# --- marks and order ------------------------------------------------------------


def load_list(name: str, default: list[str]) -> list[str]:
    """One entry per line from <config dir>/<name>. Falls back to the default."""
    path = CONFIG_DIR / name
    if not path.exists():
        return default
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(line)
    return items or default


def load_marks() -> tuple[list[str], list[str]]:
    """(marks in order, tool marks). Groups: each mark, then unmarked, then tools."""
    return load_list("marks.txt", DEFAULT_MARKS), load_list("tools.txt", DEFAULT_TOOLS)


def agents_with_labels() -> list[dict]:
    agents = cli_json("agent", "list").get("agents", [])
    labels = {t["tab_id"]: t.get("label", "") for t in cli_json("tab", "list").get("tabs", [])}
    for a in agents:
        a["tab_label"] = labels.get(a.get("tab_id"), "")
    return agents


def name_of(a: dict) -> str:
    return a["tab_label"] or a.get("terminal_title_stripped") or a.get("terminal_title") or ""


def is_tool(a: dict, tools: list[str]) -> bool:
    return name_of(a).lstrip().startswith(tuple(tools))


def group_of(a: dict, marks: list[str], tools: list[str]) -> int:
    if is_tool(a, tools):
        return len(marks) + 1
    name = name_of(a).lstrip()
    for i, mark in enumerate(marks):
        if name.startswith(mark):
            return i
    return len(marks)


def token_of(a: dict, name: str = TOKEN) -> str | None:
    return (a.get("tokens") or {}).get(name)


def ordered(agents: list[dict], marks: list[str], tools: list[str]) -> list[dict]:
    """Group by mark; inside a group keep the current order (existing token, then list order)."""
    by_current = sorted(agents, key=lambda a: (token_of(a) is None, token_of(a) or ""))
    return sorted(by_current, key=lambda a: group_of(a, marks, tools))


# A tool pane is listed only while it needs you. `done` means idle and not yet seen.
SHOW = {
    "op": "any",
    "filters": [
        {"op": "not", "filter": {"op": "exists", "field": {"token": TOOL_TOKEN}}},
        {"op": "in", "field": "status", "values": ["blocked", "done"]},
    ],
}
VIEW = {"source": SOURCE, "label": "backlog", "filter": SHOW, "sort": [{"field": {"token": TOKEN}, "order": "asc"}]}


def apply() -> int:
    marks, tools = load_marks()
    agents = agents_with_labels()
    changed = 0
    for i, a in enumerate(ordered(agents, marks, tools), start=1):
        want = {
            TOKEN: f"{group_of(a, marks, tools):02d}-{min(i, 99):02d}",
            TOOL_TOKEN: "1" if is_tool(a, tools) else None,
        }
        diff = {k: v for k, v in want.items() if token_of(a, k) != v}
        if diff:
            write_tokens(a["pane_id"], diff)
            changed += 1
    api("agent.view.set", VIEW)
    print(f"backlog: {len(agents)} agents, {changed} moved")
    return 0


def off() -> int:
    api("agent.view.clear", {"source": SOURCE})
    agents = cli_json("agent", "list").get("agents", [])
    for a in agents:
        mine = {k: None for k in (TOKEN, TOOL_TOKEN) if token_of(a, k) is not None}
        if mine:
            write_tokens(a["pane_id"], mine)
    print(f"backlog: off ({len(agents)} agents back to herdr's default order)")
    return 0


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def status() -> int:
    marks, tools = load_marks()
    print("Backlog order: " + " → ".join(marks) + " → unmarked → " + " ".join(tools) + " (hidden until they need you)")
    print(f"marks file: {CONFIG_DIR / 'marks.txt'}   tools file: {CONFIG_DIR / 'tools.txt'}\n")
    agents = agents_with_labels()
    rows = ordered(agents, marks, tools)
    w = max((width(name_of(a)) for a in rows), default=4)
    for i, a in enumerate(rows, start=1):
        name = name_of(a)
        hidden = is_tool(a, tools) and a.get("agent_status") not in ("blocked", "done")
        note = "  (hidden: " + str(a.get("agent_status")) + ")" if hidden else ""
        print(f"{i:2d}  {name}{' ' * (w - width(name))}  {a['pane_id']:<8}  {token_of(a) or '-'}{note}")
    return 0


# --- fork ------------------------------------------------------------------------


def claude_session(pane: dict) -> str | None:
    """The Claude Code session id running in a pane.

    The pane may be showing another session through Claude's agent view, so first look for
    the session whose name is the pane title (Claude Code's own `~/.claude/sessions/*.json`).
    Then the claude process in the pane. Last, what herdr reports (`agent_session`, herdr 0.9.5+):
    it can be stale, keeping the first session of the pane after Claude moved on to a new one.
    """
    sessions = {}
    for path in (Path.home() / ".claude" / "sessions").glob("*.json"):
        try:
            sessions[path.stem] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    title = str(pane.get("terminal_title_stripped") or "").strip()
    named = [d.get("sessionId") for d in sessions.values() if title and d.get("name") == title]
    if len(named) == 1:
        return named[0]
    info = cli_json("pane", "process-info", "--pane", pane["pane_id"]).get("process_info", {})
    for proc in info.get("foreground_processes", []):
        if proc.get("name") == "claude" and str(proc.get("pid")) in sessions:
            return sessions[str(proc.get("pid"))].get("sessionId")
    return (pane.get("agent_session") or {}).get("value")


def fork() -> int:
    """Fork the focused Claude Code conversation into a new tab, so it stays in the agents list.

    A fork made inside Claude (a background agent) has no pane, so it never shows up in the
    sidebar. This one gets its own tab: you see it, its status and its mark like any other agent.
    The new tab runs a normal `claude`, so whatever guards your shell puts in front of claude still apply.
    """
    pane_id = os.environ.get("HERDR_PANE_ID") or die("no focused pane")
    pane = cli_json("pane", "get", pane_id).get("pane", {})
    if pane.get("agent") != "claude":
        die(f"the focused pane is not Claude Code (agent: {pane.get('agent')})")
    session = claude_session(pane) or die("cannot find the Claude Code session of the focused pane")
    created = cli_json("tab", "create", "--workspace", pane["workspace_id"], "--cwd", pane["cwd"], "--focus")
    new_tab = (created.get("tab") or {}).get("tab_id")
    new_pane = next((p["pane_id"] for p in cli_json("pane", "list").get("panes", []) if p.get("tab_id") == new_tab), None)
    if not new_pane:
        die("the new tab has no pane")
    proc = subprocess.run([HERDR, "pane", "run", new_pane, f"claude --resume {session} --fork-session"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"cannot start claude in the new tab: {(proc.stderr or proc.stdout).strip()[:200]}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "apply":
        return apply()
    if cmd == "off":
        return off()
    if cmd == "status":
        return status()
    if cmd == "fork":
        return fork()
    die(f"unknown command '{cmd}' (apply / off / status / fork)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
