/* MIRA – Dienste: Authentik, HAProxy, nginx, grommunio, Exchange */
"use strict";

function modulKopf(vs, reiter, aktiv, wechsel, ...rechts) {
  return [h("div", { class: "leiste" }, vs.el, h("span", { class: "rechts" }, rechts)), tabs(reiter, aktiv, wechsel)];
}
function reiterSeite(typ, reiter, inhalte) {
  return async (main, q) => {
    const vs = verbAuswahl(typ, zeige);
    let aktiv = q.get("t") || reiter[0][0];
    const halter = h("div", {});
    const zeichnen = async () => {
      leeren(main, modulKopf(vs, reiter, aktiv, k => { aktiv = k; history.replaceState(null, "", `#${aktuelleRoute().name}?t=${k}`); zeichnen(); }), halter);
      leeren(halter, h("div", { class: "leer" }, "Lädt …"));
      try { await inhalte[aktiv](halter, vs.id, zeichnen); }
      catch (e) { leeren(halter, h("div", { class: "karte" }, h("h2", {}, punkt("bad"), "Fehler"), h("pre", {}, e.message))); }
    };
    await zeichnen();
  };
}

// ================================================================== Authentik
ANSICHT.authentik = { titel: "Authentik", typ: "authentik", zeigen: reiterSeite("authentik",
  [["anwendungen", "Anwendungen"], ["benutzer", "Benutzer"], ["gruppen", "Gruppen"], ["outposts", "Outposts"], ["quellen", "LDAP-Quellen"]], {
    anwendungen: async (el, vid, neu) => {
      const d = await api("GET", `/api/m/authentik/${vid}/anwendungen`);
      const op = darf("operator");
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Anwendungen", h("span", { class: "rechts" }, op ? h("button", { class: "p klein", onclick: () => akAnwendungDialog(vid, neu) }, "+ Anwendung") : null)),
        tabelle([{ t: "Name", w: a => a.name }, { t: "Anbieter", w: a => h("span", {}, marke(a.anbieter_typ || "–"), " ", h("span", { class: "klein-t leise" }, a.anbieter)) },
          { t: "Zugriff", w: a => a.gruppen.length ? h("div", { class: "chips" }, a.gruppen.map(g => h("span", { class: "chip" }, g.name,
            op ? h("button", { onclick: async e => { e.stopPropagation(); await versuch(() => api("POST", `/api/m/authentik/${vid}/anwendungen/${a.slug}/zugriff`, { binding_weg: g.binding }), "entfernt"); neu(); } }, "×") : null))) :
            a.andere_regeln ? marke(`${a.andere_regeln} Regel(n)`) : marke("alle angemeldeten", "warn") },
          { t: "", w: a => op ? h("span", {}, h("button", { class: "klein", onclick: async () => {
            const i = eingabe("g", "", { placeholder: "Gruppenname, z. B. grp_Nextcloud_Users" });
            const g = await dialog(`Zugriff auf ${a.name}`, feld("Gruppe hinzufügen", i, "Sobald eine Gruppe eingetragen ist, kommen nur noch deren Mitglieder hinein."), [["Abbrechen", null], ["Hinzufügen", () => i.value.trim() || false, "p"]]);
            if (g) { await versuch(() => api("POST", `/api/m/authentik/${vid}/anwendungen/${a.slug}/zugriff`, { gruppe: g }), "Zugriff gesetzt"); neu(); }
          } }, "+ Gruppe"), darf("admin") ? h("button", { class: "klein gefahr", onclick: async () => {
            if (!(await bestaetigen(`Anwendung ${a.name} samt Anbieter löschen?`, "Löschen"))) return;
            await versuch(() => api("DELETE", `/api/m/authentik/${vid}/anwendungen/${a.slug}`), "gelöscht"); neu();
          } }, "Löschen") : null) : "" }], d.anwendungen)));
    },
    benutzer: async (el, vid, neu) => {
      const s = eingabe("s", "", { placeholder: "Suchen …" });
      const l = h("div", {});
      const op = darf("operator");
      const laden = async () => {
        const d = await api("GET", `/api/m/authentik/${vid}/benutzer?suche=${encodeURIComponent(s.value)}`);
        leeren(l, tabelle([{ t: "", w: b => punkt(b.aktiv ? "ok" : "") }, { t: "Benutzer", w: b => b.name }, { t: "Name", w: b => b.anzeigename }, { t: "E-Mail", w: b => b.mail },
          { t: "Herkunft", w: b => marke(b.quelle === "ldap" ? "AD" : b.typ) }, { t: "Gruppen", w: b => h("span", { class: "klein-t" }, b.gruppen.join(", ")) },
          { t: "Zuletzt", w: b => datum(b.letzte_anmeldung) }], d.benutzer, { klick: op ? b => akBenutzerDialog(vid, b, laden) : null }));
      };
      let t; s.addEventListener("input", () => { clearTimeout(t); t = setTimeout(laden, 300); });
      leeren(el, h("div", { class: "karte" }, h("div", { class: "leiste" }, s, h("span", { class: "rechts" }, op ? h("button", { class: "klein", onclick: async () => {
        const f = h("div", { class: "raster" }, feld("Benutzername", eingabe("name")), feld("Name", eingabe("anzeigename")), feld("E-Mail", eingabe("mail")),
          feld("Art", auswahl("typ", [["internal", "intern"], ["external", "extern"], ["service_account", "Dienstkonto"]], "internal")), pwFeld("passwort", "Passwort", "leer = keins"));
        await dialog("Lokaler Authentik-Benutzer", h("div", {}, h("p", { class: "leise klein-t" }, "Normale Mitarbeiter im AD anlegen – Authentik übernimmt sie. Hier nur für Konten, die es nur in Authentik gibt."), f),
          [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/authentik/${vid}/benutzer`, lesenForm(f)), "angelegt"), "p"]]);
        laden();
      } }, "+ Nur-Authentik-Benutzer") : null)), l));
      await laden();
    },
    gruppen: async (el, vid, neu) => {
      const d = await api("GET", `/api/m/authentik/${vid}/gruppen`);
      const op = darf("operator");
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Gruppen", h("span", { class: "rechts" }, op ? h("button", { class: "klein", onclick: async () => {
        const f = h("div", {}, feld("Name", eingabe("name")), haken("Admin-Rechte in Authentik (Superuser)", false, null, "admin").el);
        await dialog("Neue Authentik-Gruppe", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/authentik/${vid}/gruppen`, lesenForm(f)), "angelegt"), "p"]]);
        neu();
      } }, "+ Gruppe") : null)),
      h("p", { class: "leise klein-t" }, "Gruppen aus dem AD kommen über die LDAP-Quelle und werden im AD gepflegt (MIRA › Gruppen)."),
      tabelle([{ t: "Gruppe", w: g => g.name }, { t: "", w: g => g.admin ? marke("Superuser", "warn") : "" }, { t: "Mitglieder", w: g => g.mitglieder, z: true }],
        d.gruppen, { klick: async g => {
          const x = await api("GET", `/api/m/authentik/${vid}/gruppen/${g.pk}`);
          await dialog(g.name, tabelle([{ t: "Benutzer", w: b => b.name }, { t: "Name", w: b => b.anzeigename }], x.liste, { leer: "keine Mitglieder" }),
            [...(darf("admin") ? [["Gruppe löschen", async () => { if (!(await bestaetigen(`${g.name} löschen?`))) return false; await versuch(() => api("DELETE", `/api/m/authentik/${vid}/gruppen/${g.pk}`), "gelöscht"); }, "gefahr"]] : []), ["Schließen", null]]);
          neu();
        } })));
    },
    outposts: async (el, vid) => {
      const d = await api("GET", `/api/m/authentik/${vid}/outposts`);
      leeren(el, h("div", { class: "karte" }, tabelle([{ t: "", w: o => punkt(o.verbunden.length ? "ok" : "bad") }, { t: "Outpost", w: o => o.name }, { t: "Art", w: o => marke(o.typ) },
        { t: "Anbieter", w: o => o.anbieter, z: true }, { t: "Verbunden", w: o => o.verbunden.map(v => `${v.host || "?"} (${v.version || "?"}, ${datum(v.letzter_kontakt)})`).join(", ") || "nicht verbunden" }], d.outposts)));
    },
    quellen: async (el, vid) => {
      const d = await api("GET", `/api/m/authentik/${vid}/quellen`);
      leeren(el, h("div", { class: "karte" }, tabelle([{ t: "", w: q => punkt(q.aktiv ? "ok" : "") }, { t: "Quelle", w: q => q.name }, { t: "Server", w: q => q.server },
        { t: "Basis", w: q => h("span", { class: "klein-t" }, q.basis) }, { t: "Abgleich", w: q => [q.sync_benutzer ? "Benutzer" : null, q.sync_gruppen ? "Gruppen" : null].filter(Boolean).join(" + ") },
        { t: "Stand", w: q => h("span", { class: "klein-t" }, q.status && q.status.last_sync_status ? `${q.status.last_sync_status} ${datum(q.status.last_sync)}` : (q.status && JSON.stringify(q.status).slice(0, 80)) || "") }], d.quellen, { leer: "Keine LDAP-Quelle" })));
    },
  }) };

