# DevPulse

DevPulse is a keyboard-first Omarchy dashboard for discovering and managing local development servers.

## Features

- Automatic localhost server discovery
- Best-effort framework and runtime detection (Next.js, Vite, RedwoodJS, NestJS, Astro, Nuxt, Remix/React Router, Django, FastAPI/Uvicorn, Flask, Rails, Go, Rust, and PHP)
- Project names and compact working-directory paths
- Git branch and dirty-state indicator
- CPU and memory usage
- Open the selected server in the default browser
- Open a terminal in the project directory
- Copy the localhost URL
- Safe, confirmed SIGTERM stop for current-user processes
- Keyboard navigation with native Omarchy panel behavior
- Native Omarchy bar widget and popup styling

## Screenshot

![DevPulse panel](preview.png)

## Installation

DevPulse is ready for an Omarchy plugin repository. Once this repository has a public Git URL, install it with:

```bash
omarchy plugin add <DEV_PULSE_GIT_URL> --enable --yes
```

The installer validates `manifest.json`, places the plugin in `~/.config/omarchy/plugins/artim.devpulse/`, and enables the bar widget. For a local checkout during development, copy the folder there and run:

```bash
omarchy-shell shell rescanPlugins
omarchy plugin enable artim.devpulse --section right
```

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `j` / `Down` | Next server |
| `k` / `Up` | Previous server |
| `Enter` | Open selected server in the default browser |
| `t` | Open a terminal in the project directory |
| `c` | Copy the localhost URL |
| `x` | Ask for confirmation, then send SIGTERM to the selected server |
| `r` | Refresh immediately |
| `Esc` | Close the panel |

Left-click the bar widget to toggle the panel. Middle-click refreshes; right-click opens the panel. The panel can also be summoned through shell IPC:

```bash
omarchy-shell shell summon artim.devpulse '{}'
```

## Security

DevPulse is local-only. It has no telemetry, accounts, cloud backend, or network requests. It never uses `sudo`, reads `.env` files, evaluates project scripts, or executes arbitrary project commands. Discovery is limited to TCP listeners owned by the current user. Stopping a server verifies the current UID and `/proc` start time immediately before sending `SIGTERM`; there is no force-kill action.

## How discovery works

Every few seconds the service runs one bounded scan: `ss -H -ltnp` finds TCP listeners and one `ps` snapshot supplies process CPU, RSS, command, and owner data. `/proc/<pid>` supplies the working directory and start time. Project markers (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Gemfile`, `composer.json`, and related files), Git directories, framework commands, runtimes, and common development ports are combined into a score so desktop/system daemons do not fill the panel. IPv4/IPv6 duplicates collapse to one PID+port row.

Git status is intentionally refreshed less often than listeners and is cached in the long-lived shell service. Package metadata is parsed defensively and malformed files simply fall back to generic runtime labels.

## Requirements

Stock Omarchy components only:

- Omarchy shell / Quickshell 0.3+
- Linux `/proc`, `ss`, and `ps`
- Python 3 (standard library only) for the scanner and safe stop helper
- `git` for optional branch metadata
- `wl-copy` for clipboard actions
- `xdg-terminal-exec` and `omarchy-launch-browser` for configured app launching

## Development

From the plugin directory:

```bash
omarchy plugin validate .
qmllint -I "$OMARCHY_PATH/shell" Service.qml BarWidget.qml Panel.qml
python3 -m unittest discover -s tests -v
omarchy-shell shell rescanPlugins
```

The fixture tests create only loopback listeners, verify discovery, exercise Git metadata parsing, and test exact-PID SIGTERM plus stale-PID protection. The scanner also collapses IPv4/IPv6 listener duplicates by PID and logical port. No fixture process is left running.

## License

MIT. See [LICENSE](LICENSE).
