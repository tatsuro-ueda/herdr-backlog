#!/usr/bin/env python3
"""Backlog: sort the herdr agents sidebar by the mark at the start of each tab name.

Order: 🐞 → 🔴 → 🐝 → 🟡 → 🪲 → 🟢 → 🚪 → unmarked → ⏳ → 🔧. Inside one group the current order is kept.
The bugs (🐞 🐝 🪲) are small jobs: same colour, just above their big one.
Later tabs (⏳) are things you are waiting on: always listed, below the unmarked ones.
Tool tabs (🔧) stay out of the list while they are idle or working, and show up
at the bottom only when they need you (blocked, or done and not yet seen).
A focus tab (🎯) holds one line you want to keep in mind; it is always the first row.

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
  focus-edit     ask for the focus line and its minutes, then pin it      (popup pane)
  focus-watch    keep the focus tab alive, mark it ⏰ when time is up       (runs in the focus tab)
  focus-restore  rebuild the focus tab after a herdr restart               (startup)
  focus-closed   forget the focus when you close its tab by hand          (tab.closed)

Python 3.9+ standard library only.
"""
from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path

SOURCE = "backlog"  # metadata source id and view source id
TOKEN = "backlog"  # token name on each pane
TOOL_TOKEN = "backlog_tool"  # set to "1" on tool panes, cleared on the others
DEFAULT_MARKS = ["🐞", "🔴", "🐝", "🟡", "🪲", "🟢", "🚪"]
DEFAULT_LATER = ["⏳"]
DEFAULT_TOOLS = ["🔧"]
FOCUS_MARK = "🎯"  # the focus tab; not configurable, always first
FOCUS_GROUP = -1
FOCUS_TOKEN_PREFIX = "!!"  # sorts before "00" as a string, so other groups keep their values

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


def load_marks() -> tuple[list[str], list[str], list[str]]:
    """(marks in order, later marks, tool marks). Groups: each mark, unmarked, each later mark, tools."""
    return (
        load_list("marks.txt", DEFAULT_MARKS),
        load_list("later.txt", DEFAULT_LATER),
        load_list("tools.txt", DEFAULT_TOOLS),
    )


def agents_with_labels() -> list[dict]:
    agents = cli_json("agent", "list").get("agents", [])
    labels = {t["tab_id"]: t.get("label", "") for t in cli_json("tab", "list").get("tabs", [])}
    for a in agents:
        a["tab_label"] = labels.get(a.get("tab_id"), "")
    return agents


def name_of(a: dict) -> str:
    return a["tab_label"] or a.get("terminal_title_stripped") or a.get("terminal_title") or ""


def is_tool(a: dict, tools: list[str]) -> bool:
    return not is_focus(a) and name_of(a).lstrip().startswith(tuple(tools))


def is_focus(a: dict) -> bool:
    """Only a tab name counts (a terminal title that happens to start with 🎯 does not)."""
    return (a.get("tab_label") or "").lstrip().startswith(FOCUS_MARK)


def group_of(a: dict, marks: list[str], later: list[str], tools: list[str]) -> int:
    if is_focus(a):
        return FOCUS_GROUP
    if is_tool(a, tools):
        return len(marks) + len(later) + 1
    name = name_of(a).lstrip()
    for i, mark in enumerate(marks):
        if name.startswith(mark):
            return i
    for i, mark in enumerate(later):
        if name.startswith(mark):
            return len(marks) + 1 + i
    return len(marks)


def token_of(a: dict, name: str = TOKEN) -> str | None:
    return (a.get("tokens") or {}).get(name)


