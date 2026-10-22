"""Names and places, in one spot (no heavy imports: the waybar module and the CLI read these)."""

import os
import shutil

APP_ID = "io.github.its_danilo.InputForge"
EDITOR_ID = APP_ID + ".Editor"  # the editor over the game (layer-shell)
UNIT = "inputforge.service"
NS_HUD, NS_BAR, NS_EDITOR = "inputforge-hud", "inputforge-bar", "inputforge-editor"  # layer namespaces

# the directory that holds the `inputforge` package (a checkout or ~/.local/share/inputforge)
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
RUNTIME = os.path.join(RUNTIME_DIR, "inputforge")
SOCKET = os.environ.get("INPUTFORGE_SOCKET") or os.path.join(RUNTIME, "engine.sock")  # env: tests
WAYBAR_STATE = os.path.join(RUNTIME, "waybar.json")
BAR_PID = os.path.join(RUNTIME, "bar.pid")
CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "inputforge")
CONFIG = os.path.join(CONFIG_DIR, "config.json")

LAYER_SHELL_LIB = next((p for p in ("/usr/lib/libgtk4-layer-shell.so", "/usr/lib64/libgtk4-layer-shell.so",
                                    "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so") if os.path.exists(p)),
                       "libgtk4-layer-shell.so")

# optional integrations: used when present, ignored otherwise
CALOS = os.path.isdir(os.path.expanduser("~/.local/share/calos"))
CALOS_THEME = os.path.expanduser("~/.config/calos/current/theme")  # colors follow the calOS theme
CALOS_GAMEMODE = os.path.join(RUNTIME_DIR, "calos-gamemode.profile")  # calOS Game Mode is on
AURUM_CLI = shutil.which("openaurum-cli") or os.path.expanduser("~/.local/bin/openaurum-cli")  # OpenAurum


def _render_drivers():
    out = set()
    try:
        for d in os.listdir("/sys/class/drm"):
            if d.startswith("renderD"):
                out.add(os.path.basename(os.path.realpath(f"/sys/class/drm/{d}/device/driver")))
    except OSError:
        pass
    return out


def intel_gpu_env(env):
    """On hybrid laptops (an Intel/AMD iGPU drives the screens + an NVIDIA dGPU), keep GTK and
    its renderer on the iGPU: launched with NVIDIA's GL env (common in Hyprland configs, for games)
    the window rendered on the NVIDIA and every frame crossed GPUs (~20% CPU just hovering,
    laggy UI, dGPU awake). GSK's GL renderer, since GTK's Vulkan one may pick the NVIDIA.
    Nothing changes on single-GPU machines. INPUTFORGE_GPU=default turns it off.
    Call before GTK is imported."""
    mesa = "/usr/share/glvnd/egl_vendor.d/50_mesa.json"
    drivers = _render_drivers()
    if env.get("INPUTFORGE_GPU") == "default" or "nvidia" not in drivers \
            or not drivers & {"i915", "xe", "amdgpu"} or not os.path.exists(mesa):
        return env
    env.pop("LIBVA_DRIVER_NAME", None)
    env["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    env["__EGL_VENDOR_LIBRARY_FILENAMES"] = mesa
    env.setdefault("GSK_RENDERER", "gl")
    return env
