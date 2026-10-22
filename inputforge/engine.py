"""InputForge engine (`inputforge engine`, the inputforge.service user unit).

Arms one profile at a time, from the focused window: a game profile whose class/title
matches, else the global one ("Qualquer janela") when it's enabled. Only an armed profile
with enabled bindings (or a run, the recorder, a capture...) makes it read the keyboard.

Runs are tied to the window that was focused when they started: when focus leaves it they
pause (or stop, per profile) and release every key they hold, so a clicker never clicks on
the desktop; they resume when that window is back, stop when it closes. The panic key
(hold Esc by default) stops everything, always.

IPC: $XDG_RUNTIME_DIR/inputforge/engine.sock, one JSON per line.
  request  {"id": n, "cmd": "...", ...}  ->  {"id": n, "ok": true, "result": ...}
  {"cmd": "subscribe"} turns the connection into an event stream: {"event": "state", ...}
State for waybar: $XDG_RUNTIME_DIR/inputforge/waybar.json + SIGRTMIN+14 to waybar.
"""

import asyncio
import fcntl
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time

from evdev import ecodes as e

from inputforge import config, hypr, keys, screen
from inputforge.devices import DeviceManager, Outputs
from inputforge import paths
from inputforge.paths import RUNTIME, SOCKET, WAYBAR_STATE
WAYBAR_SIGNAL = 14
OWN_CLASS = paths.APP_ID
AURUM_CLI = paths.AURUM_CLI  # OpenAurum (Pichau Aurum V60), optional
GAMEMODE_FILE = paths.CALOS_GAMEMODE  # calOS Game Mode, optional
PUBLISH_S = 0.1
CAPTURE_S = 8
PICK_S = 90


def _log(*args):
    print(*args, file=sys.stderr, flush=True)


def rate_text(action):
    r = action["rate"]
    if r["unit"] == "cps":
        v = r["value"]
        return f"{v:g} CPS"
    return f"a cada {r['value']:g} ms"


class StopMacro(Exception):
    """A wait_screen step timed out with on_timeout = stop."""


class Run:
    """One running binding. `ok` is set while it may act (not paused, countdown over)."""

    def __init__(self, engine, binding, profile, source):
        self.id = config.new_id()
        self.engine = engine
        self.binding = binding
        self.profile = profile
        self.source = source  # "hotkey" | "window" | "cli"
        self.status = "countdown" if source == "window" else "running"
        self.window = None
        self.paused_by = None  # "blur" | None
        self.ok = asyncio.Event()
        self.task = None
        self.started = time.time()
        self.countdown_until = 0
        self.count = 0
        self.held = set()

    def info(self):
        a = self.binding["action"]
        kind = a["type"]
        detail = ""
        if kind == "clicker":
            detail = rate_text(a)
            if a["limit"]["kind"] == "count":
                detail += f" · {self.count}/{int(a['limit']['value'])}"
        elif kind == "macro":
            rep = a["repeat"]
            detail = f"{len(a['steps'])} passos" + (" · ∞" if rep == 0 else f" · {self.count}/{rep}" if rep > 1 else "")
        return {"id": self.id, "binding": self.binding["id"], "profile": self.profile["id"],
                "profile_name": self.profile["name"], "name": self.binding["name"], "kind": kind,
                "status": self.status, "detail": detail, "started": self.started,
                "countdown_until": self.countdown_until, "trigger": self.binding["trigger"],
                "mode": self.binding["mode"]}

    # helpers for runners --------------------------------------------------
    async def gate(self):
        await self.ok.wait()

    def press(self, code, value, via=None):
        if value:
            self.held.add(code)
        else:
            self.held.discard(code)
        self.engine.out.key(code, value, via)

    def release(self):
        self.engine.out.release(self.held)
        self.held.clear()

    def pause(self, why):
        if self.status == "running":
            self.status = "paused"
            self.paused_by = why
            self.ok.clear()
            self.release()

    def resume(self):
        if self.status == "paused":
            self.status = "running"
            self.paused_by = None
            self.ok.set()


