"""InputForge Game Bar: a compact panel over the game (SUPER + CTRL + A with the game on screen).

Lists the armed profile's bindings (the game's, else "Qualquer janela"):
  1-9 / click   start or stop a clicker/macro, run a command, turn a remap on/off
  P / the name  switch to another profile of this game (several can match one window:
                every Roblox game runs in Sober); remembered per game
  ↑↓ + Enter    the same on the selected row
  E             the full window (over a fullscreen game it opens over it, app.py)
  Esc           close (SUPER + CTRL + A again too)
Starting something closes the panel: its countdown shows on the HUD, over the game.

libadwaita, like the window (it follows the GTK theme), in a layer-shell surface on the
overlay layer: the game stays fullscreen under it and stays playable. The keyboard is
on-demand: the panel gets it when it opens (Hyprland focuses on-demand layers on map, so
SUPER + CTRL + A, 1 works), a click on the game takes it back, a click here returns it. It
reports its rectangle (ui_overlay): runs rest only while the pointer is on it.
One instance; the CLI toggles it through BAR_PID.
"""

import json
import os
import signal
import socket
import subprocess
import sys

from inputforge.paths import intel_gpu_env  # noqa: E402

intel_gpu_env(os.environ)  # before GTK loads: render on the iGPU of hybrid laptops (paths.py)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Adw, Gdk, GLib, GLibUnix, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from inputforge import hypr, ipc, keys, paths  # noqa: E402
from inputforge import widgets as W  # noqa: E402
from inputforge.paths import BAR_PID, SOCKET  # noqa: E402

MODE_NAME = {"toggle": "alternar", "hold": "segurar", "once": "uma vez"}
CSS = """
window.inputforge-panel { border-radius: 16px; border: 1px solid alpha(currentColor, 0.14); }
window.inputforge-panel headerbar { border-top-left-radius: 16px; border-top-right-radius: 16px; }
.panel-foot { padding: 4px 14px 14px 18px; }
window.inputforge-panel list.boxed-list > row:selected { background: alpha(currentColor, 0.07); }
"""


