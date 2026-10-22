"""Key names, labels and the xkb keymap.

Config files keep keys as evdev names ("KEY_F8", "BTN_SIDE"): stable across layouts,
readable by hand. Labels come from the br layout (the physical ABNT2 keycaps, and Game
Mode always types br); text typing uses the layout Hyprland has active right now.
"""

import ctypes
import os
import re
import unicodedata

from evdev import ecodes as e

HYPR_INPUT = os.path.expanduser("~/.config/hypr/input.lua")
XKB_KEYCODES = "/usr/share/X11/xkb/keycodes/evdev"

# modifier name -> evdev codes (either side counts). AltGr is its own modifier on br.
MODS = {
    "ctrl": (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL),
    "shift": (e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT),
    "alt": (e.KEY_LEFTALT,),
    "altgr": (e.KEY_RIGHTALT,),
    "super": (e.KEY_LEFTMETA, e.KEY_RIGHTMETA),
}
MOD_OF = {code: name for name, codes in MODS.items() for code in codes}
MOD_ORDER = ["super", "ctrl", "alt", "altgr", "shift"]
MOD_LABEL = {"super": "Super", "ctrl": "Ctrl", "alt": "Alt", "altgr": "AltGr", "shift": "Shift"}
HYPR_MODMASK = {"shift": 1, "ctrl": 4, "alt": 8, "super": 64, "altgr": 128}

MOUSE_BUTTONS = [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA,
                 e.BTN_FORWARD, e.BTN_BACK, e.BTN_TASK]
MOUSE_LABEL = {
    e.BTN_LEFT: "Clique esquerdo", e.BTN_RIGHT: "Clique direito", e.BTN_MIDDLE: "Botão do meio",
    e.BTN_SIDE: "Lateral trás", e.BTN_EXTRA: "Lateral frente", e.BTN_FORWARD: "Avançar",
    e.BTN_BACK: "Voltar", e.BTN_TASK: "Botão Task",
}
# Hyprland's names for mouse buttons (SUPER + K reads them as mouse:<x11 code>)
HYPR_MOUSE = {e.BTN_LEFT: "mouse:272", e.BTN_RIGHT: "mouse:273", e.BTN_MIDDLE: "mouse:274",
              e.BTN_SIDE: "mouse:275", e.BTN_EXTRA: "mouse:276", e.BTN_FORWARD: "mouse:277",
              e.BTN_BACK: "mouse:278", e.BTN_TASK: "mouse:279"}

SPECIAL_LABEL = {
    e.KEY_ESC: "Esc", e.KEY_ENTER: "Enter", e.KEY_KPENTER: "Enter (num)", e.KEY_BACKSPACE: "Backspace",
    e.KEY_TAB: "Tab", e.KEY_SPACE: "Espaço", e.KEY_CAPSLOCK: "Caps Lock", e.KEY_LEFT: "←",
    e.KEY_RIGHT: "→", e.KEY_UP: "↑", e.KEY_DOWN: "↓", e.KEY_HOME: "Home", e.KEY_END: "End",
    e.KEY_PAGEUP: "PgUp", e.KEY_PAGEDOWN: "PgDn", e.KEY_INSERT: "Insert", e.KEY_DELETE: "Del",
    e.KEY_SYSRQ: "Print", e.KEY_PAUSE: "Pause", e.KEY_SCROLLLOCK: "Scroll Lock",
    e.KEY_NUMLOCK: "Num Lock", e.KEY_LEFTCTRL: "Ctrl", e.KEY_RIGHTCTRL: "Ctrl dir.",
    e.KEY_LEFTSHIFT: "Shift", e.KEY_RIGHTSHIFT: "Shift dir.", e.KEY_LEFTALT: "Alt",
    e.KEY_RIGHTALT: "AltGr", e.KEY_LEFTMETA: "Super", e.KEY_RIGHTMETA: "Super dir.",
    e.KEY_COMPOSE: "Menu", e.KEY_VOLUMEUP: "Vol +", e.KEY_VOLUMEDOWN: "Vol −", e.KEY_MUTE: "Mudo",
    e.KEY_PLAYPAUSE: "Play", e.KEY_NEXTSONG: "Próxima", e.KEY_PREVIOUSSONG: "Anterior",
    e.KEY_CALC: "Calculadora",
}
SPECIAL_LABEL.update({getattr(e, f"KEY_F{i}"): f"F{i}" for i in range(1, 25)})
for _i in range(10):
    SPECIAL_LABEL[getattr(e, f"KEY_KP{_i}")] = f"{_i} (num)"