async function akBenutzerDialog(vid, b, neu) {
  const f = h("div", { class: "raster" }, feld("Name", eingabe("anzeigename", b.anzeigename)), feld("E-Mail", eingabe("mail", b.mail)));
  const aktiv = haken("Aktiv", b.aktiv, null, "aktiv");
  const r = await dialog(`${b.name}`, h("div", {}, b.quelle === "ldap" ? h("p", { class: "leise klein-t" }, "Kommt aus dem AD – Änderungen dort machen, sonst überschreibt der nächste Abgleich sie.") : null, f, aktiv.el,
    h("p", { class: "klein-t" }, "Gruppen: ", b.gruppen.join(", ") || "–")),
  [["Wiederherstellungs-Link", async () => { const x = await api("POST", `/api/m/authentik/${vid}/benutzer/${b.pk}/wiederherstellung`); await dialog("Link (einmalig gültig)", h("pre", {}, x.link)); return false; }],
    ...(darf("admin") ? [["Löschen", async () => { if (!(await bestaetigen(`${b.name} in Authentik löschen?`))) return false; await versuch(() => api("DELETE", `/api/m/authentik/${vid}/benutzer/${b.pk}`), "gelöscht"); }, "gefahr"]] : []),
    ["Abbrechen", null], ["Speichern", () => versuch(() => api("PATCH", `/api/m/authentik/${vid}/benutzer/${b.pk}`, { ...lesenForm(f), aktiv: aktiv.i.checked }), "gespeichert"), "p"]]);
  if (r) neu();
}

async function akAnwendungDialog(vid, neu) {
  const art = auswahl("art", [["oidc", "OAuth2 / OpenID Connect (App kann SSO)"], ["proxy", "Proxy (App kann kein SSO – Authentik davor)"]], "oidc");
  const mehr = h("div", {});
  const zeichne = () => leeren(mehr, art.value === "oidc"
    ? h("div", {}, feld("Rücksprung-Adressen (eine je Zeile)", h("textarea", { name: "redirect_uris", rows: 3, placeholder: "https://cloud.firma.de/apps/user_oidc/code" }), "steht in der Doku der App"),
      feld("Start-Adresse", eingabe("start_url", "", { placeholder: "https://cloud.firma.de" })))
    : h("div", { class: "raster" }, feld("Externe Adresse", eingabe("extern_url", "", { placeholder: "https://app.firma.de" })),
      feld("Interne Adresse", eingabe("intern_url", "", { placeholder: "http://<IP_App>:8080" }), "nur bei Modus Proxy"),
      feld("Modus", auswahl("modus", [["proxy", "Proxy (Outpost leitet weiter)"], ["forward_single", "Forward-Auth (Proxy fragt Authentik)"]], "proxy")),
      haken("Eingebetteten Outpost verwenden", true, null, "outpost_eingebettet").el, haken("Basic-Auth-Header annehmen (für Apps/Clients)", false, null, "header_auth").el));
  art.addEventListener("change", zeichne); zeichne();
  const name = eingabe("name");
  const gruppe = eingabe("gruppe", "", { placeholder: "grp_App_Users" });
  const r = await dialog("Neue Anwendung in Authentik", h("div", {}, feld("Name", name), feld("Art", art), mehr, feld("Zugriff nur für Gruppe", gruppe, "leer = alle angemeldeten Benutzer")),
    [["Abbrechen", null], ["Anlegen", async () => {
      const d = { name: name.value, art: art.value, ...lesenForm(mehr), gruppen: gruppe.value ? [gruppe.value.trim()] : [] };
      if (d.redirect_uris !== undefined) d.redirect_uris = d.redirect_uris.split("\n");
      return versuch(() => api("POST", `/api/m/authentik/${vid}/anwendungen`, d));
    }, "p"]]);
  if (r && r.client_id) await dialog("Anwendung angelegt – Daten für die App", h("div", {}, h("p", {}, "Diese Werte in der App eintragen. Das Secret wird nur jetzt angezeigt."),
    h("pre", {}, `Issuer / Discovery: ${r.issuer}.well-known/openid-configuration\nClient-ID:     ${r.client_id}\nClient-Secret: ${r.client_secret}`)));
  neu();
}

