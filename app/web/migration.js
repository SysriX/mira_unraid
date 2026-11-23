/* MIRA – Routing › HAProxy-Migration: configuration from one HAProxy server (haproxy-ui or OPNsense)
 * into another – choose what to import, rename backends, servers, frontends, ACLs (Bedingungen) and
 * rules (Regeln); all references follow. Import only saves; check and apply on the target afterwards. */
"use strict";

ANSICHT.migration = { titel: "HAProxy-Migration", zeigen: async main => {
  await ladeModule();
  const haps = SERVERS.filter(s => s.kind === "haproxy" && s.enabled);
  if (haps.length < 2) {
    leeren(main, h("div", { class: "karte leer" }, h("p", {}, "Für eine Migration braucht es mindestens zwei HAProxy-Server (Quelle und Ziel)."),
      h("p", { class: "leise" }, "z. B. den HAProxy-Container (haproxy-ui) und das HAProxy-Plugin der OPNsense."),
      h("button", { class: "p", onclick: () => serverDialog({ kind: "haproxy" }) }, "HAProxy-Server hinzufügen")));
    return;
  }
  const opt = s => [s.id, `${s.name} (${s.access === "opnsense" ? "OPNsense" : "haproxy-ui"}${s.site ? " · " + s.site : ""})`];
  const quelle = auswahl("source", haps.map(opt), localStorage.getItem("mira.mig.q") || haps[0].id);
  const ziel = auswahl("target", haps.map(opt), localStorage.getItem("mira.mig.z") || haps[1].id);
  const bereich = h("div", {});
  leeren(main, h("div", { class: "karte" }, h("h2", {}, "1 · Quelle und Ziel"),
    h("p", { class: "leise klein-t" }, "Liest die Konfiguration der Quelle (Backends mit Servern, Adressen/Routen, Frontends mit Bedingungen und Regeln). Danach auswählen, umbenennen und in das Ziel übernehmen. Das Ziel wird nur gespeichert (haproxy-ui: Entwurf, OPNsense: gespeichert) – aktiv wird es erst mit „Prüfen/Übernehmen“."),
    h("div", { class: "raster" }, feld("Quelle", quelle), feld("Ziel", ziel)),
    h("div", { class: "leiste" }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: async e => {
      if (quelle.value === ziel.value) return hinweis("Quelle und Ziel müssen verschieden sein", true);
      localStorage.setItem("mira.mig.q", quelle.value); localStorage.setItem("mira.mig.z", ziel.value);
      e.target.disabled = true; leeren(bereich, h("div", { class: "leer" }, h("span", { class: "dreher" }), " Lese Quelle und Ziel …"));
      try { migAssistent(bereich, await api("POST", "/api/servers/migration/read", { source: Number(quelle.value), target: Number(ziel.value) })); }
      catch (er) { leeren(bereich, h("div", { class: "karte" }, h("h2", {}, punkt("bad"), "Fehler"), h("pre", {}, er.message))); }
      e.target.disabled = false;
    } }, "Einlesen")))), bereich);
} };