SPECIAL_LABEL.update({e.KEY_KPPLUS: "+ (num)", e.KEY_KPMINUS: "− (num)", e.KEY_KPASTERISK: "* (num)",
                      e.KEY_KPSLASH: "/ (num)", e.KEY_KPDOT: ", (num)"})

# Hyprland key names for SUPER + K (xkb keysym names, as hyprctl binds prints them)
HYPR_KEY = {e.KEY_ESC: "Escape", e.KEY_ENTER: "Return", e.KEY_SPACE: "space", e.KEY_TAB: "Tab",
            e.KEY_BACKSPACE: "BackSpace", e.KEY_LEFT: "left", e.KEY_RIGHT: "right", e.KEY_UP: "up",
            e.KEY_DOWN: "down", e.KEY_DELETE: "Delete", e.KEY_SYSRQ: "Print",
            e.KEY_CAPSLOCK: "Caps_Lock", e.KEY_MINUS: "minus", e.KEY_EQUAL: "equal",
            e.KEY_COMMA: "comma", e.KEY_DOT: "period"}

# keyboard keys a virtual keyboard may send (everything below the button range)
KEYBOARD_CODES = [c for c in range(1, 0x100) if c in e.KEY]


def code(name):
    """evdev name -> code, None when unknown."""
    value = e.ecodes.get(name) if isinstance(name, str) else None
    return value if isinstance(value, int) else None


def name(code_):
    """evdev code -> the canonical name (BTN_LEFT, not BTN_MOUSE; KEY_MUTE...)."""
    table = e.BTN if code_ in e.BTN else e.KEY
    found = table.get(code_)
    if isinstance(found, (list, tuple)):
        for candidate in found:
            if candidate in ("BTN_LEFT", "BTN_SIDE", "BTN_EXTRA", "BTN_MIDDLE", "BTN_RIGHT"):
                return candidate
        return sorted(found, key=lambda n: (n.startswith(("KEY_MIN", "BTN_MISC", "BTN_MOUSE")), n))[0]
    return found


def is_mouse(code_):
    return code_ in MOUSE_BUTTONS


# ------------------------------------------------------------------------------- xkb

class _RuleNames(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]


_xkb = ctypes.CDLL("libxkbcommon.so.0")
_xkb.xkb_context_new.restype = ctypes.c_void_p
_xkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p
_xkb.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RuleNames), ctypes.c_int]
_xkb.xkb_keymap_num_layouts.argtypes = [ctypes.c_void_p]
_xkb.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_xkb.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
_xkb.xkb_state_new.restype = ctypes.c_void_p
_xkb.xkb_state_new.argtypes = [ctypes.c_void_p]
_xkb.xkb_state_unref.argtypes = [ctypes.c_void_p]
_xkb.xkb_state_update_mask.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 6
_xkb.xkb_state_key_get_utf32.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_xkb.xkb_state_key_get_utf32.restype = ctypes.c_uint32
_xkb.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_xkb.xkb_state_key_get_one_sym.restype = ctypes.c_uint32

# keysyms of the dead keys, by the combining mark they add
_DEAD = {"́": 0xFE51, "̀": 0xFE50, "̂": 0xFE52, "̃": 0xFE53, "̈": 0xFE57}


def _hypr_layouts():
    kb = {"kb_layout": "br", "kb_variant": "", "kb_options": ""}
    try:
        text = open(HYPR_INPUT).read()
        for key, value in re.findall(r'(kb_layout|kb_variant|kb_options)\s*=\s*"([^"]*)"', text):
            kb[key] = value
    except OSError:
        pass
    return kb