class Bar:
    def __init__(self, app):
        self.app = app
        self.buf = b""
        data = ipc.call("get")
        self.cfg, self.st, self.labels = data["config"], data["state"], data["labels"]
        self.profile_id = (self.st.get("armed") or {}).get("id") or "global"
        W.install_css()
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Adw.ApplicationWindow(application=app, default_width=600)
        self.win.add_css_class("inputforge-panel")
        LayerShell.init_for_window(self.win)
        LayerShell.set_namespace(self.win, paths.NS_BAR)
        LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.ON_DEMAND)
        LayerShell.set_exclusive_zone(self.win, -1)
        mon = hypr.focused_monitor()
        self.mon = mon
        if mon:
            monitors = Gdk.Display.get_default().get_monitors()
            for i in range(monitors.get_n_items()):
                m = monitors.get_item(i)
                if m.get_connector() == mon.get("name"):
                    LayerShell.set_monitor(self.win, m)

        tv = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="InputForge")
        self.header.set_title_widget(self.title)
        tv.add_top_bar(self.header)
        # the list keeps its natural height (no empty band under the last row); a scroller
        # only when there are too many rows for the screen
        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.SINGLE,
                                valign=Gtk.Align.START, margin_start=14, margin_end=14, margin_top=6, margin_bottom=10)
        self.list.connect("row-activated", lambda _l, row: self.activate(row.get_index()))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tv.set_content(self.body)
        self.foot = Gtk.Box(spacing=8, css_classes=["panel-foot"])
        tv.add_bottom_bar(self.foot)
        self.win.set_content(tv)

        keyc = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keyc.connect("key-pressed", self.on_key)
        self.win.add_controller(keyc)
        self.render()
        self.subscribe()
        self.win.present()

    # ---- data ------------------------------------------------------------------
    def profile(self):
        return next((p for p in self.cfg["profiles"] if p["id"] == self.profile_id), self.cfg["profiles"][0])

    def rows(self):
        return list(self.profile()["bindings"])

    def running(self, bid):
        return next((r for r in self.st.get("runs", []) if r["binding"] == bid), None)

    def region_name(self, rid):
        return next((r["name"] for p in self.cfg["profiles"] for r in p.get("regions", []) if r["id"] == rid), None)

    def subscribe(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(SOCKET)
        except OSError:
            return
        self.sock.sendall(b'{"id": "sub", "cmd": "subscribe"}\n')
        self.report_rect()
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
        changed = False
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("event") == "state":
                # the runs, and the game's profile when it was switched (choose_profile)
                st = msg["state"]
                runs = [(r["binding"], r["status"]) for r in st.get("runs", [])]
                if runs != [(r["binding"], r["status"]) for r in self.st.get("runs", [])] or st.get("candidates") != self.st.get("candidates"):
                    changed = True
                self.st["runs"] = st.get("runs", [])
                if st.get("candidates"):
                    self.st["candidates"] = st["candidates"]
                    armed = (st.get("armed") or {}).get("id")
                    if armed in [c["id"] for c in st["candidates"]]:
                        self.profile_id = armed
            elif msg.get("event") == "config":
                self.cfg = msg["config"]
                changed = True
        if changed:
            self.render()
        return True

    def report_rect(self):
        """Where the panel is (centered on its monitor): runs rest while the pointer is on it."""
        if not getattr(self, "sock", None) or not self.mon:
            return False
        w, h = self.win.get_width(), self.win.get_height()
        if w <= 1 or h <= 1:  # not allocated yet: once it is
            GLib.timeout_add(50, self.report_rect)
            return False
        mw, mh = int(self.mon["width"] / self.mon["scale"]), int(self.mon["height"] / self.mon["scale"])
        rect = [self.mon["x"] + (mw - w) // 2, self.mon["y"] + (mh - h) // 2, w, h]
        try:
            self.sock.sendall(json.dumps({"id": "sub", "cmd": "ui_overlay", "open": True, "rect": rect}).encode() + b"\n")
        except OSError:
            pass
        return False

    # ---- drawing ---------------------------------------------------------------
    def render(self):
        p = self.profile()
        glob = p["id"] == "global"
        cands = self.st.get("candidates") or []
        focus = (self.st.get("focus") or {}).get("class") or ""
        self.title.set_subtitle("Sem perfil pra este jogo: qualquer janela" if glob and focus else p["name"])

        # the profile switcher, at the header's start
        if getattr(self, "switcher", None):
            self.header.remove(self.switcher)
            self.switcher = None
        if len(cands) > 1:
            btn = Gtk.MenuButton(label=p["name"], tooltip_text="Trocar o perfil (P)", always_show_arrow=True,
                                 css_classes=["flat"])
            pop = Gtk.Popover()
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for c in cands:
                b = Gtk.Button(css_classes=["flat"])
                row = Gtk.Box(spacing=8)
                mark = Gtk.Image.new_from_icon_name("object-select-symbolic") if c["id"] == p["id"] else Gtk.Image()
                mark.set_pixel_size(16)
                row.append(mark)
                row.append(Gtk.Label(label=c["name"] + ("" if c.get("enabled", True) else "  (desligado)"), xalign=0))
                b.set_child(row)
                b.connect("clicked", lambda _b, pid=c["id"]: (pop.popdown(), self.choose(pid)))
                col.append(b)
            pop.set_child(col)
            btn.set_popover(pop)
            self.switcher = btn
            self.header.pack_start(btn)

        sel = self.list.get_selected_row()
        sel_i = sel.get_index() if sel else 0
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        rows = self.rows()
        if not rows:
            empty = Adw.ActionRow(title="Nenhum atalho neste perfil", subtitle="Aperte E para criar no InputForge", activatable=False)
            self.list.append(empty)
        for i, b in enumerate(rows):
            self.list.append(self.row(i, b))
        if rows:
            self.list.select_row(self.list.get_row_at_index(min(sel_i, len(rows) - 1)))
        if (c := self.body.get_first_child()) is not None:
            self.body.remove(c)
            if isinstance(c, Gtk.ScrolledWindow):
                c.set_child(None)
        if len(rows) > 7:
            self.body.append(Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                min_content_height=7 * 66))
        else:
            self.body.append(self.list)
        GLib.idle_add(self.report_rect)

        while (c := self.foot.get_first_child()) is not None:
            self.foot.remove(c)
        hint = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label", "caption"])
        hint.set_markup("<b>1–9</b> liga/desliga · " + ("<b>P</b> perfil · " if len(cands) > 1 else "") + "<b>Esc</b> fecha")
        self.foot.append(hint)
        if self.st.get("runs"):
            self.foot.append(W.pill_button("Parar tudo", self.stop_all))
        edit = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Label(label="Criar perfil" if glob and focus else "Editar"))
        box.append(W.keycap("E"))
        edit.set_child(box)
        edit.connect("clicked", lambda _b: self.edit())
        self.foot.append(edit)

    def row(self, i, b):
        t = b["action"]["type"]
        mode = "" if t in ("remap", "command") else f" · {MODE_NAME[b['mode']]}"
        row = Adw.ActionRow(title=b["name"], subtitle=W.summary(b, self.labels) + mode, activatable=True)
        lead = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        lead.append(W.keycap(str(i + 1) if i < 9 else "·"))
        lead.append(W.type_icon(t, extra=t))
        row.add_prefix(lead)
        if not b["enabled"] and t != "remap":
            row.add_css_class("dim-label")
        if t != "remap":
            row.add_suffix(W.keycaps(b["trigger"], self.labels, self.region_name(b["trigger"].get("region"))))
        run = self.running(b["id"])
        if t == "remap":
            sw = Gtk.Switch(active=b["enabled"], valign=Gtk.Align.CENTER, can_focus=False)
            sw.connect("state-set", lambda _s, _v: (self.activate(i), True)[1])
            row.add_suffix(sw)
        else:
            if run:
                row.add_suffix(Gtk.Label(label="pausado" if run["status"] == "paused" else "rodando", css_classes=["live"]))
            icon = "media-playback-stop-symbolic" if run else "media-playback-start-symbolic"
            row.add_suffix(W.icon_button(icon, "Parar" if run else ("Ligar e iniciar" if not b["enabled"] else "Iniciar"),
                                         lambda: self.activate(i)))
        return row

    # ---- actions ---------------------------------------------------------------
    def activate(self, i):
        rows = self.rows()
        if not 0 <= i < len(rows):
            return
        b = rows[i]
        t = b["action"]["type"]
        if t == "remap" or not b["enabled"]:
            # a remap is on/off; a disabled clicker/macro gets turned on, then starts
            b["enabled"] = not b["enabled"] if t == "remap" else True
            try:
                self.cfg = ipc.call("set_config", config=self.cfg)
            except (ipc.EngineDown, RuntimeError):
                pass
            self.render()
            if t == "remap":
                return
        if self.running(b["id"]):
            try:
                ipc.call("stop", run=b["id"])
            except (ipc.EngineDown, RuntimeError):
                pass
            return
        try:
            ipc.call("run", binding=b["id"])  # countdown on the HUD, then it runs in the game
        except (ipc.EngineDown, RuntimeError):
            return
        self.app.quit()

    def choose(self, pid):
        try:
            ipc.call("choose_profile", profile=pid)
        except (ipc.EngineDown, RuntimeError):
            return
        self.profile_id = pid
        self.render()

    def stop_all(self):
        try:
            ipc.call("stop")
        except (ipc.EngineDown, RuntimeError):
            pass

    def edit(self):
        self.win.set_visible(False)
        env = dict(os.environ, PYTHONPATH=paths.PACKAGE_ROOT)
        subprocess.Popen([sys.executable, "-m", "inputforge.cli", "open", "--full"], env=env,
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.app.quit()

    def on_key(self, ctrl, keyval, keycode, state):
        name = Gdk.keyval_name(keyval) or ""
        if name == "Escape":
            self.app.quit()
            return True
        # number row / numpad by keycode, so it works on Colemak and with Shift (br: ! @ #...)
        evdev = keycode - 8
        digits = {getattr(keys.e, f"KEY_{n}"): n for n in range(1, 10)}
        digits.update({getattr(keys.e, f"KEY_KP{n}"): n for n in range(1, 10)})
        if evdev in digits:
            self.activate(digits[evdev] - 1)
            return True
        if evdev == keys.e.KEY_P:  # next profile of this game
            ids = [c["id"] for c in self.st.get("candidates") or []]
            if len(ids) > 1:
                cur = ids.index(self.profile_id) if self.profile_id in ids else -1
                self.choose(ids[(cur + 1) % len(ids)])
            return True
        if evdev == keys.e.KEY_E:
            self.edit()
            return True
        return False  # arrows, Tab and Enter: the list's own navigation


def running_pid():
    try:
        pid = int(open(BAR_PID).read())
        if b"inputforge.bar" in open(f"/proc/{pid}/cmdline", "rb").read():
            return pid
    except (OSError, ValueError):
        pass
    return None


def main():
    if running_pid():
        return
    os.makedirs(os.path.dirname(BAR_PID), exist_ok=True)
    with open(BAR_PID, "w") as f:
        f.write(str(os.getpid()))
    app = Adw.Application(application_id=None)
    app.connect("activate", lambda a: setattr(a, "bar", Bar(a)))
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: app.quit() or False)
    try:
        app.run([])
    finally:
        try:
            if int(open(BAR_PID).read()) == os.getpid():
                os.unlink(BAR_PID)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
