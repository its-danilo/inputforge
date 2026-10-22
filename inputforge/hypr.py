"""Hyprland sockets: requests (.socket.sock) and the event stream (.socket2.sock).

The engine is a systemd user service, so its HYPRLAND_INSTANCE_SIGNATURE may be stale
after Hyprland restarts: the instance is found again from $XDG_RUNTIME_DIR/hypr/*
(newest socket that answers) whenever a request fails.
"""

import asyncio
import glob
import json
import os
import socket

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
_instance = {"dir": None}


def _candidates():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    dirs = [os.path.join(RUNTIME, "hypr", sig)] if sig else []
    others = glob.glob(os.path.join(RUNTIME, "hypr", "*", ".socket.sock"))
    others.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    dirs += [os.path.dirname(p) for p in others]
    return [d for d in dict.fromkeys(dirs) if os.path.exists(os.path.join(d, ".socket.sock"))]


def _raw(directory, cmd, timeout=1.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(os.path.join(directory, ".socket.sock"))
        s.sendall(cmd.encode())
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
        return out.decode(errors="replace")
    finally:
        s.close()


def request(cmd, timeout=1.0):
    """Blocking request ("j/activewindow", "cursorpos", "dispatch ..."); "" when Hyprland is gone."""
    if _instance["dir"]:
        try:
            return _raw(_instance["dir"], cmd, timeout)
        except OSError:
            _instance["dir"] = None
    for d in _candidates():
        try:
            out = _raw(d, cmd, timeout)
        except OSError:
            continue
        _instance["dir"] = d
        return out
    return ""


def jrequest(cmd, default=None):
    out = request("j/" + cmd)
    try:
        return json.loads(out)
    except ValueError:
        return default


def cursor_pos():
    out = request("cursorpos").strip()
    try:
        x, y = out.split(",")
        return int(float(x)), int(float(y))
    except ValueError:
        return None


def move_cursor(x, y):
    return request(f"dispatch hl.dsp.cursor.move({{x={int(x)},y={int(y)}}})")


def active_window():
    w = jrequest("activewindow", {}) or {}
    if not isinstance(w, dict) or not w.get("address"):
        return None
    ws = w.get("workspace") or {}
    return {"address": w["address"], "class": w.get("class") or "", "title": w.get("title") or "",
            "initialClass": w.get("initialClass") or "", "pid": w.get("pid"),
            "workspace": ws.get("name") or "", "fullscreen": bool(w.get("fullscreen"))}


def clients():
    out = []
    for w in jrequest("clients", []) or []:
        if isinstance(w, dict) and w.get("mapped", True) and w.get("class"):
            out.append({"address": w.get("address"), "class": w.get("class"), "title": w.get("title") or "",
                        "workspace": (w.get("workspace") or {}).get("name") or ""})
    return out


def active_layout_index():
    devices = jrequest("devices", {}) or {}
    kbs = [k for k in devices.get("keyboards", []) if isinstance(k, dict)]
    main = [k for k in kbs if k.get("main")] or kbs
    try:
        return int(main[0].get("active_layout_index", 0))
    except (IndexError, TypeError, ValueError):
        return 0


def sync_layout(device_prefix):
    """New virtual keyboards start on layout 0: put them on the one the main keyboard uses
    (e.g. a second layout), the way `hyprctl switchxkblayout all` would."""
    devices = jrequest("devices", {}) or {}
    idx = active_layout_index()
    for k in devices.get("keyboards", []):
        if k.get("name", "").startswith(device_prefix) and k.get("active_layout_index") != idx:
            request(f"switchxkblayout {k['name']} {idx}")


def game_on_screen():
    """The focused monitor shows the special workspace "game" (calOS keeps games there)."""
    m = focused_monitor() or {}
    return (m.get("specialWorkspace") or {}).get("name") == "special:game"


def focused_monitor():
    for m in jrequest("monitors", []) or []:
        if m.get("focused"):
            return m
    return None


async def events(on_event):
    """Forever: on_event(name, data) for every socket2 line, reconnecting when Hyprland restarts.
    on_event("connected", "") after each (re)connection."""
    while True:
        reader = None
        for d in _candidates():
            try:
                reader, writer = await asyncio.open_unix_connection(os.path.join(d, ".socket2.sock"))
                _instance["dir"] = d
                break
            except OSError:
                continue
        if reader is None:
            await asyncio.sleep(2)
            continue
        on_event("connected", "")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                name, _, data = line.decode(errors="replace").rstrip("\n").partition(">>")
                on_event(name, data)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
        await asyncio.sleep(1)
