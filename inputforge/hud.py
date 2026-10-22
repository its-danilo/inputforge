"""InputForge HUD: pills in a corner of the screen while something runs.

Started by the engine when there is something to show (runs, the recorder, the point
picker, a toast) and quits on its own after IDLE_S with nothing left. Layer-shell surface
on the overlay layer (above fullscreen games), click-through, on the focused monitor.
Needs LD_PRELOAD=libgtk4-layer-shell.so (the engine sets it).
"""

import json
import math
import os
import socket
import sys
import time

from inputforge.paths import intel_gpu_env  # noqa: E402

intel_gpu_env(os.environ)  # before GTK loads: render on the Intel, see paths.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

import cairo  # noqa: E402

from inputforge import hypr, keys, theme  # noqa: E402
from inputforge import paths  # noqa: E402
from inputforge.paths import SOCKET  # noqa: E402

NAME = paths.NS_HUD
IDLE_S = 4.0
FONT = "Inter, 'Noto Sans', sans-serif"
ICON = {"clicker": "󰍽", "macro": "󰑮", "remap": "󰌌", "command": "󰆍"}


def css(c):
    return f"""
    window {{ background: transparent; }}
    .stack {{ margin: 14px; }}
    .pill {{
        font-family: {FONT};
        background: alpha({c['deep']}, 0.82);
        border: 1px solid alpha({c['fg']}, 0.10);
        border-radius: 18px;
        padding: 10px 14px 10px 12px;
        margin-bottom: 8px;
        box-shadow: 0 10px 30px alpha(black, 0.35);
        transition: opacity 300ms ease-out;
    }}
    .pill.running {{ border-color: alpha({c['accent']}, 0.55); }}
    .pill.paused {{ opacity: 0.72; }}
    .pill.rec {{ border-color: alpha({c['red']}, 0.7); }}
    .pill.gone {{ opacity: 0; }}
    .icon {{ font-family: 'CaskaydiaMono Nerd Font'; font-size: 20px; color: {c['accent']};
             min-width: 34px; min-height: 34px; border-radius: 11px;
             background: alpha({c['accent']}, 0.14); margin-right: 11px; }}
    .rec .icon {{ color: {c['red']}; background: alpha({c['red']}, 0.16); }}
    .paused .icon {{ color: {c['muted']}; background: alpha({c['fg']}, 0.07); }}
    .title {{ color: {c['fg']}; font-weight: 700; font-size: 14px; }}
    .detail {{ color: {c['muted']}; font-size: 12px; font-weight: 500; }}
    .key {{ font-family: {FONT}; font-weight: 700; font-size: 11px; color: {c['fg']};
            background: alpha({c['fg']}, 0.08); border: 1px solid alpha({c['fg']}, 0.16);
            border-bottom-width: 2px; border-radius: 7px; padding: 2px 7px; margin-left: 12px; }}
    .brand {{ font-family: {FONT}; font-size: 10px; font-weight: 800; letter-spacing: 2px;
              color: alpha({c['fg']}, 0.45); margin: 0 4px 6px 6px; }}
    .toast .title {{ font-weight: 600; }}
    .ring {{ margin-right: 12px; }}
    .pill.count {{ padding-top: 9px; padding-bottom: 9px; }}
    """


