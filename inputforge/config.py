"""InputForge config: ~/.config/inputforge/config.json, written only by the engine.

normalize() is the gate: whatever the UI (or a hand edit) sends, the engine only ever
runs a config that went through it, with every field present and in range, unknown
keys dropped. A broken file is kept aside as config.json.broken and the defaults load.

Profiles: "global" (any window, the classic autoclicker app) is always first and can't
be deleted; game profiles match the focused window by class/title regex. Only one
profile is armed at a time: the game's when it matches, else global (if enabled).

Binding = trigger (+ mode) -> action:
  clicker  clicks a mouse button (or taps a key) at a rate, at the cursor or on points
  macro    a list of steps (keys, buttons, waits, moves, scroll, text, commands), repeated
  remap    the trigger key/button sends other keys instead (needs the device grabbed)
  command  runs a shell command
Modes: toggle (press starts, press again stops), hold (runs while held), once.

Regions (per profile): screen rectangles with a reference signature. Macro steps wait_screen
/ if_screen and screen triggers (trigger.kind "screen") compare the live screen with it:
match = looks like the reference, differ = doesn't, change = changed since the step began.
"""

import copy
import json
import os
import re
import secrets

from inputforge import keys, paths

DIR = paths.CONFIG_DIR
PATH = paths.CONFIG
VERSION = 1

MODES = ("toggle", "hold", "once")
ACTIONS = ("clicker", "macro", "remap", "command")
HUD_POSITIONS = ("top-left", "top", "top-right", "bottom-left", "bottom", "bottom-right")
STEP_TYPES = ("key", "button", "wait", "move", "moveto", "scroll", "text", "command", "wait_screen", "if_screen")
CONDS = ("match", "differ", "change")  # vs the region's reference: looks like it / doesn't / changed
GRID = 8  # a region's signature: GRID x GRID cells of average color


def new_id():
    return secrets.token_hex(4)


def _trigger(key="KEY_F8", mods=()):
    return {"kind": "key", "key": key, "mods": list(mods), "hold_ms": 0, "block": False, "region": "", "cond": "match"}


DEFAULT_SETTINGS = {
    "global_enabled": True,
    "chosen": {},  # window class -> profile id, when several profiles match the same game
    "countdown": 3,  # s before a run started from the window begins
    "panic": {"key": "KEY_ESC", "mods": [], "hold_ms": 800, "block": False},
    "record_stop": {"key": "KEY_F12", "mods": [], "hold_ms": 0, "block": False},
    "hud": {"enabled": True, "position": "top-right", "announce": True},
    "aurum": {"enabled": True},
}

DEFAULT_CLICKER = {
    "type": "clicker",
    "target": "BTN_LEFT",  # a mouse button or a key (auto-key)
    "rate": {"unit": "cps", "value": 15},  # or {"unit": "ms", "value": 66}
    "hold_ms": 12,  # how long each click stays down
    "double": False,
    "jitter": 0,  # % random on the interval (human-like)
    "where": "cursor",  # or "points"
    "points": [],  # [{"x": .., "y": ..}] global layout coordinates, cycled
    "point_jitter": 0,  # px random around each point
    "limit": {"kind": "none", "value": 0},  # none | count (clicks) | time (s)
}

DEFAULT_MACRO = {"type": "macro", "steps": [], "repeat": 1, "speed": 1.0, "gap_ms": 0}
DEFAULT_REMAP = {"type": "remap", "to": ["KEY_E"]}
DEFAULT_COMMAND = {"type": "command", "cmd": ""}
DEFAULT_ACTION = {"clicker": DEFAULT_CLICKER, "macro": DEFAULT_MACRO,
                  "remap": DEFAULT_REMAP, "command": DEFAULT_COMMAND}


def default_binding(kind="clicker", **over):
    b = {"id": new_id(), "name": {"clicker": "Autoclick", "macro": "Macro", "remap": "Remapear",
                                  "command": "Comando"}[kind],
         "enabled": True, "mode": "toggle", "trigger": _trigger(), "action": copy.deepcopy(DEFAULT_ACTION[kind])}
    if kind == "remap":
        b["trigger"]["block"] = True
    b.update(over)
    return b


