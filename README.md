# Backlog — a herdr plugin

Sort the [herdr](https://herdr.dev) agents sidebar by the mark at the start of each tab name.

```
🐞 fix typo        ← small jobs (🐞 🐝 🪲) sit just above the big ones of their colour
🔴 fix prod        ← do this first
🔴 morning
🟡 review PR
🟢 docs
🚪 old session
   scratch         ← unmarked tabs come after the marks
⏳ waiting on CI   ← later: below everything you can act on now
🔧 transfer        ← tools: hidden until they need you
```

Rename a tab, and it moves. Put `🔴` in front of the tab name and it goes to the top within a fraction of a second. Nothing else to configure, nothing to click. Your sidebar becomes a backlog: scan from the top, work down.

Some tabs are waiting on something else: a long build, a reply, tomorrow. Put `⏳` in front and they sink below the unmarked tabs. They stay in the list, out of the way.

Some agents are tools you keep open all day: a transfer helper, a research assistant, a break timer. Put `🔧` in front of their tab names and they leave the list while they are idle or working. They come back at the bottom only when they need you, and leave again once you have looked.

The point is one glance. Everything you have running, its status and its priority, is in the agents list, and nothing you need to look at is anywhere else.

## Fork into a tab

A fork made inside Claude Code runs as a background agent with no pane, so it never shows up in the agents list. Press `prefix+f` (after binding it, below) on a Claude pane instead: the conversation is forked (`claude --resume <id> --fork-session`) into a new tab next to the original. The fork is an agent like any other, with its own status and its own mark, and the original keeps going in its tab.

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "backlog.fork"
description = "backlog: fork this Claude conversation into a new tab"
```

The new tab runs a normal `claude` in your shell, so anything your shell puts in front of `claude` (aliases, guards) still applies.

## Focus line

Keep one line in mind while you work: what you are doing right now, and until when. Bind the `backlog.focus` action to a key, press it, type the line and the minutes (default 60):

```toml
[[keys.command]]
key = "prefix+t"
type = "plugin_action"
command = "backlog.focus"
description = "backlog: set or clear the focus line"
```

The line becomes the first row of the agents list, above every mark:

```
🎯 finish the release notes ~15:00   ← always first
🔴 fix prod
🟡 review PR
```

When the time is up it turns into `🎯⏰ …`. Press the key again to change it, keep it with a new time, or type `-` to clear it. Closing its tab clears it too.

The line lives in its own tab, where a small watcher keeps it listed and updates the name. The text is stored in `focus.json` in the plugin's config directory (past lines go to `focus-history.jsonl`), and the tab is rebuilt when the herdr server starts. Only tabs the plugin created are renamed or closed; a tab you name `🎯 …` yourself is just sorted first.

## Install

```sh
herdr plugin install tatsuro-ueda/herdr-backlog
```

herdr 0.9.3 or later, Python 3.9 or later (standard library only), Linux or macOS.

If you connect to other machines from the herdr sidebar, install the plugin on each of them too. herdr uses the agent view of the machine that is currently selected, so a machine without the plugin shows herdr's default order.

## How it works

Two herdr features, nothing else.

1. Each agent pane gets a display token `backlog = "<group>-<index>"` through `herdr pane report-metadata`. The group is the position of the mark in the list (`🐞` is `00`, then unmarked, then `⏳`, then tools), the index keeps the current order inside a group. Tool panes (`🔧`) also get `backlog_tool = "1"`.
2. An agent view sorted by that token is set through the socket API (`agent.view.set`), which the CLI does not expose yet. The same view filters out tool panes unless their status is `blocked` (waiting for input) or `done` (finished and not yet seen).

The plugin runs on three triggers and exits. There is no daemon.

| Trigger | What happens |
|---|---|
| `tab.renamed`, `tab.created`, `pane.agent_detected` | re-sort |
| `tab.closed` | clear the focus line if you closed its tab |
| herdr server startup | re-sort and set the view again (views live in server memory), rebuild the focus tab |
| action **Backlog: sort now** | re-sort by hand |

It only writes tokens that changed, so a run with nothing to do touches nothing.

## Marks

The default order is `🐞 → 🔴 → 🐝 → 🟡 → 🪲 → 🟢 → 🚪 → unmarked → ⏳`. The bugs are small jobs: `🐞` is a small red, `🐝` a small yellow, `🪲` a small green, so inside one colour the quick ones come first. To change the marks above unmarked, create `marks.txt` in the plugin's config directory, one mark per line, top first:

```sh
printf '🔥\n🔴\n🟡\n🟢\n' > "$(herdr plugin config-dir backlog)/marks.txt"
```

A mark can be any string, not only an emoji: `[A]`, `!!`, `today:` all work. Lines starting with `#` are ignored.

Marks that go below unmarked live in `later.txt` in the same directory, in the same format. The default is `⏳`:

```sh
printf '⏳\n💤\n' > "$(herdr plugin config-dir backlog)/later.txt"
```

Tool marks live in `tools.txt` in the same directory, in the same format. The default is `🔧`:

```sh
printf '🔧\n🛠\n' > "$(herdr plugin config-dir backlog)/tools.txt"
```

A hidden tool is still one click away: its tab stays in the tab bar, and on another machine it is under that machine in the machines list. Remove the `🔧` and it is back in the list for good.

### Let the agent set its own mark

The plugin never renames anything. It reads the mark, and the name comes from you or from the agent. If a tab has no name of its own, the pane's terminal title is used, and Claude Code puts the session name there. So a hook that renames the session moves the tab.

For example, a Claude Code `UserPromptSubmit` hook can return `sessionTitle`. This one turns the first character into `🚪` whenever you type `/wrap-up`, so a session you are wrapping up sinks to the old-session group by itself:

```sh
#!/bin/sh
# UserPromptSubmit hook: "/wrap-up" -> session name starts with 🚪
jq -c 'select(.prompt | startswith("/wrap-up"))
  | (.session_title // "") as $t
  | select($t | startswith("🚪") | not)
  | {hookSpecificOutput: {hookEventName: "UserPromptSubmit",
      sessionTitle: ("🚪" + ($t | .[1:] | ltrimstr("\ufe0f")))}}'
```

## Actions and panes

- **Backlog: sort now** — re-sort by hand (`herdr plugin action invoke backlog.sort`).
- **Backlog: stop sorting** — clear the view and every token; the sidebar goes back to herdr's default order (`herdr plugin action invoke backlog.off`). Run **sort now** to turn it back on.
- **Backlog: fork this Claude conversation into a new tab** — see [Fork into a tab](#fork-into-a-tab).
- **Backlog order** — a popup listing the agents in backlog order with their tokens (`herdr plugin pane open --plugin backlog --entrypoint status`).

Bind a key if you like:

```toml
[[keys.command]]
key = "prefix+b"
type = "plugin_action"
command = "backlog.sort"
description = "backlog: sort now"
```

## Good to know

- Inside one group, the order is whatever it was. To move a tab across groups, change its mark. Moving tabs by hand across groups does not stick: the next rename puts them back.
- The tab name wins; if a tab has no name, the pane's terminal title is used.
- Tokens and the view are held by the herdr server in memory. The startup hook restores them, so a server restart is harmless.
- `herdr plugin log list --plugin backlog` shows every run with its exit code and output.

## Author

Feel Physics / Tatsuro Ueda — [feel-physics.jp](https://feel-physics.jp/). MIT, see [LICENSE](LICENSE).

See also [Recess](https://github.com/tatsuro-ueda/recess), another herdr plugin: your video plays while the agents work, and pauses when herdr calls you back.
