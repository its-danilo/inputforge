#!/usr/bin/env bash
# InputForge installer: everything goes to ~/.local (your user only);
# sudo only if /dev/uinput still needs the udev rule (virtual keyboard/mouse).
#
#   ./install.sh               install or update (and start the engine)
#   ./install.sh --check       only check the dependencies
#   ./install.sh --no-udev     install without touching /etc (no sudo)
#   ./install.sh --uninstall   remove everything this script installed
set -euo pipefail

APP_ID=io.github.its_danilo.InputForge
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOCAL=$HOME/.local
SHARE=$LOCAL/share/inputforge
BIN=$LOCAL/bin
DESKTOP=$LOCAL/share/applications/$APP_ID.desktop
ICON=$LOCAL/share/icons/hicolor/scalable/apps/$APP_ID.svg
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
UNIT_NAME=inputforge.service
UNIT=$UNIT_DIR/$UNIT_NAME
RULE=/etc/udev/rules.d/60-inputforge.rules
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}/inputforge

bold=$'\e[1m' green=$'\e[32m' yellow=$'\e[33m' red=$'\e[31m' reset=$'\e[0m'
[[ -t 1 ]] || bold='' green='' yellow='' red='' reset=''
ok()   { echo "${green}✓${reset} $*"; }
warn() { echo "${yellow}!${reset} $*"; }
fail() { echo "${red}✗${reset} $*"; }
step() { echo; echo "${bold}$*${reset}"; }

packages() {
  local id=""
  [[ -r /etc/os-release ]] && id=$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")
  case " $id " in
    *" arch "*) echo "sudo pacman -S --needed python-gobject gtk4 libadwaita gtk4-layer-shell python-evdev grim libxkbcommon" ;;
    *)          echo "(instale: Python 3, PyGObject, GTK 4, libadwaita 1.5+, gtk4-layer-shell, python-evdev, grim, libxkbcommon)" ;;
  esac
}

