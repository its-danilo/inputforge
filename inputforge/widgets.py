"""Small building blocks for the GTK window: keycaps, libadwaita rows wired to callbacks,
the region signature preview and the binding summaries. Colors come from the GTK theme
(libadwaita named colors), so the window follows calOS (or any GTK theme) on its own."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

# var(--accent-*): the theme's own accent (calOS sets these); libadwaita defines them too
CSS = """
.keycap { font-weight: 700; font-size: 0.86em; padding: 1px 8px; border-radius: 7px;
          background: alpha(currentColor, 0.07); border: 1px solid alpha(currentColor, 0.2);
          border-bottom-width: 2px; }
.keycap.big { font-size: 1.05em; padding: 4px 12px; border-radius: 9px; }
.keycap.accent { background: alpha(var(--accent-bg-color), 0.18); border-color: alpha(var(--accent-bg-color), 0.65); }
.keyplus { opacity: 0.5; font-size: 0.85em; }
button.capturing { background: alpha(var(--accent-bg-color), 0.3); }
.type-icon { min-width: 34px; min-height: 34px; border-radius: 10px; color: var(--accent-fg-color);
             background: var(--accent-bg-color); }
.type-icon.remap { background: alpha(currentColor, 0.75); color: @window_bg_color; }
.type-icon.command { background: alpha(currentColor, 0.1); color: @window_fg_color; }
.type-icon.step { min-width: 30px; min-height: 30px; border-radius: 9px;
                  background: alpha(var(--accent-bg-color), 0.16); color: var(--accent-color); }
.type-icon.step.wait { background: alpha(currentColor, 0.08); color: @window_fg_color; }
.live { color: var(--accent-color); font-weight: 700; }
.live-dot { min-width: 8px; min-height: 8px; border-radius: 99px; background: var(--accent-bg-color); }
.hero { padding: 6px 2px 2px 2px; }
.hero-title { font-size: 21pt; font-weight: 800; }
.cps-number { font-size: 30pt; font-weight: 800; font-feature-settings: "tnum"; }
.nested { margin-left: 26px; }
.sig { border-radius: 8px; }
row.cond { border-left: 3px solid alpha(var(--accent-bg-color), 0.7); }
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


MOD_LABEL = {"super": "Super", "ctrl": "Ctrl", "alt": "Alt", "altgr": "AltGr", "shift": "Shift"}
MOD_CODE = {"ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "alt": "KEY_LEFTALT",
            "altgr": "KEY_RIGHTALT", "super": "KEY_LEFTMETA"}
MOUSE = [("BTN_LEFT", "Esquerdo"), ("BTN_RIGHT", "Direito"), ("BTN_MIDDLE", "Meio"),
         ("BTN_SIDE", "Lateral trás"), ("BTN_EXTRA", "Lateral frente")]
MODES = [("toggle", "Alternar", "Aperta liga, aperta de novo desliga"),
         ("hold", "Segurar", "Roda enquanto você segura"),
         ("once", "Uma vez", "Cada toque roda uma vez")]
SCREEN_MODES = {"toggle": "Cada vez que a tela fica assim, liga/desliga",
                "hold": "Roda enquanto a tela estiver assim",
                "once": "Roda quando a tela fica assim"}
CONDS = [("match", "igual à referência"), ("differ", "diferente da referência"), ("change", "mudou")]
TYPES = {
    "clicker": ("input-mouse-symbolic", "Autoclicker", "Cliques (ou toques de tecla) em ritmo fixo, no cursor ou em pontos da tela"),
    "macro": ("media-playlist-repeat-symbolic", "Macro", "Teclas, cliques, esperas e movimentos em sequência; dá pra gravar jogando"),
    "remap": ("input-keyboard-symbolic", "Remapear", "A tecla ou botão vira outra coisa enquanto o perfil está ativo"),
    "command": ("utilities-terminal-symbolic", "Comando", "Roda um comando do shell no atalho"),
}
STEPS = {
    "key": ("input-keyboard-symbolic", "Tecla"), "button": ("input-mouse-symbolic", "Clique"),
    "wait": ("alarm-symbolic", "Esperar"), "move": ("object-flip-horizontal-symbolic", "Mover"),
    "moveto": ("find-location-symbolic", "Ir para"), "scroll": ("view-continuous-symbolic", "Rolar"),
    "text": ("insert-text-symbolic", "Texto"), "command": ("utilities-terminal-symbolic", "Comando"),
    "wait_screen": ("view-reveal-symbolic", "Esperar tela"), "if_screen": ("emblem-system-symbolic", "Se a tela"),
}


