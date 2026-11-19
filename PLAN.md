# Mooring: Plan and Architecture

A Phone Link–style hub for Linux: messages, notifications, photos, files, clipboard and
screen mirroring for an Android phone, all in one window.

- **Target:** Fedora (Niri compositor, Wayland)
- **Stack:** Python 3.12+ and PyQt6, Catppuccin theming
- **Status:** v0.1.0. All phases done and tested live. Calls are out of scope.

This file started as the build plan. It now records what was built and where reality
differed from the plan, so the reasoning isn't lost.

---

## 1. Core design decision

**Mooring is a front end. It does not implement a phone protocol.**

| Layer | Provided by | How Mooring talks to it |
|---|---|---|
| Phone ↔ PC link, pairing, TLS | `kdeconnectd` (KDE Connect) | D-Bus, via **jeepney** |
| SMS, notifications, files, clipboard, battery, contacts | `kdeconnectd` plugins | D-Bus |
| Screen mirroring (inside Mooring) | `scrcpy-server` (shipped with scrcpy) | Mooring's own client: adb + sockets, H.264 decoded with **PyAV** |
| Mirroring in a separate window, single phone apps | `scrcpy` / `adb` | `QProcess` |
| UI, state, theming | **Mooring** (PyQt6) | |

**Changed from the plan:** the plan assumed QtDBus. PyQt6's `QDBusArgument` has no read
API, so KDE Connect's SMS structs can't be decoded with it. All D-Bus goes through jeepney,
driven by a `QSocketNotifier` (`backend/qjeepney.py`), so nothing blocks the GUI thread.

**Added after the plan:** Wayland can't embed another app's window, so "mirroring inside
the main window" can't reuse scrcpy's window. Mooring speaks the scrcpy v5.0.1 protocol
itself (`backend/scrcpy_protocol.py`, `backend/scrcpy_client.py`).

---

## 2. Architecture

```
┌──────────────────────────── Mooring (PyQt6) ─────────────────────────────┐
│  MainWindow: sidebar (Messages | Photos | Notifications | Files | Apps |  │
│              Mirror | Settings) + device header + tray                    │
│        │ Qt signals/slots                                                 │
│  Models (QAbstractListModel): conversations, messages, notifications,     │
│        photos                                                             │
│        │                                                                  │
│  PhoneBackend (interface, backend/base.py)                                │
│   ├─ KdeConnectBackend ─ jeepney/DBusClient ─► kdeconnectd (session bus)  │
│   │    ├─ SmsChannel           (kdeconnect_sms.py)                        │
│   │    └─ NotificationChannel  (kdeconnect_notifications.py)              │
│   └─ FakeBackend (synthetic data: `mooring --fake`, tests)                │
│  MirrorBackend ─ QProcess ─► adb / scrcpy (apps, pop-out, Wi-Fi pairing)  │
│  MirrorSession ─ adb forward + sockets ─► scrcpy-server on the phone      │
│  Services: contacts (vCards), notifier (fdo D-Bus), photos + thumbnails   │
│            (thread pool, ~/.cache/mooring 0700), settings, desktop        │
└───────────────────────────────────────────────────────────────────────────┘
```

Rules that held up:
- The UI never talks to D-Bus; it uses `PhoneBackend` signals and the models.
- Every phone call is asynchronous. Blocking work (photo scans, thumbnails, video
  decoding, file copies) runs on worker threads; only the latest video frame reaches the GUI.
- Signals are accepted only from the bus name that owns the service (kdeconnectd, the
  notification daemon), so other session processes can't spoof them.

---

## 3. Repository layout

