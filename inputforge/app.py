"""InputForge window: GTK 4 + libadwaita (like OpenAurum), a normal window.

Sidebar: Início, Qualquer janela, the game profiles, Ajustes. Content: an Adw.NavigationView,
so a binding's editor slides in over its profile and the back button returns.

The config is edited in place (self.cfg) and saved 350 ms after the last change through the
engine (set_config). The engine echoes every saved config to all windows; an echo of our own
save is ignored, anything else (the Game Bar, a region marked in game, another window)
replaces self.cfg and rebuilds the page.

Over a fullscreen game (`inputforge open --full`, the Game Bar's E) it can't be a window: a
fullscreen window covers every other one, and taking the game out of fullscreen can crash some
games. So with `--over` it's the same window in a layer-shell surface (LD_PRELOAD by the CLI)
over the game, which stays fullscreen and untouched:
  - the keyboard goes where you click (on-demand): click the game to play, here to type
  - drag the header to move it; Esc or the close button closes it
  - it tells the engine its rectangle (ui_overlay): runs rest while the pointer is on it
Single instance per shape (paths.APP_ID / paths.EDITOR_ID): a second launch presents it.
"""

import os
import sys

from inputforge.paths import intel_gpu_env  # noqa: E402

intel_gpu_env(os.environ)  # before GTK loads: render on the iGPU of hybrid laptops (paths.py)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from inputforge import hypr, ipc  # noqa: E402
from inputforge import widgets as W  # noqa: E402
from inputforge.client import Client  # noqa: E402
from inputforge.editor import Editor  # noqa: E402
from inputforge.pages import Pages  # noqa: E402
from inputforge.paths import APP_ID, EDITOR_ID, NS_EDITOR  # noqa: E402

OVER = "--over" in sys.argv[1:]  # the editor over a fullscreen game (layer-shell)
OVER_CSS = """
window.inputforge-over { border-radius: 14px; border: 1px solid alpha(currentColor, 0.16); }
"""