# ---------------------------------------------------------------------------- keycaps

def keycap(text, big=False, accent=False):
    lbl = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    lbl.add_css_class("keycap")
    if big:
        lbl.add_css_class("big")
    if accent:
        lbl.add_css_class("accent")
    return lbl


def key_label(labels, name):
    return labels.get(name) or (name or "").removeprefix("KEY_").removeprefix("BTN_")


def trigger_text(trig, labels, region_name=None):
    if trig.get("kind") == "screen":
        cond = dict(CONDS).get(trig.get("cond"), "")
        return f"tela: {region_name or 'região?'} · {cond.split(' ')[0]}"
    if not trig.get("key"):
        return "nenhum"
    return " + ".join([MOD_LABEL[m] for m in trig.get("mods", [])] + [key_label(labels, trig["key"])])


def keycaps(trig, labels, region_name=None, big=False):
    """A row of keycaps for a trigger (Ctrl + G), or the region chip of a screen trigger."""
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    if trig.get("kind") == "screen":
        box.append(keycap("👁  " + trigger_text(trig, labels, region_name), big=big))
        return box
    if not trig.get("key"):
        box.append(Gtk.Label(label="nenhum", css_classes=["dim-label"]))
        return box
    for m in trig.get("mods", []):
        box.append(keycap(MOD_LABEL[m], big=big))
        box.append(Gtk.Label(label="+", css_classes=["keyplus"]))
    box.append(keycap(key_label(labels, trig["key"]), big=big, accent=big))
    return box


def codes_caps(codes, labels, big=False):
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    if not codes:
        box.append(Gtk.Label(label="nada", css_classes=["dim-label"]))
    for i, c in enumerate(codes):
        if i:
            box.append(Gtk.Label(label="+", css_classes=["keyplus"]))
        box.append(keycap(key_label(labels, c), big=big))
    return box


def type_icon(kind, step=False, extra=None):
    name = STEPS[kind][0] if step else TYPES[kind][0]
    img = Gtk.Image.new_from_icon_name(name)
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("type-icon")
    if step:
        box.add_css_class("step")
    if extra:
        box.add_css_class(extra)
    img.set_hexpand(True)
    box.append(img)
    return box


# --------------------------------------------------------------------------- summaries

def fmt(n, digits=1):
    s = f"{n:.{digits}f}".rstrip("0").rstrip(".") if isinstance(n, float) else str(n)
    return s.replace(".", ",")


def rate_text(a):
    r = a["rate"]
    return f"{fmt(float(r['value']), 2)} CPS" if r["unit"] == "cps" else f"a cada {fmt(r['value'])} ms"


def summary(b, labels):
    a = b["action"]
    t = a["type"]
    if t == "clicker":
        target = dict(MOUSE).get(a["target"])
        what = f"Clique {target.lower()}" if target else f"Tecla {key_label(labels, a['target'])}"
        where = f"em {len(a['points'])} pontos" if a["where"] == "points" else "no cursor"
        lim = {"count": f" · {fmt(a['limit']['value'])} cliques", "time": f" · por {fmt(a['limit']['value'])} s"}.get(a["limit"]["kind"], "")
        return f"{what} · {rate_text(a)} · {where}{lim}"
    if t == "macro":
        rep = "repete até parar" if a["repeat"] == 0 else ("roda 1 vez" if a["repeat"] == 1 else f"repete {a['repeat']}×")
        eyes = " · olha a tela" if any(s["t"].endswith("_screen") for s in a["steps"]) else ""
        n = len(a["steps"])
        return f"{n} passo{'s' if n != 1 else ''} · {rep}{eyes}"
    if t == "remap":
        return "vira " + (" + ".join(key_label(labels, c) for c in a["to"]) or "nada (desativada)")
    return a["cmd"] or "nenhum comando ainda"