```
mooring/
├─ pyproject.toml           # setuptools; extras: dev, mirror (PyAV)
├─ PLAN.md, README.md, LICENSE (GPL-3.0-or-later)
├─ data/                    # .desktop + AppStream metainfo (io.github.ms4ndst.Mooring)
├─ packaging/mooring.spec   # RPM; scripts/build-rpm.sh builds it
├─ docs/                    # dbus-introspection.txt (verified API), screenshots/
├─ spikes/                  # Phase 0 and mirroring spikes (print counts/types only)
├─ src/mooring/
│  ├─ app.py                # QApplication, tray, single instance, CLI flags
│  ├─ backend/              # base, kdeconnect(+_sms,+_notifications), qjeepney,
│  │                        # dbus_paths, sms_decode, fake, mirror, scrcpy_*, pyav
│  ├─ models/  services/  theme/  resources/icons/
│  └─ ui/                   # main_window, pages/, widgets/
└─ tests/                   # pytest-qt; fakes: bus, KDE Connect daemon, adb/scrcpy runner,
                            # scrcpy server streaming real H.264
```

---

## 4. Phases: plan vs. outcome

| Phase | Planned | Outcome |
|---|---|---|
| 0 Spike | Verify the D-Bus API | Done. Real API in `docs/dbus-introspection.txt` (e.g. `isPaired`, not `isTrusted`; SMS struct layout; no end marker for cached thread pages). QtDBus ruled out → jeepney. |
| 1 Skeleton | Window, theme, tray, fake backend | Done. |
| 2 Device | Discover, pair, reachability, battery | Done, plus active-link display (Wi-Fi / Bluetooth) and daemon-restart handling. "Disconnected within seconds" depends on KDE Connect's link timeouts (~20 s over Bluetooth). |
| 3 Messages | Threads, compose, notifications, MMS | Done: lazy paging, contacts from synced vCards (incl. vCard 2.1 quoted-printable), Open/Reply notifications, MMS thumbnails. Sending is text-only. |
| 4 Photos/files/clipboard | sftp mount, photo grid, send files | Done. Photo folders are scanned broadly (phones disagree on names). |
| 5 Mirroring & apps | scrcpy window, apps in own windows | Done, and extended: a **Mirror** page shows the phone inside Mooring (own scrcpy-protocol client, PyAV); **Pop out** and single apps use scrcpy windows. |
| – Notifications page | (not in the plan) | Done: list, dismiss, reply to chat notifications. |
| 7 Packaging | Desktop file, metainfo, RPM | Done: app ID, icons, start at login, RPM. Flatpak deferred (scrcpy/adb would need host access). |

---

## 5. Lessons worth keeping

- **Verify against the live system.** Several plan assumptions were wrong (QtDBus,
  `isTrusted`, folder names, scrcpy's protocol before 4.0) and only live checks caught them.
- **KDE Connect's Bluetooth link (beta)** can go dead while still listed, and swallows SMS
  traffic. Prefer Wi-Fi; Mooring explains this in the status bar.
- **Phones tag video oddly** (seen: YCgCo colour space). Name the source colour space
  explicitly when converting.
- **Fedora's RPM launcher runs `python3 -sP`**, which hides `pip install --user` packages;
  `backend/pyav.py` looks for PyAV there explicitly.
- **Qt + threads + Pillow:** never let a per-object `QThreadPool` be destroyed by Python's
  GC while jobs run (GIL deadlock). Use the global pool and parentless signal objects.

## 6. Privacy rules (unchanged)

- Never log message bodies, phone numbers, contact names, app names or file names; only
  IDs and counts.
- Test fixtures use synthetic data only; tests isolate `XDG_DATA_HOME`, `XDG_CACHE_HOME`
  and `XDG_CONFIG_HOME` and never run the real adb/scrcpy.
- Caches live in `~/.cache/mooring` with `0700` permissions; Settings has "Clear cache".

## 7. Open items

Everything above has been tested live against a real phone. Possible additions, only if
they turn out to be missed in daily use:

- Mirror: phone audio, Ctrl+C/V passthrough, multi-touch.
- Messages: sending pictures (MMS), new group conversations.
- Notification action buttons; HEIC thumbnails; Flatpak.

Out of scope: phone calls (not needed; the phone is always at hand).
