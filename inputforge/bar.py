"""InputForge Game Bar: a compact panel over the game (SUPER + CTRL + A with the game on screen).

Lists the armed profile's bindings (the game's, else "Qualquer janela"):
  1-9 / click   start or stop a clicker/macro, run a command, turn a remap on/off
  ↑↓ + Enter    the same on the selected row
  E             edit in the full window (hides the game, like before)
  Esc           close (SUPER + CTRL + A again too)
Starting something closes the panel: its countdown shows on the HUD, over the game.

Layer-shell surface on the overlay layer, so the game stays fullscreen under it, with the
keyboard while open. Native GTK (no WebKit): it opens instantly. One instance; the CLI
toggles it through BAR_PID.
"""

import json
import os
import signal
import socket
import subprocess
import sys

from inputforge.paths import intel_gpu_env  # noqa: E402

intel_gpu_env(os.environ)  # before GTK loads: render on the Intel, see paths.py

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gdk, GLib, GLibUnix, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

from inputforge import hypr, ipc, keys, theme  # noqa: E402
from inputforge import paths  # noqa: E402
from inputforge.paths import BAR_PID, SOCKET  # noqa: E402

NAME = paths.NS_BAR
FONT = "Inter, 'Noto Sans', sans-serif"
ICON = {"clicker": "󰍽", "macro": "󰑮", "remap": "󰌌", "command": "󰆍"}
TYPE_NAME = {"clicker": "Autoclicker", "macro": "Macro", "remap": "Remapear", "command": "Comando"}
MODE_NAME = {"toggle": "alternar", "hold": "segurar", "once": "uma vez"}
MOUSE_NAME = {"BTN_LEFT": "esquerdo", "BTN_RIGHT": "direito", "BTN_MIDDLE": "do meio",
              "BTN_SIDE": "lateral trás", "BTN_EXTRA": "lateral frente"}


def css(c):
    return f"""
    window {{ background: transparent; }}
    .panel {{
        font-family: {FONT};
        background: linear-gradient(165deg, alpha({c['surface2']}, 0.97), alpha({c['bg']}, 0.97));
        border: 1px solid alpha({c['fg']}, 0.14);
        border-radius: 22px;
        padding: 18px;
        box-shadow: 0 30px 80px alpha(black, 0.55);
    }}
    .head {{ margin: 2px 4px 14px 4px; }}
    .logo {{ font-family: 'CaskaydiaMono Nerd Font'; font-size: 17px; color: {c['on_accent']};
             min-width: 34px; min-height: 34px; border-radius: 11px;
             background: linear-gradient(135deg, {c['accent']}, {c['accent2']}); margin-right: 12px; }}
    .brand {{ color: {c['fg']}; font-weight: 800; font-size: 16px; letter-spacing: -0.3px; }}
    .sub {{ color: {c['muted']}; font-size: 12px; font-weight: 500; }}
    .chip {{ color: {c['fg']}; font-size: 12px; font-weight: 650; border-radius: 99px; padding: 4px 11px;
             background: alpha({c['accent']}, 0.14); border: 1px solid alpha({c['accent']}, 0.4); }}
    .row {{
        border-radius: 15px; padding: 10px 12px; margin-bottom: 6px;
        background: alpha({c['fg']}, 0.04); border: 1px solid alpha({c['fg']}, 0.07);
        transition: background 150ms ease, border-color 150ms ease;
    }}
    .row:hover {{ background: alpha({c['fg']}, 0.07); }}
    .row.sel {{ border-color: alpha({c['accent']}, 0.75); background: alpha({c['accent']}, 0.08); }}
    .row.off {{ opacity: 0.5; }}
    .num {{ font-weight: 800; font-size: 12px; color: {c['muted']}; min-width: 24px; min-height: 24px;
            border-radius: 7px; background: alpha({c['fg']}, 0.06); border: 1px solid alpha({c['fg']}, 0.12);
            border-bottom-width: 2px; margin-right: 12px; }}
    .ticon {{ font-family: 'CaskaydiaMono Nerd Font'; font-size: 18px; color: {c['on_accent']};
              min-width: 36px; min-height: 36px; border-radius: 11px; margin-right: 12px;
              background: linear-gradient(135deg, {c['accent']}, {c['accent2']}); }}
    .ticon.remap {{ background: alpha({c['fg']}, 0.85); color: {c['bg']}; }}
    .ticon.command {{ background: alpha({c['fg']}, 0.1); color: {c['fg']}; }}
    .name {{ color: {c['fg']}; font-weight: 700; font-size: 14px; }}
    .detail {{ color: {c['muted']}; font-size: 12px; font-weight: 500; }}
    .key {{ font-weight: 700; font-size: 11.5px; color: {c['fg']}; padding: 3px 8px; border-radius: 7px;
            background: alpha({c['fg']}, 0.08); border: 1px solid alpha({c['fg']}, 0.16); border-bottom-width: 2px;
            margin-left: 10px; }}
    .state {{ font-weight: 700; font-size: 12px; border-radius: 99px; padding: 5px 12px; margin-left: 12px;
              color: {c['muted']}; background: alpha({c['fg']}, 0.06); }}
    .state.run {{ color: {c['on_accent']}; background: linear-gradient(135deg, {c['accent']}, {c['accent2']}); }}
    .state.paused {{ color: {c['fg']}; background: alpha({c['fg']}, 0.14); }}
    .state.on {{ color: {c['fg']}; background: alpha({c['accent']}, 0.22); }}
    .empty {{ color: {c['muted']}; font-size: 13px; padding: 22px; }}
    .foot {{ margin: 12px 4px 0 4px; }}
    .hint {{ color: {c['muted']}; font-size: 11.5px; font-weight: 550; }}
    .hint b {{ color: {c['fg']}; }}
    .btn {{ font-family: {FONT}; font-weight: 650; font-size: 12.5px; color: {c['fg']}; border-radius: 10px;
            padding: 6px 12px; background: alpha({c['fg']}, 0.06); border: 1px solid alpha({c['fg']}, 0.14);
            box-shadow: none; margin-left: 8px; }}
    .btn:hover {{ background: alpha({c['fg']}, 0.11); }}
    .btn.primary {{ color: {c['on_accent']}; border-color: transparent;
                    background: linear-gradient(135deg, {c['accent']}, {c['accent2']}); }}
    """