def default_profile(name="Novo perfil", match_class="", **over):
    p = {"id": new_id(), "name": name, "enabled": True, "icon": "",
         "match": {"class": match_class, "title": ""}, "on_blur": "pause", "regions": [], "bindings": []}
    p.update(over)
    return p


def defaults():
    glob = default_profile("Qualquer janela", id="global")
    glob["match"] = {"class": "", "title": ""}
    glob["bindings"] = [default_binding("clicker", name="Autoclick rápido")]
    return {"version": VERSION, "settings": copy.deepcopy(DEFAULT_SETTINGS), "profiles": [glob]}


# ----------------------------------------------------------------------- normalize

def _num(value, default, lo, hi, kind=float):
    try:
        value = kind(value)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(lo, min(hi, value))


def _str(value, default="", limit=200):
    return value[:limit] if isinstance(value, str) else default


def _bool(value, default):
    return value if isinstance(value, bool) else default


def _key(value, default=None, mouse_ok=True):
    c = keys.code(value)
    if c is None or (not mouse_ok and keys.is_mouse(c)):
        return default
    return keys.name(c)


def _regex(value):
    value = _str(value, "", 300)
    try:
        re.compile(value)
    except re.error:
        return ""
    return value


def _norm_trigger(t, default):
    t = t if isinstance(t, dict) else {}
    out = dict(default)
    out["kind"] = t.get("kind") if t.get("kind") in ("key", "screen") else default.get("kind", "key")
    out["region"] = _str(t.get("region"), "", 16)
    out["cond"] = t.get("cond") if t.get("cond") in CONDS else "match"
    out["key"] = _key(t.get("key"), default["key"])
    mods = t.get("mods") if isinstance(t.get("mods"), list) else []
    out["mods"] = [m for m in keys.MOD_ORDER if m in mods and keys.MODS[m][0] != keys.code(out["key"])]
    out["hold_ms"] = _num(t.get("hold_ms"), default["hold_ms"], 0, 5000, int)
    out["block"] = _bool(t.get("block"), default["block"])
    return out


def _norm_codes(value, default):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return list(default)
    out = [k for k in (_key(v) for v in value[:8]) if k]
    return out


def _norm_step(s):
    if not isinstance(s, dict) or s.get("t") not in STEP_TYPES:
        return None
    t = s["t"]
    out = {"t": t, "id": _str(s.get("id"), "", 16) or new_id()}
    if t in ("key", "button"):
        out["codes"] = _norm_codes(s.get("codes"), [])
        if t == "button":
            out["codes"] = [c for c in out["codes"] if keys.is_mouse(keys.code(c))][:1] or ["BTN_LEFT"]
        elif not out["codes"]:
            return None
        out["act"] = s.get("act") if s.get("act") in ("tap", "down", "up", "double") else "tap"
        out["hold_ms"] = _num(s.get("hold_ms"), 20, 0, 60000, int)
    elif t == "wait":
        out["ms"] = _num(s.get("ms"), 100, 0, 3600000, int)
        out["rand_ms"] = _num(s.get("rand_ms"), 0, 0, 600000, int)
    elif t == "move":
        out["dx"] = _num(s.get("dx"), 0, -20000, 20000, int)
        out["dy"] = _num(s.get("dy"), 0, -20000, 20000, int)
        out["ms"] = _num(s.get("ms"), 0, 0, 60000, int)  # 0 = jump, else glide over ms
    elif t == "moveto":
        out["x"] = _num(s.get("x"), 0, -100000, 100000, int)
        out["y"] = _num(s.get("y"), 0, -100000, 100000, int)
    elif t == "scroll":
        out["dy"] = _num(s.get("dy"), -1, -100, 100, int)
        out["dx"] = _num(s.get("dx"), 0, -100, 100, int)
    elif t == "text":
        out["value"] = _str(s.get("value"), "", 2000)
    elif t == "command":
        out["cmd"] = _str(s.get("cmd"), "", 2000)
    elif t in ("wait_screen", "if_screen"):
        out["region"] = _str(s.get("region"), "", 16)
        out["cond"] = s.get("cond") if s.get("cond") in CONDS else "match"
        if t == "wait_screen":
            out["timeout_ms"] = _num(s.get("timeout_ms"), 0, 0, 3600000, int)  # 0 = no limit
            out["on_timeout"] = s.get("on_timeout") if s.get("on_timeout") in ("continue", "stop") else "continue"
        else:
            out["skip"] = _num(s.get("skip"), 1, 1, 100, int)  # condition false: skip this many steps
    return out


