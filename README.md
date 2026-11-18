# Mooring

A Phone Link–style hub for Linux: messages, notifications, photos, files, clipboard and
screen mirroring for an Android phone, in one window. Built with PyQt6 and themed with
Catppuccin. Developed on Fedora + Niri (Wayland).

Mooring is a front end. The phone protocol is handled by `kdeconnectd` (reached over D-Bus),
and mirroring by `scrcpy`. See [PLAN.md](PLAN.md) for the architecture and phases.

## Status

Phase 1 (skeleton): the window, sidebar, Catppuccin theming, tray icon, single-instance
guard, Messages/Notifications/Settings pages, all running against an in-memory
`FakeBackend`. The real KDE Connect backend comes in Phase 2; until then, the app falls
back to the fake backend even without `--fake`.

## Setup (Fedora)

```bash
sudo dnf install kde-connect fuse-sshfs android-tools scrcpy
sudo firewall-cmd --permanent --add-service=kdeconnect && sudo firewall-cmd --reload

python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

PyQt6 6.12 ships broken type stubs, so the `dev` extra pins 6.11 so mypy stays usable.
The runtime itself works with either version.

## Run

```bash
.venv/bin/python -m mooring --fake            # synthetic data, no phone needed
.venv/bin/python -m mooring --fake --flavor latte
```

Or press F5 in VS Code ("Mooring (fake backend)").

Niri has no built-in tray or notification daemon. The tray icon shows up in Waybar (its
StatusNotifierItem module). Without a tray, closing the window quits the app.

## Develop

```bash
.venv/bin/pytest            # headless (QT_QPA_PLATFORM=offscreen)
.venv/bin/ruff check src tests && .venv/bin/ruff format src tests
.venv/bin/mypy
```

## Privacy

Mooring handles your SMS, contacts and photos, and other people's. Never log message bodies,
phone numbers or contact names, only IDs and counts. Test fixtures use synthetic data only.
