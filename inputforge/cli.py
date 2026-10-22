"""InputForge command line (bin/inputforge).

  inputforge [open]          the window. With a game on screen (special:game or a fullscreen
                             window): the Game Bar over it instead (toggle; bar.py)
  inputforge open --full     the window even with a game on screen: over it (the Game Bar's E)
  inputforge engine          the engine (what inputforge.service runs)
  inputforge toggle NAME     start/stop a binding by name or id (e.g. from a compositor bind)
  inputforge stop            stop every run          panic   stop everything + release keys
  inputforge status          engine state (JSON)     reload  re-read the config file
  inputforge theme           recolor the window/HUD (calOS: calos-theme-set calls it)
  inputforge waybar          a waybar custom module (signal 14)
  inputforge binds           bindings as `hyprctl binds -j` objects (calOS: SUPER + K > Jogos)
  inputforge hud             run the HUD by hand (debug)

Config: ~/.config/inputforge/config.json (written by the engine; the window edits it).
Needs python-evdev, the user in `input` (read keys), /dev/uinput writable (uaccess rule,
data/60-inputforge.rules), Hyprland, GTK 4 + libadwaita, gtk4-layer-shell and grim.
"""

import json
import os
import signal
import subprocess
import sys

from inputforge import paths  # noqa: E402


def notify(text):
    subprocess.run(["notify-send", "-a", "InputForge", "-t", "2500", "󰍽  InputForge", text],
                   stderr=subprocess.DEVNULL)


def open_window(full=False):
    """With a game on screen (special:game or a fullscreen window): the Game Bar over it
    (toggle). With --full (the bar's E), or with no game: the window. Over a fullscreen game
    the window is a layer over it (app.py --over): taking a game out of fullscreen can crash it.
    The game is never hidden or shown from here: that's the user's own key (on calOS SUPER + G,
    which Game Mode's lock and the keyboard lights follow)."""
    from inputforge import hypr, ipc
    if not ipc.ensure_engine():
        notify(f"O motor não iniciou: veja journalctl --user -u {paths.UNIT}")
    w = hypr.active_window()
    in_game = hypr.game_on_screen() or bool(w and w["fullscreen"] and w["class"] != paths.APP_ID)
    if in_game and not full:
        toggle_bar()
        return
    game = w if w and w["fullscreen"] and w["class"] != paths.APP_ID else next(
        (c for c in hypr.jrequest("clients", []) or []
         if (c.get("workspace") or {}).get("name") == "special:game" and c.get("fullscreen")), None) if in_game else None
    if game:  # a fullscreen game: the window goes over it as a layer, the game stays untouched
        layer_exec("inputforge.app", "--over")
    from inputforge import app
    app.main()


def layer_exec(module, *args):
    """Become `python -m module` with gtk4-layer-shell preloaded (before libwayland)."""
    env = dict(os.environ)
    env["LD_PRELOAD"] = paths.LAYER_SHELL_LIB
    env["PYTHONPATH"] = paths.PACKAGE_ROOT
    os.execve(sys.executable, [sys.executable, "-m", module, *args], env)


def toggle_bar():
    try:
        pid = int(open(paths.BAR_PID).read())
        if b"inputforge.bar" in open(f"/proc/{pid}/cmdline", "rb").read():
            os.kill(pid, signal.SIGTERM)
            return
    except (OSError, ValueError):
        pass
    layer_exec("inputforge.bar")


def waybar():
    try:
        with open(paths.WAYBAR_STATE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"text": "", "tooltip": "", "class": "idle"}
    if not os.path.exists(paths.SOCKET):
        data = {"text": "", "tooltip": "", "class": "idle"}
    print(json.dumps(data, ensure_ascii=False))


def binds():
    """SUPER + K reads `hyprctl binds -j`; these rows go into its Jogos tab."""
    from inputforge import config, keys
    try:
        with open(paths.CONFIG) as f:
            cfg = config.normalize(json.load(f))
    except (OSError, ValueError):
        print("[]")
        return
    km = keys.Keymap()
    mode_text = {"toggle": "liga/desliga", "hold": "enquanto segura", "once": "uma vez"}

    def hkey(name):
        c = keys.code(name)
        if c in keys.HYPR_MOUSE:
            return keys.HYPR_MOUSE[c]
        if c in keys.HYPR_KEY:
            return keys.HYPR_KEY[c]
        label = km.label(c)
        return label.lower() if len(label) == 1 and label.isalpha() else label

    def row(trig, text):
        mask = sum(keys.HYPR_MODMASK[m] for m in trig["mods"])
        if trig["hold_ms"]:
            text += " (segurar " + f"{trig['hold_ms'] / 1000:g}".replace(".", ",") + " s)"
        return {"modmask": mask, "key": hkey(trig["key"]), "submap": "", "has_description": True,
                "description": f"Jogos: InputForge · {text}"}

    out = []
    for p in cfg["profiles"]:
        if not p["enabled"] or (p["id"] == "global" and not cfg["settings"]["global_enabled"]):
            continue
        where = "qualquer janela" if p["id"] == "global" else p["name"]
        for b in p["bindings"]:
            if not b["enabled"] or (b["trigger"]["kind"] == "screen" and b["action"]["type"] != "remap"):
                continue  # screen triggers have no key to list
            if b["action"]["type"] == "remap":
                to = " + ".join(km.label(keys.code(t)) for t in b["action"]["to"]) or "nada"
                text = f"{where}: vira {to}"
            else:
                text = f"{where}: {b['name']} ({mode_text[b['mode']]})"
            out.append(row(b["trigger"], text))
    out.append(row(cfg["settings"]["panic"], "parar tudo"))
    print(json.dumps(out, ensure_ascii=False))


def call(cmd, quiet=False, **args):
    from inputforge import ipc
    try:
        return ipc.call(cmd, **args)
    except ipc.EngineDown:
        if not quiet:
            print(f"InputForge: o motor não está rodando (systemctl --user start {paths.UNIT})", file=sys.stderr)
            sys.exit(1)
    except RuntimeError as ex:
        print(f"InputForge: {ex}", file=sys.stderr)
        sys.exit(1)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "open"
    if cmd == "open":
        open_window(full="--full" in sys.argv[2:])
    elif cmd == "engine":
        from inputforge import engine
        engine.main()
    elif cmd == "hud":
        from inputforge import hud
        hud.main()
    elif cmd == "waybar":
        waybar()
    elif cmd == "binds":
        binds()
    elif cmd == "theme":
        call("theme", quiet=True)
    elif cmd == "status":
        print(json.dumps(call("state"), indent=1, ensure_ascii=False))
    elif cmd in ("stop", "panic", "reload"):
        call(cmd)
    elif cmd == "toggle" and len(sys.argv) > 2:
        call("toggle", binding=" ".join(sys.argv[2:]))
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