function migAssistent(el, d) {
  const src = d.source, tgt = d.target, tn = d.target_names, m = d.model;
  const zuOpn = tgt.access === "opnsense", vonOpn = src.access === "opnsense";
  // state: what is chosen and how it is called in the target
  const st = { backends: {}, routes: {}, frontends: {} };
  const frei = (liste, name) => !liste.includes(name);
  const vorschlag = (liste, name) => { let n = `${name}_import`, i = 2; while (liste.includes(n)) n = `${name}_import${i++}`; return n; };
  for (const b of m.backends) {
    const konflikt = !frei(tn.backends, b.name);
    st.backends[b.key] = { sel: !b.infra, name: konflikt ? vorschlag(tn.backends, b.name) : b.name, action: "rename",
      servers: Object.fromEntries(b.servers.map(s => [s.key, zuOpn && !frei(tn.servers, s.name) ? vorschlag(tn.servers, s.name) : s.name])) };
  }
  for (const r of m.routes) st.routes[r.key] = { sel: !(zuOpn && vonOpn), host: r.host, action: tn.routes.includes(r.host) ? "skip" : "add" };
  for (const f of m.frontends) {
    const konflikt = !frei(tn.frontends, f.name);
    st.frontends[f.key] = { sel: !f.infra && !(vonOpn && !zuOpn && m.routes.some(r => r.from_frontend === f.key)), action: "rename",
      name: konflikt ? vorschlag(tn.frontends, f.name) : f.name,
      acls: Object.fromEntries(f.acls.map(a => [a.key, zuOpn && tn.acls.includes(a.name) ? vorschlag(tn.acls, a.name) : a.name])),
      rules: Object.fromEntries(f.rules.map(r => [r.key, zuOpn && tn.rules.includes(r.name) ? vorschlag(tn.rules, r.name) : r.name])) };
  }
  const beName = key => { const s = st.backends[key]; return s && s.sel && s.action !== "skip" ? s.name : key; };
  const beZiehen = key => { if (st.backends[key] && !tn.backends.includes(key)) st.backends[key].sel = true; };

  const optionen = h("div", {});
  const ergebnis = h("div", {});
  const tabellen = h("div", {});
  const nameEingabe = (obj, feld, alt) => eingabe("", obj[feld], { oninput: e => { obj[feld] = e.target.value.trim(); }, onchange: zeichnen, placeholder: alt });

  function zeichnen() {
    const be = m.backends.map(b => {
      const s = st.backends[b.key];
      const konflikt = tn.backends.includes(b.name), doppelt = s.action === "rename" && tn.backends.includes(s.name);
      return h("tr", { class: s.sel ? "" : "mig-aus" },
        h("td", {}, h("input", { type: "checkbox", checked: s.sel, onchange: e => { s.sel = e.target.checked; zeichnen(); } })),
        h("td", {}, h("b", {}, b.name), b.infra ? h("div", {}, marke("haproxy-unraid intern")) : null),
        h("td", {}, s.action === "skip" ? h("span", { class: "leise" }, `${b.name} im Ziel`) : nameEingabe(s, "name", b.name),
          konflikt ? h("div", { class: "mig-konflikt" }, `${b.name} gibt es im Ziel – `,
            h("select", { onchange: e => { s.action = e.target.value; zeichnen(); } }, [["rename", "umbenennen"], ["skip", "vorhandenes benutzen"]].map(([v, t]) => h("option", { value: v, selected: s.action === v }, t)))) : null,
          doppelt ? h("div", { class: "mig-konflikt" }, "dieser Name ist im Ziel schon vergeben") : null),
        h("td", {}, marke(b.mode)), h("td", { class: "klein-t" }, b.balance || ""),
        h("td", {}, h("details", {}, h("summary", { class: "klein-t" }, `${b.servers.length} Server`),
          h("div", { class: "mig-details" }, b.servers.map(sv => feld(`${sv.name} → ${sv.address}${sv.port ? ":" + sv.port : ""}${sv.ssl ? " (ssl)" : ""}`,
            eingabe("", s.servers[sv.key], { oninput: e => { s.servers[sv.key] = e.target.value.trim(); } }),
            zuOpn && tn.servers.includes(s.servers[sv.key]) ? "gibt es im Ziel schon – umbenennen" : null)))),
          b.extra.length ? h("details", {}, h("summary", { class: "klein-t" }, `${b.extra.length} weitere Zeilen`), h("pre", {}, b.extra.join("\n"))) : null));
    });
    const ro = m.routes.map(r => {
      const s = st.routes[r.key];
      const vorhanden = tn.routes.includes(s.host);
      return h("tr", { class: s.sel ? "" : "mig-aus" },
        h("td", {}, h("input", { type: "checkbox", checked: s.sel, onchange: e => { s.sel = e.target.checked; if (s.sel) beZiehen(r.backend); zeichnen(); } })),
        h("td", {}, eingabe("", s.host, { oninput: e => { s.host = e.target.value.trim().toLowerCase(); }, onchange: zeichnen })),
        h("td", {}, "→ ", h("b", {}, beName(r.backend)), beName(r.backend) !== r.backend ? h("div", { class: "leise klein-t" }, `vorher ${r.backend}`) : null),
        h("td", {}, marke(r.access)), h("td", {}, marke(r.tls_mode === "durch" ? "durchreichen" : "TLS-Ende")),
        h("td", { class: "klein-t leise" }, r.note || ""),
        h("td", {}, vorhanden ? h("div", { class: "mig-konflikt" }, "gibt es im Ziel – ",
          h("select", { onchange: e => { s.action = e.target.value; } }, [["skip", "überspringen"], ["replace", "ersetzen"]].map(([v, t]) => h("option", { value: v, selected: s.action === v }, t)))) : ""));
    });
    const fe = m.frontends.map(f => {
      const s = st.frontends[f.key];
      const konflikt = tn.frontends.includes(f.name), doppelt = s.action === "rename" && tn.frontends.includes(s.name);
      return h("tr", { class: s.sel ? "" : "mig-aus" },
        h("td", {}, h("input", { type: "checkbox", checked: s.sel, onchange: e => {
          s.sel = e.target.checked;
          if (s.sel) { beZiehen(f.default_backend); f.rules.filter(r => r.kind === "use_backend").forEach(r => beZiehen(r.backend)); }
          zeichnen();
        } })),
        h("td", {}, h("b", {}, f.name), f.infra ? h("div", {}, marke("haproxy-unraid intern")) : null),
        h("td", {}, s.action === "skip" ? h("span", { class: "leise" }, `${f.name} im Ziel bleibt unverändert`) : nameEingabe(s, "name", f.name),
          konflikt ? h("div", { class: "mig-konflikt" }, `${f.name} gibt es im Ziel – `,
            h("select", { onchange: e => { s.action = e.target.value; zeichnen(); } }, [["rename", "umbenennen"], ["skip", "nicht importieren"]].map(([v, t]) => h("option", { value: v, selected: s.action === v }, t)))) : null,
          doppelt ? h("div", { class: "mig-konflikt" }, "dieser Name ist im Ziel schon vergeben") : null),
        h("td", { class: "klein-t" }, f.binds.map(b => `${b.address === "0.0.0.0" ? "" : b.address}:${b.port}${b.ssl ? " ssl" : ""}`).join(", "), " ", marke(f.mode)),
        h("td", {}, h("details", {}, h("summary", { class: "klein-t" }, `${f.acls.length} Bedingungen · ${f.rules.length} Regeln`),
          f.acls.length ? [h("h4", {}, "Bedingungen (ACLs)"), h("div", { class: "mig-details" }, f.acls.map(a => feld(`${a.name}: ${a.criteria.join(" | ")}`,
            eingabe("", s.acls[a.key], { oninput: e => { s.acls[a.key] = e.target.value.trim(); } }),
            zuOpn && tn.acls.includes(s.acls[a.key]) ? "gibt es im Ziel schon – umbenennen" : null)))] : null,
          f.rules.length ? [h("h4", {}, "Regeln"), h("div", { class: "mig-details" }, f.rules.map(r => feld(
            r.kind === "use_backend" ? `use_backend ${beName(r.backend)} ${r.test} ${r.cond.join(" ")}` : `${r.line} ${r.test} ${r.cond.join(" ")}`,
            eingabe("", s.rules[r.key], { oninput: e => { s.rules[r.key] = e.target.value.trim(); } }),
            zuOpn ? (tn.rules.includes(s.rules[r.key]) ? "gibt es im Ziel schon – umbenennen" : "Name der Aktion in der OPNsense") : "Name nur zur Übersicht")))] : null,
          f.extra.length ? [h("h4", {}, "Weitere Zeilen"), h("pre", {}, f.extra.join("\n"))] : null,
          f.default_backend ? h("p", { class: "klein-t" }, "Standard-Backend: ", h("b", {}, beName(f.default_backend))) : null)));
    });
    const kopf = t => h("thead", {}, h("tr", {}, t.map(x => h("th", {}, x))));
    leeren(tabellen,
      h("div", { class: "karte" }, h("h2", {}, "Backends", h("span", { class: "rechts leise klein-t" }, `${Object.values(st.backends).filter(x => x.sel).length} von ${m.backends.length} gewählt`)),
        h("div", { class: "tab-wrap" }, h("table", { class: "mig-tab" }, kopf(["", "Quelle", "Name im Ziel", "Modus", "Verteilung", "Server (umbenennen)"]), h("tbody", {}, be)))),
      m.routes.length ? h("div", { class: "karte" }, h("h2", {}, "Adressen (Name → Backend)"),
        h("p", { class: "leise klein-t" }, zuOpn ? "Werden in der OPNsense zu je einer Bedingung (Host bzw. SNI) und einer Regel „use_backend“ im gewählten Frontend (unten)." : "Werden in haproxy-ui in listen/dienste.map eingetragen (Tabelle „Dienste“)."),
        h("div", { class: "tab-wrap" }, h("table", { class: "mig-tab" }, kopf(["", "Adresse", "Backend", "Zugang", "Modus", "Herkunft", ""]), h("tbody", {}, ro)))) : null,
      m.frontends.length ? h("div", { class: "karte" }, h("h2", {}, "Frontends"),
        h("p", { class: "leise klein-t" }, zuOpn ? "Bedingungen werden zu ACLs, use_backend-Regeln zu Aktionen; alles andere als eigene Regel (custom) mit eingebetteten Bedingungen."
          : "Werden als Block „MIRA-Import“ in dienste.cfg angehängt. Achtung: dieselben Ports wie fe_http/fe_https gehen nicht doppelt."),
        h("div", { class: "tab-wrap" }, h("table", { class: "mig-tab" }, kopf(["", "Quelle", "Name im Ziel", "Bind", "Bedingungen und Regeln (umbenennen)"]), h("tbody", {}, fe)))) : null);
  }

  // options for an OPNsense target
  const tfSel = auswahl("route_frontend", [["", "– wählen –"], ...d.target_frontends.map(f => [f.uuid, `${f.name} (${f.bind}, ${f.mode})`]), ["new", "neues Frontend anlegen"]], "");
  const neuName = eingabe("new_frontend_name", "fe_import"), neuBind = eingabe("new_frontend_bind", "0.0.0.0:443");
  const neuBox = h("div", { class: "raster versteckt" }, feld("Name des neuen Frontends", neuName), feld("Bind", neuBind));
  tfSel.addEventListener("change", () => neuBox.classList.toggle("versteckt", tfSel.value !== "new"));
  const netze = eingabe("internal_networks", "", { placeholder: "z. B. 192.168.0.0/16 10.0.0.0/8" });
  const zert = auswahl("certificate", [["", "– keins –"], ...d.certificates], "");
  if (zuOpn) leeren(optionen, h("div", { class: "karte" }, h("h2", {}, "Einstellungen für die OPNsense"),
    h("div", { class: "raster" }, m.routes.length ? feld("Frontend für die Adressen", tfSel, "dort werden die Regeln angehängt") : null,
      m.routes.length ? feld("Interne Netze", netze, "für Zugang intern/extern; leer = Adressen von überall erreichbar") : null,
      feld("Zertifikat für TLS-Frontends", zert, "Zertifikate selbst werden nicht kopiert")), neuBox));

  const daten = () => ({ source: src.id, target: tgt.id,
    options: zuOpn ? { ...lesenForm(optionen), route_frontend_label: (d.target_frontends.find(f => f.uuid === tfSel.value) || {}).name || "" } : {},
    choices: { backends: st.backends, routes: Object.fromEntries(Object.entries(st.routes).map(([k, v]) => [k, { sel: v.sel, host: v.host, action: v.sel ? v.action : "" }])),
      frontends: st.frontends } });
  zeichnen();
  leeren(el,
    d.warnings.length ? h("div", { class: "karte" }, h("h2", {}, punkt("warn"), "Hinweise zur Quelle"), h("ul", {}, d.warnings.map(x => h("li", { class: "klein-t" }, x)))) : null,
    h("div", { class: "karte" }, h("h2", {}, `2 · Auswählen und umbenennen: ${src.name} → ${tgt.name}`),
      h("p", { class: "leise klein-t" }, "Umbenennungen gelten überall: Standard-Backend, use_backend-Regeln, Adressen und Bedingungen in den Regeln werden mitgeändert. Bei Namen, die es im Ziel schon gibt: umbenennen oder „vorhandenes benutzen“ (dann zeigen die Verweise auf das Objekt im Ziel).")),
    tabellen, optionen,
    h("div", { class: "karte" }, h("h2", {}, "3 · Vorschau und Import", h("span", { class: "rechts" },
      h("button", { onclick: async () => {
        leeren(ergebnis, h("span", { class: "dreher" }), " erzeuge Vorschau …");
        try {
          const r = await api("POST", "/api/servers/migration/preview", daten());
          leeren(ergebnis, r.warnings.length ? h("ul", {}, r.warnings.map(x => h("li", { class: "klein-t" }, punkt("warn"), " ", x))) : null,
            h("pre", {}, r.preview.split("\n").map(z => h("span", { class: z.startsWith("+") ? "plus" : z.startsWith("!") ? "minus" : z.startsWith("→") ? "at" : "" }, z + "\n"))));
        } catch (e) { leeren(ergebnis, h("pre", {}, e.message)); }
      } }, "Vorschau"),
      h("button", { class: "p", onclick: async e => {
        if (!(await bestaetigen(`Auswahl in „${tgt.name}“ importieren? Das Ziel wird nur gespeichert, aktiv wird es erst mit „Prüfen/Übernehmen“.`, "Importieren", false))) return;
        e.target.disabled = true; leeren(ergebnis, h("span", { class: "dreher" }), " importiere …");
        try {
          const r = await api("POST", "/api/servers/migration/apply", daten());
          leeren(ergebnis, h("p", {}, punkt(r.ok ? "ok" : "bad"), r.ok ? " Import gespeichert." : " Import nicht vollständig."),
            h("div", { class: "log" }, r.log.map(x => h("div", { class: x.ok ? (x.warn ? "" : "ok") : "bad" }, h("div", {}, x.text, x.details ? h("small", {}, x.details) : null)))),
            r.warnings.length ? h("ul", {}, r.warnings.map(x => h("li", { class: "klein-t" }, x))) : null,
            h("div", { class: "leiste" }, h("button", { onclick: () => srvAktion(r.target, "check") }, `${tgt.name} prüfen`),
              h("button", { class: "p", onclick: () => srvOeffnen(r.target) }, `${tgt.name} öffnen`)));
        } catch (er) { leeren(ergebnis, h("pre", {}, er.message)); }
        e.target.disabled = false;
      } }, "Importieren"))),
    ergebnis));
}