// ================================================================== HAProxy
ANSICHT.haproxy = { titel: "HAProxy", typ: "haproxy", zeigen: reiterSeite("haproxy",
  [["status", "Status"], ["dienste", "Dienste"], ["aenderungen", "Änderungen"], ["dateien", "Dateien"]], {
    status: async (el, vid) => {
      const z = await api("GET", `/api/m/haproxy/${vid}/zustand`);
      const zeilen = z.stats.zeilen || [];
      const fe = zeilen.filter(r => r.typ === "frontend"), be = zeilen.filter(r => r.typ === "backend"), sv = zeilen.filter(r => r.typ === "server");
      const down = sv.filter(s => s.status && !/^UP|no check/i.test(s.status));
      leeren(el, h("div", { class: "kacheln" },
        h("div", { class: "kachel" }, h("div", { class: "w" }, fe.reduce((a, r) => a + r.scur, 0)), h("div", { class: "l" }, "Verbindungen jetzt")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, fe.reduce((a, r) => a + r.req_rate, 0)), h("div", { class: "l" }, "Anfragen/s")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, `${sv.length - down.length}/${sv.length}`), h("div", { class: "l" }, "Server erreichbar")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, z.offen ? "ja" : "nein"), h("div", { class: "l" }, "Ungespeicherte Änderungen"))),
      z.stats.fehler ? h("div", { class: "karte" }, punkt("bad"), " ", z.stats.fehler) : null,
      h("div", { class: "karte" }, h("h2", {}, "Backends und Server", h("span", { class: "rechts" }, h("a", { href: z.ui_url, target: "_blank", rel: "noopener" }, "haproxy-ui öffnen ↗"))),
        tabelle([{ t: "", w: r => punkt(/^UP|OPEN/i.test(r.status) ? "ok" : /no check/i.test(r.status) ? "" : "bad") }, { t: "Backend", w: r => r.proxy }, { t: "Server", w: r => r.typ === "backend" ? h("b", {}, "gesamt") : r.name },
          { t: "Status", w: r => r.status }, { t: "Prüfung", w: r => h("span", { class: "klein-t" }, r.check) }, { t: "jetzt", w: r => r.scur, z: true }, { t: "gesamt", w: r => r.stot, z: true },
          { t: "4xx", w: r => r.hrsp_4xx, z: true }, { t: "5xx", w: r => r.hrsp_5xx, z: true }, { t: "Fehler", w: r => r.econ + r.ereq, z: true }], [...be, ...sv].sort((a, b) => (a.proxy + a.typ).localeCompare(b.proxy + b.typ)))),
      h("div", { class: "karte" }, h("h2", {}, "Zertifikate", h("span", { class: "rechts" }, darf("operator") ? h("button", { class: "klein", onclick: () => hapAuftrag(vid, "zertifikate") }, "Jetzt holen") : null)),
        tabelle([{ t: "", w: c => punkt(c.tage == null ? "bad" : c.tage < 14 ? "bad" : c.tage < 30 ? "warn" : "ok") }, { t: "Datei", w: c => c.datei }, { t: "Namen", w: c => h("span", { class: "klein-t" }, (c.namen || []).join(", ") || c.cn) },
          { t: "läuft ab", w: c => datum(c.ende) }, { t: "Tage", w: c => c.tage, z: true }], z.zertifikate)));
    },
    dienste: async (el, vid, neu) => {
      const d = await api("GET", `/api/m/haproxy/${vid}/dienste`);
      const op = darf("operator");
      const zeilen = d.dienste.map(x => ({ ...x }));
      const body = h("tbody", {});
      const zeichne = () => leeren(body, zeilen.map((z, i) => h("tr", {},
        h("td", {}, eingabe("", z.name, { oninput: e => (z.name = e.target.value), disabled: !op, placeholder: "name.firma.de" })),
        h("td", {}, auswahl("", [["", "–"], ...d.backends.map(b => [b, b])], z.backend)),
        h("td", {}, auswahl("", [["alle", "alle"], ["intern", "nur intern"], ["extern", "nur extern"]], z.zugang || "alle")),
        h("td", {}, auswahl("", [["tls", "TLS-Ende (HAProxy)"], ["durch", "Durchreichen"]], z.modus || "tls")),
        h("td", {}, eingabe("", z.notiz, { oninput: e => (z.notiz = e.target.value), disabled: !op })),
        h("td", {}, op ? h("button", { class: "klein", onclick: () => { zeilen.splice(i, 1); zeichne(); } }, "×") : null))));
      body.addEventListener("change", e => {
        const tr = e.target.closest("tr"); const i = [...body.children].indexOf(tr); const s = tr.querySelectorAll("select");
        Object.assign(zeilen[i], { backend: s[0].value, zugang: s[1].value, modus: s[2].value });
      });
      zeichne();
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Dienste (Name → Backend)", h("span", { class: "rechts" }, h("a", { href: (await api("GET", `/api/m/haproxy/${vid}/zustand`)).ui_url, target: "_blank", rel: "noopener" }, "Assistent „Neue Adresse“ in haproxy-ui ↗"))),
        h("p", { class: "leise klein-t" }, "Änderungen landen erst im Entwurf. Unter „Änderungen“ prüfen und übernehmen – HAProxy springt bei Fehlern automatisch zurück."),
        h("div", { class: "tab-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Backend", "Zugang", "Modus", "Notiz", ""].map(t => h("th", {}, t)))), body)),
        op ? h("div", { class: "leiste", style: { marginTop: "10px" } }, h("button", { onclick: () => { zeilen.push({ name: "", backend: "", zugang: "alle", modus: "tls", notiz: "" }); zeichne(); } }, "+ Zeile"),
          h("span", { class: "rechts" }, h("button", { class: "p", onclick: async () => {
            const r = await versuch(() => api("POST", `/api/m/haproxy/${vid}/dienste`, { dienste: zeilen.filter(z => z.name) }), "im Entwurf gespeichert");
            if (r.hinweise && r.hinweise.length) dialog("Hinweise", h("ul", {}, r.hinweise.map(x => h("li", {}, x))));
          } }, "Speichern (Entwurf)"))) : null));
    },
    aenderungen: async (el, vid, neu) => {
      const [d, a] = await Promise.all([api("GET", `/api/m/haproxy/${vid}/diff`), api("GET", `/api/m/haproxy/${vid}/auftrag`)]);
      const diff = d.diff || "";
      const pre = h("pre", {}, diff ? diff.split("\n").map(z => h("span", { class: z.startsWith("+") ? "plus" : z.startsWith("-") ? "minus" : z.startsWith("@@") ? "at" : "" }, z + "\n")) : "Keine Unterschiede zur laufenden Konfiguration.");
      const erg = a.ergebnis;
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Entwurf ↔ laufend", h("span", { class: "rechts" },
        darf("operator") ? [h("button", { class: "klein", onclick: () => hapAuftrag(vid, "pruefen", neu) }, "Prüfen"),
          h("button", { class: "klein gefahr", disabled: !diff, onclick: async () => { if (await bestaetigen("Entwurf verwerfen?")) { await versuch(() => api("POST", `/api/m/haproxy/${vid}/verwerfen`), "verworfen"); neu(); } } }, "Verwerfen")] : null,
        darf("admin") ? [h("button", { class: "klein", onclick: async () => { if (await bestaetigen("Auf den vorherigen Stand zurückspringen?", "Zurück")) hapAuftrag(vid, "zurueck", neu); } }, "Zurückspringen"),
          h("button", { class: "p klein", disabled: !diff, onclick: async () => { if (await bestaetigen("Entwurf übernehmen? HAProxy prüft vorher und springt bei Problemen zurück.", "Übernehmen", false)) hapAuftrag(vid, "uebernehmen", neu); } }, "Übernehmen")] : null)), pre),
      h("div", { class: "karte" }, h("h2", {}, "Letzter Auftrag", h("span", { class: "rechts" }, a.waechter ? marke("Wächter läuft", "ok") : marke("Wächter antwortet nicht", "bad"))),
        erg ? [h("p", {}, punkt(erg.code === 0 ? "ok" : "bad"), ` ${erg.aktion} · ${erg.zeit} · Code ${erg.code}`), h("pre", {}, erg.log)] : h("p", { class: "leise" }, a.laeuft ? "läuft …" : "noch keiner")));
    },
    dateien: async (el, vid) => {
      const d = await api("GET", `/api/m/haproxy/${vid}/dateien`);
      const ed = h("textarea", { class: "gross", spellcheck: "false", disabled: !darf("admin") });
      let pfad = null;
      const liste = h("div", { class: "chips" }, d.dateien.map(p => h("button", { class: "klein", onclick: async e => {
        liste.querySelectorAll("button").forEach(b => b.classList.remove("p")); e.target.classList.add("p");
        pfad = p; ed.value = (await api("GET", `/api/m/haproxy/${vid}/datei?pfad=${encodeURIComponent(p)}`)).text;
      } }, p)));
      leeren(el, h("div", { class: "karte" }, liste), h("div", { class: "karte" }, ed,
        darf("admin") ? h("div", { class: "leiste", style: { marginTop: "8px" } }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: () => pfad && versuch(() => api("POST", `/api/m/haproxy/${vid}/datei`, { pfad, text: ed.value }), "im Entwurf gespeichert") }, "Speichern (Entwurf)"))) : null));
    },
  }) };

