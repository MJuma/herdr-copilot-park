# herdr-copilot-park

A [Herdr](https://herdr.dev) plugin that parks idle
[GitHub Copilot CLI](https://github.com/github/copilot-cli) tabs, much like a
browser unloads a tab.

An idle Copilot CLI session still holds 300–500 MB of memory. Parking stops
Copilot, but keeps the tab. The tab shows a small placeholder (about 1 MB) with
the session title. Press **Enter** in the tab to load the same session again,
with the same options. Thus you can keep hundreds of sessions in tabs.

```
  Fix Grub Rescue Issue

  Parked Copilot session. Copilot is not running.

  Session   0d1ac088-2fcb-49da-ae7e-301896542c95
  Parked    2026-10-25 20:28
  Command   copilot --resume=0d1ac088-… --allow-all --model claude-opus-5.5

  Press Enter to load.  Press Ctrl+C to return to the shell.
```

## Requirements

- Herdr 0.9.5 or later, on Linux or macOS
- `bash` and `python3` on the machine where the panes run
- GitHub Copilot CLI

## Install

Install the plugin on each machine whose Copilot tabs you want to park. For a
saved SSH machine, install it on that machine, because Herdr runs plugins
where the panes run.

```bash
herdr plugin install MJuma/herdr-copilot-park
```

Then add the key bindings to `~/.config/herdr/config.toml` on the same machine.
Herdr reads custom command bindings from the server where the panes run.

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "herdr-copilot-park.park"
description = "park this Copilot tab"

[[keys.command]]
key = "prefix+shift+u"
type = "plugin_action"
command = "herdr-copilot-park.park-all"
description = "park all idle Copilot tabs"
```

Apply the change:

```bash
herdr server reload-config
```

## Use

| Key | Action |
|---|---|
| `prefix+u` | Park the Copilot session in the focused pane |
| `prefix+shift+u` | Park every idle Copilot session on this Herdr server |
| `Enter` in a parked tab | Load the session again |
| `Ctrl+C` in a parked tab | Return to the shell. The tab prints the resume command. |

You can also run the actions from the command line:

```bash
herdr plugin action invoke herdr-copilot-park.park
herdr plugin action invoke herdr-copilot-park.park-all
```

The sidebar shows a parked tab as the agent `parked`.

## Show parked tabs in color

A parked tab reports the state `idle`, so it has the same status icon as an
idle Copilot session. Herdr cannot change that icon for one agent. But you can
change the color of the agent name. For example, this layout shows the tab
name first, hides the name `copilot`, and shows `parked` in yellow:

```toml
[ui.sidebar.agents]
rows = [
  ["state_icon", { token = "tab", bold = true },
   { token = "agent", dim = true, rules = [
     { equals = "copilot", hide = true },
     { equals = "parked", fg = "#e5c07b", dim = false },
   ] }],
  [{ token = "machine", dim = true, rules = [{ equals = "Local", hide = true }] },
   { token = "workspace", dim = true }],
]
```

Put it in `~/.config/herdr/config.toml` and run `herdr server reload-config`.
See [Sidebar row layouts](https://herdr.dev/docs/configuration/#sidebar-row-layouts)
for the other tokens and rules.

## What parking does

1. It checks that the pane runs Copilot CLI and that Copilot is `idle` or
   `done`. It does not park a session that works or waits for an answer.
2. It reads the Copilot command line from Herdr, and keeps all the options
   except the options that select a session. This includes options from a
   shell alias.
3. It sends Ctrl+D. Copilot exits only when its input box is empty. If you have
   an unsent draft, Copilot keeps running, keeps the draft, and Herdr shows
   "Not parked".
4. It reads the session ID from the exit summary that Copilot prints
   (`Resume copilot --resume=<id>`). If that is not found, it uses the session
   that the Copilot process last logged.
5. It starts the placeholder in the same pane. The placeholder reports itself
   to Herdr with its own command as the resume command.

## Restarts

- **A parked tab stays parked.** After a Herdr server or machine restart, Herdr
  starts the placeholder again, not Copilot.
- **A loaded tab comes back as Copilot.** Herdr's own Copilot integration
  records a session ID only after the first prompt in a Copilot process. When
  the placeholder loads a session, it sends the same report a few seconds after
  Copilot starts. Thus Herdr can also restore a loaded session that has no new
  prompt. Install Herdr's integration with `herdr integration install copilot`.

## Notes

- The placeholder is copied to the plugin state directory, for example
  `~/.local/state/herdr/plugins/herdr-copilot-park/parked.sh`. Parked tabs keep
  that path, so a plugin update or move does not break them.
- Herdr does not accept an apostrophe in a resume command. The plugin replaces
  an apostrophe in a title with `’`, and does not park a session whose options
  contain one.
- Close a parked tab to forget it. The session stays in `copilot --resume`.
- The plugin depends on the Copilot exit summary, the Copilot process log, and
  the Herdr agent API. A change in Copilot CLI or Herdr can break it.

## Development

```bash
git clone https://github.com/MJuma/herdr-copilot-park
cd herdr-copilot-park
python3 -m unittest discover -s tests
herdr plugin link "$PWD"
herdr plugin log list --plugin herdr-copilot-park
```

## License

[MIT](LICENSE)
