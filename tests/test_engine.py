"""Engine tests with no real I/O: fake devices, fake outputs, fake Hyprland, fake screen.
Nothing is typed or clicked on this machine; the config goes to a temp dir.

  python3 tests/test_engine.py
"""

import asyncio
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["INPUTFORGE_SOCKET"] = os.path.join(tempfile.mkdtemp(), "engine.sock")

from evdev import ecodes as e  # noqa: E402

from inputforge import config, hypr, keys, screen  # noqa: E402
from inputforge import engine as eng  # noqa: E402

config.DIR = tempfile.mkdtemp()
config.PATH = os.path.join(config.DIR, "config.json")

GAME = {"address": "0xgame", "class": "org.vinegarhq.Sober", "title": "Roblox", "initialClass": "org.vinegarhq.Sober",
        "pid": 1, "workspace": "special:game", "fullscreen": True}
FOCUS = {"w": dict(GAME)}
MOVES = []
hypr.active_window = lambda: dict(FOCUS["w"]) if FOCUS["w"] else None
hypr.move_cursor = lambda x, y: MOVES.append((x, y))
hypr.cursor_pos = lambda: (100, 200)
hypr.active_layout_index = lambda: 0
hypr.sync_layout = lambda *a: None


class FakeOut:
    def __init__(self):
        self.held = {}
        self.log = []

    def key(self, code, value, via=None):
        if value and code in self.held:
            return
        if not value and code not in self.held:
            return
        if value:
            self.held[code] = via
        else:
            self.held.pop(code)
        self.log.append((keys.name(code), value, "clone" if via else "virt"))

    def rel(self, dx=0, dy=0):
        self.log.append(("move", dx, dy))

    def scroll(self, dy=0, dx=0):
        self.log.append(("scroll", dy, dx))

    def release(self, codes):
        for c in list(codes):
            if c in self.held:
                self.key(c, 0)

    def release_all(self):
        self.release(list(self.held))

    def close(self):
        pass


class FakeDM:
    def __init__(self, loop, out, on_event):
        self.listening = False
        self.want = set()
        self.grab_all = False
        self.need_mouse = False

    def listen(self, on):
        self.listening = on

    def set_need_mouse(self, on):
        self.need_mouse = on

    def set_want(self, codes, grab_all=None):
        self.want = set(codes)
        if grab_all is not None:
            self.grab_all = grab_all

    def info(self):
        return []

    def close(self):
        pass


eng.Outputs = FakeOut
eng.DeviceManager = FakeDM
eng.Engine.write_waybar = lambda self, d: setattr(self, "_wb", d)
eng.Engine.update_hud = lambda self, st: None
eng.Engine._aurum_cmd = lambda self, args: None


class Dev:
    def __init__(self, grabbed=False):
        self.clone = object() if grabbed else None
        self.codes = set(keys.KEYBOARD_CODES)
        self.grabbed = grabbed


def ev(code, value, type_=e.EV_KEY):
    return types.SimpleNamespace(type=type_, code=code, value=value)


def tap(E, d, code):
    E.on_input(d, ev(code, 1))
    E.on_input(d, ev(code, 0))


def fresh():
    FOCUS["w"] = dict(GAME)
    if os.path.exists(config.PATH):
        os.unlink(config.PATH)
    E = eng.Engine()
    E.refresh_focus()
    E.apply()
    return E


def presses(E):
    return [k for k, v, _ in E.out.log if v == 1]


# ----------------------------------------------------------------------------- tests

async def test_clicker_pause_resume():
    E = fresh()
    d = Dev()
    assert E.armed["id"] == "global"
    tap(E, d, e.KEY_F8)
    await asyncio.sleep(0.35)
    clicks = presses(E).count("BTN_LEFT")
    assert 4 <= clicks <= 7, clicks
    FOCUS["w"] = {**GAME, "address": "0xother", "class": "Alacritty", "initialClass": "Alacritty"}
    E.refresh_focus()
    run = next(iter(E.runs.values()))
    assert run.status == "paused" and not E.out.held
    n = len(E.out.log)
    await asyncio.sleep(0.2)
    assert len(E.out.log) == n, "clicked while paused"
    FOCUS["w"] = dict(GAME)
    E.refresh_focus()
    assert run.status == "running"
    tap(E, d, e.KEY_F8)
    await asyncio.sleep(0.01)
    assert not E.runs and not E.out.held