async function hapAuftrag(vid, aktion, neu) {
  await versuch(() => api("POST", `/api/m/haproxy/${vid}/auftrag`, { aktion }));
  hinweis(`Auftrag „${aktion}“ gestellt …`);
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const a = await api("GET", `/api/m/haproxy/${vid}/auftrag`);
    if (!a.laeuft && a.ergebnis) {
      await dialog(a.ergebnis.code === 0 ? `${aktion}: erfolgreich` : `${aktion}: Fehler (Code ${a.ergebnis.code})`, h("pre", {}, a.ergebnis.log));
      if (neu) neu();
      return;
    }
  }
  hinweis("Keine Antwort vom HAProxy-Container (läuft hap-waechter?)", true);
}

// ================================================================== nginx
ANSICHT.nginx = { titel: "nginx", typ: "nginx", zeigen: async (main) => {
  const vs = verbAuswahl("nginx", zeige);
  const vid = vs.id;
  const d = await api("GET", `/api/m/nginx/${vid}/hosts`);
  const op = darf("operator");
  leeren(main, h("div", { class: "leiste" }, vs.el, h("span", { class: "rechts" },
    h("button", { onclick: async () => { const r = await api("POST", `/api/m/nginx/${vid}/pruefen`); dialog(r.ok ? "Konfiguration in Ordnung" : "Fehler in der Konfiguration", h("pre", {}, r.ausgabe)); } }, "nginx -t"),
    op ? h("button", { onclick: () => versuch(() => api("POST", `/api/m/nginx/${vid}/neu-laden`), "neu geladen") }, "Neu laden") : null,
    op ? h("button", { class: "p", onclick: () => nginxHost(vid, null) }, "+ Neuer Host") : null)),
  h("div", { class: "karte" }, tabelle([{ t: "Datei", w: x => x.datei }, { t: "Namen", w: x => x.namen.join(", ") },
    { t: "Art", w: x => marke({ proxy: "Proxy", statisch: "Webseite", php: "PHP", umleitung: "Umleitung" }[x.typ] || x.typ) },
    { t: "Ziel", w: x => h("span", { class: "klein-t" }, x.host ? (x.host.ziel || x.host.root || (x.typ === "statisch" ? "eigener Ordner" : "")) : "") },
    { t: "", w: x => x.verwaltet ? marke("MIRA", "akz") : marke("von Hand") }],
  d.hosts, { klick: x => x.verwaltet ? nginxHost(vid, x) : nginxDatei(vid, x.datei), leer: "Noch keine Hosts" })));
} };