uinput_rule_present() {
  # any rule that already gives the logged-in user /dev/uinput (Steam's, calOS's, ours)
  grep -lsE 'KERNEL=="uinput".*uaccess' /etc/udev/rules.d/*.rules /usr/lib/udev/rules.d/*.rules >/dev/null
}

do_check() {
  step "Dependências"
  local missing=0
  if command -v python3 >/dev/null; then
    ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
  else
    fail "python3 não encontrado"; missing=1
  fi
  if python3 - 2>/dev/null <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Adw, Gtk, Gtk4LayerShell
assert (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) >= (1, 5)
PY
  then
    ok "GTK 4 + libadwaita + gtk4-layer-shell"
  else
    fail "falta PyGObject, GTK 4, libadwaita 1.5+ ou gtk4-layer-shell"; missing=1
  fi
  if python3 -c "import evdev" 2>/dev/null; then ok "python-evdev"; else fail "falta python-evdev"; missing=1; fi
  if python3 -c "import ctypes; ctypes.CDLL('libxkbcommon.so.0')" 2>/dev/null; then ok "libxkbcommon"; else fail "falta libxkbcommon"; missing=1; fi
  if command -v grim >/dev/null; then ok "grim (observador de tela)"; else fail "falta grim"; missing=1; fi
  if command -v hyprctl >/dev/null; then ok "Hyprland"; else warn "hyprctl não encontrado: o InputForge foi feito para o Hyprland (foco, cursor, layouts)"; fi
  if id -nG | tr ' ' '\n' | grep -qx input; then
    ok "usuário no grupo input (ler os atalhos)"
  else
    warn "seu usuário não está no grupo input: sudo usermod -aG input \$USER  (e entre de novo na sessão)"
  fi
  if (( missing )); then
    echo; echo "Para instalar o que falta:  $(packages)"
    return 1
  fi
}

install_udev() {
  if [[ -w /dev/uinput ]] && uinput_rule_present; then
    ok "/dev/uinput já liberado para o seu usuário"
    return
  fi
  echo "A regra udev deixa o seu usuário criar teclado/mouse virtuais (/dev/uinput)."
  echo "Vai pedir a senha do sudo para copiar $RULE:"
  sudo install -Dm644 "$SRC/data/60-inputforge.rules" "$RULE"
  sudo udevadm control --reload-rules
  sudo udevadm trigger --action=change --subsystem-match=misc
  ok "regra udev instalada ($RULE)"
}

migrate_calos() {
  # the first InputForge lived inside calOS (~/.local/share/calos/inputforge): carry the
  # config over once and stop its old unit so two engines never read the keyboard
  local old_cfg=${XDG_CONFIG_HOME:-$HOME/.config}/calos/inputforge/config.json
  if [[ -f $old_cfg && ! -f $CONFIG/config.json ]]; then
    mkdir -p "$CONFIG"
    cp "$old_cfg" "$CONFIG/config.json"
    ok "config do calOS copiada → ${CONFIG/#$HOME/\~}/config.json"
  fi
  if command -v systemctl >/dev/null && systemctl --user cat calos-inputforge.service &>/dev/null; then
    systemctl --user disable --now calos-inputforge.service 2>/dev/null || true
    ok "serviço antigo calos-inputforge.service desligado"
  fi
}

do_install() {
  local udev=1
  [[ ${1:-} == --no-udev ]] && udev=0
  do_check || { echo; fail "Instale as dependências acima e rode ./install.sh de novo."; exit 1; }

  step "Instalando em ${LOCAL/#$HOME/\~}"
  rm -rf "$SHARE"
  mkdir -p "$SHARE/inputforge" "$BIN"
  cp "$SRC"/inputforge/*.py "$SHARE/inputforge/"
  ok "programa → ${SHARE/#$HOME/\~}"
  install -m755 "$SRC/bin/inputforge" "$BIN/inputforge"
  ok "comando → ${BIN/#$HOME/\~}/inputforge"
  # absolute Exec: desktop launchers often don't have ~/.local/bin in their PATH
  mkdir -p "$(dirname "$DESKTOP")"
  # and an absolute Icon: a launcher already running may not rescan the icon theme for a new name
  sed -e "s|^Exec=inputforge|Exec=$BIN/inputforge|" -e "s|^Icon=.*|Icon=$ICON|" "$SRC/data/$APP_ID.desktop" >"$DESKTOP"
  install -Dm644 "$SRC/data/$APP_ID.svg" "$ICON"
  ok "atalho no menu de aplicativos (InputForge)"
  install -Dm644 "$SRC/data/inputforge.service" "$UNIT"
  migrate_calos

  if (( udev )); then
    step "Teclado e mouse virtuais"
    install_udev
  fi

  if command -v systemctl >/dev/null; then
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME" &>/dev/null
    systemctl --user restart "$UNIT_NAME"  # an update: the engine runs the new code
    ok "motor ligado ($UNIT_NAME, inicia junto com a sessão)"
  fi

  step "Pronto"
  case ":$PATH:" in
    *":$BIN:"*) ;;
    *) warn "${BIN/#$HOME/\~} não está no seu PATH: adicione  export PATH=\"\$HOME/.local/bin:\$PATH\"  ao seu shell" ;;
  esac
  echo "Abra o ${bold}InputForge${reset} pelo menu de aplicativos ou rode:  inputforge"
  echo "Atalho de teclado, regras de janela e waybar: veja a seção \"Integração\" do README."
}

do_uninstall() {
  step "Removendo o InputForge"
  if command -v systemctl >/dev/null; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  fi
  rm -f "$UNIT" "$DESKTOP" "$ICON" "$BIN/inputforge"
  rm -rf "$SHARE"
  command -v systemctl >/dev/null && systemctl --user daemon-reload 2>/dev/null || true
  ok "programa, comando, atalho, ícone e serviço removidos"
  if [[ -f $RULE ]]; then
    echo "Removendo a regra udev (pede a senha do sudo):"
    sudo rm -f "$RULE" && sudo udevadm control --reload-rules && ok "regra udev removida"
  fi
  if [[ -d $CONFIG ]]; then
    read -rp "Apagar também os seus perfis, atalhos e regiões (${CONFIG/#$HOME/\~})? [s/N] " ans
    if [[ $ans == [sSyY]* ]]; then
      rm -rf "$CONFIG"
      ok "configuração apagada"
    else
      ok "configuração mantida"
    fi
  fi
}

case ${1:-} in
  ""|--no-udev) do_install "${1:-}" ;;
  --check)      do_check ;;
  --uninstall)  do_uninstall ;;
  -h|--help)    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' ;;
  *)            echo "uso: ./install.sh [--check | --no-udev | --uninstall]" >&2; exit 2 ;;
esac