class Engine:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.cfg = config.load()
        self.keymap = keys.Keymap()
        self.out = Outputs()
        self.dev = DeviceManager(self.loop, self.out, self.on_input)
        self.focus = None  # active window
        self.last_game = None  # last focused window that isn't InputForge
        self.recent = []  # last focused windows, distinct classes, newest first
        self.armed = None  # profile
        self.by_key = {}  # trigger code -> [(binding, profile)]
        self.runs = {}  # id -> Run
        self.down = set()  # physical keys/buttons held (all devices)
        self.active = {}  # trigger code -> [binding ids fired on its press] (for release)
        self.long = {}  # trigger code -> TimerHandle (hold_ms triggers)
        self.remapped = {}  # source code -> [target codes] (held remaps)
        self.subscribers = set()
        self.recorder = None
        self.capture = None  # {"future", "pressed", "mods", "timer"}
        self.picker = None  # {"points", "timer", "max", "hint", "on_done"}
        self.watch = screen.Watcher(self.loop)
        self.screen_trigs = []  # [(binding, profile, region)] of the armed profile
        self.screen_task = None
        self.ref_capture = None  # {"until"}: a region reference being captured (HUD countdown)
        # InputForge surfaces over the game: connection -> None (the Game Bar: covers it, all
        # runs rest while it's open) or [x, y, w, h] (the editor over a fullscreen game: runs
        # rest only while the pointer is on it)
        self.ui_overlay = {}
        self.overlay_now = False
        self.overlay_task = None
        self.toast = None  # {"text", "until"}
        self.submap = ""
        self.hud_proc = None
        self.waybar_last = None
        self.aurum_last = None
        self.aurum_timer = None
        self.state_pending = False
        self.last_publish = 0.0

    # ------------------------------------------------------------------ start/stop
    async def start(self):
        os.makedirs(RUNTIME, mode=0o700, exist_ok=True)
        if os.path.exists(SOCKET):
            os.unlink(SOCKET)
        self.server = await asyncio.start_unix_server(self.client, SOCKET)
        os.chmod(SOCKET, 0o600)
        self.loop.call_later(0.5, hypr.sync_layout, "inputforge-")
        self.loop.create_task(hypr.events(self.on_hypr))
        if self.cfg["settings"]["aurum"]["enabled"]:
            self._aurum_cmd(["glow", "off"])  # a crash may have left keys lit (no-op otherwise)
        self.refresh_focus()
        self.apply()

    def shutdown(self):
        for run in list(self.runs.values()):
            self.stop_run(run, publish=False)
        self.out.release_all()
        self.dev.close()
        self.out.close()
        self.write_waybar({"text": "", "tooltip": "", "class": "idle"})
        if self.aurum_last:
            self._aurum_cmd(["glow", "off"])
        try:
            os.unlink(SOCKET)
        except OSError:
            pass

    # ------------------------------------------------------------------ hyprland
    def on_hypr(self, name, data):
        if name == "connected":  # the submap events only tell changes: ask for the current one
            sm = hypr.request("submap").strip()
            self.submap = "" if sm in ("", "default") else sm
        if name in ("activewindowv2", "connected", "workspace", "focusedmon", "activespecial"):
            self.refresh_focus()
        elif name == "closewindow":
            addr = "0x" + data.strip()
            for run in list(self.runs.values()):
                if run.window == addr:
                    self.stop_run(run)
        elif name == "submap":
            self.submap = data.strip()
            self.publish()

    def refresh_focus(self):
        w = hypr.active_window()
        old = self.focus["address"] if self.focus else None
        self.focus = w
        if w and w["class"] != OWN_CLASS:
            self.last_game = w
            # recent distinct windows: the window offers "create a profile" for the last game
            # even after the terminal/browser took focus
            self.recent = [w] + [r for r in self.recent if r["class"] != w["class"]][:7]
        if (w["address"] if w else None) != old:
            self.on_focus_change()

    def focus_address(self):
        """The window runs may act in: none while the Game Bar covers the game (a clicker
        would click inside it)."""
        if self.overlay_now or not self.focus:
            return None
        return self.focus["address"]

    def update_overlay(self):
        """overlay_now: the Game Bar is open, or the pointer is on the editor over the game.
        While an editor is open the pointer is checked 10 times a second."""
        rects = [r for r in self.ui_overlay.values() if r]
        now = any(r is None for r in self.ui_overlay.values())
        if not now and rects:
            pos = hypr.cursor_pos()
            now = bool(pos) and any(x <= pos[0] < x + w and y <= pos[1] < y + h for x, y, w, h in rects)
        if rects and not self.overlay_task:
            self.overlay_task = self.loop.create_task(self._watch_pointer())
        if now != self.overlay_now:
            self.overlay_now = now
            self.on_focus_change()

    async def _watch_pointer(self):
        try:
            while any(self.ui_overlay.values()):
                await asyncio.sleep(0.1)
                self.update_overlay()
        finally:
            self.overlay_task = None

    def on_focus_change(self):
        addr = self.focus_address()
        for run in list(self.runs.values()):
            if run.window is None:
                continue
            if addr == run.window:
                if run.paused_by == "blur":
                    run.resume()
            elif run.status == "running":
                if run.profile.get("on_blur") == "stop":
                    self.stop_run(run, publish=False)
                else:
                    run.pause("blur")
        self.apply()

    # ------------------------------------------------------------------ arming
    def match(self, profile, w):
        m = profile["match"]
        if not m["class"] and not m["title"]:
            return False
        try:
            if m["class"] and not re.search(m["class"], w["class"]) and not re.search(m["class"], w["initialClass"]):
                return False
            if m["title"] and not re.search(m["title"], w["title"]):
                return False
        except re.error:
            return False
        return True

    def candidates(self, w, include_off=False):
        """Game profiles that match window w (several games can share one window class:
        every Roblox game runs in Sober). Only enabled ones arm; the switcher lists all."""
        if not w or w["class"] == OWN_CLASS:
            return []
        return [p for p in self.cfg["profiles"][1:] if (include_off or p["enabled"]) and self.match(p, w)]

    def game_window(self):
        """The game the windows talk about: the focused window, or the last game while
        InputForge itself has focus."""
        return self.focus if self.focus and self.focus["class"] != OWN_CLASS else self.last_game

    def profile_for(self, w):
        cands = self.candidates(w)
        if not cands:
            return None
        chosen = self.cfg["settings"]["chosen"].get(w["class"])
        return next((p for p in cands if p["id"] == chosen), cands[0])

    def resolve_profile(self):
        p = self.profile_for(self.focus)
        if p:
            return p
        g = self.cfg["profiles"][0]
        return g if self.cfg["settings"]["global_enabled"] else None

    def choose_profile(self, pid):
        """Make pid the active profile for the game it matches (the focused window, or the
        last game when InputForge itself has focus); remembered in the config."""
        w = self.game_window()
        p = next((p for p in self.candidates(w, include_off=True) if p["id"] == pid), None)
        if not p:
            raise ValueError("esse perfil não serve para a janela do jogo")
        p["enabled"] = True  # picking a switched-off profile turns it on
        self.cfg["settings"]["chosen"][w["class"]] = pid
        config.save(self.cfg)
        self.apply()
        self.broadcast({"event": "config", "config": self.cfg})
        self.show_toast(f"Perfil {p['name']} ativo")
        return True

    def apply(self):
        prev = self.armed["id"] if self.armed else None
        self.armed = self.resolve_profile()
        self.by_key = {}
        want = set()
        if self.armed and not self.overlay_now:  # hotkeys rest while the Game Bar/editor has the pointer
            for b in self.armed["bindings"]:
                if not b["enabled"]:
                    continue
                c = keys.code(b["trigger"]["key"])
                if c is None or (b["trigger"]["kind"] == "screen" and b["action"]["type"] != "remap"):
                    continue
                self.by_key.setdefault(c, []).append((b, self.armed))
                if b["trigger"]["block"] or b["action"]["type"] == "remap":
                    want.add(c)
        cur = self.armed["id"] if self.armed else None
        if cur != prev:
            self.release_remaps()
            if self.armed and self.armed["id"] != "global" and self.armed["bindings"] \
                    and self.cfg["settings"]["hud"]["announce"]:
                self.show_toast(f"Perfil {self.armed['name']} ativo")
        self.update_screen_triggers()
        grab_all = bool(self.capture or self.picker)
        panic = keys.code(self.cfg["settings"]["panic"]["key"])
        self.dev.set_need_mouse(grab_all or bool(self.recorder) or keys.is_mouse(panic)
                                or any(keys.is_mouse(c) for c in self.by_key))
        self.dev.set_want(want, grab_all=grab_all)
        self.dev.listen(self.needs_input())
        self.publish()

    def needs_input(self):
        return bool(self.by_key or self.runs or self.recorder or self.capture or self.picker)

    def release_remaps(self):
        for targets in self.remapped.values():
            self.out.release([keys.code(t) for t in targets])
        self.remapped.clear()

    # ------------------------------------------------------------------ input
    def mods_held(self):
        return {m for m, codes in keys.MODS.items() if any(c in self.down for c in codes)}

    def trig_matches(self, trig, code):
        return keys.code(trig["key"]) == code and set(trig["mods"]) <= self.mods_held()

    def on_input(self, d, ev):
        """Every event of every open device; True = swallow (when the device is grabbed)."""
        if ev.type == e.EV_KEY:
            if ev.value == 1:
                self.down.add(ev.code)
            elif ev.value == 0:
                self.down.discard(ev.code)
        if self.capture:
            return self.on_capture(ev)
        if self.picker:
            return self.on_pick(ev)
        if ev.type != e.EV_KEY:
            if self.recorder:
                self.recorder.event(ev)
            return False
        code, value = ev.code, ev.value
        if value == 2:
            return code in self.remapped
        # panic: always first
        panic = self.cfg["settings"]["panic"]
        if self.trig_matches(panic, code) or (value == 0 and code in self.long and code == keys.code(panic["key"])):
            if value == 1:
                self.arm_long(code, panic["hold_ms"], self.panic)
            else:
                self.cancel_long(code)
            if not self.recorder:
                return False
        if self.recorder:
            stop = self.cfg["settings"]["record_stop"]
            if self.trig_matches(stop, code) and value == 1:
                self.loop.call_soon(self.record_stop)
                return True
            if code == keys.code(stop["key"]):
                return True
            self.recorder.event(ev)
            return False
        if value == 1:
            return self.on_press(code, d)
        return self.on_release(code, d)

    def arm_long(self, code, hold_ms, fn):
        self.cancel_long(code)
        if hold_ms <= 0:
            fn()
            return
        self.long[code] = self.loop.call_later(hold_ms / 1000, self._fire_long, code, fn)

    def _fire_long(self, code, fn):
        self.long.pop(code, None)
        if code in self.down:
            fn()

    def cancel_long(self, code):
        h = self.long.pop(code, None)
        if h:
            h.cancel()

    def on_press(self, code, d):
        cands = [(b, p) for b, p in self.by_key.get(code, []) if self.trig_matches(b["trigger"], code)]
        if not cands:
            return False
        best = max(len(b["trigger"]["mods"]) for b, _ in cands)
        cands = [(b, p) for b, p in cands if len(b["trigger"]["mods"]) == best]
        swallow = False
        fired = []
        for b, p in cands:
            swallow = swallow or b["trigger"]["block"] or b["action"]["type"] == "remap"
            if b["action"]["type"] == "remap":
                self.remap_down(code, b, d)
                fired.append(b["id"])
            elif b["trigger"]["hold_ms"] > 0:
                self.arm_long(code, b["trigger"]["hold_ms"], lambda b=b, p=p: self.fire(b, p, "down"))
                fired.append(b["id"])
            else:
                self.fire(b, p, "down")
                fired.append(b["id"])
        self.active[code] = fired
        return swallow

    def on_release(self, code, d):
        self.cancel_long(code)
        fired = self.active.pop(code, [])
        swallow = False
        if code in self.remapped:
            targets = self.remapped.pop(code)
            for t in reversed(targets):
                self.out.key(keys.code(t), 0)
            swallow = True
        for run in list(self.runs.values()):
            if run.binding["id"] in fired and run.binding["mode"] == "hold":
                self.stop_run(run)
        for b, _ in self.by_key.get(code, []):
            if b["id"] in fired and b["trigger"]["block"]:
                swallow = True
        return swallow

    def remap_down(self, code, b, d):
        targets = b["action"]["to"]
        self.remapped[code] = targets
        for t in targets:
            c = keys.code(t)
            via = d.clone if d.clone is not None and c in d.codes else None
            self.out.key(c, 1, via)

    def fire(self, b, p, edge):
        kind = b["action"]["type"]
        if kind == "command":
            self.run_command(b["action"]["cmd"])
            return
        existing = [r for r in self.runs.values() if r.binding["id"] == b["id"]]
        if existing:
            if b["mode"] in ("toggle", "once"):
                for r in existing:
                    self.stop_run(r)
            return
        self.start_run(b, p, "hotkey")

    def run_command(self, cmd):
        if cmd.strip():
            subprocess.Popen(["sh", "-c", cmd], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def panic(self):
        n = len(self.runs)
        for run in list(self.runs.values()):
            self.stop_run(run, publish=False)
        self.out.release_all()
        self.release_remaps()
        if self.recorder:
            self.record_stop()
        if self.picker:
            self.pick_stop()
        self.show_toast("Tudo parado" if n else "Pânico: nada rodando")
        self.apply()

    # ------------------------------------------------------------------ runs
    def find_binding(self, binding_id):
        for p in self.cfg["profiles"]:
            for b in p["bindings"]:
                if b["id"] == binding_id:
                    return b, p
        return None, None

    def start_run(self, b, p, source):
        if b["action"]["type"] == "remap":
            return None
        if b["action"]["type"] == "command":
            self.run_command(b["action"]["cmd"])
            return None
        run = Run(self, b, p, source)
        self.runs[run.id] = run
        run.task = self.loop.create_task(self._run(run))
        self.apply()
        return run

    async def _run(self, run):
        try:
            if run.status == "countdown":
                n = self.cfg["settings"]["countdown"]
                run.countdown_until = time.time() + n
                self.publish()
                await asyncio.sleep(n)
                self.refresh_focus()
            run.window = self.focus["address"] if self.focus else None
            if self.overlay_now:  # started from the Game Bar/editor, still over the game: wait
                run.status = "paused"
                run.paused_by = "blur"
            if run.paused_by != "blur":
                run.status = "running"
                run.ok.set()
            self.publish()
            kind = run.binding["action"]["type"]
            if kind == "clicker":
                await self.run_clicker(run)
            elif kind == "macro":
                await self.run_macro(run)
        except asyncio.CancelledError:
            pass
        except Exception as ex:  # a bad step must not take the engine down
            _log("run failed:", repr(ex))
            self.show_toast(f"Erro em {run.binding['name']}")
        finally:
            run.release()
            if self.runs.pop(run.id, None):
                self.apply()

    def stop_run(self, run, publish=True):
        run.release()
        if run.task and not run.task.done():
            run.task.cancel()
        self.runs.pop(run.id, None)
        if publish:
            self.apply()

    async def tap(self, run, code, hold, via=None):
        run.press(code, 1, via)
        await asyncio.sleep(hold)
        run.press(code, 0, via)

    async def run_clicker(self, run):
        a = run.binding["action"]
        r = a["rate"]
        interval = 1 / r["value"] if r["unit"] == "cps" else r["value"] / 1000
        target = keys.code(a["target"])
        hold = min(a["hold_ms"] / 1000, interval * (0.25 if a["double"] else 0.5))
        lim = a["limit"]
        if run.binding["mode"] == "once" and lim["kind"] == "none":
            lim = {"kind": "count", "value": 1}
        points = a["points"] if a["where"] == "points" else []
        active_time = 0.0
        nxt = self.loop.time()
        i = 0
        while True:
            if not run.ok.is_set():
                await run.gate()
                nxt = self.loop.time()
            if lim["kind"] == "count" and run.count >= lim["value"]:
                break
            if lim["kind"] == "time" and active_time >= lim["value"]:
                break
            t0 = self.loop.time()
            if points:
                pt = points[i % len(points)]
                j = a["point_jitter"]
                hypr.move_cursor(pt["x"] + random.randint(-j, j), pt["y"] + random.randint(-j, j))
                i += 1
                await asyncio.sleep(0.004)  # let the pointer land before the press
            await self.tap(run, target, hold)
            if a["double"]:
                await asyncio.sleep(hold)
                await self.tap(run, target, hold)
            run.count += 1
            if run.count % 25 == 0 or lim["kind"] == "count":
                self.publish()
            jit = a["jitter"] / 100
            step = interval * (1 + random.uniform(-jit, jit)) if jit else interval
            nxt += step
            delay = nxt - self.loop.time()
            if delay < -interval * 4:  # fell far behind (paused / system busy): don't burst
                nxt = self.loop.time()
                delay = 0
            await asyncio.sleep(max(0.0, delay))
            active_time += self.loop.time() - t0

    def region(self, profile, rid):
        """A region of the binding's profile (or of the global one)."""
        for p in (profile, self.cfg["profiles"][0]):
            for r in p.get("regions", []):
                if r["id"] == rid:
                    return r
        return None

    async def run_macro(self, run):
        a = run.binding["action"]
        speed = a["speed"]
        rep = a["repeat"]
        if run.binding["mode"] == "hold" and rep == 1:
            rep = 0  # hold = repeat while held
        regions = [self.region(run.profile, st["region"]) for st in a["steps"] if st["t"] in ("wait_screen", "if_screen")]
        token = self.watch.subscribe([r for r in regions if r]) if regions else None
        run.bases = {}  # step id -> signature when a "change" condition first looked
        try:
            while rep == 0 or run.count < rep:
                t0 = self.loop.time()
                i = 0
                steps = a["steps"]
                while i < len(steps):
                    await run.gate()
                    i += 1 + (await self.step(run, steps[i], speed) or 0)
                run.count += 1
                self.publish()
                if not steps:
                    break
                if a["gap_ms"] and (rep == 0 or run.count < rep):
                    await asyncio.sleep(a["gap_ms"] / 1000 / speed)
                if regions and self.loop.time() - t0 < 1 / screen.RATE:
                    await self.watch.next_frame()  # a loop of skipped screen checks: once per capture
                await asyncio.sleep(0)  # an all-zero-wait macro must not starve the loop
        except StopMacro:
            pass
        finally:
            if token:
                self.watch.unsubscribe(token)

    async def step(self, run, s, speed):
        """Runs one step; returns how many of the next steps to skip (if_screen)."""
        t = s["t"]
        if t in ("wait_screen", "if_screen"):
            return await self.screen_step(run, s)
        if t in ("key", "button"):
            codes = [keys.code(c) for c in s["codes"]]
            hold = s["hold_ms"] / 1000 / speed
            if s["act"] in ("down", "tap", "double"):
                for _ in range(2 if s["act"] == "double" else 1):
                    for c in codes:
                        run.press(c, 1)
                    if s["act"] == "down":
                        break
                    await asyncio.sleep(hold)
                    for c in reversed(codes):
                        run.press(c, 0)
                    if s["act"] == "double":
                        await asyncio.sleep(hold)
            else:
                for c in reversed(codes):
                    run.press(c, 0)
        elif t == "wait":
            ms = s["ms"] + (random.uniform(0, s["rand_ms"]) if s["rand_ms"] else 0)
            await asyncio.sleep(ms / 1000 / speed)
        elif t == "move":
            if s["ms"] <= 0:
                self.out.rel(s["dx"], s["dy"])
            else:
                n = max(1, int(s["ms"] / speed / 8))
                done_x = done_y = 0
                for k in range(1, n + 1):
                    x, y = round(s["dx"] * k / n), round(s["dy"] * k / n)
                    self.out.rel(x - done_x, y - done_y)
                    done_x, done_y = x, y
                    await asyncio.sleep(s["ms"] / speed / n / 1000)
        elif t == "moveto":
            hypr.move_cursor(s["x"], s["y"])
            await asyncio.sleep(0.004)
        elif t == "scroll":
            self.out.scroll(s["dy"], s["dx"])
        elif t == "text":
            layout = hypr.active_layout_index()
            for code, shift, altgr in self.keymap.text_strokes(s["value"], layout):
                await run.gate()
                if altgr:
                    run.press(e.KEY_RIGHTALT, 1)
                if shift:
                    run.press(e.KEY_LEFTSHIFT, 1)
                await self.tap(run, code, 0.012 / speed)
                if shift:
                    run.press(e.KEY_LEFTSHIFT, 0)
                if altgr:
                    run.press(e.KEY_RIGHTALT, 0)
                await asyncio.sleep(0.01 / speed)
        elif t == "command":
            self.run_command(s["cmd"])

    async def screen_step(self, run, s):
        reg = self.region(run.profile, s["region"])
        if not reg or (s["cond"] != "change" and not reg["ref"]):
            return s.get("skip", 0) if s["t"] == "if_screen" else 0  # unmarked region: condition false
        sig = await self.watch.sample(reg)
        base = run.bases.setdefault(s["id"], sig) if s["cond"] == "change" else None
        if s["t"] == "if_screen":
            return 0 if screen.check(sig, reg, s["cond"], base) else s["skip"]
        deadline = self.loop.time() + s["timeout_ms"] / 1000 if s["timeout_ms"] else None
        while not screen.check(sig, reg, s["cond"], base):
            if deadline and self.loop.time() >= deadline:
                if s["on_timeout"] == "stop":
                    raise StopMacro()
                return 0
            await run.gate()
            sig = await self.watch.sample(reg)
        if s["cond"] == "change":
            run.bases.pop(s["id"], None)  # next time, wait for a new change
        return 0

    # ------------------------------------------------------------------ screen triggers
    def update_screen_triggers(self):
        trigs = []
        if self.armed and not self.overlay_now:
            for b in self.armed["bindings"]:
                t = b["trigger"]
                if b["enabled"] and t["kind"] == "screen" and b["action"]["type"] != "remap":
                    reg = self.region(self.armed, t["region"])
                    if reg and (reg["ref"] or t["cond"] == "change"):
                        trigs.append((b, self.armed, reg))
        sig = [(b["id"], screen.rect(r), b["trigger"]["cond"], b["mode"]) for b, _, r in trigs]
        if sig == getattr(self, "_screen_sig", None) and (self.screen_task or not trigs):
            self.screen_trigs = trigs
            return
        self._screen_sig = sig
        self.screen_trigs = trigs
        if self.screen_task:
            self.screen_task.cancel()
            self.screen_task = None
        if trigs:
            self.screen_task = self.loop.create_task(self._screen_triggers())

    async def _screen_triggers(self):
        token = self.watch.subscribe([r for _, _, r in self.screen_trigs])
        state, bases = {}, {}
        try:
            while True:
                await self.watch.next_frame()
                for b, p, reg in list(self.screen_trigs):
                    got = self.watch.latest.get(screen.rect(reg))
                    if not got:
                        continue
                    sig = got[1]
                    base = bases.setdefault(b["id"], sig) if b["trigger"]["cond"] == "change" else None
                    on = screen.check(sig, reg, b["trigger"]["cond"], base)
                    was = state.get(b["id"], on if b["trigger"]["cond"] != "change" else False)
                    state[b["id"]] = on
                    running = [r for r in self.runs.values() if r.binding["id"] == b["id"]]
                    if on and not was:  # rising edge
                        if b["trigger"]["cond"] == "change":
                            bases.pop(b["id"], None)
                        if b["action"]["type"] == "command":
                            self.run_command(b["action"]["cmd"])
                        elif b["mode"] == "toggle":
                            self.fire(b, p, "down")
                        elif not running:
                            self.start_run(b, p, "screen")
                    elif was and not on and b["mode"] == "hold":
                        for r in running:
                            self.stop_run(r)
        except asyncio.CancelledError:
            pass
        finally:
            self.watch.unsubscribe(token)

    # ------------------------------------------------------------------ regions
    def find_profile(self, pid):
        return next((p for p in self.cfg["profiles"] if p["id"] == pid), None)

    def save_region(self, pid, rid, **fields):
        """Updates (or creates) a region in the live config, saves and tells everyone."""
        p = self.find_profile(pid)
        if not p:
            return None
        reg = next((r for r in p.setdefault("regions", []) if r["id"] == rid), None)
        if not reg:
            reg = {"id": rid, "name": f"Região {len(p['regions']) + 1}", "x": 0, "y": 0, "w": 0, "h": 0,
                   "ref": None, "tolerance": 6}
            p["regions"].append(reg)
        reg.update(fields)
        self.cfg = config.normalize(self.cfg)
        config.save(self.cfg)
        self.refresh_running_bindings()
        self.apply()
        self.broadcast({"event": "config", "config": self.cfg})
        return reg

    def region_mark(self, pid, rid):
        """Two clicks on the screen (corners), then the reference is captured right away:
        mark it while it shows the state to wait for (e.g. the skill ready)."""
        def done(points):
            if len(points) < 2:
                self.show_toast("Região não marcada")
                return
            (x0, y0), (x1, y1) = (points[0]["x"], points[0]["y"]), (points[1]["x"], points[1]["y"])
            box = {"x": min(x0, x1), "y": min(y0, y1), "w": max(4, abs(x1 - x0)), "h": max(4, abs(y1 - y0))}
            self.loop.create_task(self._mark_done(pid, rid, box))
        self.pick_start(max_points=2, hint=["Clique no canto de cima, à esquerda", "Agora no canto de baixo, à direita"],
                        on_done=done)

    async def _mark_done(self, pid, rid, box):
        try:
            ref = await self.watch.capture_now({**box, "tolerance": 6})
        except OSError:
            ref = None
        self.save_region(pid, rid, ref=ref, **box)
        self.show_toast("Região marcada" if ref else "Região marcada, sem referência")

    def region_capture(self, pid, rid, delay):
        """Re-capture the reference after `delay` s (time to bring the screen to that state)."""
        async def go():
            self.ref_capture = {"until": time.time() + delay}
            self.publish()
            await asyncio.sleep(delay)
            self.ref_capture = None
            p = self.find_profile(pid)
            reg = p and next((r for r in p.get("regions", []) if r["id"] == rid), None)
            if not reg or not reg["w"]:
                self.show_toast("Marque a região antes")
                return
            try:
                ref = await self.watch.capture_now(reg)
            except OSError:
                self.show_toast("Não consegui capturar a tela")
                return
            self.save_region(pid, rid, ref=ref)
            self.show_toast(f"Referência de {reg['name']} capturada")
        self.loop.create_task(go())

    # ------------------------------------------------------------------ capture
    def capture_start(self):
        if self.capture:
            self.capture_end(None)
        fut = self.loop.create_future()
        self.capture = {"future": fut, "pressed": None, "mods": set(),
                        "timer": self.loop.call_later(CAPTURE_S, self.capture_end, None)}
        self.apply()
        return fut

    def on_capture(self, ev):
        if ev.type != e.EV_KEY:
            return False  # pointer motion keeps flowing while everything is grabbed
        cap = self.capture
        code = ev.code
        if ev.value == 1:
            if code in keys.MOD_OF and cap["pressed"] is None:
                cap["mods"].add(code)
            elif cap["pressed"] is None:
                cap["pressed"] = code
        elif ev.value == 0:
            if cap["pressed"] == code or (cap["pressed"] is None and code in cap["mods"]):
                main = code
                mods = [keys.MOD_OF[m] for m in cap["mods"] if m != main]
                self.capture_end({"key": keys.name(main),
                                  "mods": [m for m in keys.MOD_ORDER if m in mods],
                                  "label": self.keymap.label(main)})
        return True

    def capture_end(self, result):
        cap = self.capture
        if not cap:
            return
        cap["timer"].cancel()
        self.capture = None
        if not cap["future"].done():
            cap["future"].set_result(result)
        self.apply()

    # ------------------------------------------------------------------ point picker
    def pick_start(self, max_points=0, hint=None, on_done=None):
        if self.picker:
            self.pick_stop()
        self.picker = {"points": [], "timer": self.loop.call_later(PICK_S, self.pick_stop),
                       "max": max_points, "hint": hint or [], "on_done": on_done}
        self.apply()

    def on_pick(self, ev):
        if ev.type != e.EV_KEY:
            return False
        if ev.code == e.BTN_LEFT and ev.value == 1:
            pos = hypr.cursor_pos()
            if pos:
                self.picker["points"].append({"x": pos[0], "y": pos[1]})
                self.broadcast({"event": "pick", "points": self.picker["points"], "done": False})
                self.publish()
                if self.picker["max"] and len(self.picker["points"]) >= self.picker["max"]:
                    self.loop.call_soon(self.pick_stop)
        elif ev.value == 1 and ev.code in (e.BTN_RIGHT, e.KEY_ESC, e.KEY_ENTER):
            self.loop.call_soon(self.pick_stop)
        return ev.code in (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.KEY_ESC, e.KEY_ENTER)

    def pick_stop(self):
        p = self.picker
        if not p:
            return
        p["timer"].cancel()
        self.picker = None
        if p.get("on_done"):
            p["on_done"](p["points"])
        else:
            self.broadcast({"event": "pick", "points": p["points"], "done": True})
        self.apply()

    # ------------------------------------------------------------------ recorder
    def record_start(self, motion):
        if self.recorder:
            return
        self.recorder = Recorder(self, motion, self.cfg["settings"]["countdown"])
        self.apply()

    def record_stop(self):
        rec = self.recorder
        if not rec:
            return []
        self.recorder = None
        steps = rec.steps()
        self.broadcast({"event": "recorded", "steps": steps})
        self.show_toast(f"Gravado: {len(steps)} passos")
        self.apply()
        return steps

    # ------------------------------------------------------------------ outputs
    def show_toast(self, text, seconds=2.5):
        self.toast = {"text": text, "until": time.time() + seconds}
        self.publish()

    def state(self):
        now = time.time()
        if self.toast and self.toast["until"] < now:
            self.toast = None
        armed = self.armed
        return {
            "armed": {"id": armed["id"], "name": armed["name"],
                      "bindings": sum(1 for b in armed["bindings"] if b["enabled"])} if armed else None,
            "focus": {k: self.focus[k] for k in ("class", "title", "workspace")} if self.focus else None,
            "candidates": [{"id": p["id"], "name": p["name"], "enabled": p["enabled"]} for p in
                           self.candidates(self.game_window(), include_off=True)],
            "game_profile": (lambda p: {"id": p["id"], "name": p["name"]} if p else None)(self.profile_for(self.game_window())),
            "last_game": {k: self.last_game[k] for k in ("class", "title")} if self.last_game else None,
            "recent": [{k: r[k] for k in ("class", "title", "workspace", "fullscreen")} for r in self.recent],
            "runs": [r.info() for r in self.runs.values()],
            "recording": self.recorder.info() if self.recorder else None,
            "capturing": bool(self.capture),
            "picking": {"points": self.picker["points"],
                        "hint": (self.picker["hint"][len(self.picker["points"])]
                                 if len(self.picker["points"]) < len(self.picker["hint"]) else None)} if self.picker else None,
            "ref_capture": self.ref_capture,
            "watching": self.watch.active(),
            "overlay": self.overlay_now,
            "toast": self.toast,
            "listening": self.dev.listening,
            "grabbed": [d["name"] for d in self.dev.info() if d["grabbed"]],
            "gamemode": self.gamemode(),
        }

    def gamemode(self):
        if not os.path.exists(GAMEMODE_FILE):
            return "off"
        return "on" if self.submap.startswith("gaming") else "paused"

    def publish(self):
        """Coalesced and rate-limited: at most one state every PUBLISH_S (the window, the HUD
        and waybar all redraw on it; a fast macro loop used to publish on every turn)."""
        if self.state_pending:
            return
        self.state_pending = True
        wait = max(0.0, self.last_publish + PUBLISH_S - self.loop.time())
        self.loop.call_later(wait, self._publish)

    def _publish(self):
        self.state_pending = False
        self.last_publish = self.loop.time()
        st = self.state()
        self.broadcast({"event": "state", "state": st})
        self.update_waybar(st)
        self.update_hud(st)
        self.update_aurum(st)

    def broadcast(self, msg):
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode()
        for w in list(self.subscribers):
            try:
                w.write(line)
            except (OSError, RuntimeError):
                self.subscribers.discard(w)

    def write_waybar(self, data):
        try:
            with open(WAYBAR_STATE + ".tmp", "w") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(WAYBAR_STATE + ".tmp", WAYBAR_STATE)
        except OSError:
            return
        subprocess.Popen(["pkill", f"-RTMIN+{WAYBAR_SIGNAL}", "-x", "waybar"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def update_waybar(self, st):
        runs = [r for r in st["runs"] if r["status"] != "countdown"]
        if st["recording"]:
            data = {"text": "󰑊", "class": "recording", "tooltip": "InputForge: gravando macro"}
        elif runs:
            paused = all(r["status"] == "paused" for r in runs)
            lines = [f"{'⏸' if r['status'] == 'paused' else '▶'} {r['name']} — {r['detail']}" for r in runs]
            data = {"text": "󰍽", "class": "paused" if paused else "running",
                    "tooltip": "InputForge\n" + "\n".join(lines) + "\n\nclique: abrir · direito: parar tudo"}
        else:
            data = {"text": "", "tooltip": "", "class": "idle"}
        if data != self.waybar_last:
            self.waybar_last = data
            self.write_waybar(data)

    # HUD: a layer-shell process, started when there's something to show; it quits by itself
    def update_hud(self, st):
        if not self.cfg["settings"]["hud"]["enabled"]:
            return
        show = st["runs"] or st["recording"] or st["picking"] or st["toast"] or st["ref_capture"]
        if not show:
            return
        if self.hud_proc and self.hud_proc.poll() is None:
            return
        env = dict(os.environ)
        env["LD_PRELOAD"] = paths.LAYER_SHELL_LIB
        env["PYTHONPATH"] = paths.PACKAGE_ROOT
        try:
            self.hud_proc = subprocess.Popen([sys.executable, "-m", "inputforge.hud"], env=env,
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except OSError as ex:
            _log("hud:", ex)

    # Aurum V60: the keys of what's running glow (OpenAurum `glow`), debounced to save writes
    def update_aurum(self, st):
        if not self.cfg["settings"]["aurum"]["enabled"] or not os.access(AURUM_CLI, os.X_OK):
            want = None
        else:
            names = keys.xkb_names()
            want = set()
            for r in st["runs"]:
                if r["status"] != "running":
                    continue
                c = keys.code(r["trigger"]["key"])
                b, _ = self.find_binding(r["binding"])
                if c is not None and not keys.is_mouse(c):
                    if e.KEY_F1 <= c <= e.KEY_F10:  # the V60 has no F row: Fn + 1..0
                        c = e.KEY_1 + (c - e.KEY_F1)
                    elif c in (e.KEY_F11, e.KEY_F12):
                        c = e.KEY_MINUS if c == e.KEY_F11 else e.KEY_EQUAL
                elif b and b["action"]["type"] == "clicker":
                    c = keys.code(b["action"]["target"])
                    if keys.is_mouse(c):
                        c = None
                else:
                    c = None
                if c is not None and names.get(c):
                    want.add(names[c])
            want = sorted(want) or None
        if want == self.aurum_last:
            return
        if self.aurum_timer:
            self.aurum_timer.cancel()
        self.aurum_timer = self.loop.call_later(0.3, self._aurum_apply, want)

    def _aurum_apply(self, want):
        self.aurum_timer = None
        if want == self.aurum_last:
            return
        self.aurum_last = want
        self._aurum_cmd(["glow", *want] if want else ["glow", "off"])

    def _aurum_cmd(self, args):
        if os.access(AURUM_CLI, os.X_OK):
            subprocess.Popen([AURUM_CLI, *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    # ------------------------------------------------------------------ IPC
    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                self.loop.create_task(self.handle(msg, writer))
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.subscribers.discard(writer)
            if writer in self.ui_overlay:
                del self.ui_overlay[writer]
                self.update_overlay()
            writer.close()

    async def handle(self, msg, writer):
        rid = msg.get("id")
        try:
            result = await self.command(msg, writer)
            reply = {"id": rid, "ok": True, "result": result}
        except Exception as ex:
            _log("command failed:", msg.get("cmd"), repr(ex))
            reply = {"id": rid, "ok": False, "error": str(ex)}
        try:
            writer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
        except (OSError, RuntimeError):
            pass

    async def command(self, msg, writer):
        cmd = msg.get("cmd")
        if cmd == "ping":
            return "pong"
        if cmd == "subscribe":
            self.subscribers.add(writer)
            return {"state": self.state()}
        if cmd == "get":
            return {"config": self.cfg, "state": self.state(), "labels": self.keymap.labels(),
                    "positions": config.HUD_POSITIONS,
                    "env": {"calos": paths.CALOS, "aurum": os.access(AURUM_CLI, os.X_OK),
                            "config": paths.CONFIG.replace(os.path.expanduser("~"), "~"), "unit": paths.UNIT}}
        if cmd == "set_config":
            self.cfg = config.normalize(msg.get("config"))
            config.save(self.cfg)
            self.refresh_running_bindings()
            self.apply()
            self.broadcast({"event": "config", "config": self.cfg})
            return self.cfg
        if cmd == "reload":
            self.cfg = config.load()
            self.refresh_running_bindings()
            self.apply()
            self.broadcast({"event": "config", "config": self.cfg})
            return True
        if cmd == "run":
            b, p = self.find_binding(msg.get("binding"))
            if not b:
                raise ValueError("atalho não encontrado")
            for r in [r for r in self.runs.values() if r.binding["id"] == b["id"]]:
                self.stop_run(r)
                return None  # it was running: stopped
            run = self.start_run(b, p, msg.get("source") or "window")
            return run.id if run else b["action"]["type"]  # "command": ran right away
        if cmd == "toggle":  # CLI (e.g. a Hyprland bind): by binding id or name, no countdown
            target = msg.get("binding")
            for p in self.cfg["profiles"]:
                for b in p["bindings"]:
                    if target in (b["id"], b["name"]):
                        running = [r for r in self.runs.values() if r.binding["id"] == b["id"]]
                        for r in running:
                            self.stop_run(r)
                        if not running:
                            self.start_run(b, p, "cli")
                        return not running
            raise ValueError("atalho não encontrado")
        if cmd == "stop":
            rid = msg.get("run")
            for r in list(self.runs.values()):
                if rid in (None, r.id, r.binding["id"]):
                    self.stop_run(r, publish=False)
            self.out.release_all()
            self.apply()
            return True
        if cmd == "panic":
            self.panic()
            return True
        if cmd == "capture":
            return await self.capture_start()
        if cmd == "capture_cancel":
            self.capture_end(None)
            return True
        if cmd == "record_start":
            self.record_start(msg.get("motion") if msg.get("motion") in ("none", "relative", "absolute") else "none")
            return True
        if cmd == "record_stop":
            return self.record_stop()
        if cmd == "pick_start":
            self.pick_start()
            return True
        if cmd == "pick_stop":
            self.pick_stop()
            return True
        if cmd == "choose_profile":
            return self.choose_profile(msg.get("profile"))
        if cmd == "ui_overlay":  # the Game Bar opened over the game (until it disconnects)
            rect = msg.get("rect")
            if msg.get("open"):
                self.ui_overlay[writer] = [int(v) for v in rect][:4] if isinstance(rect, list) and len(rect) == 4 else None
            else:
                self.ui_overlay.pop(writer, None)
            self.update_overlay()
            return True
        if cmd == "region_mark":
            self.region_mark(msg.get("profile"), msg.get("region") or config.new_id())
            return True
        if cmd == "region_capture":
            self.region_capture(msg.get("profile"), msg.get("region"), float(msg.get("delay") or 0))
            return True
        if cmd == "region_test":  # how the region looks now vs its reference
            p = self.find_profile(msg.get("profile"))
            reg = p and next((r for r in p.get("regions", []) if r["id"] == msg.get("region")), None)
            if not reg or not reg["w"]:
                raise ValueError("região não marcada")
            sig = await self.watch.capture_now(reg)
            pct = screen.differ_pct(sig, reg["ref"]) if reg["ref"] else None
            return {"differ": pct, "match": pct is not None and pct <= reg["tolerance"], "sig": sig}
        if cmd == "windows":
            return {"clients": [c for c in hypr.clients() if c["class"] != OWN_CLASS],
                    "last_game": self.last_game}
        if cmd == "theme":
            self.broadcast({"event": "theme"})
            return True
        if cmd == "toast":
            self.show_toast(str(msg.get("text", ""))[:120])
            return True
        if cmd == "state":
            return self.state()
        raise ValueError(f"comando desconhecido: {cmd}")

    def refresh_running_bindings(self):
        """A saved config replaces the binding dicts: running runs pick up the new ones
        (or stop, when their binding is gone or disabled)."""
        for run in list(self.runs.values()):
            b, p = self.find_binding(run.binding["id"])
            if not b or not b["enabled"] or b["action"]["type"] != run.binding["action"]["type"]:
                self.stop_run(run, publish=False)
            else:
                run.binding, run.profile = b, p


class Recorder:
    """Records keys, buttons, wheel and (optionally) mouse motion into macro steps."""

    def __init__(self, engine, motion, countdown):
        self.engine = engine
        self.motion = motion
        self.start = time.monotonic() + countdown
        self.events = []  # (t, kind, a, b)
        self.started_wall = time.time() + countdown

    def info(self):
        return {"motion": self.motion, "events": len(self.events), "starts": self.started_wall}

    def event(self, ev):
        t = time.monotonic()
        if t < self.start:
            return
        if ev.type == e.EV_KEY and ev.value in (0, 1):
            if self.motion == "absolute" and keys.is_mouse(ev.code) and ev.value == 1:
                pos = hypr.cursor_pos()
                if pos:
                    self.events.append((t, "moveto", pos[0], pos[1]))
            self.events.append((t, "key", ev.code, ev.value))
        elif ev.type == e.EV_REL:
            if ev.code == e.REL_WHEEL:
                self.events.append((t, "scroll", ev.value, 0))
            elif ev.code == e.REL_HWHEEL:
                self.events.append((t, "scroll", 0, ev.value))
            elif self.motion == "relative" and ev.code in (e.REL_X, e.REL_Y):
                dx, dy = (ev.value, 0) if ev.code == e.REL_X else (0, ev.value)
                if self.events and self.events[-1][1] == "move" and t - self.events[-1][0] < 0.016:
                    pt, _, px, py = self.events[-1]
                    self.events[-1] = (pt, "move", px + dx, py + dy)
                else:
                    self.events.append((t, "move", dx, dy))
        if len(self.events) > 20000:
            self.engine.loop.call_soon(self.engine.record_stop)

    def steps(self):
        raw = []
        last = self.start
        down = {}
        for t, kind, a, b in self.events:
            gap = int((t - last) * 1000)
            if gap >= 4:
                raw.append({"t": "wait", "ms": gap, "rand_ms": 0})
            last = t
            if kind == "key":
                name = keys.name(a)
                if not name:
                    continue
                st = "button" if keys.is_mouse(a) else "key"
                if b == 1:
                    down[a] = True
                elif a not in down:
                    continue  # released a key pressed before the recording started
                else:
                    down.pop(a, None)
                raw.append({"t": st, "codes": [name], "act": "down" if b else "up", "hold_ms": 20})
            elif kind == "move":
                raw.append({"t": "move", "dx": a, "dy": b, "ms": 0})
            elif kind == "moveto":
                raw.append({"t": "moveto", "x": a, "y": b})
            elif kind == "scroll":
                raw.append({"t": "scroll", "dy": a, "dx": b})
        for code in down:  # still held at the end: release
            name = keys.name(code)
            raw.append({"t": "button" if keys.is_mouse(code) else "key", "codes": [name], "act": "up", "hold_ms": 20})
        # down, wait, up of the same key -> one tap holding for that wait
        out = []
        i = 0
        while i < len(raw):
            s = raw[i]
            if (s["t"] in ("key", "button") and s["act"] == "down" and i + 2 < len(raw)
                    and raw[i + 1]["t"] == "wait" and raw[i + 2]["t"] == s["t"]
                    and raw[i + 2]["act"] == "up" and raw[i + 2]["codes"] == s["codes"]):
                out.append({**s, "act": "tap", "hold_ms": raw[i + 1]["ms"]})
                i += 3
                continue
            if (s["t"] in ("key", "button") and s["act"] == "down" and i + 1 < len(raw)
                    and raw[i + 1]["t"] == s["t"] and raw[i + 1]["act"] == "up" and raw[i + 1]["codes"] == s["codes"]):
                out.append({**s, "act": "tap", "hold_ms": 10})
                i += 2
                continue
            out.append(s)
            i += 1
        if out and out[0]["t"] == "wait":
            out.pop(0)
        return [n for n in (config._norm_step(s) for s in out[:5000]) if n]


def main():
    os.makedirs(RUNTIME, mode=0o700, exist_ok=True)
    lock = open(os.path.join(RUNTIME, "engine.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _log("InputForge engine already running")
        sys.exit(0)

    async def run():
        engine = Engine()
        await engine.start()
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        await stop.wait()
        engine.shutdown()

    asyncio.run(run())


if __name__ == "__main__":
    main()
