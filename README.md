# InputForge

Autoclicker, macros, rotações que **olham a tela** e remapeamento de teclas **por jogo**, para
Linux com Hyprland. Um motor em segundo plano lê o teclado e o mouse direto do kernel
(evdev) e "digita" por teclado e mouse virtuais (uinput). Por isso os atalhos funcionam no
meio de um jogo em tela cheia, até com o compositor bloqueando os atalhos dele.

- **Autoclicker:** CPS ou intervalo, qualquer botão (ou uma tecla: "auto-tecla"), no cursor
  ou revezando pontos marcados na tela, clique duplo, variação humana, limite por cliques ou
  por tempo.
- **Macros:** teclas, combos, cliques, esperas (com variação aleatória), movimento relativo
  (câmera 3D) ou absoluto, rolagem, texto digitado (com acentos, pelo layout ativo) e
  comandos. Repetição, velocidade e um **gravador** que transforma o que você joga em passos.
- **Observador de tela:** marque um pedaço da tela (um slot de skill, uma barra de vida, um
  botão) e use **"Esperar tela"**, **"Se a tela…"** ou um **gatilho por tela**. Exemplo:
  "use cada skill assim que ela ficar pronta", sem precisar saber a recarga nem o ping.
- **Remapear:** uma tecla ou botão do mouse vira outra coisa (ou nada) só enquanto o jogo está
  em foco.
- **Perfis por jogo:** ligam sozinhos quando a janela do jogo ganha foco (classe/título por
  regex). Um perfil "Qualquer janela" vale no resto. Tudo o que roda **pausa quando o jogo
  perde o foco** e volta quando ele volta, e **segurar Esc** para tudo.
- **No jogo:** um painel rápido por cima do jogo (1–9 liga e desliga, troca de perfil), a
  janela completa por cima do jogo sem tirá-lo da tela cheia e um HUD discreto com o que
  está rodando, as contagens e a gravação.
- **Vários perfis para a mesma janela:** todos os jogos do Roblox rodam no Sober, por exemplo.
  Escolha qual vale no painel (clique no nome do perfil ou aperte P) ou na tela inicial; a
  escolha fica lembrada por jogo.

A janela é GTK 4 + libadwaita e segue o tema do sistema; o HUD e o painel rápido são GTK 4 +
layer-shell. Tudo em português.

## Requisitos

- Hyprland (foco de janelas, cursor, layouts de teclado)
- Python 3, PyGObject, GTK 4, libadwaita 1.5+, gtk4-layer-shell
- python-evdev, libxkbcommon, grim (observador de tela)
- seu usuário no grupo `input` (para ler os atalhos) e `/dev/uinput` liberado (o instalador
  cuida da regra udev)

No Arch:

```sh
sudo pacman -S --needed python-gobject gtk4 libadwaita gtk4-layer-shell python-evdev grim libxkbcommon
sudo usermod -aG input $USER   # e entre de novo na sessão
```

## Instalação

```sh
git clone https://github.com/its-danilo/inputforge ~/Projects/inputforge
cd ~/Projects/inputforge
./install.sh            # ou --check, --no-udev, --uninstall
```

Tudo vai para `~/.local` (`~/.local/share/inputforge`, o comando `~/.local/bin/inputforge`, o
atalho do launcher e o serviço `inputforge.service`, que já fica ligado e inicia com a sessão).
O `sudo` só é pedido se o `/dev/uinput` ainda não estiver liberado para o seu usuário.
Para atualizar: `git pull && ./install.sh`.

## Uso

Abra o **InputForge** pelo launcher (ou `inputforge`). Na tela inicial tem o **clique
rápido** (o F8 liga e desliga em qualquer janela); o resto vive nos **perfis**:

1. Com o jogo aberto, a tela inicial mostra **"Janela detectada → Criar perfil"**.
2. No perfil, **Novo atalho**: Autoclicker, Macro, Remapear ou Comando. Escolha o gatilho
   (tecla, combo, botão lateral do mouse… ou **Tela**) e o modo: **Alternar** (aperta liga,
   aperta de novo desliga), **Segurar** (roda enquanto segura) ou **Uma vez**.
3. No jogo, aperte o atalho. O HUD mostra o que está rodando.

**Dentro do jogo**, o atalho que abre o InputForge (veja Integração) abre o **painel rápido**
por cima do jogo: **1–9** ou clique liga e desliga, **P** (ou clicar no nome do perfil) troca
o perfil, **E** abre a janela completa e **Esc** fecha. Enquanto o painel está aberto, o que
está rodando pausa e os atalhos descansam (um autoclicker clicaria dentro dele).

Com um jogo em **tela cheia**, a **janela completa** abre por cima dele sem tirá-lo da tela
cheia (tirar pode derrubar alguns jogos). Ela é uma camada (layer), não uma janela comum: o
teclado vai para quem você clicar (clique no jogo para jogar, no InputForge para editar),
arraste pela barra do título para mover, e Esc ou o × fecham. O que roda pausa só enquanto o
mouse está em cima dela. Sem jogo em tela cheia, ela é uma janela normal.

**Iniciar pela janela** (▶): uma contagem aparece no HUD (tempo de levar o mouse até o alvo) e
o atalho passa a valer na janela que estiver em foco quando ela terminar.

### Observador de tela

No perfil, **Regiões da tela → Nova região**: clique nos dois cantos da região, dentro do
jogo, **enquanto ela mostra o estado que interessa** (a skill pronta, o botão visível…). Esse
estado vira a **referência**. Depois:

- **Esperar tela** (passo de macro): até a região ficar *igual à referência*, *diferente* ou
  *mudar*, com tempo máximo (depois segue ou para a macro).
- **Se a tela…** (passo de macro): se a condição vale, segue; **senão pula N passos**.
- **Gatilho Tela** (no atalho): dispara sozinho quando a região fica assim.

Rotação de skills, por exemplo, em loop: *Se Slot E igual → Tecla E → Esperar 700 ms*, e o
mesmo para R, F, C… Se o jogo ignorar um toque (lag, outra skill em uso), o slot continua
"pronto" e o InputForge tenta de novo na volta seguinte.

Dicas: prefira elementos da interface (o cenário 3D muda o tempo todo), regiões pequenas, e
ajuste a **tolerância** se dispara cedo/tarde demais. O observador captura a tela com o `grim`
cerca de 15 vezes por segundo, só enquanto algo o usa.

## Integração

O instalador não mexe na configuração do Hyprland nem da waybar. Exemplos (o Hyprland
nem sempre tem `~/.local/bin` no PATH; com o caminho completo funciona sempre):

```lua
-- hyprland.lua (config em Lua)
hl.bind("SUPER + CTRL + A", hl.dsp.exec_cmd("~/.local/bin/inputforge"), { description = "Apps: InputForge" })
hl.window_rule({ match = { class = "^(io.github.its_danilo.InputForge)$" },
                 center = true, float = true, size = "980 720" })
hl.layer_rule({ match = { namespace = "inputforge-hud" }, blur = true, ignore_alpha = 0.3 })
hl.layer_rule({ match = { namespace = "inputforge-bar" }, blur = true, ignore_alpha = 0.3 })
```

```ini
# hyprland.conf
bind = SUPER CTRL, A, exec, ~/.local/bin/inputforge
windowrule = float, class:^(io.github.its_danilo.InputForge)$
windowrule = center, class:^(io.github.its_danilo.InputForge)$
windowrule = size 980 720, class:^(io.github.its_danilo.InputForge)$
layerrule = blur, inputforge-hud
layerrule = blur, inputforge-bar
```

Se o seu compositor tiver um modo "jogo" que restringe os atalhos (um submap), repita lá o
bind do InputForge. O painel abre quando o jogo está em tela cheia (ou num workspace
especial chamado `game`).

Waybar: um ícone só enquanto algo roda (clique abre, botão direito para tudo):

```jsonc
"custom/inputforge": {
  "exec": "~/.local/bin/inputforge waybar", "return-type": "json", "signal": 14,
  "on-click": "~/.local/bin/inputforge", "on-click-right": "~/.local/bin/inputforge stop"
}
```

Cores: a janela segue o tema GTK do sistema. O HUD e o painel rápido seguem o tema do calOS
(rode `~/.local/bin/inputforge theme` ao trocar de tema); fora dele, uma paleta escura
própria, e `~/.config/inputforge/theme.json` sobrescreve qualquer cor:
`{"accent": "#7aa2f7", "background": "#16161e", "foreground": "#c0caf5"}`.

Opcional: com o [OpenAurum](https://github.com/its-danilo/openaurum) instalado, a tecla do
atalho que está rodando acende no Pichau Aurum V60.

## Comandos

```text
inputforge                 abre a janela (com um jogo na tela: o painel rápido)
inputforge open --full     o editor (por cima do jogo, se ele estiver na tela)
inputforge toggle "Nome"   liga/desliga um atalho pelo nome (para usar num bind do compositor)
inputforge stop | panic    para tudo / para tudo e solta todas as teclas
inputforge status          estado do motor (JSON)
inputforge reload          relê o config.json
inputforge theme           recolore a janela e o HUD
inputforge waybar | binds  módulo da waybar / atalhos como `hyprctl binds -j`
```

## Segurança e privacidade

- O motor só lê o teclado enquanto algo precisa: um perfil com atalhos ativo, algo rodando,
  o gravador ou uma captura de tecla. Nada do que você digita é salvo; o gravador só grava
  quando você manda.
- Só **prende** um dispositivo (EVIOCGRAB) para remapear ou bloquear uma tecla, nunca com uma
  tecla pressionada. Um vigia solta tudo se o motor travar, e o kernel solta se ele morrer.
- Tudo o que roda fica preso à janela onde começou: perdeu o foco, pausa e solta as teclas.
- Autoclickers e macros são ok na maioria dos jogos single-player e em muitos jogos do
  Roblox, mas jogos online competitivos podem banir por automação. Confira as regras do jogo.

## Arquivos

| Onde | O quê |
|---|---|
| `~/.config/inputforge/config.json` | perfis, atalhos, regiões e ajustes (escrito pelo motor) |
| `$XDG_RUNTIME_DIR/inputforge/` | socket do motor, estado da waybar |
| `inputforge/engine.py` | motor: perfis, gatilhos, execuções, gravador, IPC |
| `inputforge/devices.py` | leitura (evdev), grab e teclado/mouse virtuais (uinput) |
| `inputforge/screen.py` | observador de tela (grim) |
| `inputforge/app.py`, `pages.py`, `editor.py`, `widgets.py` | janela (GTK 4 + libadwaita) |
| `inputforge/hud.py`, `inputforge/bar.py` | HUD e painel rápido (GTK 4 + layer-shell) |

## Desenvolvimento

```sh
python3 tests/test_engine.py          # motor com teclado, mouse, Hyprland e tela falsos
bin/inputforge status                 # roda direto do checkout
```

Teste sem mandar teclas de verdade: o motor real digita na janela em foco.
