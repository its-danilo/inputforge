"""InputForge window: GTK 4 + libadwaita (like OpenAurum), a normal window.

Sidebar: Início, Qualquer janela, the game profiles, Ajustes. Content: an Adw.NavigationView,
so a binding's editor slides in over its profile and the back button returns.

The config is edited in place (self.cfg) and saved 350 ms after the last change through the
engine (set_config). The engine echoes every saved config to all windows; an echo of our own
save is ignored, anything else (the Game Bar, a region marked in game, another window)
replaces self.cfg and rebuilds the page.

Opened over a game (`inputforge open --full` with a fullscreen game on screen) it opens beside
it: the CLI passes `--beside ADDRESS`, the game leaves fullscreen while this window is open
(so both can be used, the mouse decides who gets the input) and goes back to fullscreen when
it closes. Single instance (paths.APP_ID): a second launch presents this window.
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
from inputforge.paths import APP_ID  # noqa: E402


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
        self.beside = None  # address of the game this window opened beside
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

    # ---- beside the game ---------------------------------------------------------------
    def open_beside(self, address):
        """Unfullscreen the game this window opened over, and bring this window to the game's
        workspace if it was born elsewhere; on_close puts the fullscreen back."""
        self.beside = address
        GLib.timeout_add(250, self._place_beside)

    def _place_beside(self):
        game = next((c for c in (hypr.jrequest("clients", []) or []) if c.get("address") == self.beside), None)
        if not game:
            self.beside = None
            return False
        mine = next((c for c in hypr.jrequest("clients", []) or [] if c.get("pid") == os.getpid()), None)
        if mine and (mine.get("workspace") or {}).get("name") != (game.get("workspace") or {}).get("name"):
            ws = game["workspace"]["name"]
            hypr.request(f'dispatch hl.dsp.window.move({{workspace = "{ws}", window = "address:{mine["address"]}"}})')
        if game.get("fullscreen"):
            hypr.request(f'dispatch hl.dsp.window.fullscreen({{action = "unset", window = "address:{self.beside}"}})')
        if mine:
            hypr.request(f'dispatch hl.dsp.focus({{window = "address:{mine["address"]}"}})')
        return False

    def on_close(self, *_a):
        self._save_soon.flush()  # an edit still waiting for its debounce
        if self.beside:
            game = next((c for c in (hypr.jrequest("clients", []) or []) if c.get("address") == self.beside), None)
            if game and not game.get("fullscreen"):
                hypr.request(f'dispatch hl.dsp.window.fullscreen({{mode = "fullscreen", action = "set", window = "address:{self.beside}"}})')
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.win = None

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if not self.win:
            self.win = Window(self)
        if "--beside" in args and args.index("--beside") + 1 < len(args):
            self.win.open_beside(args[args.index("--beside") + 1])
        self.win.present()
        return 0


def main(argv=None):
    sys.exit(App().run([sys.argv[0], *(argv or [])]))


if __name__ == "__main__":
    main(sys.argv[1:])
