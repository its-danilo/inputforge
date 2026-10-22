"""InputForge window: GTK4 + WebKitGTK 6 hosting ui/ (HTML/CSS/JS), bridged to the engine.

JS -> here: window.webkit.messageHandlers.inputforge.postMessage(JSON {id, cmd, ...}).
Most commands go straight to the engine socket (one subscribed connection, so replies
and events share it); a few are local (theme_colors, close, engine_start, return_to_game).
Here -> JS: __ifReply(id, ok, result) and __ifEvent(msg).

Two shapes:
  window   a normal floating window (launcher, desktop): paths.APP_ID
  --overlay the editor OVER the game (Game Bar > E): a layer-shell surface, so the game stays
           fullscreen and Game Mode / the Aurum don't change. Its own app id. It tells the
           engine (ui_overlay) so runs pause and hotkeys rest while it's open: a clicker
           would otherwise click inside the editor. Needs LD_PRELOAD=libgtk4-layer-shell.so.
InputForge never hides or shows the game itself (that's SUPER + G's job): doing it from
here flipped Game Mode's lock, and the Aurum with it, at every click.
Opaque, in the theme's background color (the glass effects live inside the page).
"""

import json
import os
import socket
import sys
import threading

from inputforge.paths import intel_gpu_env  # noqa: E402

intel_gpu_env(os.environ)  # before GTK loads: render on the Intel, see paths.py

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

from inputforge import hypr, ipc, theme  # noqa: E402
from inputforge.paths import SOCKET  # noqa: E402

from inputforge.paths import APP_ID, NS_EDITOR  # noqa: E402
from inputforge.paths import EDITOR_ID as OVERLAY_ID  # noqa: E402
OVERLAY = "--overlay" in sys.argv[1:]
UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
DEBUG = bool(os.environ.get("INPUTFORGE_DEBUG"))
SUB_ID = "subscribe"


