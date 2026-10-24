# Backlog — a herdr plugin

Sort the [herdr](https://herdr.dev) agents sidebar by the mark at the start of each tab name.

```
🔴 fix prod        ← top: do this first
🔴 morning
🟡 review PR
🟢 docs
🚪 old session
   scratch         ← unmarked tabs come after the marks
🔧 transfer        ← tools: hidden until they need you
```

Rename a tab, and it moves. Put `🔴` in front of the tab name and it goes to the top within a fraction of a second. Nothing else to configure, nothing to click. Your sidebar becomes a backlog: scan from the top, work down.

Some agents are tools you keep open all day: a transfer helper, a research assistant, a break timer. Put `🔧` in front of their tab names and they leave the list while they are idle or working. They come back at the bottom only when they need you, and leave again once you have looked.

## Install

```sh
herdr plugin install tatsuro-ueda/herdr-backlog
```

herdr 0.9.3 or later, Python 3.9 or later (standard library only), Linux or macOS.

If you connect to other machines from the herdr sidebar, install the plugin on each of them too. herdr uses the agent view of the machine that is currently selected, so a machine without the plugin shows herdr's default order.

## How it works

Two herdr features, nothing else.

1. Each agent pane gets a display token `backlog = "<group>-<index>"` through `herdr pane report-metadata`. The group is the position of the mark in the list (`🔴` is `00`), the index keeps the current order inside a group. Tool panes (`🔧`) also get `backlog_tool = "1"`.
2. An agent view sorted by that token is set through the socket API (`agent.view.set`), which the CLI does not expose yet. The same view filters out tool panes unless their status is `blocked` (waiting for input) or `done` (finished and not yet seen).

The plugin runs on three triggers and exits. There is no daemon.

| Trigger | What happens |
|---|---|
| `tab.renamed`, `tab.created`, `pane.agent_detected` | re-sort |
| herdr server startup | re-sort and set the view again (views live in server memory) |
| action **Backlog: sort now** | re-sort by hand |

It only writes tokens that changed, so a run with nothing to do touches nothing.

## Marks

The default order is `🔴 → 🟡 → 🟢 → 🚪 → unmarked`. To change it, create `marks.txt` in the plugin's config directory, one mark per line, top first:

```sh
printf '🔥\n🔴\n🟡\n🟢\n' > "$(herdr plugin config-dir backlog)/marks.txt"
```

A mark can be any string, not only an emoji: `[A]`, `!!`, `today:` all work. Lines starting with `#` are ignored.

Tool marks live in `tools.txt` in the same directory, in the same format. The default is `🔧`:

```sh
printf '🔧\n🛠\n' > "$(herdr plugin config-dir backlog)/tools.txt"
```

A hidden tool is still one click away: its tab stays in the tab bar, and on another machine it is under that machine in the machines list. Remove the `🔧` and it is back in the list for good.

## Actions and panes

- **Backlog: sort now** — re-sort by hand (`herdr plugin action invoke backlog.sort`).
- **Backlog: stop sorting** — clear the view and every token; the sidebar goes back to herdr's default order (`herdr plugin action invoke backlog.off`). Run **sort now** to turn it back on.
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
