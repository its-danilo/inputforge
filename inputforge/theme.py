"""Colors for the window, the HUD and the Game Bar.

On calOS they follow the current theme (its waybar.css + alacritty.toml) and change live
(`inputforge theme`). Elsewhere: a built-in dark palette, with any key overridden by
~/.config/inputforge/theme.json, e.g. {"accent": "#7aa2f7", "background": "#16161e"}.
"""

import colorsys
import json
import os
import re

from inputforge import paths

THEME = paths.CALOS_THEME
OVERRIDES = os.path.join(paths.CONFIG_DIR, "theme.json")


def _hex(c):
    c = c.strip().strip("'\"")
    if c.startswith("0x"):
        c = "#" + c[2:]
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", c)
    return "#" + m.group(1).lower() if m else None


def _rgb(c):
    return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _to_hex(r, g, b):
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in (r, g, b))


def luminance(c):
    def ch(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def mix(a, b, t):
    ra, rb = _rgb(a), _rgb(b)
    return _to_hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def shift_hue(c, deg, sat=1.0, light=1.0):
    h, l, s = colorsys.rgb_to_hls(*_rgb(c))
    return _to_hex(*colorsys.hls_to_rgb((h + deg / 360) % 1, max(0, min(1, l * light)), max(0, min(1, s * sat))))


def colors():
    out = {"background": "#16161e", "foreground": "#c8c8d0", "accent": "#7aa2f7"}
    try:
        for name, value in re.findall(r"@define-color\s+([\w-]+)\s+([^;]+);", open(os.path.join(THEME, "waybar.css")).read()):
            if _hex(value):
                out[name] = _hex(value)
    except OSError:
        pass
    try:
        with open(OVERRIDES) as f:
            for name, value in json.load(f).items():
                if name in ("background", "foreground", "accent") and _hex(str(value)):
                    out[name] = _hex(str(value))
    except (OSError, ValueError, AttributeError):
        pass
    term = {}
    try:
        section = ""
        for line in open(os.path.join(THEME, "alacritty.toml")):
            line = line.split("#", 1)[0] if not re.search(r"=\s*[\"']?#", line) else line
            m = re.match(r"\s*\[([\w.]+)\]", line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"\s*(\w+)\s*=\s*[\"']?(#[0-9a-fA-F]{6}|0x[0-9a-fA-F]{6})", line)
            if m and section.startswith("colors"):
                term[f"{section}.{m.group(1)}"] = _hex(m.group(2))
    except OSError:
        pass
    bg = term.get("colors.primary.background") or out["background"]
    fg = term.get("colors.primary.foreground") or out["foreground"]
    accent = out["accent"]
    if luminance(bg) > 0.4:  # the UI is designed dark: keep it readable with a light theme
        bg, fg = "#121216", "#e6e6ea"
    red = term.get("colors.normal.red") or "#e05f65"
    # an accent too close to grey (graphite, vague) gets a second hue from the theme's blue
    h, l, s = colorsys.rgb_to_hls(*_rgb(accent))
    accent2 = shift_hue(accent, -30, 1.05, 1.08) if s > 0.18 else (term.get("colors.normal.blue") or mix(accent, fg, 0.5))
    return {
        "bg": bg,
        "fg": fg,
        "accent": accent,
        "accent2": accent2,
        "on_accent": "#0b0b0f" if luminance(accent) > 0.32 else "#ffffff",
        "red": red,
        "muted": mix(fg, bg, 0.45),
        "line": mix(fg, bg, 0.86),
        "surface": mix(bg, fg, 0.05),
        "surface2": mix(bg, fg, 0.09),
        "deep": mix(bg, "#000000", 0.35),
    }