async def test_game_profile_remap_hold_points_panic():
    E = fresh()
    p = config.default_profile("Sober", r"^org\.vinegarhq\.Sober$")
    p["bindings"] = [
        config.default_binding("remap", trigger={"key": "KEY_CAPSLOCK", "block": True},
                               action={"type": "remap", "to": ["KEY_LEFTSHIFT"]}),
        config.default_binding("macro", mode="hold", trigger={"key": "BTN_SIDE"},
                               action={"type": "macro", "repeat": 1, "steps": [
                                   {"t": "key", "codes": ["KEY_E"], "act": "tap", "hold_ms": 10},
                                   {"t": "wait", "ms": 40}, {"t": "text", "value": "gg á"}]}),
        config.default_binding("clicker", trigger={"key": "KEY_F8", "mods": ["ctrl"], "block": True},
                               action={"type": "clicker", "target": "BTN_RIGHT", "rate": {"unit": "cps", "value": 50},
                                       "where": "points", "points": [{"x": 10, "y": 20}, {"x": 30, "y": 40}],
                                       "limit": {"kind": "count", "value": 3}}),
    ]
    E.cfg["profiles"].append(p)
    E.cfg = config.normalize(E.cfg)
    E.apply()
    assert E.armed["name"] == "Sober"
    g = Dev(grabbed=True)
    E.out.log.clear()
    assert E.on_input(g, ev(e.KEY_CAPSLOCK, 1)) is True
    assert E.on_input(g, ev(e.KEY_CAPSLOCK, 0)) is True
    assert E.out.log[0][:2] == ("KEY_LEFTSHIFT", 1) and not E.out.held
    E.out.log.clear()
    d = Dev()
    E.on_input(d, ev(e.BTN_SIDE, 1))
    await asyncio.sleep(0.3)
    E.on_input(d, ev(e.BTN_SIDE, 0))
    await asyncio.sleep(0.02)
    assert not E.runs and not E.out.held
    assert "KEY_E" in presses(E) and "KEY_G" in presses(E) and "KEY_LEFTBRACE" in presses(E)  # á = ´ + a
    MOVES.clear()
    E.on_input(d, ev(e.KEY_LEFTCTRL, 1))
    assert E.on_input(g, ev(e.KEY_F8, 1)) is True
    E.on_input(g, ev(e.KEY_F8, 0))
    E.on_input(d, ev(e.KEY_LEFTCTRL, 0))
    await asyncio.sleep(0.2)
    assert MOVES == [(10, 20), (30, 40), (10, 20)] and not E.runs
    tap(E, d, e.KEY_F8)  # plain F8: nothing in this profile
    assert not E.runs
    E.on_input(d, ev(e.BTN_SIDE, 1))
    await asyncio.sleep(0.05)
    E.on_input(d, ev(e.KEY_ESC, 1))
    await asyncio.sleep(0.9)
    assert not E.runs, "panic didn't stop"


async def test_recorder_and_capture():
    E = fresh()
    got = []
    E.broadcast = got.append
    d = Dev()
    E.cfg["settings"]["countdown"] = 0
    E.record_start("relative")
    E.on_input(d, ev(e.KEY_W, 1))
    await asyncio.sleep(0.05)
    E.on_input(d, ev(e.KEY_W, 0))
    E.on_input(d, ev(e.REL_X, 5, e.EV_REL))
    E.on_input(d, ev(e.REL_Y, -3, e.EV_REL))
    await asyncio.sleep(0.03)
    tap(E, d, e.BTN_LEFT)
    tap(E, d, e.KEY_F12)
    await asyncio.sleep(0.01)
    steps = next(m["steps"] for m in got if m.get("event") == "recorded")
    assert [s["t"] for s in steps] == ["key", "move", "wait", "button"], steps
    assert steps[0]["act"] == "tap" and steps[1]["dx"] == 5
    fut = E.capture_start()
    assert E.dev.grab_all
    E.on_input(d, ev(e.KEY_LEFTCTRL, 1))
    tap(E, d, e.KEY_G)
    E.on_input(d, ev(e.KEY_LEFTCTRL, 0))
    assert await fut == {"key": "KEY_G", "mods": ["ctrl"], "label": "G"}
    assert not E.dev.grab_all