async function nginxHost(vid, x) {
  const hst = x ? x.host : { typ: "proxy", https: true, http_umleiten: true, websocket: true, sicherheits_header: true };
  const f = h("div", {});
  const typ = auswahl("typ", [["proxy", "Reverse-Proxy"], ["statisch", "Webseite (statisch)"], ["php", "Webseite mit PHP"], ["umleitung", "Umleitung"]], hst.typ);
  const art = h("div", {});
  const zeichne = () => {
    const t = typ.value;
    leeren(art,
      t === "proxy" ? h("div", { class: "raster" }, feld("Ziel", eingabe("ziel", hst.ziel, { placeholder: "http://<IP_App>:8080" })),
        haken("WebSockets", hst.websocket ?? true, null, "websocket").el, haken("Puffer aus (Streaming)", hst.puffer_aus, null, "puffer_aus").el,
        haken("Zertifikat des Ziels prüfen", hst.ziel_tls_pruefen, null, "ziel_tls_pruefen").el) : null,
      t === "statisch" || t === "php" ? h("div", { class: "raster" }, feld("Ordner im nginx-Container", eingabe("root", hst.root, { placeholder: "leer = eigener Ordner je Seite" })),
        t === "php" ? feld("PHP-FPM", eingabe("php_ziel", hst.php_ziel, { placeholder: "php:9000" })) : null,
        haken("Single-Page-App (alles auf index.html)", hst.spa, null, "spa").el, haken("Verzeichnisliste", hst.verzeichnisliste, null, "verzeichnisliste").el) : null,
      t === "umleitung" ? h("div", { class: "raster" }, feld("Ziel", eingabe("ziel", hst.ziel, { placeholder: "https://neu.firma.de" })),
        feld("Code", auswahl("code", [["301", "301 dauerhaft"], ["302", "302 vorübergehend"], ["307", "307"], ["308", "308"]], hst.code || "301")),
        haken("Pfad anhängen", hst.pfad_behalten ?? true, null, "pfad_behalten").el) : null);
  };
  typ.addEventListener("change", zeichne); zeichne();
  const orte = (hst.orte || []).map(o => ({ ...o }));
  const orteEl = h("div", {});
  const orteZ = () => leeren(orteEl, orte.map((o, i) => h("div", { class: "leiste" }, eingabe("", o.pfad, { placeholder: "/api/", oninput: e => (o.pfad = e.target.value) }),
    eingabe("", o.ziel, { placeholder: "http://<IP>:9000", oninput: e => (o.ziel = e.target.value) }),
    h("label", { class: "klein-t" }, h("input", { type: "checkbox", checked: o.websocket, onchange: e => (o.websocket = e.target.checked) }), " WS"),
    h("button", { class: "klein", onclick: () => { orte.splice(i, 1); orteZ(); } }, "×"))), h("button", { class: "klein", onclick: () => { orte.push({ pfad: "", ziel: "" }); orteZ(); } }, "+ Pfad mit eigenem Ziel"));
  orteZ();
  f.append(h("div", { class: "raster" }, feld("Name", eingabe("name", hst.name, { placeholder: "cloud", disabled: !!x }), "Dateiname"), feld("Art", typ),
    feld("Adressen (server_name)", eingabe("namen", hst.namen, { placeholder: "cloud.firma.de www.cloud.firma.de" }))),
  art,
  h("details", {}, h("summary", {}, "HTTPS, Ports, Header"), h("div", { class: "raster", style: { marginTop: "10px" } },
    haken("HTTPS", hst.https, "Zertifikat <name>.crt/.key im Zertifikat-Ordner, oder Pfade unten", "https").el, haken("HTTP → HTTPS umleiten", hst.http_umleiten ?? true, null, "http_umleiten").el,
    feld("Zertifikat (Pfad im Container)", eingabe("zert", hst.zert, { placeholder: "Standard" })), feld("Schlüssel (Pfad im Container)", eingabe("schluessel", hst.schluessel, { placeholder: "Standard" })),
    feld("HTTP-Port", eingabe("http_port", hst.http_port, { placeholder: "80" })), feld("HTTPS-Port", eingabe("https_port", hst.https_port, { placeholder: "443" })),
    haken("PROXY-Protokoll (von HAProxy send-proxy)", hst.proxy_protocol, null, "proxy_protocol").el,
    feld("Echte Client-IP vertrauen von", eingabe("echte_ip_von", hst.echte_ip_von, { placeholder: "<IP_HAProxy>" })),
    feld("Max. Upload", eingabe("max_upload", hst.max_upload, { placeholder: "z. B. 10G" })),
    haken("Sicherheits-Header", hst.sicherheits_header, null, "sicherheits_header").el, haken("gzip", hst.gzip, null, "gzip").el)),
  h("details", {}, h("summary", {}, "Weitere Pfade"), orteEl),
  h("details", {}, h("summary", {}, "Eigene Zeilen"), h("textarea", { name: "extra", rows: 4 }, hst.extra || "")));
  const daten = () => { const d = lesenForm(f); if (x) d.name = hst.name; d.orte = orte.filter(o => o.pfad && o.ziel); return d; };
  const op = darf("operator");
  await dialog(x ? `Host ${hst.name}` : "Neuer nginx-Host", f, [
    ["Vorschau", async () => { const r = await api("POST", `/api/m/nginx/${vid}/vorschau`, { host: daten() }); await dialog("Erzeugte Konfiguration", h("pre", {}, r.text)); return false; }],
    ...(x && op ? [["Löschen", async () => { if (!(await bestaetigen(`Host ${hst.name} löschen?`))) return false; await versuch(() => api("DELETE", `/api/m/nginx/${vid}/hosts/${x.datei}`), "gelöscht"); }, "gefahr"]] : []),
    ...(x && (hst.typ === "statisch" || hst.typ === "php") && !hst.root ? [["Dateien", async () => { await nginxWeb(vid, hst.name); return false; }]] : []),
    ["Abbrechen", null],
    ...(op ? [["Speichern + laden", async () => { const r = await api("POST", `/api/m/nginx/${vid}/hosts`, { host: daten(), alt_datei: x ? x.datei : null }); hinweis(r.laden); }, "p"]] : [])]);
  zeige();
}

async function nginxDatei(vid, datei) {
  const d = await api("GET", `/api/m/nginx/${vid}/datei?name=${encodeURIComponent(datei)}`);
  const t = h("textarea", { class: "gross", spellcheck: "false", disabled: !darf("admin") }, d.text);
  await dialog(datei, t, [["Schließen", null], ...(darf("admin") ? [["Speichern + laden", async () => { const r = await api("POST", `/api/m/nginx/${vid}/datei`, { datei, text: t.value }); hinweis(r.laden); }, "p"]] : [])]);
  zeige();
}

async function nginxWeb(vid, site) {
  let pfad = "";
  const liste = h("div", {});
  const laden = async () => {
    const d = await api("GET", `/api/m/nginx/${vid}/web/${encodeURIComponent(site)}?pfad=${encodeURIComponent(pfad)}`);
    leeren(liste, h("p", { class: "klein-t" }, `/${pfad}`, pfad ? h("button", { class: "klein", onclick: () => { pfad = pfad.split("/").slice(0, -1).join("/"); laden(); } }, "↑") : null),
      tabelle([{ t: "Name", w: e => e.ordner ? h("b", {}, e.name + "/") : e.name }, { t: "Größe", w: e => groesse(e.groesse), z: true },
        { t: "", w: e => darf("operator") ? h("button", { class: "klein gefahr", onclick: async ev => { ev.stopPropagation(); if (await bestaetigen(`${e.name} löschen?`)) { await api("DELETE", `/api/m/nginx/${vid}/web/${encodeURIComponent(site)}?pfad=${encodeURIComponent((pfad ? pfad + "/" : "") + e.name)}`); laden(); } } }, "×") : "" }],
      d.eintraege, { klick: e => { if (e.ordner) { pfad = (pfad ? pfad + "/" : "") + e.name; laden(); } }, leer: "leer" }));
  };
  const hoch = h("input", { type: "file", multiple: true, onchange: async e => {
    for (const f of e.target.files) { const fd = new FormData(); fd.append("datei", f); await versuch(() => api("POST", `/api/m/nginx/${vid}/web/${encodeURIComponent(site)}/hochladen?pfad=${encodeURIComponent(pfad)}`, fd)); }
    hinweis("hochgeladen"); laden();
  } });
  await laden();
  await dialog(`Dateien von ${site}`, h("div", {}, darf("operator") ? feld("Hochladen", hoch) : null, liste));
}

