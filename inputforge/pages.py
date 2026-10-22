"""Início, a profile's page, Ajustes and the "new profile" dialog (mixed into app.Window)."""

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from inputforge import widgets as W  # noqa: E402

DESKTOP = re.compile(r"^(Alacritty|zen|yazi|com\.rtosta\.zapzap|com\.ayugram\.desktop|AyuGram|vesktop|"
                     r"io\.github\.its_danilo\..*|org\.calos\..*|Calos|Keybinds|TUI\.float|Impala|steam|Neovim|clipse|kitty|foot|firefox|chromium)$", re.I)


def pretty_class(cls, title=""):
    if cls.startswith("steam_app_"):
        return (title or "Jogo Steam")[:40]
    last = cls.split(".")[-1]
    return last[:1].upper() + last[1:] if last else (title or "Novo perfil")


class Pages:
    # ---- shared ------------------------------------------------------------------
    def root(self, title, content, banner=None, header_end=None):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        if header_end:
            hb.pack_end(header_end)
        tv.add_top_bar(hb)
        if banner:
            tv.add_top_bar(banner)
        tv.set_content(content)
        return Adw.NavigationPage(title=title, child=tv)

    def key_button(self, trig, on_capture, big=False):
        """A button showing a trigger's keycaps; clicking it captures a new key/combo."""
        btn = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Clique e aperte a tecla, combo ou botão do mouse")
        btn.set_child(W.keycaps(trig, self.labels, self.region_name(trig.get("region")), big=big))
        btn.add_css_class("flat")

        def click(_b):
            if self.capturing:
                self.call("capture_cancel")
                return
            self.capturing = True
            btn.add_css_class("capturing")
            btn.set_child(Gtk.Label(label="Aperte a tecla…  (clique esquerdo cancela)"))

            def got(ok, res):
                self.capturing = False
                btn.remove_css_class("capturing")
                if ok and res and not (res["key"] == "BTN_LEFT" and not res["mods"]):
                    on_capture(res)
                    self.save()
                btn.set_child(W.keycaps(trig, self.labels, self.region_name(trig.get("region")), big=big))
            self.call("capture", None, got)
        btn.connect("clicked", click)
        return btn

    def run_button(self, pid, bid):
        """▶/■ for a binding, kept live with the engine's runs."""
        btn = Gtk.Button(valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")

        def refresh():
            running = bool(self.runs_of(bid))
            btn.set_icon_name("media-playback-stop-symbolic" if running else "media-playback-start-symbolic")
            btn.set_tooltip_text("Parar" if running else "Iniciar (com contagem)")
        refresh()
        self.live.append(refresh)
        btn.connect("clicked", lambda _b: self.call("run", {"binding": bid}))
        return btn

    # ---- Início --------------------------------------------------------------------
    def home_page(self):
        page = Adw.PreferencesPage()
        st = self.st

        # status
        g = Adw.PreferencesGroup()
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["hero"])
        title = Gtk.Label(xalign=0, css_classes=["hero-title"], wrap=True)
        lead = Gtk.Label(xalign=0, css_classes=["dim-label"], wrap=True)
        hero.append(title)
        hero.append(lead)
        g.add(hero)
        page.add(g)

        g = Adw.PreferencesGroup(title="Agora")
        engine = Adw.ActionRow(title="Motor")
        engine.add_prefix(Gtk.Image.new_from_icon_name("system-run-symbolic"))
        focus = Adw.ActionRow(title="Janela em foco")
        focus.add_prefix(Gtk.Image.new_from_icon_name("focus-windows-symbolic"))
        g.add(engine)
        g.add(focus)
        prof_box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        prof_holder = {"row": None, "sig": None}
        if self.env.get("calos"):
            gm = Adw.ActionRow(title="Game Mode")
            gm.add_prefix(Gtk.Image.new_from_icon_name("input-gaming-symbolic"))
            g.add(gm)
        else:
            gm = None
        page.add(g)
        pg = Adw.PreferencesGroup(title="Perfil do jogo",
                                  description="Quando mais de um perfil serve para a mesma janela (todo jogo do Roblox roda no Sober), escolha qual vale.")
        pg.add(prof_box)
        page.add(pg)

        def refresh_status():
            s = self.st
            runs = s.get("runs", [])
            armed = s.get("armed")
            if runs:
                title.set_label(f"{'Um atalho' if len(runs) == 1 else f'{len(runs)} atalhos'} em ação")
            elif armed and armed["id"] != "global":
                title.set_label(f"Pronto pra {armed['name']}")
            else:
                title.set_label("Forje seus reflexos")
            lead.set_label("Autoclicker, macros e remapeamento por jogo. Os atalhos funcionam até no meio do jogo em tela cheia; "
                           "segure Esc para parar tudo.")
            engine.set_subtitle("online · " + ("lendo o teclado" if s.get("listening") else "sem ler o teclado")
                                + (" · observando a tela" if s.get("watching") else ""))
            f = s.get("focus")
            focus.set_subtitle(f"{f['class']} · {f['title'][:60]}" if f else "—")
            if gm:
                gm.set_subtitle({"on": "ligado (teclado travado)", "paused": "pausado (jogo escondido)"}.get(s.get("gamemode"), "desligado"))
            cands = s.get("candidates") or []
            sig = (tuple((c["id"], c.get("enabled", True)) for c in cands), (s.get("game_profile") or {}).get("id"))
            if sig != prof_holder["sig"]:
                prof_holder["sig"] = sig
                if prof_holder["row"]:
                    prof_box.remove(prof_holder["row"])
                gp = (s.get("game_profile") or {}).get("id")
                if len(cands) > 1:
                    cur = gp if gp in [c["id"] for c in cands] else cands[0]["id"]
                    row = W.combo_row("Perfil ativo", [(c["id"], c["name"] + ("" if c.get("enabled", True) else " (desligado)")) for c in cands], cur,
                                      lambda pid: self.call("choose_profile", {"profile": pid}))
                elif cands:
                    row = Adw.ActionRow(title="Perfil ativo", subtitle=cands[0]["name"])
                else:
                    row = Adw.ActionRow(title="Perfil ativo", subtitle=(armed or {}).get("name", "nenhum"))
                prof_holder["row"] = row
                prof_box.append(row)
                pg.set_visible(bool(cands))
        refresh_status()
        self.live.append(refresh_status)

        # quick clicker
        glob = self.cfg["profiles"][0]
        quick = next((b for b in glob["bindings"] if b["action"]["type"] == "clicker"), None)
        g = Adw.PreferencesGroup(title="Clique rápido", description="Vale em qualquer janela sem perfil de jogo.")
        if quick:
            a = quick["action"]
            more = W.pill_button("Mais opções", lambda: self.open_binding("global", quick["id"]), icon="document-edit-symbolic")
            g.set_header_suffix(more)
            row = Adw.ActionRow(title="Atalho", subtitle="liga e desliga")
            row.add_suffix(self.key_button(quick["trigger"], lambda r: quick["trigger"].update(key=r["key"], mods=r["mods"])))
            g.add(row)
            cps = a["rate"]["value"] if a["rate"]["unit"] == "cps" else round(1000 / a["rate"]["value"], 2)

            def set_cps(v):
                a["rate"] = {"unit": "cps", "value": v}
                self.save()
            g.add(W.spin_row("Cliques por segundo", cps, 0.5, 200, 0.5, set_cps, digits=1))
            g.add(W.combo_row("Botão", W.MOUSE[:3], a["target"], lambda v: (a.update(target=v), self.save())))
            start = Adw.ActionRow(title="Iniciar daqui", subtitle=f"Começa depois de {self.cfg['settings']['countdown']} s: tempo de levar o mouse até o alvo")
            start.add_suffix(self.run_button("global", quick["id"]))
            g.add(start)
        else:
            row = Adw.ActionRow(title="O perfil “Qualquer janela” não tem autoclicker")
            row.add_suffix(W.pill_button("Criar", lambda: self.add_binding("global", "clicker", name="Autoclick rápido"), suggested=True))
            g.add(row)
        page.add(g)

        # running now
        g = Adw.PreferencesGroup(title="Rodando agora")
        runs_box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        g.add(runs_box)
        page.add(g)
        runs_sig = {"v": None}

        def refresh_runs():
            runs = self.st.get("runs", [])
            sig = [(r["id"], r["status"], r["detail"]) for r in runs]
            if sig == runs_sig["v"]:
                return
            runs_sig["v"] = sig
            while (r := runs_box.get_row_at_index(0)) is not None:
                runs_box.remove(r)
            if not runs:
                runs_box.append(Adw.ActionRow(title="Nada rodando", subtitle="Aperte o atalho no jogo ou use ▶ num atalho"))
            for r in runs:
                status = {"paused": "pausado (o jogo saiu do foco)", "countdown": "começando…"}.get(r["status"], r["detail"])
                row = Adw.ActionRow(title=r["name"], subtitle=f"{r['profile_name']} · {status}")
                row.add_prefix(W.type_icon(r["kind"]))
                row.add_suffix(W.keycaps(r["trigger"], self.labels, self.region_name(r["trigger"].get("region"))))
                row.add_suffix(W.icon_button("media-playback-stop-symbolic", "Parar", lambda rid=r["id"]: self.call("stop", {"run": rid})))
                runs_box.append(row)
        refresh_runs()
        self.live.append(refresh_runs)

        # game profiles
        g = Adw.PreferencesGroup(title="Perfis de jogo")
        g.set_header_suffix(W.pill_button("Novo perfil", self.new_profile_dialog, icon="list-add-symbolic"))
        for p in self.cfg["profiles"][1:]:
            row = Adw.ActionRow(title=p["name"], activatable=True,
                                subtitle=f"{p['match']['class'] or p['match']['title'] or 'sem regra'} · {len(p['bindings'])} atalhos")
            row.add_prefix(Gtk.Image.new_from_icon_name("input-gaming-symbolic"))
            if (self.st.get("armed") or {}).get("id") == p["id"]:
                row.add_suffix(Gtk.Label(label="ativo", css_classes=["live"]))
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", lambda _r, pid=p["id"]: self.go(("profile", pid)))
            g.add(row)
        if len(self.cfg["profiles"]) == 1:
            g.set_description("Crie um perfil por jogo: ele liga sozinho quando o jogo está em foco.")
        page.add(g)

        banner = self.detect_banner()
        return self.root("Início", page, banner=banner)

    def detect_banner(self):
        """“X ainda não tem perfil” for the last game focused (state.recent)."""
        recent = self.st.get("recent") or ([self.st["last_game"]] if self.st.get("last_game") else [])

        def matches(p, w):
            try:
                m = p["match"]
                if not m["class"] and not m["title"]:
                    return False
                return (not m["class"] or re.search(m["class"], w["class"])) and (not m["title"] or re.search(m["title"], w["title"]))
            except re.error:
                return False
        # games only: in the game workspace or fullscreen (not btop, a terminal, the browser...)
        w = next((w for w in recent if (w.get("workspace") == "special:game" or w.get("fullscreen"))
                  and not DESKTOP.match(w["class"]) and not any(matches(p, w) for p in self.cfg["profiles"][1:])), None)
        if not w:
            return None
        banner = Adw.Banner(title=f"{pretty_class(w['class'], w['title'])} ainda não tem perfil", button_label="Criar perfil", revealed=True)
        banner.connect("button-clicked", lambda _b: self.create_profile(w["class"], w["title"]))
        return banner

    # ---- profile -------------------------------------------------------------------
    def profile_page(self, pid):
        p = self.profile(pid)
        glob = pid == "global"
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Perfil" if not glob else "Qualquer janela",
                                 description=None if not glob else "Vale em todo lugar quando nenhum perfil de jogo está ativo: o autoclicker clássico.")
        if glob:
            g.add(W.switch_row("Ligado", self.cfg["settings"]["global_enabled"],
                               lambda v: (self.cfg["settings"].update(global_enabled=v), self.save())))
        else:
            def rename(v):
                p["name"] = v
                self.save()
                self._rename_soon()
            self._rename_soon = W.later(600, self.fill_sidebar)
            g.add(W.entry_row("Nome", p["name"], rename))
            g.add(W.switch_row("Ligado", p["enabled"], lambda v: (p.update(enabled=v), self.save())))
            g.add(W.entry_row("Classe da janela (regex)", p["match"]["class"], lambda v: (p["match"].update({"class": v}), self.save()), monospace=True))
            g.add(W.entry_row("Título (regex, opcional)", p["match"]["title"], lambda v: (p["match"].update(title=v), self.save()), monospace=True))
            pick = Adw.ActionRow(title="Pegar a classe de uma janela aberta")
            pick.add_suffix(W.pill_button("Escolher janela", lambda: self.window_picker(pid), icon="find-location-symbolic"))
            g.add(pick)
            g.add(W.combo_row("Quando o jogo sai do foco", [("pause", "Pausar e retomar"), ("stop", "Parar")], p["on_blur"],
                              lambda v: (p.update(on_blur=v), self.save())))
            cands = [c["id"] for c in self.st.get("candidates") or []]
            if len(cands) > 1 and pid in cands and (self.st.get("armed") or {}).get("id") != pid:
                use = Adw.ActionRow(title="Outro perfil está valendo para este jogo")
                use.add_suffix(W.pill_button("Usar este agora", lambda: self.call("choose_profile", {"profile": pid}), suggested=True))
                g.add(use)
        page.add(g)

        # bindings
        g = Adw.PreferencesGroup(title="Atalhos")
        add = Gtk.MenuButton(label="Novo atalho", valign=Gtk.Align.CENTER, css_classes=["flat"])
        pop = Gtk.Popover()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for kind, (icon, name, desc) in W.TYPES.items():
            b = Gtk.Button(css_classes=["flat"])
            bx = Gtk.Box(spacing=12)
            bx.append(W.type_icon(kind, extra=kind))
            tx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            tx.append(Gtk.Label(label=name, xalign=0, css_classes=["heading"]))
            tx.append(Gtk.Label(label=desc, xalign=0, css_classes=["dim-label", "caption"], wrap=True, max_width_chars=40))
            bx.append(tx)
            b.set_child(bx)
            b.connect("clicked", lambda _b, k=kind: (pop.popdown(), self.add_binding(pid, k)))
            col.append(b)
        pop.set_child(col)
        add.set_popover(pop)
        g.set_header_suffix(add)
        if not p["bindings"]:
            g.set_description("Nenhum atalho ainda.")
        for b in p["bindings"]:
            g.add(self.binding_row(pid, b))
        page.add(g)

        # regions
        g = Adw.PreferencesGroup(title="Regiões da tela",
                                 description="Pedaços da tela que o InputForge observa (um slot de skill, uma barra de vida, um botão). "
                                             "Marque com a tela no estado que interessa: esse estado vira a referência.")
        g.set_header_suffix(W.pill_button("Nova região", lambda: self.mark_region(pid, self.new_id()), icon="list-add-symbolic"))
        for r in p.get("regions", []):
            g.add(self.region_row(pid, r))
        page.add(g)

        if not glob:
            g = Adw.PreferencesGroup()
            row = Adw.ActionRow(title="Apagar este perfil", subtitle="Os atalhos e regiões dele somem junto")
            row.add_suffix(W.pill_button("Apagar", lambda: self.confirm(f"Apagar o perfil {p['name']}?", "Apagar",
                                                                       lambda: self.delete_profile(pid)), destructive=True))
            g.add(row)
            page.add(g)
        return self.root(p["name"] if not glob else "Qualquer janela", page)

    def binding_row(self, pid, b):
        t = b["action"]["type"]
        row = Adw.ActionRow(title=b["name"], subtitle=W.summary(b, self.labels), activatable=True)
        row.set_subtitle_lines(2)
        row.add_prefix(W.type_icon(t, extra=t))
        if t != "remap":
            row.add_suffix(W.keycaps(b["trigger"], self.labels, self.region_name(b["trigger"].get("region"))))
        sw = Gtk.Switch(active=b["enabled"], valign=Gtk.Align.CENTER, tooltip_text="Ligado")
        sw.connect("notify::active", lambda s, _p: (b.update(enabled=s.get_active()), self.save()))
        row.add_suffix(sw)
        if t != "remap":
            row.add_suffix(self.run_button(pid, b["id"]))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda _r: self.open_binding(pid, b["id"]))
        return row

    def region_row(self, pid, r):
        row = Adw.ExpanderRow(title=r["name"],
                              subtitle=(f"{r['x']}, {r['y']} · {r['w']}×{r['h']}" if r["w"] else "não marcada")
                              + ("" if r.get("ref") else " · sem referência"))
        row.add_prefix(W.Signature(r.get("ref"), 40))
        row.add_row(W.entry_row("Nome", r["name"], lambda v: (r.update(name=v), row.set_title(v), self.save())))
        row.add_row(W.spin_row("Tolerância (%)", r["tolerance"], 0, 60, 1, lambda v: (r.update(tolerance=v), self.save()),
                               subtitle="Quanto da região pode mudar e ainda contar como “igual”"))
        actions = Adw.ActionRow(title="Referência")
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(W.pill_button("Marcar de novo", lambda: self.mark_region(pid, r["id"]), icon="find-location-symbolic"))
        box.append(W.pill_button("Recapturar", lambda: self.recapture_region(pid, r["id"]), icon="media-record-symbolic"))
        box.append(W.pill_button("Testar", lambda: self.test_region(pid, r["id"]), icon="view-reveal-symbolic"))
        box.append(W.icon_button("user-trash-symbolic", "Apagar região", lambda: self.delete_region(pid, r["id"]), css=["error"]))
        actions.add_suffix(box)
        row.add_row(actions)
        return row

    # ---- profile actions -----------------------------------------------------------------
    def add_binding(self, pid, kind, name=None):
        tr = {"kind": "key", "key": "KEY_CAPSLOCK" if kind == "remap" else "KEY_F8", "mods": [], "hold_ms": 0,
              "block": kind == "remap", "region": "", "cond": "match"}
        a = {"type": kind}
        if kind == "clicker":
            a.update(target="BTN_LEFT", rate={"unit": "cps", "value": 15}, hold_ms=12, double=False, jitter=0,
                     where="cursor", points=[], point_jitter=0, limit={"kind": "none", "value": 0})
        elif kind == "macro":
            a.update(steps=[], repeat=1, speed=1.0, gap_ms=0)
        elif kind == "remap":
            a.update(to=["KEY_LEFTSHIFT"])
        else:
            a.update(cmd="")
        b = {"id": self.new_id(), "name": name or W.TYPES[kind][1], "enabled": True,
             "mode": "once" if kind == "command" else "toggle", "trigger": tr, "action": a}
        prof = self.profile(pid)
        if kind == "clicker" and name:
            prof["bindings"].insert(0, b)
        else:
            prof["bindings"].append(b)
        self.save(now=True)
        self.rebuild()
        self.open_binding(pid, b["id"])

    def delete_profile(self, pid):
        self.cfg["profiles"] = [p for p in self.cfg["profiles"] if p["id"] != pid]
        self.save(now=True)
        self.go(("home", None))

    def window_picker(self, pid=None):
        """A dialog listing the open windows; picking one sets (or creates) a profile's rule."""
        dialog = Adw.Dialog(title="Qual janela é o jogo?", content_width=480, content_height=520)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(description="Abra o jogo antes. A regra usa a classe da janela.")
        page.add(g)
        tv.set_content(page)
        dialog.set_child(tv)

        def got(ok, res):
            if not ok:
                return
            seen = set()
            wins = [w for w in res.get("clients", []) if not (w["class"] in seen or seen.add(w["class"]))]
            wins.sort(key=lambda w: bool(DESKTOP.match(w["class"])))
            for w in wins:
                row = Adw.ActionRow(title=pretty_class(w["class"], w["title"]), subtitle=f"{w['class']} · {w['title'][:50]}", activatable=True)
                row.add_prefix(Gtk.Image.new_from_icon_name("applications-system-symbolic" if DESKTOP.match(w["class"]) else "input-gaming-symbolic"))
                row.connect("activated", lambda _r, w=w: (dialog.close(), self.pick_window(pid, w)))
                g.add(row)
            if pid is None:
                blank = Adw.ActionRow(title="Perfil em branco", subtitle="Preencha a regra depois", activatable=True)
                blank.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
                blank.connect("activated", lambda _r: (dialog.close(), self.create_profile("", "")))
                g.add(blank)
        self.call("windows", None, got)
        dialog.present(self)

    def new_profile_dialog(self):
        self.window_picker(None)

    def pick_window(self, pid, w):
        if pid is None:
            self.create_profile(w["class"], w["title"])
            return
        self.profile(pid)["match"]["class"] = "^" + re.escape(w["class"]) + "$"
        self.save(now=True)
        self.rebuild()

    def create_profile(self, cls, title):
        p = {"id": self.new_id(), "name": pretty_class(cls, title) if cls else "Novo perfil", "enabled": True, "icon": "",
             "match": {"class": "^" + re.escape(cls) + "$" if cls else "", "title": ""}, "on_blur": "pause",
             "regions": [], "bindings": []}
        self.cfg["profiles"].append(p)
        self.save(now=True)
        self.go(("profile", p["id"]))
        self.toast(f"Perfil {p['name']} criado")

    def mark_region(self, pid, rid):
        self.call("region_mark", {"profile": pid, "region": rid})
        self.toast("Clique nos dois cantos da região na tela (Esc cancela)")

    def recapture_region(self, pid, rid):
        n = self.cfg["settings"]["countdown"] or 3
        self.call("region_capture", {"profile": pid, "region": rid, "delay": n})
        self.toast(f"Captura em {n} s: deixe a região no estado certo")

    def test_region(self, pid, rid):
        def got(ok, res):
            if ok:
                self.toast("Sem referência ainda" if res["differ"] is None else
                           f"Agora: {W.fmt(round(res['differ'], 1))}% diferente → {'igual à referência' if res['match'] else 'diferente'}")
        self.call("region_test", {"profile": pid, "region": rid}, got)

    def delete_region(self, pid, rid):
        p = self.profile(pid)
        p["regions"] = [r for r in p.get("regions", []) if r["id"] != rid]
        self.save(now=True)
        self.rebuild()

    def confirm(self, text, verb, fn):
        d = Adw.AlertDialog(heading=text)
        d.add_response("cancel", "Cancelar")
        d.add_response("ok", verb)
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: fn() if r == "ok" else None)
        d.present(self)

    # ---- Ajustes ---------------------------------------------------------------------
    def settings_page(self):
        s = self.cfg["settings"]
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Segurança")
        row = Adw.ActionRow(title="Tecla de pânico", subtitle="Para tudo e solta as teclas. Funciona sempre, até gravando.")
        row.add_suffix(self.key_button(s["panic"], lambda r: s["panic"].update(key=r["key"], mods=r["mods"])))
        g.add(row)
        g.add(W.spin_row("Segurar o pânico por (ms)", s["panic"]["hold_ms"], 0, 5000, 100,
                         lambda v: (s["panic"].update(hold_ms=v), self.save()), subtitle="0 = basta tocar"))
        row = Adw.ActionRow(title="Parar a gravação", subtitle="Encerra o gravador de macros (não entra na gravação)")
        row.add_suffix(self.key_button(s["record_stop"], lambda r: s["record_stop"].update(key=r["key"], mods=r["mods"])))
        g.add(row)
        g.add(W.spin_row("Contagem antes de começar (s)", s["countdown"], 0, 30, 1, lambda v: (s.update(countdown=v), self.save()),
                         subtitle="Quando você inicia por ▶: tempo de levar o mouse até o alvo"))
        page.add(g)

        g = Adw.PreferencesGroup(title="HUD no jogo")
        g.add(W.switch_row("Mostrar o HUD", s["hud"]["enabled"], lambda v: (s["hud"].update(enabled=v), self.save()),
                           subtitle="Pílulas por cima do jogo: o que roda, contagens, gravação"))
        positions = [("top-left", "Em cima, à esquerda"), ("top", "Em cima, no meio"), ("top-right", "Em cima, à direita"),
                     ("bottom-left", "Embaixo, à esquerda"), ("bottom", "Embaixo, no meio"), ("bottom-right", "Embaixo, à direita")]
        g.add(W.combo_row("Posição", positions, s["hud"]["position"], lambda v: (s["hud"].update(position=v), self.save())))
        g.add(W.switch_row("Avisar o perfil ao entrar no jogo", s["hud"]["announce"], lambda v: (s["hud"].update(announce=v), self.save())))
        page.add(g)

        if self.env.get("aurum") or self.env.get("calos"):
            g = Adw.PreferencesGroup(title="Integrações")
            if self.env.get("aurum"):
                g.add(W.switch_row("Aurum V60 acende", s["aurum"]["enabled"], lambda v: (s["aurum"].update(enabled=v), self.save()),
                                   subtitle="A tecla do atalho brilha enquanto ele roda (F1–F12 = a fileira de números)"))
            if self.env.get("calos"):
                g.add(Adw.ActionRow(title="calOS", subtitle="Waybar mostra o que roda, os atalhos aparecem no SUPER + K › Jogos, "
                                                            "e no jogo SUPER + CTRL + A abre o painel rápido (E abre esta janela)"))
            page.add(g)

        g = Adw.PreferencesGroup(title="Motor")
        st = Adw.ActionRow(title="Estado")
        grabbed = Adw.ActionRow(title="Dispositivos presos")

        def refresh():
            s2 = self.st
            st.set_subtitle(("lendo o teclado" if s2.get("listening") else "sem ler o teclado")
                            + (" · observando a tela" if s2.get("watching") else ""))
            grabbed.set_subtitle(", ".join(s2.get("grabbed") or []) or "nenhum")
        refresh()
        self.live.append(refresh)
        g.add(st)
        g.add(grabbed)
        g.add(Adw.ActionRow(title="Configuração", subtitle=self.env.get("config", "~/.config/inputforge/config.json"), subtitle_selectable=True))
        g.add(Adw.ActionRow(title="Serviço", subtitle=f"{self.env.get('unit', 'inputforge.service')} (systemctl --user)"))
        g.add(Adw.ActionRow(title="Linha de comando", subtitle='inputforge toggle "Nome do atalho" · stop · panic · status', subtitle_selectable=True))
        page.add(g)
        return self.root("Ajustes", page)