# ------------------------------------------------------------------------ form rows

def _subtitle(row, subtitle):
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def switch_row(title, active, on_change, subtitle=None):
    row = _subtitle(Adw.SwitchRow(title=title, active=bool(active)), subtitle)
    row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
    return row


def entry_row(title, text, on_change, monospace=False):
    row = Adw.EntryRow(title=title, text=text or "")
    if monospace:
        row.add_css_class("monospace")
    row.connect("changed", lambda r: on_change(r.get_text()))
    return row


def spin_row(title, value, lo, hi, step, on_change, digits=0, subtitle=None):
    adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
    row = _subtitle(Adw.SpinRow(title=title, adjustment=adj, digits=digits), subtitle)
    row.connect("notify::value", lambda r, _p: on_change(r.get_value() if digits else int(r.get_value())))
    return row


def combo_row(title, items, value, on_change, subtitle=None):
    """items: [(value, label)]."""
    from gi.repository import Gtk as _Gtk
    model = _Gtk.StringList.new([lbl for _, lbl in items])
    row = _subtitle(Adw.ComboRow(title=title, model=model), subtitle)
    values = [v for v, _ in items]
    if value in values:
        row.set_selected(values.index(value))
    row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()])
                if 0 <= r.get_selected() < len(values) else None)
    return row


def dropdown(items, value, on_change):
    dd = Gtk.DropDown.new_from_strings([lbl for _, lbl in items])
    values = [v for v, _ in items]
    if value in values:
        dd.set_selected(values.index(value))
    dd.set_valign(Gtk.Align.CENTER)
    dd.connect("notify::selected", lambda d, _p: on_change(values[d.get_selected()])
               if 0 <= d.get_selected() < len(values) else None)
    return dd


def small_spin(value, lo, hi, step, on_change, width=5, digits=0):
    adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
    sb = Gtk.SpinButton(adjustment=adj, digits=digits, valign=Gtk.Align.CENTER, width_chars=width)
    sb.connect("value-changed", lambda s: on_change(s.get_value() if digits else int(s.get_value())))
    return sb


def icon_button(icon, tooltip, on_click, flat=True, css=None):
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    if flat:
        b.add_css_class("flat")
    for c in css or ():
        b.add_css_class(c)
    b.connect("clicked", lambda _b: on_click())
    return b


def pill_button(label, on_click, suggested=False, destructive=False, icon=None):
    b = Gtk.Button(valign=Gtk.Align.CENTER)
    if icon:
        b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
    else:
        b.set_label(label)
    b.add_css_class("pill" if suggested or destructive else "flat")
    if suggested:
        b.add_css_class("suggested-action")
    if destructive:
        b.add_css_class("destructive-action")
    b.connect("clicked", lambda _b: on_click())
    return b


class Signature(Gtk.DrawingArea):
    """A region's reference: GRID x GRID colors, brightened so dark HUDs are visible."""

    def __init__(self, ref, size=44):
        super().__init__()
        self.ref = ref
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self.draw)

    def draw(self, area, cr, w, h):
        import math
        r = 8
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.clip()
        if not self.ref:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.15)
            cr.paint()
            return
        n = int(len(self.ref) ** 0.5)
        cw, ch = w / n, h / n
        for i, (rr, gg, bb) in enumerate(self.ref):
            cr.set_source_rgb(min(1, rr / 255 * 1.6), min(1, gg / 255 * 1.6), min(1, bb / 255 * 1.6))
            cr.rectangle((i % n) * cw, (i // n) * ch, cw + 0.5, ch + 0.5)
            cr.fill()


class later:
    """Debounce: calling it (re)schedules fn ms from now; flush() runs a pending call now."""

    def __init__(self, ms, fn):
        self.ms, self.fn, self.id = ms, fn, 0

    def __call__(self, *_a):
        if self.id:
            GLib.source_remove(self.id)
        self.id = GLib.timeout_add(self.ms, self._run)

    def _run(self):
        self.id = 0
        self.fn()
        return False

    def flush(self):
        if self.id:
            GLib.source_remove(self.id)
            self._run()