class Window(Pages, Editor, Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="InputForge")
        self.set_default_size(980, 720)
        self.cfg = None
        self.st = {}
        self.labels = {}
        self.env = {}
        self.route = ("home", None)
        self.live = []  # callables that refresh parts of the page on each engine state
        self.echoes = 0  # our own saves still to come back as "config" events
        self.capturing = False
        self.record_for = None  # (pid, bid, mode) while the recorder runs for a macro
        self.pick_for = None  # (pid, bid) while points are being marked for a clicker
        self.layer_rect = None  # [x, y, w, h] on screen when it's the editor over the game
        self._save_soon = W.later(350, self._flush)

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=260)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # sidebar
        side = Adw.ToolbarView()
        head = Adw.HeaderBar(show_title=False)
        brand = Gtk.Box(spacing=8)
        brand.append(Gtk.Image.new_from_icon_name("input-gaming-symbolic"))
        brand.append(Gtk.Label(label="InputForge", css_classes=["heading"]))
        head.set_title_widget(brand)
        side.add_top_bar(head)
        self.sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar.connect("row-activated", self.on_sidebar)
        scroll = Gtk.ScrolledWindow(child=self.sidebar, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        side.set_content(scroll)
        self.split.set_sidebar(Adw.NavigationPage(title="InputForge", child=side))

        # content
        self.nav = Adw.NavigationView()
        self.split.set_content(Adw.NavigationPage(title="InputForge", child=self.nav))

        W.install_css()
        self.client = Client()
        self.client.on_event = self.on_event
        self.client.on_up = self.on_engine_up
        if OVER:
            self.over_game()
        self.load()
        self.connect("close-request", self.on_close)

    # ---- engine ------------------------------------------------------------------
    def load(self):
        def got(ok, res):
            if not ok:
                self.show_down()
                return
            self.cfg, self.st, self.labels, self.env = res["config"], res["state"], res["labels"], res.get("env", {})
            self.rebuild()
        self.client.request("get", None, got)

    def show_down(self):
        page = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="Motor parado",
                              description="O serviço inputforge não está respondendo.")
        page.set_child(W.pill_button("Iniciar o motor", self.start_engine, suggested=True))
        self.nav.replace([Adw.NavigationPage(title="InputForge", child=page)])

    def start_engine(self):
        import threading

        def run():
            ok = ipc.ensure_engine()
            GLib.idle_add(lambda: (self.client.connect(), self.load()) and False if ok else False)
        threading.Thread(target=run, daemon=True).start()

    def on_engine_up(self, up):
        if up and self.cfg is not None:
            self.load()
        elif not up:
            self.show_down()

    def on_event(self, msg):
        ev = msg.get("event")
        if ev == "state":
            prev = self.st or {}
            self.st = msg["state"]
            if (prev.get("armed") or {}).get("id") != (self.st.get("armed") or {}).get("id"):
                self.fill_sidebar()
            for fn in list(self.live):
                fn()
        elif ev == "config":
            if self.echoes > 0:
                self.echoes -= 1
                return
            self.cfg = msg["config"]
            self.rebuild(keep_editor=True)
        elif ev == "recorded":
            self.on_recorded(msg["steps"])
        elif ev == "pick":
            self.on_pick(msg["points"], msg["done"])

    def call(self, cmd, args=None, done=None, toast_errors=True):
        def reply(ok, res):
            if not ok and toast_errors:
                self.toast(str(res))
            if done:
                done(ok, res)
        self.client.request(cmd, args, reply)

    # ---- config ------------------------------------------------------------------
    def save(self, now=False):
        if now:
            self._flush()
        else:
            self._save_soon()

    def _flush(self):
        self.echoes += 1
        self.call("set_config", {"config": self.cfg})

    def profile(self, pid):
        return next((p for p in self.cfg["profiles"] if p["id"] == pid), None)

    def binding(self, pid, bid):
        p = self.profile(pid)
        return next((b for b in p["bindings"] if b["id"] == bid), None) if p else None

    def regions_for(self, pid):
        own = (self.profile(pid) or {}).get("regions", [])
        return own if pid == "global" else own + self.cfg["profiles"][0].get("regions", [])

    def region_name(self, rid):
        for p in self.cfg["profiles"]:
            for r in p.get("regions", []):
                if r["id"] == rid:
                    return r["name"]
        return None

    def runs_of(self, bid):
        return [r for r in self.st.get("runs", []) if r["binding"] == bid]

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def new_id(self):
        import secrets
        return secrets.token_hex(4)

    # ---- navigation --------------------------------------------------------------
    def fill_sidebar(self):
        lb = self.sidebar
        while (row := lb.get_row_at_index(0)) is not None:
            lb.remove(row)
        armed = (self.st.get("armed") or {}).get("id")

        def add(route, icon, title, subtitle=None, dot=False):
            row = Adw.ActionRow(title=title, activatable=True)
            if subtitle:
                row.set_subtitle(subtitle)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            if dot:
                row.add_suffix(Gtk.Box(css_classes=["live-dot"], valign=Gtk.Align.CENTER, tooltip_text="ativo agora"))
            row.route = route
            lb.append(row)
            if route == self.route:
                lb.select_row(row)

        add(("home", None), "go-home-symbolic", "Início")
        add(("profile", "global"), "applications-internet-symbolic", "Qualquer janela", dot=armed == "global")
        games = self.cfg["profiles"][1:]
        if games:
            hdr = Gtk.Label(label="JOGOS", xalign=0, css_classes=["dim-label", "caption-heading"],
                            margin_start=12, margin_top=12, margin_bottom=4)
            row = Gtk.ListBoxRow(child=hdr, activatable=False, selectable=False)
            row.route = None
            lb.append(row)
        for p in games:
            n = len(p["bindings"])
            add(("profile", p["id"]), "input-gaming-symbolic", p["name"],
                subtitle="ativo agora" if armed == p["id"] else f"{n} atalho{'s' if n != 1 else ''}" + ("" if p["enabled"] else " · desligado"),
                dot=armed == p["id"])
        add(("new-profile", None), "list-add-symbolic", "Novo perfil de jogo")
        add(("settings", None), "emblem-system-symbolic", "Ajustes")

    def on_sidebar(self, lb, row):
        route = getattr(row, "route", None)
        if not route:
            return
        if route[0] == "new-profile":
            self.new_profile_dialog()
            lb.select_row(None)
            return
        self.go(route)

    def go(self, route):
        self.route = route
        self.rebuild()
        self.split.set_show_content(True)

    def rebuild(self, keep_editor=False):
        """Sidebar + the current page; with keep_editor, an open binding editor is rebuilt too."""
        if self.cfg is None:
            return
        editor = None
        top = self.nav.get_visible_page()
        if keep_editor and top is not None and getattr(top, "editor_for", None):
            editor = top.editor_for
        self.fill_sidebar()
        self.live = []
        kind, arg = self.route
        if kind == "profile" and not self.profile(arg):
            self.route = kind, arg = ("home", None)
        page = {"home": self.home_page, "settings": self.settings_page}.get(kind)
        root = page() if page else self.profile_page(arg)
        pages = [root]
        if editor and self.binding(*editor):
            pages.append(self.binding_page(*editor))
        self.nav.replace(pages)

    # ---- over a fullscreen game -----------------------------------------------------------
    def over_game(self):
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk
        from gi.repository import Gtk4LayerShell as LayerShell
        self.LS = LayerShell
        css = Gtk.CssProvider()
        css.load_from_string(OVER_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_css_class("inputforge-over")
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, NS_EDITOR)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)
        LayerShell.set_exclusive_zone(self, -1)
        mon = hypr.focused_monitor() or {"name": None, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}
        self.mon = {"x": mon["x"], "y": mon["y"], "w": int(mon["width"] / mon["scale"]), "h": int(mon["height"] / mon["scale"])}
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            m = monitors.get_item(i)
            if m.get_connector() == mon.get("name"):
                LayerShell.set_monitor(self, m)
        w, h = min(980, self.mon["w"] - 60), min(720, self.mon["h"] - 60)
        self.set_default_size(w, h)
        self.size = (w, h)
        # anchored to the top-left corner with margins, so dragging is just new margins
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        self.margins = [(self.mon["w"] - w) // 2, (self.mon["h"] - h) // 2]
        self.apply_margins()

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.drag_begin)
        drag.connect("drag-update", self.drag_update)
        drag.connect("drag-end", lambda *_a: self.report_rect())
        self.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _kc, _st: (self.close(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.client.on_up = lambda up: (self.report_rect() if up else None, self.on_engine_up(up))
        self.report_rect()  # the client is already connected: on_up only fires on reconnections

    def apply_margins(self):
        self.LS.set_margin(self, self.LS.Edge.LEFT, self.margins[0])
        self.LS.set_margin(self, self.LS.Edge.TOP, self.margins[1])

    def drag_begin(self, gesture, x, y):
        # only from the header area (the rows, buttons and entries keep their own clicks)
        if y > 50:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.drag_from = (hypr.cursor_pos(), list(self.margins))

    def drag_update(self, gesture, dx, dy):
        start = getattr(self, "drag_from", None)
        pos = hypr.cursor_pos()
        if not start or not start[0] or not pos:
            return
        (cx, cy), (mx, my) = start
        w, h = self.size
        self.margins = [max(0, min(self.mon["w"] - w, mx + pos[0] - cx)), max(0, min(self.mon["h"] - h, my + pos[1] - cy))]
        self.apply_margins()

    def report_rect(self):
        """Where this surface is, so the engine rests runs while the pointer is on it."""
        self.drag_from = None
        w, h = self.size
        rect = [self.mon["x"] + self.margins[0], self.mon["y"] + self.margins[1], w, h]
        self.client.request("ui_overlay", {"open": True, "rect": rect})

    def on_close(self, *_a):
        self._save_soon.flush()  # an edit still waiting for its debounce
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=EDITOR_ID if OVER else APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if not self.win:
            self.win = Window(self)
        self.win.present()
        return 0


def main(argv=None):
    sys.exit(App().run([sys.argv[0], *(argv or [])]))


if __name__ == "__main__":
    main(sys.argv[1:])
