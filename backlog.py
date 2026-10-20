#!/usr/bin/env python3
"""Backlog: sort the herdr agents sidebar by the mark at the start of each tab name.

Order: 🔴 → 🟡 → 🟢 → 🚪 → unmarked. Inside one group the current order is kept.

How it works (two herdr features, nothing else):
  1. Every agent pane gets a display token  `backlog = "<group>-<index>"`
     via `herdr pane report-metadata`.
  2. An agent view sorted by that token is set with the socket API
     (`agent.view.set`), which the CLI does not expose yet.

Commands:
  apply    write tokens that changed, make sure the view is set   (startup, events, action)
  off      clear the view and every token                          (action)
  status   print the current order                                 (popup pane)

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
DEFAULT_MARKS = ["🔴", "🟡", "🟢", "🚪"]

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


def write_token(pane_id: str, value: str | None) -> None:
    args = ["pane", "report-metadata", pane_id, "--source", SOURCE]
    args += ["--clear-token", TOKEN] if value is None else ["--token", f"{TOKEN}={value}"]
    proc = subprocess.run([HERDR, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"cannot write to {pane_id}: {(proc.stderr or proc.stdout).strip()[:200]}")


# --- marks and order ------------------------------------------------------------


def load_marks() -> list[str]:
    """Marks in order, one per line, from <config dir>/marks.txt. Falls back to the defaults."""
    path = CONFIG_DIR / "marks.txt"
    if not path.exists():
        return DEFAULT_MARKS
    marks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            marks.append(line)
    return marks or DEFAULT_MARKS


def agents_with_labels() -> list[dict]:
    agents = cli_json("agent", "list").get("agents", [])
    labels = {t["tab_id"]: t.get("label", "") for t in cli_json("tab", "list").get("tabs", [])}
    for a in agents:
        a["tab_label"] = labels.get(a.get("tab_id"), "")
    return agents


def name_of(a: dict) -> str:
    return a["tab_label"] or a.get("terminal_title_stripped") or a.get("terminal_title") or ""


def group_of(a: dict, marks: list[str]) -> int:
    name = name_of(a).lstrip()
    for i, mark in enumerate(marks):
        if name.startswith(mark):
            return i
    return len(marks)


def token_of(a: dict) -> str | None:
    return (a.get("tokens") or {}).get(TOKEN)


def ordered(agents: list[dict], marks: list[str]) -> list[dict]:
    """Group by mark; inside a group keep the current order (existing token, then list order)."""
    by_current = sorted(agents, key=lambda a: (token_of(a) is None, token_of(a) or ""))
    return sorted(by_current, key=lambda a: group_of(a, marks))


VIEW = {"source": SOURCE, "label": "backlog", "sort": [{"field": {"token": TOKEN}, "order": "asc"}]}


def apply() -> int:
    marks = load_marks()
    agents = agents_with_labels()
    changed = 0
    for i, a in enumerate(ordered(agents, marks), start=1):
        value = f"{group_of(a, marks):02d}-{min(i, 99):02d}"
        if token_of(a) != value:
            write_token(a["pane_id"], value)
            changed += 1
    api("agent.view.set", VIEW)
    print(f"backlog: {len(agents)} agents, {changed} moved")
    return 0


def off() -> int:
    api("agent.view.clear", {"source": SOURCE})
    agents = cli_json("agent", "list").get("agents", [])
    for a in agents:
        if token_of(a) is not None:
            write_token(a["pane_id"], None)
    print(f"backlog: off ({len(agents)} agents back to herdr's default order)")
    return 0


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def status() -> int:
    marks = load_marks()
    print("Backlog order: " + " → ".join(marks) + " → unmarked")
    print(f"marks file: {CONFIG_DIR / 'marks.txt'}\n")
    agents = agents_with_labels()
    rows = ordered(agents, marks)
    w = max((width(name_of(a)) for a in rows), default=4)
    for i, a in enumerate(rows, start=1):
        name = name_of(a)
        print(f"{i:2d}  {name}{' ' * (w - width(name))}  {a['pane_id']:<8}  {token_of(a) or '-'}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if cmd == "apply":
        return apply()
    if cmd == "off":
        return off()
    if cmd == "status":
        return status()
    die(f"unknown command '{cmd}' (apply / off / status)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