async def test_overlay_pauses_runs():
    E = fresh()
    d = Dev()
    tap(E, d, e.KEY_F8)
    await asyncio.sleep(0.2)
    run = next(iter(E.runs.values()))
    w = object()
    await E.command({"cmd": "ui_overlay", "open": True}, w)
    n = len(E.out.log)
    await asyncio.sleep(0.3)
    assert run.status == "paused" and len(E.out.log) == n and not E.by_key
    tap(E, d, e.KEY_F8)  # typed in the editor: ignored
    assert E.runs
    await E.command({"cmd": "ui_overlay", "open": False}, w)
    await asyncio.sleep(0.2)
    assert run.status == "running" and E.by_key
    tap(E, d, e.KEY_F8)


async def test_screen_rotation_and_trigger():
    ready, cooldown = [[10, 3, 0]] * 64, [[70, 10, 0]] * 64
    shown = {882: ready, 966: cooldown, 100: ready}
    captures = []

    async def fake_capture(rects):
        captures.append(time.monotonic())
        await asyncio.sleep(0.005)
        return {r: shown[r[0]] for r in rects}

    real_capture, screen.capture = screen.capture, fake_capture
    try:
        E = fresh()
        reg = lambda rid, x: {"id": rid, "name": rid, "x": x, "y": 1012, "w": 72, "h": 52, "ref": ready, "tolerance": 6}
        p = config.default_profile("Sober", r"^org\.vinegarhq\.Sober$")
        p["regions"] = [reg("rE", 882), reg("rR", 966), reg("rX", 100)]
        steps = []
        for k, rid in (("E", "rE"), ("R", "rR")):
            steps += [{"t": "if_screen", "region": rid, "cond": "match", "skip": 2},
                      {"t": "key", "codes": [f"KEY_{k}"], "act": "tap", "hold_ms": 10},
                      {"t": "wait", "ms": 150}]
        p["bindings"] = [config.default_binding("macro", trigger={"key": "KEY_F8"},
                                                action={"type": "macro", "repeat": 0, "steps": steps}),
                         config.default_binding("command", mode="once",
                                                trigger={"kind": "screen", "region": "rX", "cond": "differ"},
                                                action={"type": "command", "cmd": "true"})]
        E.cfg["profiles"].append(p)
        E.cfg = config.normalize(E.cfg)
        E.apply()
        fired = []
        E.run_command = fired.append
        d = Dev()
        tap(E, d, e.KEY_F8)
        await asyncio.sleep(0.6)
        assert "KEY_E" in presses(E) and "KEY_R" not in presses(E)
        shown[882] = cooldown
        n0 = len(captures)
        E.out.log.clear()
        await asyncio.sleep(0.6)
        assert not presses(E)
        assert len(captures) - n0 <= 12, "the loop spins instead of waiting for captures"
        shown[966] = ready
        E.out.log.clear()
        await asyncio.sleep(0.4)
        assert "KEY_R" in presses(E)
        shown[100] = cooldown
        await asyncio.sleep(0.3)
        assert fired == ["true"]
        tap(E, d, e.KEY_F8)
        await asyncio.sleep(0.05)
        assert not E.runs
    finally:
        screen.capture = real_capture


async def test_choose_profile():
    E = fresh()
    a = config.default_profile("RoGhoul", r"^org\.vinegarhq\.Sober$")
    b = config.default_profile("Combat Rift", r"^org\.vinegarhq\.Sober$")
    E.cfg["profiles"] += [a, b]
    E.cfg = config.normalize(E.cfg)
    E.apply()
    assert E.armed["name"] == "RoGhoul"  # first match until one is chosen
    assert [c["name"] for c in E.state()["candidates"]] == ["RoGhoul", "Combat Rift"]
    E.choose_profile(b["id"])
    assert E.armed["name"] == "Combat Rift"
    assert E.cfg["settings"]["chosen"]["org.vinegarhq.Sober"] == b["id"]
    FOCUS["w"] = {**GAME, "address": "0xother", "class": "Alacritty", "initialClass": "Alacritty"}
    E.refresh_focus()
    assert E.armed["id"] == "global"
    FOCUS["w"] = dict(GAME)
    E.refresh_focus()
    assert E.armed["name"] == "Combat Rift", "the choice must be remembered"
    E.profile_off = next(p for p in E.cfg["profiles"] if p["name"] == "RoGhoul")
    E.profile_off["enabled"] = False
    E.apply()
    assert [c["name"] for c in E.state()["candidates"]] == ["RoGhoul", "Combat Rift"], "switched-off ones still listed"
    E.choose_profile(a["id"])
    assert E.armed["name"] == "RoGhoul" and E.profile_off["enabled"], "picking it turns it on"


async def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            await t()
            print(f"ok    {t.__name__}")
        except Exception as ex:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {t.__name__}: {ex!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} ok")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