def _norm_region(r):
    """A rectangle of the screen (layout coordinates) + its reference signature: GRID x GRID
    [r, g, b] cells captured when the user marked it (the state to wait for)."""
    if not isinstance(r, dict):
        return None
    ref = r.get("ref")
    ok = (isinstance(ref, list) and len(ref) == GRID * GRID
          and all(isinstance(c, list) and len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c) for c in ref))
    return {"id": _str(r.get("id"), "", 16) or new_id(), "name": _str(r.get("name"), "Região", 40) or "Região",
            "x": _num(r.get("x"), 0, -100000, 100000, int), "y": _num(r.get("y"), 0, -100000, 100000, int),
            "w": _num(r.get("w"), 0, 0, 8000, int), "h": _num(r.get("h"), 0, 0, 8000, int),
            "ref": ref if ok else None,
            "tolerance": _num(r.get("tolerance"), 6, 0, 60, int)}  # % of cells allowed to differ


def _norm_action(a, kind):
    a = a if isinstance(a, dict) else {}
    d = DEFAULT_ACTION[kind]
    out = copy.deepcopy(d)
    if kind == "clicker":
        out["target"] = _key(a.get("target"), "BTN_LEFT")
        rate = a.get("rate") if isinstance(a.get("rate"), dict) else {}
        unit = rate.get("unit") if rate.get("unit") in ("cps", "ms") else "cps"
        out["rate"] = {"unit": unit, "value": _num(rate.get("value"), 15 if unit == "cps" else 66,
                                                    0.01 if unit == "cps" else 1,
                                                    500 if unit == "cps" else 3600000)}
        out["hold_ms"] = _num(a.get("hold_ms"), d["hold_ms"], 1, 10000, int)
        out["double"] = _bool(a.get("double"), False)
        out["jitter"] = _num(a.get("jitter"), 0, 0, 90, int)
        out["where"] = a.get("where") if a.get("where") in ("cursor", "points") else "cursor"
        pts = a.get("points") if isinstance(a.get("points"), list) else []
        out["points"] = [{"x": _num(p.get("x"), 0, -100000, 100000, int), "y": _num(p.get("y"), 0, -100000, 100000, int)}
                         for p in pts[:64] if isinstance(p, dict)]
        out["point_jitter"] = _num(a.get("point_jitter"), 0, 0, 200, int)
        lim = a.get("limit") if isinstance(a.get("limit"), dict) else {}
        kind_ = lim.get("kind") if lim.get("kind") in ("none", "count", "time") else "none"
        out["limit"] = {"kind": kind_, "value": _num(lim.get("value"), 0, 0, 10000000)}
    elif kind == "macro":
        steps = a.get("steps") if isinstance(a.get("steps"), list) else []
        out["steps"] = [s for s in (_norm_step(s) for s in steps[:5000]) if s]
        out["repeat"] = _num(a.get("repeat"), 1, 0, 1000000, int)  # 0 = until stopped
        out["speed"] = _num(a.get("speed"), 1.0, 0.1, 10.0)
        out["gap_ms"] = _num(a.get("gap_ms"), 0, 0, 3600000, int)
    elif kind == "remap":
        out["to"] = _norm_codes(a.get("to"), [])  # [] = the key does nothing
    elif kind == "command":
        out["cmd"] = _str(a.get("cmd"), "", 2000)
    return out