// ================================================================== grommunio
ANSICHT.grommunio = { titel: "grommunio", typ: "grommunio", zeigen: reiterSeite("grommunio",
  [["postfaecher", "Postfächer"], ["domaenen", "Domänen"], ["ldap", "LDAP-Import"], ["status", "Status"]], {
    postfaecher: async (el, vid, neu) => {
      const doms = (await api("GET", `/api/m/grommunio/${vid}/domaenen`)).domaenen;
      if (!doms.length) return leeren(el, h("div", { class: "karte leer" }, "Noch keine Domäne in grommunio."));
      let did = Number(localStorage.getItem("mira.gr.dom")) || doms[0].id;
      if (!doms.some(d => d.id === did)) did = doms[0].id;
      const sel = auswahl("d", doms.map(d => [d.id, d.name]), did);
      const s = eingabe("s", "", { placeholder: "Suchen …" });
      const l = h("div", {});
      const op = darf("operator");
      const laden = async () => {
        did = Number(sel.value); localStorage.setItem("mira.gr.dom", did);
        const d = await api("GET", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher?suche=${encodeURIComponent(s.value)}`);
        leeren(l, tabelle([{ t: "", w: p => punkt(p.status === 0 ? "ok" : "") }, { t: "Adresse", w: p => p.name }, { t: "Name", w: p => p.anzeigename }, { t: "Art", w: p => marke(p.art) },
          { t: "", w: p => p.ldap ? marke("AD") : "" }, { t: "Belegt", w: p => p.belegt_mb ? `${p.belegt_mb} MB` : "", z: true }, { t: "Grenze", w: p => p.quota_mb ? `${p.quota_mb} MB` : "–", z: true }],
        d.postfaecher, { klick: op ? p => grPostfach(vid, did, p, laden) : null }));
      };
      sel.addEventListener("change", laden);
      let t; s.addEventListener("input", () => { clearTimeout(t); t = setTimeout(laden, 300); });
      leeren(el, h("div", { class: "karte" }, h("div", { class: "leiste" }, sel, s, h("span", { class: "rechts" }, op ? h("button", { class: "p klein", onclick: async () => {
        const f = h("div", { class: "raster" }, feld("Name / Adresse", eingabe("name", "", { placeholder: "vorname.nachname" })), feld("Anzeigename", eingabe("anzeigename")),
          pwFeld("passwort", "Passwort", "nur für Postfächer ohne AD"), feld("Speicherplatz (MB)", eingabe("quota_mb", "", { type: "number" })));
        await dialog("Neues Postfach", h("div", {}, h("p", { class: "leise klein-t" }, "Für Mitarbeiter mit AD-Konto besser „LDAP-Import“ oder „Neuer Mitarbeiter“ nutzen."), f),
          [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/grommunio/${vid}/domaenen/${sel.value}/postfaecher`, lesenForm(f)), "angelegt"), "p"]]);
        laden();
      } }, "+ Postfach") : null)), l));
      await laden();
    },
    domaenen: async (el, vid, neu) => {
      const d = await api("GET", `/api/m/grommunio/${vid}/domaenen`);
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Domänen", h("span", { class: "rechts" }, darf("admin") ? h("button", { class: "klein", onclick: async () => {
        const f = h("div", { class: "raster" }, feld("Domäne", eingabe("name", "", { placeholder: "firma.de" })), feld("Anzeigename", eingabe("anzeige")), feld("Max. Benutzer", eingabe("max_benutzer", "100", { type: "number" })));
        await dialog("Neue Domäne", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/grommunio/${vid}/domaenen`, lesenForm(f)), "angelegt"), "p"]]); neu();
      } }, "+ Domäne") : null)),
      tabelle([{ t: "", w: x => punkt(x.status === 0 ? "ok" : "") }, { t: "Domäne", w: x => x.name }, { t: "Name", w: x => x.anzeige }, { t: "Benutzer", w: x => `${x.benutzer ?? "?"} / ${x.max_benutzer ?? "?"}`, z: true }], d.domaenen)));
    },
    ldap: async (el, vid) => {
      const doms = (await api("GET", `/api/m/grommunio/${vid}/domaenen`)).domaenen;
      const s = eingabe("q", "", { placeholder: "Name im AD suchen …" });
      const dsel = auswahl("d", doms.map(d => [d.id, d.name]), doms[0]?.id);
      const l = h("div", {});
      const such = async () => {
        if (s.value.length < 2) return;
        const r = await api("GET", `/api/m/grommunio/${vid}/ldap/suche?q=${encodeURIComponent(s.value)}`);
        leeren(l, tabelle([{ t: "Name", w: x => x.name }, { t: "E-Mail", w: x => x.email }, { t: "", w: x => darf("operator") ? h("button", { class: "klein", onclick: () => versuch(() => api("POST", `/api/m/grommunio/${vid}/domaenen/${dsel.value}/ldap-import`, { id: x.ID }), "übernommen") }, "Übernehmen") : "" }], r.treffer));
      };
      let t; s.addEventListener("input", () => { clearTimeout(t); t = setTimeout(such, 300); });
      leeren(el, h("div", { class: "karte" }, h("p", { class: "leise klein-t" }, "Benutzer aus dem verbundenen LDAP/AD als Postfach übernehmen (grommunio muss am AD hängen)."), h("div", { class: "leiste" }, s, dsel), l));
    },
    status: async (el, vid) => {
      const d = await api("GET", `/api/m/grommunio/${vid}/status`);
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Dienste"), tabelle([{ t: "", w: x => punkt(x.state === "active" || x.state === 0 ? "ok" : "bad") }, { t: "Dienst", w: x => x.name }, { t: "Zustand", w: x => String(x.state) }, { t: "seit", w: x => x.since ? datum(x.since * 1000) : "" }], d.dienste || [])),
        h("div", { class: "karte" }, h("h2", {}, "System"), h("pre", {}, JSON.stringify({ ...d, dienste: undefined }, null, 2))));
    },
  }) };

async function grPostfach(vid, did, p, neu) {
  const f = h("div", { class: "raster" }, feld("Anzeigename", eingabe("anzeigename", p.anzeigename)), feld("Speicherplatz (MB)", eingabe("quota_mb", p.quota_mb || "", { type: "number" })),
    feld("Status", auswahl("status", [["0", "aktiv"], ["1", "gesperrt"], ["4", "nur Freigegeben"]], String(p.status ?? 0))));
  const al = h("div", {});
  api("GET", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher/${p.id}/aliase`).then(d => leeren(al, h("div", { class: "chips" }, d.aliase.map(a => h("span", { class: "chip" }, typeof a === "string" ? a : a.aliasname || JSON.stringify(a)))))).catch(() => {});
  await dialog(p.name, h("div", {}, f, h("h3", {}, "Aliase"), al), [
    ["+ Alias", async () => { const i = eingabe("a", "", { placeholder: "alias@firma.de" }); const a = await dialog("Neuer Alias", i, [["Abbrechen", null], ["Anlegen", () => i.value || false, "p"]]); if (a) await versuch(() => api("POST", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher/${p.id}/aliase`, { alias: a }), "Alias angelegt"); return false; }],
    ...(p.ldap ? [] : [["Passwort", async () => { const pf = h("div", {}, pwFeld("passwort")); await dialog("Passwort setzen", pf, [["Abbrechen", null], ["Setzen", () => versuch(() => api("POST", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher/${p.id}/passwort`, lesenForm(pf)), "gesetzt"), "p"]]); return false; }]]),
    ...(darf("admin") ? [["Löschen", async () => { if (!(await bestaetigen(`Postfach ${p.name} samt Inhalt löschen?`, "Löschen"))) return false; await versuch(() => api("DELETE", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher/${p.id}`), "gelöscht"); }, "gefahr"]] : []),
    ["Abbrechen", null], ["Speichern", () => versuch(() => api("PATCH", `/api/m/grommunio/${vid}/domaenen/${did}/postfaecher/${p.id}`, lesenForm(f)), "gespeichert"), "p"]]);
  neu();
}

// ================================================================== Exchange
ANSICHT.exchange = { titel: "Exchange", typ: "exchange", zeigen: reiterSeite("exchange",
  [["postfaecher", "Postfächer"], ["verteiler", "Verteiler"], ["datenbanken", "Datenbanken"], ["server", "Server"], ["warteschlangen", "Warteschlangen"], ["skript", "PowerShell"]], {
    postfaecher: async (el, vid) => {
      const s = eingabe("s", "", { placeholder: "Name oder Adresse (leer = alle, kann dauern)" });
      const l = h("div", { class: "leer" }, "Suchen und Enter drücken");
      const op = darf("operator");
      const laden = async () => {
        leeren(l, "Lädt … (Exchange-PowerShell braucht ein paar Sekunden)");
        const d = await api("GET", `/api/m/exchange/${vid}/postfaecher?suche=${encodeURIComponent(s.value)}`);
        leeren(l, tabelle([{ t: "Name", w: p => p.DisplayName }, { t: "Adresse", w: p => p.PrimarySmtpAddress }, { t: "Art", w: p => marke(String(p.RecipientTypeDetails || "").replace("Mailbox", "")) },
          { t: "Datenbank", w: p => p.Database }, { t: "Grenze", w: p => p.UseDatabaseQuotaDefaults === true || p.UseDatabaseQuotaDefaults === "True" ? "Standard" : p.ProhibitSendQuota }], d.postfaecher,
        { klick: p => exPostfach(vid, p, laden) }));
      };
      s.addEventListener("keydown", e => { if (e.key === "Enter") laden(); });
      leeren(el, h("div", { class: "karte" }, h("div", { class: "leiste" }, s, h("button", { onclick: laden }, "Suchen"), h("span", { class: "rechts" },
        op ? h("button", { class: "klein", onclick: async () => {
          const f = h("div", { class: "raster" }, feld("AD-Benutzer", eingabe("identitaet", "", { placeholder: "Anmeldename oder UPN" })), feld("Datenbank", eingabe("datenbank", "", { placeholder: "leer = automatisch" })));
          await dialog("Postfach für vorhandenen AD-Benutzer", f, [["Abbrechen", null], ["Aktivieren", () => versuch(() => api("POST", `/api/m/exchange/${vid}/postfaecher/aktivieren`, lesenForm(f)), "Postfach aktiviert"), "p"]]);
        } }, "+ Postfach aktivieren") : null,
        op ? h("button", { class: "klein", onclick: async () => {
          const f = h("div", { class: "raster" }, feld("Art", auswahl("art", [["Shared", "Freigegeben"], ["Room", "Raum"], ["Equipment", "Gerät"]], "Shared")), feld("Name", eingabe("name")),
            feld("Alias", eingabe("alias")), feld("Adresse", eingabe("adresse", "", { placeholder: "info@firma.de" })));
          await dialog("Freigegebenes Postfach / Raum", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/exchange/${vid}/postfaecher/freigegeben`, lesenForm(f)), "angelegt"), "p"]]);
        } }, "+ Freigegeben/Raum") : null)), l));
    },
    verteiler: async (el, vid, neu) => {
      const d = await api("GET", `/api/m/exchange/${vid}/verteiler`);
      const op = darf("operator");
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Verteiler", h("span", { class: "rechts" }, op ? h("button", { class: "klein", onclick: async () => {
        const f = h("div", { class: "raster" }, feld("Name", eingabe("name")), feld("Alias", eingabe("alias")), feld("Adresse", eingabe("adresse")),
          feld("Typ", auswahl("typ", [["Distribution", "Verteiler"], ["Security", "E-Mail-aktivierte Sicherheitsgruppe"]], "Distribution")));
        await dialog("Neuer Verteiler", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/exchange/${vid}/verteiler`, lesenForm(f)), "angelegt"), "p"]]); neu();
      } }, "+ Verteiler") : null)),
      tabelle([{ t: "Name", w: g => g.DisplayName }, { t: "Adresse", w: g => g.PrimarySmtpAddress }, { t: "Typ", w: g => marke(String(g.GroupType || "")) }], d.verteiler, { klick: async g => {
        const m = await api("GET", `/api/m/exchange/${vid}/verteiler/${encodeURIComponent(g.PrimarySmtpAddress)}/mitglieder`);
        const i = eingabe("m", "", { placeholder: "Adresse oder Anmeldename" });
        await dialog(g.DisplayName, h("div", {}, tabelle([{ t: "Name", w: x => x.DisplayName }, { t: "Adresse", w: x => x.PrimarySmtpAddress },
          { t: "", w: x => op ? h("button", { class: "klein", onclick: async () => { await versuch(() => api("POST", `/api/m/exchange/${vid}/verteiler/${encodeURIComponent(g.PrimarySmtpAddress)}/mitglieder`, { weg: [x.PrimarySmtpAddress] }), "entfernt"); } }, "×") : "" }], m.mitglieder),
        op ? h("div", { class: "leiste", style: { marginTop: "10px" } }, i, h("button", { onclick: () => versuch(() => api("POST", `/api/m/exchange/${vid}/verteiler/${encodeURIComponent(g.PrimarySmtpAddress)}/mitglieder`, { hinzu: [i.value] }), "hinzugefügt") }, "Hinzufügen")) : null));
      } })));
    },
    datenbanken: async (el, vid) => {
      const d = await api("GET", `/api/m/exchange/${vid}/datenbanken`);
      leeren(el, h("div", { class: "karte" }, tabelle([{ t: "", w: x => punkt(String(x.Mounted) === "true" || x.Mounted === true ? "ok" : "bad") }, { t: "Datenbank", w: x => x.Name }, { t: "Server", w: x => x.Server },
        { t: "Größe", w: x => x.DatabaseSize }, { t: "frei", w: x => x.AvailableNewMailboxSpace }, { t: "Sendegrenze", w: x => x.ProhibitSendQuota }, { t: "Letzte Vollsicherung", w: x => x.LastFullBackup || "nie" }], d.datenbanken)));
    },
    server: async (el, vid) => {
      const [d, dom] = await Promise.all([api("GET", `/api/m/exchange/${vid}/server`), api("GET", `/api/m/exchange/${vid}/domaenen`)]);
      leeren(el, h("div", { class: "karte" }, h("h2", {}, "Server"), tabelle([{ t: "Server", w: x => x.Name }, { t: "Version", w: x => x.AdminDisplayVersion }, { t: "Edition", w: x => x.Edition }, { t: "Rollen", w: x => x.ServerRole }], d.server)),
        h("div", { class: "karte" }, h("h2", {}, "Akzeptierte Domänen"), tabelle([{ t: "Domäne", w: x => x.DomainName }, { t: "Art", w: x => x.DomainType }, { t: "", w: x => String(x.Default) === "true" || x.Default === true ? marke("Standard", "akz") : "" }], dom.domaenen)),
        h("div", { class: "karte" }, h("h2", {}, "Zertifikate"), tabelle([{ t: "Betreff", w: x => x.Subject }, { t: "Dienste", w: x => x.Services }, { t: "läuft ab", w: x => x.NotAfter }, { t: "Fingerabdruck", w: x => h("span", { class: "klein-t" }, x.Thumbprint) }], d.zertifikate)));
    },
    warteschlangen: async (el, vid) => {
      const d = await api("GET", `/api/m/exchange/${vid}/warteschlangen`);
      leeren(el, h("div", { class: "karte" }, tabelle([{ t: "Warteschlange", w: x => x.Identity }, { t: "Status", w: x => x.Status }, { t: "Nachrichten", w: x => x.MessageCount, z: true },
        { t: "Ziel", w: x => x.NextHopDomain }, { t: "Letzter Fehler", w: x => h("span", { class: "klein-t" }, x.LastError || "") }], d.warteschlangen, { leer: "Keine Warteschlangen" })));
    },
    skript: async (el, vid) => {
      if (!darf("admin")) return leeren(el, h("div", { class: "karte leer" }, "Nur für Admins."));
      const t = h("textarea", { rows: 8, spellcheck: "false", placeholder: "Get-Mailbox -ResultSize 10 | Format-Table DisplayName,PrimarySmtpAddress" });
      const aus = h("pre", {}, "");
      leeren(el, h("div", { class: "karte" }, h("p", { class: "leise klein-t" }, "Für alles, was MIRA (noch) nicht als Knopf hat. Wird im Protokoll mitgeschrieben."), t,
        h("div", { class: "leiste", style: { marginTop: "8px" } }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: async e => {
          e.target.disabled = true; leeren(aus, "läuft …");
          try { leeren(aus, (await api("POST", `/api/m/exchange/${vid}/skript`, { skript: t.value })).ausgabe); } catch (er) { leeren(aus, er.message); }
          e.target.disabled = false;
        } }, "Ausführen")))), h("div", { class: "karte" }, aus));
    },
  }) };

async function exPostfach(vid, p, neu) {
  const id = p.PrimarySmtpAddress || p.Alias;
  const stat = h("p", { class: "klein-t leise" }, "Statistik lädt …");
  const rechte = h("div", {});
  api("GET", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}/statistik`).then(s => leeren(stat, `Größe ${s.TotalItemSize || "?"} · ${s.ItemCount ?? "?"} Elemente · zuletzt ${s.LastLogonTime || "nie"}`)).catch(e => leeren(stat, e.message));
  api("GET", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}/berechtigungen`).then(r => leeren(rechte, r.vollzugriff.length ? h("div", { class: "chips" }, r.vollzugriff.map(x => h("span", { class: "chip" }, x.User))) : h("span", { class: "leise" }, "niemand"))).catch(() => {});
  const std = p.UseDatabaseQuotaDefaults === true || p.UseDatabaseQuotaDefaults === "True";
  const f = h("div", { class: "raster" }, feld("Warnung ab", eingabe("quota_warnung", std ? "" : p.IssueWarningQuota, { placeholder: "z. B. 9GB – leer = Standard" })),
    feld("Senden gesperrt ab", eingabe("quota_senden", std ? "" : p.ProhibitSendQuota, { placeholder: "10GB" })), feld("Empfangen gesperrt ab", eingabe("quota_empfangen", std ? "" : p.ProhibitSendReceiveQuota, { placeholder: "11GB" })),
    feld("Weiterleiten an", eingabe("weiterleitung", String(p.ForwardingSmtpAddress || "").replace(/^smtp:/i, ""))), haken("Kopie im Postfach behalten", p.DeliverToMailboxAndForward !== false && p.DeliverToMailboxAndForward !== "False", null, "auch_behalten").el,
    haken("Im Adressbuch ausblenden", p.HiddenFromAddressListsEnabled === true || p.HiddenFromAddressListsEnabled === "True", null, "ausblenden").el);
  const adr = (Array.isArray(p.EmailAddresses) ? p.EmailAddresses : String(p.EmailAddresses || "").split(" ")).filter(a => /^smtp:/i.test(a));
  const op = darf("operator");
  await dialog(p.DisplayName, h("div", {}, stat, h("h3", {}, "Adressen"), h("div", { class: "chips" }, adr.map(a => h("span", { class: "chip" }, a.replace(/^smtp:/i, ""), /^SMTP:/.test(a) ? marke("primär", "akz") : null,
    op && !/^SMTP:/.test(a) ? h("button", { onclick: () => versuch(() => api("PATCH", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}`, { adressen_weg: [a] }), "entfernt") }, "×") : null))),
  h("h3", {}, "Vollzugriff"), rechte, h("h3", {}, "Einstellungen"), f), [
    ...(op ? [["+ Adresse", async () => { const i = eingabe("a", "", { placeholder: "alias@firma.de" }); const a = await dialog("Weitere Adresse", i, [["Abbrechen", null], ["Hinzufügen", () => i.value || false, "p"]]); if (a) await versuch(() => api("PATCH", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}`, { adressen_hinzu: [a] }), "hinzugefügt"); return false; }],
      ["+ Berechtigung", async () => { const ff = h("div", { class: "raster" }, feld("Benutzer", eingabe("benutzer")), feld("Recht", auswahl("recht", [["FullAccess", "Vollzugriff"], ["SendAs", "Senden als"]], "FullAccess")), haken("Entfernen statt erteilen", false, null, "entfernen").el);
        await dialog("Berechtigung", ff, [["Abbrechen", null], ["OK", () => versuch(() => api("POST", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}/berechtigung`, lesenForm(ff)), "gespeichert"), "p"]]); return false; }]] : []),
    ...(darf("admin") ? [["Postfach trennen", async () => { if (!(await bestaetigen(`Postfach von ${p.DisplayName} trennen? Das AD-Konto bleibt, das Postfach wird nach der Aufbewahrungszeit gelöscht.`, "Trennen"))) return false; await versuch(() => api("POST", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}/deaktivieren`), "getrennt"); }, "gefahr"]] : []),
    ["Abbrechen", null], ...(op ? [["Speichern", () => versuch(() => api("PATCH", `/api/m/exchange/${vid}/postfaecher/${encodeURIComponent(id)}`, lesenForm(f)), "gespeichert"), "p"]] : [])]);
  neu();
}