def ordered(agents: list[dict], marks: list[str], later: list[str], tools: list[str]) -> list[dict]:
    """Group by mark; inside a group keep the current order (existing token, then list order)."""
    by_current = sorted(agents, key=lambda a: (token_of(a) is None, token_of(a) or ""))
    return sorted(by_current, key=lambda a: group_of(a, marks, later, tools))


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
    marks, later, tools = load_marks()
    agents = agents_with_labels()
    changed = 0
    for i, a in enumerate(ordered(agents, marks, later, tools), start=1):
        g = group_of(a, marks, later, tools)
        want = {
            TOKEN: f"{FOCUS_TOKEN_PREFIX if g == FOCUS_GROUP else f'{g:02d}'}-{min(i, 99):02d}",
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
    marks, later, tools = load_marks()
    print("Backlog order: " + " → ".join([FOCUS_MARK] + marks + ["unmarked"] + later) + " → " + " ".join(tools) + " (hidden until they need you)")
    print(f"marks file: {CONFIG_DIR / 'marks.txt'}   later file: {CONFIG_DIR / 'later.txt'}   tools file: {CONFIG_DIR / 'tools.txt'}\n")
    agents = agents_with_labels()
    rows = ordered(agents, marks, later, tools)
    w = max((width(name_of(a)) for a in rows), default=4)
    for i, a in enumerate(rows, start=1):
        name = name_of(a)
        hidden = is_tool(a, tools) and a.get("agent_status") not in ("blocked", "done")
        note = "  (hidden: " + str(a.get("agent_status")) + ")" if hidden else ""
        print(f"{i:2d}  {name}{' ' * (w - width(name))}  {a['pane_id']:<8}  {token_of(a) or '-'}{note}")
    return 0


# --- focus -----------------------------------------------------------------------
#
# One line you want to keep in mind ("finish the release notes"), pinned as the first
# row of the agents list. It lives in its own tab, named "🎯 <text> ~HH:MM". A small
# watcher runs in that tab: it keeps the pane listed as an agent (a pane with nothing
# running drops out of the list), and adds ⏰ to the name once the time is up.
# The line itself is stored in the config dir (focus.json), never in this repository.
#
# Only tabs this plugin created are ever touched: their ids are kept in focus.json.
# A tab you named 🎯 yourself is sorted first, but never renamed, typed into or closed.

FOCUS_FILE = CONFIG_DIR / "focus.json"
FOCUS_LOG = CONFIG_DIR / "focus-history.jsonl"
FOCUS_AGENT = "focus"  # agent label of the focus pane
FOCUS_SOURCE = "backlog-focus"
FOCUS_COLS = 30  # display columns kept from the text; the sidebar is narrow
FOCUS_DEFAULT_MIN = 60
FOCUS_MAX_MIN = 24 * 60
FOCUS_TICK = 30  # seconds between watcher checks
ZERO_WIDTH = {"‍", "​", "‌", "︎", "️"}


def valid_until(value: object) -> float | None:
    """A finite epoch time within a day or so of now; anything else counts as no time."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or abs(value - time.time()) > 2 * FOCUS_MAX_MIN * 60:
        return None
    return float(value)


def focus_load() -> dict | None:
    try:
        data = json.loads(FOCUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        return None
    data["until"] = valid_until(data.get("until"))
    if not isinstance(data.get("tabs"), list):
        data["tabs"] = []
    data["tabs"] = [t for t in data["tabs"] if isinstance(t, str)]
    return data


def focus_save(data: dict | None) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if data is None:
        try:
            FOCUS_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    tmp = FOCUS_FILE.with_name(f".focus.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FOCUS_FILE)


def focus_log(event: str, data: dict | None) -> None:
    """Append one line to the history, so a finished focus is not simply lost."""
    keep = {k: v for k, v in (data or {}).items() if k in ("text", "set_at", "until")}
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with FOCUS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": int(time.time()), "event": event, **keep}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def char_width(ch: str) -> int:
    if ch in ZERO_WIDTH or unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def clip(text: str, cols: int) -> str:
    """Cut `text` to `cols` display columns (CJK counts as 2), ending with … when cut.
    Never cuts between a character and what joins onto it (accents, ZWJ emoji)."""
    text = unicodedata.normalize("NFC", text)
    if sum(char_width(c) for c in text) <= cols:
        return text
    out, used, flag_open = "", 0, False
    for ch in text:
        w = char_width(ch)
        is_flag_half = "\U0001F1E6" <= ch <= "\U0001F1FF"  # a flag is two of these
        joined = w == 0 or (out and out[-1] == "‍") or (is_flag_half and flag_open)
        if not joined and used + (2 if is_flag_half else w) > cols - 1:
            break
        flag_open = is_flag_half and not flag_open
        out, used = out + ch, used + w
    while out and out[-1] == "‍":
        out = out[:-1]
    return out + "…"


def focus_overdue(data: dict, now: float | None = None) -> bool:
    until = data.get("until")
    return until is not None and (now or time.time()) >= until


def focus_label(data: dict, now: float | None = None) -> str:
    """Tab name: the time comes first, so it survives the sidebar cutting the end off.
    The full line goes to the agent row underneath (focus_display)."""
    text = clip(" ".join(str(data["text"]).split()), FOCUS_COLS)
    mark = FOCUS_MARK + ("⏰" if focus_overdue(data, now) else "")
    until = data.get("until")
    if until is not None:
        return f"{mark} ~{time.strftime('%H:%M', time.localtime(until))} {text}"
    return f"{mark} {text}"


def focus_display(data: dict) -> str:
    """Second line of the row (where the agent name goes): the whole line, herdr cuts it to fit."""
    return " ".join(str(data["text"]).split())[:200]


def parse_minutes(raw: str) -> int | None:
    """'' → None (default), '30' / '90m' / '1.5h' → minutes, anything else → -1."""
    raw = raw.strip().lower().replace(" ", "")
    if not raw:
        return None
    mult = 60 if raw.endswith("h") else 1
    raw = raw[:-1] if raw.endswith(("h", "m")) else raw
    try:
        minutes = float(raw) * mult
        if minutes != minutes:
            return -1
        minutes = int(minutes)
    except (ValueError, OverflowError):
        return -1
    return minutes if 1 <= minutes <= FOCUS_MAX_MIN else -1


def watcher_panes() -> list[dict]:
    return [p for p in cli_json("pane", "list").get("panes", []) if p.get("agent") == FOCUS_AGENT]


def tab_ids() -> set[str]:
    return {t["tab_id"] for t in cli_json("tab", "list").get("tabs", [])}


def context_workspace() -> str | None:
    """The workspace the popup was opened from, if herdr told us."""
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    for key in ("workspace_id", "focused_workspace_id"):
        if isinstance(ctx.get(key), str) and ctx[key]:
            return ctx[key]
    for p in cli_json("pane", "list").get("panes", []):
        if p.get("focused"):
            return p.get("workspace_id")
    return None


def start_watcher(pane_id: str) -> None:
    """Run the watcher in a pane of a tab this plugin created (never in one of yours)."""
    env = {"HERDR_PLUGIN_CONFIG_DIR": str(CONFIG_DIR), "HERDR_BIN_PATH": HERDR, "HERDR_SOCKET_PATH": SOCKET}
    argv = ["exec", "env", *(f"{k}={v}" for k, v in env.items()),
            sys.executable, str(Path(__file__).resolve()), "focus-watch", pane_id]
    cmd = "clear; " + " ".join(shlex.quote(a) for a in argv)
    proc = subprocess.run([HERDR, "pane", "run", pane_id, cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"cannot start the focus watcher: {(proc.stderr or proc.stdout).strip()[:200]}")


def ensure_focus_tab(data: dict, workspace: str | None = None) -> dict:
    """Make sure exactly one of our tabs shows `data`, with its watcher running.
    Returns `data` with `tabs` updated (the live tab first). Extra tabs of ours are closed."""
    label = focus_label(data)
    alive = tab_ids()
    ours = [t for t in data.get("tabs", []) if t in alive]
    watched = {p["tab_id"]: p["pane_id"] for p in watcher_panes()}
    # A watcher's tab is ours even if its id was lost (two popups at once).
    ours += [t for t in watched if t not in ours]
    keep = next((t for t in ours if t in watched), None) or (ours[0] if ours else None)
    if keep is None:
        ws = workspace or data.get("workspace_id") or context_workspace()
        args = ["tab", "create", "--label", label] + (["--workspace", ws] if ws else [])
        created = cli_json(*args)
        keep = (created.get("tab") or {}).get("tab_id")
        pane_id = (created.get("root_pane") or {}).get("pane_id")
        if not keep or not pane_id:
            die("the new focus tab has no pane")
        data["tabs"] = [keep]
        focus_save(data)
        start_watcher(pane_id)
    else:
        data["tabs"] = [keep]
        focus_save(data)
        cli_json("tab", "rename", keep, label)
        if keep not in watched:
            pane_id = next((p["pane_id"] for p in cli_json("pane", "list").get("panes", []) if p.get("tab_id") == keep), None)
            if pane_id:
                start_watcher(pane_id)
    for extra in ours:
        if extra != keep:
            subprocess.run([HERDR, "tab", "close", extra], capture_output=True, text=True)
    return data


def close_focus_tabs(data: dict | None) -> int:
    """Close our tabs only: the ones in focus.json and the ones our watcher runs in."""
    mine = set((data or {}).get("tabs", [])) | {p["tab_id"] for p in watcher_panes()}
    alive = tab_ids()
    for tab_id in mine & alive:
        subprocess.run([HERDR, "tab", "close", tab_id], capture_output=True, text=True)
    return len(mine & alive)


def minutes_left(data: dict) -> int | None:
    until = data.get("until")
    return None if until is None else int((until - time.time()) // 60)


def focus_edit() -> int:
    try:
        return focus_edit_inner()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print("\n  something went wrong (see the line above). Press Enter to close.")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
        return 1 if exc.code not in (0, None) else 0


def focus_edit_inner() -> int:
    cur = focus_load()
    print("Focus: one line to keep in mind, pinned at the top of the agents list.\n")
    if cur:
        left = minutes_left(cur)
        print(f"  now: {cur['text']}" + ("" if left is None else f"  ({left} min left)" if left >= 0 else "  (time is up)"))
        print("\n  Enter = keep it   - = clear it\n")
    try:
        text = " ".join(input("  Focus> ").split())
        if text == "-":
            if cur:
                focus_log("clear", cur)
            n = close_focus_tabs(cur)
            focus_save(None)
            print(f"\n  cleared ({n} tab closed)")
            time.sleep(0.8)
            return 0
        if not text and not cur:
            return 0
        same = cur is not None and (not text or text == cur["text"])
        default = "keep the time" if same else str(FOCUS_DEFAULT_MIN)
        while True:
            minutes = parse_minutes(input(f"  Minutes (Enter = {default})> "))
            if minutes != -1:
                break
            print(f"  a number of minutes from 1 to {FOCUS_MAX_MIN}, like 30, 90 or 1h")
    except (EOFError, KeyboardInterrupt):
        return 0
    now = time.time()
    if same:
        data = dict(cur)
    else:
        if cur:
            focus_log("replace", cur)
        data = {"text": text, "set_at": int(now), "tabs": cur.get("tabs", []) if cur else []}
    if minutes is not None:
        data["until"] = int(now + minutes * 60)
    elif not same:
        data["until"] = int(now + FOCUS_DEFAULT_MIN * 60)
    data.setdefault("workspace_id", context_workspace())
    focus_log("set", data)
    ensure_focus_tab(data)
    return 0


def focus_restore() -> int:
    """At startup: rebuild the focus tab. herdr may also restore our old tab as a plain
    shell; its id is in focus.json, so it is reused (or closed) instead of doubled."""
    data = focus_load()
    if data:
        ensure_focus_tab(data)
    return 0


def focus_closed() -> int:
    """On tab.closed: if you closed the focus tab by hand, forget the focus too,
    so it does not come back at the next restart. A replaced tab does not count."""
    data = focus_load()
    if not data or not data.get("tabs"):
        return 0
    time.sleep(1)  # let a replacement (ensure_focus_tab) finish first
    data = focus_load()
    if not data or not data.get("tabs"):
        return 0
    if not (set(data["tabs"]) & tab_ids()) and not watcher_panes():
        focus_log("closed", data)
        focus_save(None)
    return 0


def focus_watch(pane_id: str) -> int:
    def report() -> None:
        subprocess.run([HERDR, "pane", "report-agent", pane_id, "--source", FOCUS_SOURCE,
                        "--agent", FOCUS_AGENT, "--state", "idle"], capture_output=True, text=True)

    def show(text: str) -> None:
        subprocess.run([HERDR, "pane", "report-metadata", pane_id, "--source", FOCUS_SOURCE,
                        "--display-agent", text], capture_output=True, text=True)

    report()
    while True:
        try:
            data = focus_load()
            if not data:
                print("\033[2J\033[H\n  no focus set. Press the key bound to backlog.focus to set one.")
                return 0
            pane = cli_json("pane", "get", pane_id).get("pane", {})
            tab_id = pane.get("tab_id")
            if tab_id and data.get("tabs") and tab_id not in data["tabs"]:
                # Another focus tab took over (two popups at once): leave quietly.
                subprocess.run([HERDR, "tab", "close", tab_id], capture_output=True, text=True)
                return 0
            if pane.get("agent") != FOCUS_AGENT:
                report()
            if pane.get("display_agent") != focus_display(data):
                show(focus_display(data))
            label = focus_label(data)
            tab = cli_json("tab", "get", tab_id).get("tab", {}) if tab_id else {}
            if tab and tab.get("label") != label:
                cli_json("tab", "rename", tab_id, label)
            left = minutes_left(data)
            when = "" if left is None else (
                f"until {time.strftime('%H:%M', time.localtime(data['until']))}  "
                + (f"({left} min left)" if left >= 0 else "⏰ time is up"))
            print(f"\033[2J\033[H\n  {FOCUS_MARK}  {data['text']}\n\n  {when}\n\n  change or clear it with the backlog.focus action")
        except KeyboardInterrupt:
            return 0
        except (SystemExit, Exception) as exc:  # herdr briefly unavailable (update, handoff): try again
            print(f"\033[2J\033[H\n  waiting for herdr… ({type(exc).__name__})")
        try:
            time.sleep(FOCUS_TICK)
        except KeyboardInterrupt:
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
    if cmd == "focus-edit":
        return focus_edit()
    if cmd == "focus-restore":
        return focus_restore()
    if cmd == "focus-closed":
        return focus_closed()
    if cmd == "focus-watch" and len(sys.argv) > 2:
        return focus_watch(sys.argv[2])
    die(f"unknown command '{cmd}' (apply / off / status / fork / focus-edit / focus-watch <pane> / focus-restore / focus-closed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
