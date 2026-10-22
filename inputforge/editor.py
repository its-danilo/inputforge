"""A binding's editor (pushed over its profile in the content NavigationView; mixed into app.Window)."""

import copy

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from inputforge import widgets as W  # noqa: E402


class Editor:
    def open_binding(self, pid, bid):
        self.nav.push(self.binding_page(pid, bid))

    def refresh_editor(self):
        """Rebuild the open editor in place (structure changed: kind, mode, steps...), keeping
        the scroll position; replace() swaps the stack without the slide animation."""
        top = self.nav.get_visible_page()
        if not getattr(top, "editor_for", None):
            return
        scroll = top.scroller.get_vadjustment().get_value() if top.scroller else 0
        root = self.nav.get_previous_page(top)
        page = self.binding_page(*top.editor_for)
        self.nav.replace([root, page] if root else [page])
        if page.scroller:
            from gi.repository import GLib
            GLib.idle_add(lambda: page.scroller.get_vadjustment().set_value(scroll) and False)

    def binding_page(self, pid, bid):
        b = self.binding(pid, bid)
        p = self.profile(pid)
        t = b["action"]["type"]
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup()
        name = W.entry_row("Nome", b["name"], lambda v: (b.update(name=v), self.save()))
        g.add(name)
        g.add(W.switch_row("Ligado", b["enabled"], lambda v: (b.update(enabled=v), self.save())))
        page.add(g)

        page.add(self.trigger_group(pid, b))
        for grp in {"clicker": self.clicker_groups, "macro": self.macro_groups,
                    "remap": self.remap_groups, "command": self.command_groups}[t](pid, b):
            page.add(grp)

        g = Adw.PreferencesGroup()
        row = Adw.ActionRow(title="Duplicar ou apagar este atalho")
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        box.append(W.pill_button("Duplicar", lambda: self.duplicate_binding(pid, bid), icon="edit-copy-symbolic"))
        box.append(W.pill_button("Apagar", lambda: self.confirm(f"Apagar o atalho {b['name']}?", "Apagar",
                                                                lambda: self.delete_binding(pid, bid)), destructive=True))
        row.add_suffix(box)
        g.add(row)
        page.add(g)

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        if t != "remap":
            hb.pack_end(self.run_button(pid, bid))
        tv.add_top_bar(hb)
        tv.set_content(page)
        nav = Adw.NavigationPage(title=f"{W.TYPES[t][1]} · {p['name'] if pid != 'global' else 'Qualquer janela'}", child=tv)
        nav.editor_for = (pid, bid)
        nav.scroller = next((c for c in _walk(page) if isinstance(c, Gtk.ScrolledWindow)), None)
        nav.connect("hidden", lambda *_a: self.rebuild_root_soon())
        return nav

    def rebuild_root_soon(self):
        # back on the profile page: its rows show the edited names/summaries
        from gi.repository import GLib
        GLib.idle_add(lambda: (self.rebuild() if not getattr(self.nav.get_visible_page(), "editor_for", None) else None) and False)

    # ---- trigger -------------------------------------------------------------------
    def trigger_group(self, pid, b):
        t = b["action"]["type"]
        tr = b["trigger"]
        g = Adw.PreferencesGroup(title="Tecla de origem" if t == "remap" else "Gatilho")
        if t != "remap":
            g.add(W.combo_row("Tipo", [("key", "Tecla ou botão"), ("screen", "Tela (observador)")], tr["kind"],
                              lambda v: (tr.update(kind=v), self.save(), self.refresh_editor())))
        if t != "remap" and tr["kind"] == "screen":
            regs = self.regions_for(pid)
            if not regs:
                g.set_description("Este perfil não tem regiões da tela. Crie uma na página do perfil (Regiões da tela → Nova região).")
            else:
                if tr["region"] not in [r["id"] for r in regs]:
                    tr["region"] = regs[0]["id"]
                g.add(W.combo_row("Região", [(r["id"], r["name"]) for r in regs], tr["region"],
                                  lambda v: (tr.update(region=v), self.save())))
                g.add(W.combo_row("Dispara quando ficar", W.CONDS, tr["cond"], lambda v: (tr.update(cond=v), self.save())))
            if t != "command":
                g.add(W.combo_row("Modo", [(v, n) for v, n, _ in W.MODES], b["mode"],
                                  lambda v: (b.update(mode=v), self.save(), self.refresh_editor()),
                                  subtitle=W.SCREEN_MODES[b["mode"]] + ". Só vale com o perfil ativo"))
            return g
        row = Adw.ActionRow(title="Tecla", subtitle="Teclado ou mouse; segure Ctrl/Shift/Alt junto para um combo"
                            if t != "remap" else "Ela some pro jogo enquanto o perfil está ativo")
        row.add_suffix(self.key_button(tr, lambda r: tr.update(key=r["key"], mods=[] if t == "remap" else r["mods"]), big=True))
        g.add(row)
        if t not in ("remap", "command"):
            g.add(W.combo_row("Modo", [(v, n) for v, n, _ in W.MODES], b["mode"],
                              lambda v: (b.update(mode=v), self.save(), self.refresh_editor()),
                              subtitle=dict((v, d) for v, _, d in W.MODES)[b["mode"]]))
        if t != "remap":
            g.add(W.switch_row("Bloquear a tecla original", tr["block"], lambda v: (tr.update(block=v), self.save()),
                               subtitle="O jogo não recebe a tecla do atalho"))
            g.add(W.spin_row("Segurar antes de disparar (ms)", tr["hold_ms"], 0, 5000, 50, lambda v: (tr.update(hold_ms=v), self.save())))
        return g

    # ---- clicker -------------------------------------------------------------------
    def clicker_groups(self, pid, b):
        a = b["action"]
        out = []
        g = Adw.PreferencesGroup(title="O que clicar")
        is_mouse = a["target"] in dict(W.MOUSE)
        items = W.MOUSE + [("__key", "Uma tecla (auto-tecla)" if is_mouse else f"Tecla {W.key_label(self.labels, a['target'])}")]

        def target(v):
            if v == "__key":
                if is_mouse:
                    self.capture_target(a)
                return
            a["target"] = v
            self.save()
            self.refresh_editor()
        g.add(W.combo_row("Alvo", items, a["target"] if is_mouse else "__key", target))
        if not is_mouse:
            row = Adw.ActionRow(title="Tecla tocada", subtitle="Toca essa tecla no ritmo abaixo")
            row.add_suffix(self.key_button({"kind": "key", "key": a["target"], "mods": []},
                                           lambda r: a.update(target=r["key"])))
            g.add(row)
        out.append(g)

        g = Adw.PreferencesGroup(title="Velocidade")
        cps = a["rate"]["unit"] == "cps"
        g.add(W.combo_row("Medida", [("cps", "Cliques por segundo"), ("ms", "Intervalo entre cliques")], a["rate"]["unit"],
                          lambda v: self.set_rate_unit(a, v)))
        g.add(W.spin_row("Cliques por segundo" if cps else "Intervalo (ms)", a["rate"]["value"],
                         0.1 if cps else 1, 500 if cps else 3600000, 0.5 if cps else 10,
                         lambda v: (a["rate"].update(value=v), self.save()), digits=1 if cps else 0))
        g.add(W.spin_row("Duração de cada clique (ms)", a["hold_ms"], 1, 10000, 1, lambda v: (a.update(hold_ms=v), self.save())))
        g.add(W.spin_row("Variação humana (%)", a["jitter"], 0, 90, 1, lambda v: (a.update(jitter=v), self.save()),
                         subtitle="Sorteia o intervalo em ± essa porcentagem"))
        g.add(W.switch_row("Clique duplo", a["double"], lambda v: (a.update(double=v), self.save())))
        out.append(g)

        g = Adw.PreferencesGroup(title="Onde")
        g.add(W.combo_row("Posição", [("cursor", "No cursor"), ("points", "Em pontos fixos (revezando)")], a["where"],
                          lambda v: (a.update(where=v), self.save(), self.refresh_editor())))
        if a["where"] == "points":
            for i, pt in enumerate(a["points"]):
                row = Adw.ActionRow(title=f"Ponto {i + 1}", subtitle=f"{pt['x']}, {pt['y']}")
                row.add_suffix(W.icon_button("user-trash-symbolic", "Tirar", lambda i=i: (a["points"].pop(i), self.save(), self.refresh_editor())))
                g.add(row)
            row = Adw.ActionRow(title="Marcar pontos na tela",
                                subtitle="Clique em cada ponto (o clique não chega no app); botão direito termina")
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            box.append(W.pill_button("Marcar", lambda: self.pick_points(pid, b["id"]), icon="find-location-symbolic"))
            if a["points"]:
                box.append(W.pill_button("Limpar", lambda: (a.update(points=[]), self.save(), self.refresh_editor())))
            row.add_suffix(box)
            g.add(row)
            g.add(W.spin_row("Espalhar (± px)", a["point_jitter"], 0, 200, 1, lambda v: (a.update(point_jitter=v), self.save())))
        out.append(g)

        g = Adw.PreferencesGroup(title="Limite")
        g.add(W.combo_row("Parar depois de", [("none", "Sem limite"), ("count", "Um número de cliques"), ("time", "Um tempo")],
                          a["limit"]["kind"], lambda v: (a["limit"].update(kind=v), self.save(), self.refresh_editor())))
        if a["limit"]["kind"] != "none":
            g.add(W.spin_row("Cliques" if a["limit"]["kind"] == "count" else "Segundos", a["limit"]["value"], 0, 10000000, 1,
                             lambda v: (a["limit"].update(value=v), self.save())))
        out.append(g)
        return out

    def set_rate_unit(self, a, unit):
        if a["rate"]["unit"] == unit:
            return
        v = a["rate"]["value"] or 1
        a["rate"] = {"unit": "ms", "value": round(1000 / v)} if unit == "ms" else {"unit": "cps", "value": round(1000 / v, 2)}
        self.save()
        self.refresh_editor()

    def capture_target(self, a):
        self.toast("Aperte a tecla que o autoclicker vai tocar")

        def got(ok, res):
            if ok and res and not res["key"].startswith("BTN_"):
                a["target"] = res["key"]
                self.save()
            self.refresh_editor()
        self.call("capture", None, got)

    def pick_points(self, pid, bid):
        self.pick_for = (pid, bid)
        self.call("pick_start")
        self.toast("Clique nos pontos da tela · botão direito termina")

    def on_pick(self, points, done):
        if not self.pick_for:
            return
        b = self.binding(*self.pick_for)
        if b and b["action"]["type"] == "clicker":
            b["action"]["points"] = list(points)
        if done:
            self.pick_for = None
            self.save(now=True)
            self.refresh_editor()

    # ---- macro ---------------------------------------------------------------------
    def macro_groups(self, pid, b):
        a = b["action"]
        out = []
        g = Adw.PreferencesGroup(title="Gravar",
                                 description="Depois da contagem, tudo o que você apertar vira passo, com os tempos. "
                                             f"{W.trigger_text(self.cfg['settings']['record_stop'], self.labels)} termina.")
        rec_opts = {"motion": "none", "mode": "replace"}
        g.add(W.combo_row("Mouse", [("none", "Sem movimento"), ("relative", "Movimento relativo (câmera 3D)"),
                                    ("absolute", "Posição dos cliques (menus)")], "none", lambda v: rec_opts.update(motion=v)))
        g.add(W.combo_row("Os passos gravados", [("replace", "Substituem os atuais"), ("append", "Entram no fim")],
                          "replace", lambda v: rec_opts.update(mode=v)))
        recording = self.record_for is not None and self.record_for[1] == b["id"]
        row = Adw.ActionRow(title="Gravando…" if recording else "Gravar jogando")
        row.add_suffix(W.pill_button("Parar" if recording else "Gravar",
                                     (lambda: self.call("record_stop")) if recording else
                                     (lambda: self.start_recording(pid, b["id"], rec_opts)),
                                     suggested=not recording, destructive=recording,
                                     icon="media-playback-stop-symbolic" if recording else "media-record-symbolic"))
        g.add(row)
        out.append(g)

        g = Adw.PreferencesGroup(title=f"Passos · {len(a['steps'])}")
        add = Gtk.MenuButton(label="Adicionar", valign=Gtk.Align.CENTER, css_classes=["flat"])
        pop = Gtk.Popover()
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for kind, (icon, name) in W.STEPS.items():
            btn = Gtk.Button(css_classes=["flat"])
            btn.set_child(Adw.ButtonContent(icon_name=icon, label=name))
            btn.connect("clicked", lambda _b, k=kind: (pop.popdown(), self.add_step(pid, b, k)))
            grid.append(btn)
        pop.set_child(grid)
        add.set_popover(pop)
        g.set_header_suffix(add)
        if not a["steps"]:
            g.set_description("Grave jogando ou adicione passos.")
        covered = 0
        for i, s in enumerate(a["steps"]):
            g.add(self.step_row(pid, b, i, s, nested=covered > 0))
            covered = s["skip"] if s["t"] == "if_screen" else max(0, covered - 1)
        out.append(g)

        g = Adw.PreferencesGroup(title="Repetição")
        g.add(W.switch_row("Repetir até parar", a["repeat"] == 0,
                           lambda v: (a.update(repeat=0 if v else 1), self.save(), self.refresh_editor())))
        if a["repeat"] != 0:
            g.add(W.spin_row("Vezes", a["repeat"], 1, 1000000, 1, lambda v: (a.update(repeat=v), self.save()),
                             subtitle="No modo Segurar, 1 vez = repete enquanto segura" if b["mode"] == "hold" else None))
        g.add(W.spin_row("Pausa entre voltas (ms)", a["gap_ms"], 0, 3600000, 50, lambda v: (a.update(gap_ms=v), self.save())))
        g.add(W.spin_row("Velocidade (×)", a["speed"], 0.1, 10, 0.1, lambda v: (a.update(speed=round(v, 2)), self.save()), digits=2))
        out.append(g)
        return out

    def start_recording(self, pid, bid, opts):
        self.record_for = (pid, bid, opts["mode"])

        def started(ok, _res):
            if ok:
                n = self.cfg["settings"]["countdown"]
                self.toast(f"Gravando{f' em {n} s' if n else ''}… {W.trigger_text(self.cfg['settings']['record_stop'], self.labels)} termina")
            else:
                self.record_for = None
            self.refresh_editor()
        self.call("record_start", {"motion": opts["motion"]}, started)

    def on_recorded(self, steps):
        rf = self.record_for
        self.record_for = None
        b = rf and self.binding(rf[0], rf[1])
        if not b:
            return
        b["action"]["steps"] = (b["action"]["steps"] + steps) if rf[2] == "append" else steps
        self.save(now=True)
        self.toast(f"{len(steps)} passos gravados")
        self.refresh_editor()

    def add_step(self, pid, b, kind):
        s = {"t": kind, "id": self.new_id()}
        first = (self.regions_for(pid) or [{}])[0].get("id", "")
        s.update({
            "key": {"codes": ["KEY_E"], "act": "tap", "hold_ms": 20},
            "button": {"codes": ["BTN_LEFT"], "act": "tap", "hold_ms": 20},
            "wait": {"ms": 100, "rand_ms": 0}, "move": {"dx": 0, "dy": 0, "ms": 0},
            "moveto": {"x": 0, "y": 0}, "scroll": {"dy": -1, "dx": 0}, "text": {"value": ""},
            "command": {"cmd": ""},
            "wait_screen": {"region": first, "cond": "match", "timeout_ms": 0, "on_timeout": "continue"},
            "if_screen": {"region": first, "cond": "match", "skip": 1},
        }[kind])
        b["action"]["steps"].append(s)
        self.save()
        self.refresh_editor()

    def step_row(self, pid, b, i, s, nested=False):
        steps = b["action"]["steps"]
        t = s["t"]
        row = Adw.ActionRow(title=W.STEPS[t][1])
        if nested:
            row.add_css_class("nested")
        if t == "if_screen":
            row.add_css_class("cond")
        row.add_prefix(W.type_icon(t, step=True, extra="wait" if t == "wait" else None))

        def setv(key, value, structural=False):
            s[key] = value
            self.save()
            if structural:
                self.refresh_editor()

        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        unit = lambda text: Gtk.Label(label=text, css_classes=["dim-label"])  # noqa: E731
        if t == "key":
            def codes(r):
                s["codes"] = [W.MOD_CODE[m] for m in r["mods"]] + [r["key"]]
            btn = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text="Trocar a tecla (combo vale)")
            btn.set_child(W.codes_caps(s["codes"], self.labels))

            def cap(_b):
                btn.set_child(Gtk.Label(label="Aperte…"))

                def got(ok, res):
                    if ok and res and not (res["key"] == "BTN_LEFT" and not res["mods"]):
                        codes(res)
                        self.save()
                    btn.set_child(W.codes_caps(s["codes"], self.labels))
                self.call("capture", None, got)
            btn.connect("clicked", cap)
            box.append(btn)
            box.append(W.dropdown([("tap", "Tocar"), ("down", "Pressionar"), ("up", "Soltar")], s["act"], lambda v: setv("act", v, True)))
            if s["act"] == "tap":
                box.append(W.small_spin(s["hold_ms"], 0, 60000, 10, lambda v: setv("hold_ms", v)))
                box.append(unit("ms"))
        elif t == "button":
            box.append(W.dropdown(W.MOUSE, s["codes"][0], lambda v: setv("codes", [v])))
            box.append(W.dropdown([("tap", "Clique"), ("double", "Duplo"), ("down", "Pressionar"), ("up", "Soltar")], s["act"],
                                  lambda v: setv("act", v, True)))
            if s["act"] in ("tap", "double"):
                box.append(W.small_spin(s["hold_ms"], 0, 60000, 10, lambda v: setv("hold_ms", v)))
                box.append(unit("ms"))
        elif t == "wait":
            box.append(W.small_spin(s["ms"], 0, 3600000, 50, lambda v: setv("ms", v), width=7))
            box.append(unit("ms ±"))
            box.append(W.small_spin(s["rand_ms"], 0, 600000, 10, lambda v: setv("rand_ms", v)))
            box.append(unit("aleatório"))
        elif t == "move":
            box.append(unit("x"))
            box.append(W.small_spin(s["dx"], -20000, 20000, 1, lambda v: setv("dx", v)))
            box.append(unit("y"))
            box.append(W.small_spin(s["dy"], -20000, 20000, 1, lambda v: setv("dy", v)))
            box.append(unit("em"))
            box.append(W.small_spin(s["ms"], 0, 60000, 10, lambda v: setv("ms", v)))
            box.append(unit("ms"))
        elif t == "moveto":
            box.append(unit("x"))
            box.append(W.small_spin(s["x"], -100000, 100000, 1, lambda v: setv("x", v), width=6))
            box.append(unit("y"))
            box.append(W.small_spin(s["y"], -100000, 100000, 1, lambda v: setv("y", v), width=6))
        elif t == "scroll":
            box.append(W.small_spin(s["dy"], -100, 100, 1, lambda v: setv("dy", v)))
            box.append(unit("(+ cima, − baixo)"))
        elif t in ("text", "command"):
            key = "value" if t == "text" else "cmd"
            entry = Gtk.Entry(text=s[key], valign=Gtk.Align.CENTER, width_chars=24,
                              placeholder_text="gg wp" if t == "text" else "notify-send oi")
            entry.connect("changed", lambda e: setv(key, e.get_text()))
            box.append(entry)
        elif t in ("wait_screen", "if_screen"):
            regs = self.regions_for(pid)
            items = [(r["id"], r["name"]) for r in regs] or [("", "(nenhuma região)")]
            box.append(W.dropdown(items, s["region"], lambda v: setv("region", v)))
            box.append(W.dropdown([(v, {"match": "igual", "differ": "diferente", "change": "mudou"}[v]) for v, _ in W.CONDS],
                                  s["cond"], lambda v: setv("cond", v)))
            if t == "wait_screen":
                box.append(unit("máx."))
                box.append(W.small_spin(s["timeout_ms"], 0, 3600000, 500, lambda v: setv("timeout_ms", v), width=6))
                box.append(unit("ms →"))
                box.append(W.dropdown([("continue", "segue"), ("stop", "para")], s["on_timeout"], lambda v: setv("on_timeout", v)))
            else:
                box.append(unit("senão pula"))
                box.append(W.small_spin(s["skip"], 1, 100, 1, lambda v: setv("skip", v, True), width=3))
        row.add_suffix(box)

        # ⋮: move up/down, duplicate, delete
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"])
        pop = Gtk.Popover()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        def act(label, icon, fn):
            btn = Gtk.Button(css_classes=["flat"])
            btn.set_child(Adw.ButtonContent(icon_name=icon, label=label))
            btn.connect("clicked", lambda _b: (pop.popdown(), fn(), self.save(), self.refresh_editor()))
            col.append(btn)
        if i > 0:
            act("Subir", "go-up-symbolic", lambda: steps.insert(i - 1, steps.pop(i)))
        if i < len(steps) - 1:
            act("Descer", "go-down-symbolic", lambda: steps.insert(i + 1, steps.pop(i)))
        act("Duplicar", "edit-copy-symbolic", lambda: steps.insert(i + 1, {**copy.deepcopy(s), "id": self.new_id()}))
        act("Apagar", "user-trash-symbolic", lambda: steps.pop(i))
        pop.set_child(col)
        menu.set_popover(pop)
        row.add_suffix(menu)
        return row

    # ---- remap / command -------------------------------------------------------------
    def remap_groups(self, pid, b):
        a = b["action"]
        g = Adw.PreferencesGroup(title="Vira", description="Combos valem (capture “Ctrl + C” e a tecla manda os dois). "
                                                         "O resto do teclado/mouse passa sem mudança.")
        row = Adw.ActionRow(title="Teclas enviadas")
        btn = Gtk.Button(css_classes=["flat"], valign=Gtk.Align.CENTER)
        btn.set_child(W.codes_caps(a["to"], self.labels, big=True))

        def cap(_b):
            btn.set_child(Gtk.Label(label="Aperte…"))

            def got(ok, res):
                if ok and res and not (res["key"] == "BTN_LEFT" and not res["mods"]):
                    a["to"] = [W.MOD_CODE[m] for m in res["mods"]] + [res["key"]]
                    self.save()
                btn.set_child(W.codes_caps(a["to"], self.labels, big=True))
            self.call("capture", None, got)
        btn.connect("clicked", cap)
        row.add_suffix(btn)
        g.add(row)
        quick = [("", "(escolher)")] + W.MOUSE + [("__none", "Nada: desativa a tecla")]
        g.add(W.combo_row("Atalhos rápidos", quick, "",
                          lambda v: (a.update(to=[] if v == "__none" else [v]), self.save(), self.refresh_editor()) if v else None))
        return [g]

    def command_groups(self, pid, b):
        a = b["action"]
        g = Adw.PreferencesGroup(title="Comando", description="Roda com sh -c, sem esperar terminar, com o PATH da sua sessão.")
        g.add(W.entry_row("Comando", a["cmd"], lambda v: (a.update(cmd=v), self.save()), monospace=True))
        return [g]

    # ---- binding actions -------------------------------------------------------------
    def duplicate_binding(self, pid, bid):
        p = self.profile(pid)
        b = copy.deepcopy(self.binding(pid, bid))
        b["id"] = self.new_id()
        b["name"] += " (cópia)"
        p["bindings"].append(b)
        self.save(now=True)
        self.nav.pop()
        self.open_binding(pid, b["id"])
        self.toast("Duplicado")

    def delete_binding(self, pid, bid):
        p = self.profile(pid)
        p["bindings"] = [b for b in p["bindings"] if b["id"] != bid]
        self.save(now=True)
        self.nav.pop()


def _walk(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from _walk(child)
        child = child.get_next_sibling()