class Hud:
    def __init__(self, app):
        self.app = app
        self.state = None
        self.labels = keys.Keymap()
        self.idle_since = time.monotonic()
        self.monitor = None
        self.position = "top-right"
        self.buf = b""
        self.colors = theme.colors()
        self.provider = Gtk.CssProvider()
        self.provider.load_from_string(css(self.colors))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.win = Gtk.Window(application=app)
        LayerShell.init_for_window(self.win)
        LayerShell.set_namespace(self.win, NAME)
        LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(self.win, -1)
        self.stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_css_class("stack")
        self.win.set_child(self.stack)
        self.win.connect("realize", self.on_realize)
        self.place()
        self.connect()
        GLib.timeout_add(250, self.tick)

    def on_realize(self, w):
        surface = w.get_surface()
        surface.set_input_region(cairo.Region())  # click-through
        surface.connect("layout", lambda *a: surface.set_input_region(cairo.Region()))

    def place(self):
        try:
            cfg = json.load(open(paths.CONFIG))
            self.position = cfg["settings"]["hud"]["position"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        pos = self.position
        E = LayerShell.Edge
        for edge in (E.TOP, E.BOTTOM, E.LEFT, E.RIGHT):
            LayerShell.set_anchor(self.win, edge, False)
        LayerShell.set_anchor(self.win, E.BOTTOM if pos.startswith("bottom") else E.TOP, True)
        if pos.endswith("left"):
            LayerShell.set_anchor(self.win, E.LEFT, True)
        elif pos.endswith("right"):
            LayerShell.set_anchor(self.win, E.RIGHT, True)
        LayerShell.set_margin(self.win, E.TOP, 36 if pos.startswith("top") else 0)
        LayerShell.set_margin(self.win, E.BOTTOM, 24 if pos.startswith("bottom") else 0)
        self.stack.set_valign(Gtk.Align.END if pos.startswith("bottom") else Gtk.Align.START)
        halign = Gtk.Align.START if pos.endswith("left") else Gtk.Align.END if pos.endswith("right") else Gtk.Align.CENTER
        self.stack.set_halign(halign)
        self.follow_monitor()

    def follow_monitor(self):
        m = hypr.focused_monitor()
        name = m.get("name") if m else None
        if name == self.monitor:
            return
        self.monitor = name
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            mon = monitors.get_item(i)
            if mon.get_connector() == name:
                LayerShell.set_monitor(self.win, mon)

    # ---- engine connection -------------------------------------------------
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(SOCKET)
        except OSError:
            sys.exit(0)
        self.sock.sendall(b'{"id": 1, "cmd": "subscribe"}\n')
        self.sock.setblocking(False)
        GLib.io_add_watch(self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP,
                          self.on_data)

    def on_data(self, fd, cond):
        try:
            chunk = self.sock.recv(1 << 20)
        except BlockingIOError:
            return True
        except OSError:
            chunk = b""
        if not chunk:
            self.app.quit()
            return False
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "state":
                self.render(msg["state"])
            elif msg.get("event") == "theme":
                self.colors = theme.colors()
                self.provider.load_from_string(css(self.colors))
            elif msg.get("event") == "config":
                self.place()
            elif msg.get("id") == 1 and msg.get("ok"):
                self.render(msg["result"]["state"])
        return True

    # ---- drawing -------------------------------------------------------------
    def key_label(self, trig):
        if trig.get("kind") == "screen":
            return "tela"
        c = keys.code(trig.get("key"))
        if c is None:
            return None
        parts = [keys.MOD_LABEL[m] for m in trig.get("mods", [])] + [self.labels.label(c)]
        return " + ".join(parts)

    def pill(self, icon, title, detail=None, cls=(), key=None):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.add_css_class("pill")
        for c in cls:
            box.add_css_class(c)
        ic = Gtk.Label(label=icon)
        ic.add_css_class("icon")
        box.append(ic)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("title")
        col.append(t)
        if detail:
            d = Gtk.Label(label=detail, xalign=0)
            d.add_css_class("detail")
            col.append(d)
        box.append(col)
        if key:
            k = Gtk.Label(label=key, valign=Gtk.Align.CENTER)
            k.add_css_class("key")
            box.append(k)
        return box

    def countdown(self, until, title, hint):
        """A pill like the others: a ring that drains smoothly with the seconds left inside,
        the name of what's about to start and what to do meanwhile."""
        total = max(0.5, until - time.time())
        c = self.colors
        acc = [int(c["accent"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
        acc2 = [int(c["accent2"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
        fgc = [int(c["fg"][i:i + 2], 16) / 255 for i in (1, 3, 5)]
        font = Pango.FontDescription.from_string("Inter Bold 15")
        area = Gtk.DrawingArea()
        area.set_content_width(40)
        area.set_content_height(40)
        area.set_valign(Gtk.Align.CENTER)
        area.add_css_class("ring")

        def draw(a, cr, w, h):
            left = max(0.0, until - time.time())
            cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 3
            cr.set_line_width(3.5)
            cr.set_source_rgba(*fgc, 0.12)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
            if left > 0:
                grad = cairo.LinearGradient(0, 0, w, h)
                grad.add_color_stop_rgb(0, *acc)
                grad.add_color_stop_rgb(1, *acc2)
                cr.set_source(grad)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * left / total)
                cr.stroke()
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(font)
            layout.set_text(str(math.ceil(left)) if left > 0 else "✓", -1)
            ink, _ = layout.get_pixel_extents()
            cr.set_source_rgb(*fgc)
            cr.move_to(cx - (ink.x + ink.width / 2), cy - (ink.y + ink.height / 2))
            PangoCairo.show_layout(cr, layout)

        area.set_draw_func(draw)
        GLib.timeout_add(33, lambda: (area.queue_draw(), time.time() < until + 0.3)[1])
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.add_css_class("pill")
        box.add_css_class("running")
        box.add_css_class("count")
        box.append(area)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("title")
        col.append(t)
        d = Gtk.Label(label=hint, xalign=0)
        d.add_css_class("detail")
        col.append(d)
        box.append(col)
        return box

    def render(self, st):
        self.state = st
        sig = json.dumps([st.get("runs"), st.get("recording"), st.get("picking"), st.get("toast"),
                          st.get("ref_capture")], sort_keys=True)
        if sig == getattr(self, "_sig", None):
            return
        self._sig = sig
        child = self.stack.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.stack.remove(child)
            child = nxt
        items = []
        for r in st.get("runs", []):
            if r["status"] == "countdown":
                hint = "Leve o mouse até onde vai clicar" if r["kind"] == "clicker" else "Prepare o jogo, já vai começar"
                items.append(self.countdown(r["countdown_until"], r["name"], hint))
                continue
            paused = r["status"] == "paused"
            detail = ("Pausado — volte pro jogo" if paused else r["detail"]) or None
            items.append(self.pill(ICON.get(r["kind"], "󰍽"), r["name"], detail,
                                   cls=("paused",) if paused else ("running",), key=self.key_label(r["trigger"])))
        rec = st.get("recording")
        if rec:
            if rec["starts"] > time.time():
                items.append(self.countdown(rec["starts"], "Gravação", f"Começa já · {self.stop_label()} termina"))
            else:
                stop = self.stop_label()
                items.append(self.pill("󰑊", "Gravando macro", f"{rec['events']} eventos", cls=("rec",), key=stop))
        pick = st.get("picking")
        if pick:
            n = len(pick["points"])
            if pick.get("hint"):
                items.append(self.pill("󰆽", pick["hint"], "Marcando região da tela · Esc cancela", cls=("running",)))
            else:
                items.append(self.pill("󰆽", "Marcando pontos", f"{n} ponto{'s' if n != 1 else ''} · botão direito termina",
                                       cls=("running",)))
        cap = st.get("ref_capture")
        if cap and cap["until"] > time.time():
            items.append(self.countdown(cap["until"], "Capturando a referência", "Deixe a tela no estado certo"))
        toast = st.get("toast")
        if toast and toast["until"] > time.time():
            items.append(self.pill("󰄬", toast["text"], cls=("toast",)))
        if items:
            brand = Gtk.Label(label="INPUTFORGE", xalign=0 if self.position.endswith("left") else 1)
            brand.add_css_class("brand")
            self.stack.append(brand)
            for it in items:
                self.stack.append(it)
            self.idle_since = None
            self.follow_monitor()
            self.win.set_visible(True)
        else:
            self.win.set_visible(False)
            if self.idle_since is None:
                self.idle_since = time.monotonic()

    def stop_label(self):
        try:
            cfg = json.load(open(paths.CONFIG))
            return self.key_label(cfg["settings"]["record_stop"])
        except (OSError, ValueError, KeyError, TypeError):
            return "F12"

    def tick(self):
        st = self.state or {}
        toast = st.get("toast")
        if toast and toast["until"] <= time.time():
            st = dict(st, toast=None)
            self.render(st)
            self.state = st
        rec = st.get("recording")
        if rec and rec["starts"] <= time.time() and not getattr(self, "_rec_live", False):
            self._rec_live = True
            self._sig = None  # countdown over: redraw as "Gravando"
            self.render(st)
        if self.idle_since is not None and time.monotonic() - self.idle_since > IDLE_S:
            self.app.quit()
            return False
        return True


def main():
    app = Gtk.Application(application_id=None)
    app.connect("activate", lambda a: setattr(a, "hud", Hud(a)))
    app.run([])


if __name__ == "__main__":
    main()