def summary(b, labels):
    a = b["action"]
    t = a["type"]
    if t == "clicker":
        r = a["rate"]
        rate = f"{r['value']:g} CPS" if r["unit"] == "cps" else f"a cada {r['value']:g} ms"
        what = f"clique {MOUSE_NAME[a['target']]}" if a["target"] in MOUSE_NAME else f"tecla {labels.get(a['target'], a['target'])}"
        where = f"{len(a['points'])} pontos" if a["where"] == "points" else "no cursor"
        text = f"{what} · {rate} · {where}"
        return text[0].upper() + text[1:]
    if t == "macro":
        rep = "até parar" if a["repeat"] == 0 else ("1 vez" if a["repeat"] == 1 else f"{a['repeat']}×")
        return f"{len(a['steps'])} passos · {rep}"
    if t == "remap":
        to = " + ".join(labels.get(c, c) for c in a["to"]) or "nada"
        return f"vira {to}"
    return (a["cmd"] or "sem comando")[:60]


class Bar:
    def __init__(self, app):
        self.app = app
        self.sel = 0
        self.buf = b""
        data = ipc.call("get")
        self.cfg, self.st, self.labels = data["config"], data["state"], data["labels"]
        self.km = keys.Keymap()
        self.colors = theme.colors()
        provider = Gtk.CssProvider()
        provider.load_from_string(css(self.colors))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.profile_id = (self.st.get("armed") or {}).get("id") or "global"

        self.win = Gtk.Window(application=app)
        LayerShell.init_for_window(self.win)
        LayerShell.set_namespace(self.win, NAME)
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

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.add_css_class("panel")
        self.panel.set_size_request(560, -1)
        self.win.set_child(self.panel)
        keyc = Gtk.EventControllerKey()
        keyc.connect("key-pressed", self.on_key)
        self.win.add_controller(keyc)
        self.render()
        self.subscribe()
        self.win.present()

    # ---- data ------------------------------------------------------------------
    def profile(self):
        for p in self.cfg["profiles"]:
            if p["id"] == self.profile_id:
                return p
        return self.cfg["profiles"][0]

    def rows(self):
        return [b for b in self.profile()["bindings"]]

    def running(self, bid):
        for r in self.st.get("runs", []):
            if r["binding"] == bid:
                return r
        return None

    def subscribe(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(SOCKET)
        except OSError:
            return
        self.sock.sendall(b'{"id": "sub", "cmd": "subscribe"}\n')
        # runs pause and hotkeys rest while the panel covers the game (until this disconnects)
        self.sock.sendall(b'{"id": "sub", "cmd": "ui_overlay", "open": true}\n')
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
                # the runs only: while the panel has the keyboard, Hyprland may report no
                # focused window, and the armed profile must not jump to the global one
                self.st["runs"] = msg["state"].get("runs", [])
                changed = True
            elif msg.get("event") == "config":
                self.cfg = msg["config"]
                changed = True
        if changed:
            self.render()
        return True

    # ---- drawing ---------------------------------------------------------------
    def key_label(self, trig):
        if trig.get("kind") == "screen":
            return "tela"
        c = keys.code(trig.get("key"))
        if c is None:
            return None
        return " + ".join([keys.MOD_LABEL[m] for m in trig.get("mods", [])] + [self.km.label(c)])

    def render(self):
        child = self.panel.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.panel.remove(child)
            child = nxt
        p = self.profile()
        glob = p["id"] == "global"

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.add_css_class("head")
        logo = Gtk.Label(label="󰍽")
        logo.add_css_class("logo")
        head.append(logo)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        t = Gtk.Label(label="InputForge", xalign=0)
        t.add_css_class("brand")
        col.append(t)
        focus = (self.st.get("focus") or {}).get("class") or ""
        sub = "Sem perfil pra este jogo: atalhos de qualquer janela" if glob and focus else "Atalhos do perfil ativo"
        s = Gtk.Label(label=sub, xalign=0)
        s.add_css_class("sub")
        col.append(s)
        head.append(col)
        chip = Gtk.Label(label=p["name"], valign=Gtk.Align.CENTER)
        chip.add_css_class("chip")
        head.append(chip)
        self.panel.append(head)

        rows = self.rows()
        self.sel = max(0, min(self.sel, len(rows) - 1))
        if not rows:
            e = Gtk.Label(label="Nenhum atalho neste perfil ainda. Aperte E pra criar no InputForge.")
            e.add_css_class("empty")
            self.panel.append(e)
        scroller_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, b in enumerate(rows):
            scroller_box.append(self.row(i, b))
        if len(rows) > 7:
            sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            sc.set_min_content_height(7 * 64)
            sc.set_child(scroller_box)
            self.panel.append(sc)
        else:
            self.panel.append(scroller_box)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.add_css_class("foot")
        hint = Gtk.Label(xalign=0, hexpand=True, valign=Gtk.Align.CENTER)
        hint.set_markup("<b>1–9</b> liga/desliga · <b>Esc</b> fecha")
        hint.add_css_class("hint")
        foot.append(hint)
        if self.st.get("runs"):
            stop = Gtk.Button(label="Parar tudo")
            stop.add_css_class("btn")
            stop.connect("clicked", lambda *a: self.stop_all())
            foot.append(stop)
        if glob and focus:
            create = Gtk.Button(label="Criar perfil  E")
        else:
            create = Gtk.Button(label="Editar  E")
        create.add_css_class("btn")
        create.add_css_class("primary")
        create.connect("clicked", lambda *a: self.edit())
        foot.append(create)
        self.panel.append(foot)

    def row(self, i, b):
        t = b["action"]["type"]
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.add_css_class("row")
        if i == self.sel:
            box.add_css_class("sel")
        if not b["enabled"]:
            box.add_css_class("off")
        num = Gtk.Label(label=str(i + 1) if i < 9 else "·", valign=Gtk.Align.CENTER)
        num.add_css_class("num")
        box.append(num)
        ic = Gtk.Label(label=ICON[t], valign=Gtk.Align.CENTER)
        ic.add_css_class("ticon")
        ic.add_css_class(t)
        box.append(ic)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        n = Gtk.Label(label=b["name"], xalign=0, ellipsize=3)
        n.add_css_class("name")
        col.append(n)
        mode = "" if t in ("remap", "command") else f" · {MODE_NAME[b['mode']]}"
        d = Gtk.Label(label=summary(b, self.labels) + mode, xalign=0, ellipsize=3)
        d.add_css_class("detail")
        col.append(d)
        box.append(col)
        k = self.key_label(b["trigger"])
        if k:
            kl = Gtk.Label(label=k, valign=Gtk.Align.CENTER)
            kl.add_css_class("key")
            box.append(kl)
        run = self.running(b["id"])
        if t == "remap":
            text, cls = ("ligado", "on") if b["enabled"] else ("desligado", None)
        elif run:
            text, cls = ("pausado", "paused") if run["status"] == "paused" else ("rodando", "run")
        elif t == "command":
            text, cls = "rodar", None
        else:
            text, cls = ("iniciar", None) if b["enabled"] else ("desligado", None)
        st = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
        st.add_css_class("state")
        if cls:
            st.add_css_class(cls)
        box.append(st)
        click = Gtk.GestureClick()
        click.connect("released", lambda *a, i=i: self.activate(i))
        box.add_controller(click)
        return box

    # ---- actions ---------------------------------------------------------------
    def activate(self, i):
        rows = self.rows()
        if not 0 <= i < len(rows):
            return
        b = rows[i]
        self.sel = i
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
        if evdev == keys.e.KEY_E:
            self.edit()
            return True
        if name in ("Down", "Tab"):
            self.sel = min(self.sel + 1, max(0, len(self.rows()) - 1))
            self.render()
            return True
        if name in ("Up", "ISO_Left_Tab"):
            self.sel = max(self.sel - 1, 0)
            self.render()
            return True
        if name in ("Return", "KP_Enter", "space"):
            self.activate(self.sel)
            return True
        return False


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
    app = Gtk.Application(application_id=None)
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