def _norm_binding(b):
    if not isinstance(b, dict):
        return None
    action = b.get("action") if isinstance(b.get("action"), dict) else {}
    kind = action.get("type") if action.get("type") in ACTIONS else "clicker"
    d = default_binding(kind)
    out = {"id": _str(b.get("id"), "", 16) or new_id(), "name": _str(b.get("name"), d["name"], 60) or d["name"],
           "enabled": _bool(b.get("enabled"), True),
           "mode": b.get("mode") if b.get("mode") in MODES else "toggle",
           "trigger": _norm_trigger(b.get("trigger"), d["trigger"]),
           "action": _norm_action(action, kind)}
    if kind == "remap":
        out["trigger"]["block"] = True
        out["trigger"]["mods"] = []
        out["trigger"]["hold_ms"] = 0
        out["trigger"]["kind"] = "key"
    return out


def _norm_profile(p, is_global=False):
    if not isinstance(p, dict):
        return None
    d = default_profile()
    match = p.get("match") if isinstance(p.get("match"), dict) else {}
    out = {"id": "global" if is_global else (_str(p.get("id"), "", 16) or new_id()),
           "name": _str(p.get("name"), d["name"], 60) or d["name"],
           "enabled": _bool(p.get("enabled"), True),
           "icon": _str(p.get("icon"), "", 300),
           "match": {"class": "" if is_global else _regex(match.get("class")),
                     "title": "" if is_global else _regex(match.get("title"))},
           "on_blur": p.get("on_blur") if p.get("on_blur") in ("pause", "stop") else "pause",
           "regions": [],
           "bindings": []}
    seen = set()
    for r in (p.get("regions") if isinstance(p.get("regions"), list) else [])[:64]:
        nr = _norm_region(r)
        if nr and nr["id"] not in seen:
            seen.add(nr["id"])
            out["regions"].append(nr)
    seen = set()
    for b in (p.get("bindings") if isinstance(p.get("bindings"), list) else [])[:200]:
        nb = _norm_binding(b)
        if nb and nb["id"] not in seen:
            seen.add(nb["id"])
            out["bindings"].append(nb)
    return out


def normalize(cfg):
    cfg = cfg if isinstance(cfg, dict) else {}
    s = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    ds = DEFAULT_SETTINGS
    hud = s.get("hud") if isinstance(s.get("hud"), dict) else {}
    aurum = s.get("aurum") if isinstance(s.get("aurum"), dict) else {}
    settings = {
        "global_enabled": _bool(s.get("global_enabled"), ds["global_enabled"]),
        "chosen": {_str(k, "", 200): _str(v, "", 16) for k, v in (s.get("chosen") or {}).items()
                   if isinstance(k, str) and isinstance(v, str)} if isinstance(s.get("chosen"), dict) else {},
        "countdown": _num(s.get("countdown"), ds["countdown"], 0, 30, int),
        "panic": _norm_trigger(s.get("panic"), ds["panic"]),
        "record_stop": _norm_trigger(s.get("record_stop"), ds["record_stop"]),
        "hud": {"enabled": _bool(hud.get("enabled"), True),
                "position": hud.get("position") if hud.get("position") in HUD_POSITIONS else "top-right",
                "announce": _bool(hud.get("announce"), True)},
        "aurum": {"enabled": _bool(aurum.get("enabled"), True)},
    }
    profiles = cfg.get("profiles") if isinstance(cfg.get("profiles"), list) else []
    glob = next((p for p in profiles if isinstance(p, dict) and p.get("id") == "global"), None)
    out = [_norm_profile(glob or defaults()["profiles"][0], is_global=True)]
    seen = {"global"}
    for p in profiles[:100]:
        if isinstance(p, dict) and p.get("id") != "global":
            np = _norm_profile(p)
            if np and np["id"] not in seen:
                seen.add(np["id"])
                out.append(np)
    return {"version": VERSION, "settings": settings, "profiles": out}


def load():
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except FileNotFoundError:
        cfg = defaults()
        save(cfg)
        return cfg
    except (OSError, ValueError):
        try:
            os.replace(PATH, PATH + ".broken")
        except OSError:
            pass
        cfg = defaults()
        save(cfg)
        return cfg
    return normalize(raw)


def save(cfg):
    os.makedirs(DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PATH)