class Bridge:
    """The engine connection: requests out, replies + events in (GLib main loop)."""

    def __init__(self, on_message, on_up):
        self.on_message = on_message
        self.on_up = on_up
        self.sock = None
        self.buf = b""
        self.watch = None
        self.up = False
        self.connect()

    def connect(self):
        if self.sock:
            return True
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(SOCKET)
        except OSError:
            s.close()
            self.set_up(False)
            return False
        self.sock = s
        self.buf = b""
        s.sendall(json.dumps({"id": SUB_ID, "cmd": "subscribe"}).encode() + b"\n")
        if OVERLAY:  # runs pause and hotkeys rest while the editor covers the game
            s.sendall(json.dumps({"id": SUB_ID, "cmd": "ui_overlay", "open": True}).encode() + b"\n")
        s.setblocking(False)
        self.watch = GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT,
                                       GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR, self.readable)
        self.set_up(True)
        return True

    def set_up(self, up):
        if up != self.up:
            self.up = up
            self.on_up(up)

    def drop(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.sock:
            self.sock.close()
            self.sock = None
        self.set_up(False)
        GLib.timeout_add(2000, self.retry)

    def retry(self):
        return not self.connect()  # keep trying every 2 s until it's back

    def readable(self, fd, cond):
        try:
            chunk = self.sock.recv(1 << 20)
        except BlockingIOError:
            return True
        except OSError:
            chunk = b""
        if not chunk:
            self.watch = None
            self.drop()
            return False
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == SUB_ID:
                continue
            self.on_message(msg)
        return True

    def send(self, msg):
        if not self.sock and not self.connect():
            return False
        try:
            self.sock.setblocking(True)
            self.sock.sendall(json.dumps(msg).encode() + b"\n")
            self.sock.setblocking(False)
            return True
        except OSError:
            self.drop()
            return False


class Window:
    def __init__(self, app):
        self.app = app
        self.win = Gtk.ApplicationWindow(application=app, title="InputForge")
        self.win.set_default_size(1280, 820)
        self.win.set_decorated(False)  # Hyprland draws the border; the page has its close button
        if OVERLAY:
            self.layer_shell()
        bg = theme.colors()["bg"]
        css = Gtk.CssProvider()
        css.load_from_string(f"window, window.background {{ background: {bg}; }}")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        manager = WebKit.UserContentManager()
        manager.register_script_message_handler("inputforge", None)
        manager.connect("script-message-received::inputforge", self.on_script)
        self.view = WebKit.WebView(user_content_manager=manager)
        rgba = Gdk.RGBA()
        rgba.parse(bg)
        # opaque: with a transparent view the windows behind showed through the page
        # (WebKit composites the transparent base even under an opaque body), unblurred
        self.view.set_background_color(rgba)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(DEBUG)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_enable_back_forward_navigation_gestures(False)
        if not DEBUG:
            self.view.connect("context-menu", lambda *a: True)
        self.view.connect("decide-policy", self.on_policy)
        self.view.load_uri("file://" + UI + ("?overlay" if OVERLAY else ""))
        self.win.set_child(self.view)

        self.bridge = Bridge(self.on_engine, self.on_engine_up)
        self.win.present()

    def layer_shell(self):
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as LayerShell
        LayerShell.init_for_window(self.win)
        LayerShell.set_namespace(self.win, NS_EDITOR)
        LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.EXCLUSIVE)
        LayerShell.set_exclusive_zone(self.win, -1)
        mon = hypr.focused_monitor()
        if mon:
            monitors = Gdk.Display.get_default().get_monitors()
            for i in range(monitors.get_n_items()):
                m = monitors.get_item(i)
                if m.get_connector() == mon.get("name"):
                    LayerShell.set_monitor(self.win, m)
            # centered (no anchors), with room around it on small screens
            self.win.set_default_size(min(1280, int(mon["width"] / mon["scale"]) - 80),
                                      min(820, int(mon["height"] / mon["scale"]) - 80))

    # JS -> Python ---------------------------------------------------------------
    def on_script(self, manager, value):
        try:
            msg = json.loads(value.to_string())
        except (ValueError, AttributeError):
            return
        rid = msg.get("id")
        cmd = msg.get("cmd")
        if cmd == "theme_colors":
            self.reply(rid, True, theme.colors())
        elif cmd == "close":
            self.reply(rid, True, True)
            self.win.close()
        elif cmd == "return_to_game":
            # a run (or a region pick) started from here: get out of the way, its countdown
            # shows on the HUD. The game is left as it is (over it, it's right there).
            self.reply(rid, True, True)
            self.win.close()
        elif cmd == "engine_start":
            def start():
                ok = ipc.ensure_engine()
                GLib.idle_add(lambda: (self.bridge.connect(), self.reply(rid, ok, ok or "não iniciou"))[1] and False)
            threading.Thread(target=start, daemon=True).start()
        elif not self.bridge.send(msg):
            self.reply(rid, False, "motor parado")

    def on_policy(self, view, decision, kind):
        # never navigate away from the app page (links open in the browser)
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith("file://" + os.path.dirname(UI)):
                decision.ignore()
                Gio.AppInfo.launch_default_for_uri(uri, None)
                return True
        return False

    # Python -> JS ---------------------------------------------------------------
    def js(self, code):
        self.view.evaluate_javascript(code, -1, None, None, None, None, None)

    def reply(self, rid, ok, result):
        self.js(f"window.__ifReply && window.__ifReply({json.dumps(rid)}, {json.dumps(bool(ok))}, {json.dumps(result)})")

    def on_engine(self, msg):
        if "event" in msg:
            self.js(f"window.__ifEvent && window.__ifEvent({json.dumps(msg)})")
        elif "id" in msg:
            self.reply(msg["id"], msg.get("ok"), msg.get("result") if msg.get("ok") else msg.get("error"))

    def on_engine_up(self, up):
        self.js(f"window.__ifEvent && window.__ifEvent({json.dumps({'event': 'engine', 'up': up})})")


def main():
    app = Gtk.Application(application_id=OVERLAY_ID if OVERLAY else APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    state = {}

    def activate(a):
        if state.get("w"):
            state["w"].win.present()
            return
        state["w"] = Window(a)

    app.connect("activate", activate)
    sys.exit(app.run([sys.argv[0]]))


if __name__ == "__main__":
    main()