class Keymap:
    """The same keymap Hyprland builds from input.lua (~/.config/xkb is on xkbcommon's path)."""

    def __init__(self):
        kb = _hypr_layouts()
        names = _RuleNames(None, None, kb["kb_layout"].encode(), kb["kb_variant"].encode(),
                           kb["kb_options"].encode() or None)
        self.keymap = _xkb.xkb_keymap_new_from_names(_xkb.xkb_context_new(0), ctypes.byref(names), 0)
        self.layouts = _xkb.xkb_keymap_num_layouts(self.keymap) if self.keymap else 0
        self._chars = {}
        if self.keymap:
            self.shift = 1 << _xkb.xkb_keymap_mod_get_index(self.keymap, b"Shift")
            self.level3 = 1 << _xkb.xkb_keymap_mod_get_index(self.keymap, b"Mod5")

    def _lookup(self, layout, mods):
        state = _xkb.xkb_state_new(self.keymap)
        _xkb.xkb_state_update_mask(state, mods, 0, 0, 0, 0, layout)
        out = {}
        for c in KEYBOARD_CODES:
            out[c] = (_xkb.xkb_state_key_get_utf32(state, c + 8), _xkb.xkb_state_key_get_one_sym(state, c + 8))
        _xkb.xkb_state_unref(state)
        return out

    def label(self, code_, layout=0):
        """The keycap: F8, Esc, Ç, ´ ... (layout 0 = br, the physical keys)."""
        if code_ in SPECIAL_LABEL:
            return SPECIAL_LABEL[code_]
        if code_ in MOUSE_LABEL:
            return MOUSE_LABEL[code_]
        if self.keymap and code_ in KEYBOARD_CODES:
            utf, sym = self._lookup(layout, 0).get(code_, (0, 0))
            for dead, mark in ((0xFE51, "´"), (0xFE50, "`"), (0xFE52, "^"), (0xFE53, "~"), (0xFE57, "¨")):
                if sym == dead:
                    return mark
            if utf and chr(utf).isprintable() and chr(utf).strip():
                return chr(utf).upper()
        found = name(code_) or "?"
        return found.removeprefix("KEY_").removeprefix("BTN_").capitalize()

    def labels(self):
        """{evdev name: label} of every key and mouse button, for the UI."""
        if self.keymap:
            base = self._lookup(0, 0)
        out = {}
        for c in KEYBOARD_CODES + MOUSE_BUTTONS:
            n = name(c)
            if not n:
                continue
            if c in SPECIAL_LABEL or c in MOUSE_LABEL or not self.keymap:
                out[n] = self.label(c)
                continue
            utf, sym = base.get(c, (0, 0))
            dead = {0xFE51: "´", 0xFE50: "`", 0xFE52: "^", 0xFE53: "~", 0xFE57: "¨"}.get(sym)
            if dead:
                out[n] = dead
            elif utf and chr(utf).isprintable() and chr(utf).strip():
                out[n] = chr(utf).upper()
            else:
                out[n] = n.removeprefix("KEY_").capitalize()
        return out

    def chars(self, layout):
        """char -> (code, needs shift, needs AltGr), plus dead key codes, for typing text."""
        if layout in self._chars:
            return self._chars[layout]
        table, dead = {}, {}
        if self.keymap:
            for mods, flags in ((0, (False, False)), (self.shift, (True, False)),
                                (self.level3, (False, True)), (self.shift | self.level3, (True, True))):
                for c, (utf, sym) in self._lookup(layout, mods).items():
                    if utf and chr(utf) not in table and c not in MOD_OF:
                        table[chr(utf)] = (c, *flags)
                    if 0xFE50 <= sym <= 0xFE57 and sym not in dead:
                        dead[sym] = (c, *flags)
        table["\n"] = (e.KEY_ENTER, False, False)
        table["\t"] = (e.KEY_TAB, False, False)
        self._chars[layout] = (table, dead)
        return self._chars[layout]

    def text_strokes(self, text, layout):
        """[(code, shift, altgr)] that type `text` on `layout`; accents go through dead keys.
        Characters the layout can't make are skipped."""
        table, dead = self.chars(layout)
        out = []
        for ch in text:
            if ch in table:
                out.append(table[ch])
                continue
            parts = unicodedata.normalize("NFD", ch)
            if len(parts) == 2 and parts[1] in _DEAD and _DEAD[parts[1]] in dead and parts[0] in table:
                out.append(dead[_DEAD[parts[1]]])
                out.append(table[parts[0]])
        return out


# ------------------------------------------------------------------- xkb key names

def xkb_names():
    """evdev code -> xkb key name (AD02 ...), for OpenAurum's `glow` (Pichau Aurum V60), optional."""
    out = {}
    try:
        for xname, kc in re.findall(r"^\s*<(\w+)>\s*=\s*(\d+);", open(XKB_KEYCODES).read(), re.M):
            out.setdefault(int(kc) - 8, xname)
    except OSError:
        pass
    return out
