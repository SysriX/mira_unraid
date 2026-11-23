/* MIRA – Routing › Server: several HAProxy (haproxy-ui / OPNsense) and nginx (local / SSH) instances,
 * plus the pages for HAProxy on an OPNsense (os-haproxy over its API). */
"use strict";

const SRV_OPEN = { haproxy: "haproxy", nginx: "nginx" };
const srvPunkt = s => punkt(!s.enabled ? "" : s.status.startsWith("ok") ? "ok" : s.status.startsWith("fehler") ? "bad" : "warn");

function srvOeffnen(s) { localStorage.setItem("mira.verb.srv:" + s.kind, s.id); location.hash = "#" + SRV_OPEN[s.kind]; }

async function srvAktion(s, was) {
  if (was === "reload") {
    const text = s.kind === "haproxy"
      ? (s.access === "opnsense" ? `Gespeicherte Änderungen auf „${s.name}“ übernehmen? MIRA prüft vorher (haproxy -c) und übernimmt nur bei fehlerfreier Prüfung.`
        : `Entwurf auf „${s.name}“ übernehmen? haproxy-ui prüft vorher und springt bei Problemen zurück.`)
      : `nginx auf „${s.name}“ prüfen und neu laden?`;
    if (!(await bestaetigen(text, s.kind === "haproxy" ? "Übernehmen" : "Neu laden", false))) return;
  }
  hinweis(was === "check" ? "prüfe …" : "läuft …");
  const r = await versuch(() => api("POST", `/api/servers/${s.id}/${was}`));
  await dialog(was === "check" ? (r.ok ? `${s.name}: Konfiguration in Ordnung` : `${s.name}: Fehler in der Konfiguration`)
    : `${s.name}: ${r.meldung || (r.ok ? "erledigt" : "Fehler")}`, h("pre", {}, r.ausgabe || r.pruefung || r.rest_diff || "–"));
}

// ------------------------------------------------------------------ server list
ANSICHT.server = { titel: "Server", zeigen: async main => {
  await ladeModule();
  const a = darf("admin"), op = darf("operator");
  const zeilen = SERVERS;
  const statusBox = {};
  leeren(main,
    h("div", { class: "karte" }, h("h2", {}, "HAProxy- und nginx-Server", h("span", { class: "rechts" },
      h("button", { class: "klein", onclick: async e => { e.target.disabled = true; await versuch(() => api("POST", "/api/servers/test-all"), "geprüft"); zeige(); } }, "Alle testen"),
      a ? h("button", { class: "p klein", onclick: () => serverDialog({}) }, "+ Server") : null)),
    h("p", { class: "leise klein-t" }, "Beliebig viele Instanzen nebeneinander: HAProxy über haproxy-ui oder das HAProxy-Plugin einer OPNsense, nginx lokal (Ordner + Docker-Socket) oder auf anderen Hosts per SSH. Unter HAProxy bzw. nginx wird oben zwischen den Servern umgeschaltet."),
    tabelle([
      { t: "", w: srvPunkt },
      { t: "Name", w: s => h("div", {}, h("b", {}, s.name), s.site ? h("div", { class: "leise klein-t" }, s.site) : null) },
      { t: "Art", w: s => marke(s.kind_title, s.kind === "haproxy" ? "akz" : "") },
      { t: "Zugang", w: s => h("span", { class: "klein-t" }, s.access_title) },
      { t: "Adresse", w: s => h("span", { class: "klein-t" }, s.address) },
      { t: "Status", w: s => (statusBox[s.id] = h("span", { class: "klein-t" }, s.enabled ? (s.status.replace(/^(ok|fehler): /, "") || "noch nicht geprüft") : "ausgeschaltet",
        s.checked_at ? h("div", { class: "leise" }, datum(s.checked_at)) : null)) },
      { t: "", w: s => h("span", { class: "knopfreihe", onclick: e => e.stopPropagation() },
        h("button", { class: "klein", onclick: () => srvOeffnen(s), disabled: !s.enabled }, "Öffnen"),
        h("button", { class: "klein", onclick: async () => { const r = await api("POST", `/api/servers/${s.id}/test`).catch(er => ({ ok: false, meldung: er.message })); hinweis(r.meldung, !r.ok); zeige(); } }, "Testen"),
        op && s.enabled ? h("button", { class: "klein", onclick: () => srvAktion(s, "check") }, "Prüfen") : null,
        (s.kind === "haproxy" ? a : op) && s.enabled ? h("button", { class: "klein", onclick: () => srvAktion(s, "reload") }, s.kind === "haproxy" ? "Übernehmen" : "Neu laden") : null) }],
    zeilen, { klick: a ? s => serverDialog(s) : null, leer: "Noch keine Server – „+ Server“." })));
} };

async function serverDialog(s) {
  if (!SERVER_ARTEN.length) await ladeModule();
  const neu = !s.id;
  let kind = s.kind || "haproxy";
  let access = s.access || (SERVER_ARTEN.find(x => x.kind === kind) || {}).access;
  const halter = h("div", {});
  const name = eingabe("name", s.name || "");
  const site = eingabe("site", s.site || "", { placeholder: "z. B. Unraid, Büro, Rechenzentrum" });
  const sort = eingabe("sort", s.sort ?? 100, { type: "number" });
  const aktiv = haken("Eingeschaltet", s.enabled ?? true);
  const erg = h("div", { class: "klein-t" });
  const kindSel = neu ? auswahl("kind", [["haproxy", "HAProxy"], ["nginx", "nginx"]], kind) : null;
  const accSel = h("select", {});
  const fuelleAcc = () => leeren(accSel, SERVER_ARTEN.filter(x => x.kind === kind).map(x => h("option", { value: x.access, selected: x.access === access }, x.title)));
  const zeichne = () => {
    const art = SERVER_ARTEN.find(x => x.access === access);
    const werte = s.access === access ? (s.settings || {}) : {};
    leeren(halter, h("p", { class: "leise klein-t" }, art.description), schemaFormular(art.fields, werte, s.access === access ? (s.secret_set || []) : []),
      access === "ssh" && werte.host_key ? h("p", { class: "klein-t" }, "Gemerkter Host-Schlüssel: ", h("code", {}, werte.host_key.slice(0, 40) + "…"), " ",
        h("button", { class: "klein", onclick: async () => { if (await bestaetigen("Gemerkten SSH-Host-Schlüssel vergessen? Beim nächsten Verbinden wird der dann gesehene Schlüssel gemerkt.", "Vergessen")) { await versuch(() => api("POST", `/api/servers/${s.id}/forget-host-key`), "vergessen"); s.settings = { ...werte, host_key: "" }; zeichne(); } } }, "vergessen")) : null);
    if (!name.value) name.value = art.title.split(" ")[0] === "nginx" ? "nginx" : "HAProxy";
  };
  if (kindSel) kindSel.addEventListener("change", () => { kind = kindSel.value; access = SERVER_ARTEN.find(x => x.kind === kind).access; fuelleAcc(); zeichne(); });
  accSel.addEventListener("change", () => { access = accSel.value; zeichne(); });
  fuelleAcc(); zeichne();
  const daten = () => {
    const w = lesenForm(halter);
    return { id: s.id, kind, access, name: name.value, site: site.value, sort: Number(sort.value) || 100, enabled: aktiv.i.checked, values: w };
  };
  await dialog(neu ? "Neuer Server" : `Server „${s.name}“`, h("div", {},
    h("div", { class: "raster" }, kindSel ? feld("Art", kindSel) : null, feld("Zugang", accSel), feld("Name", name), feld("Standort / Notiz", site), feld("Reihenfolge", sort)),
    aktiv.el, halter,
    h("div", { class: "leiste" }, h("button", { onclick: async () => { leeren(erg, "prüfe …"); const r = await api("POST", "/api/servers/test", daten()).catch(e => ({ ok: false, meldung: e.message })); leeren(erg, punkt(r.ok ? "ok" : "bad"), " ", r.meldung); } }, "Testen"), erg),
    s.migrated_from ? h("p", { class: "leise klein-t" }, `Aus der früheren Verbindung Nr. ${s.migrated_from} übernommen (MIRA 0.4.0).`) : null),
  [...(neu ? [] : [["Entfernen", async () => { if (!(await bestaetigen(`Server „${s.name}“ aus MIRA entfernen? Am Server selbst ändert sich nichts.`, "Entfernen"))) return false; await versuch(() => api("DELETE", `/api/servers/${s.id}`), "entfernt"); }, "gefahr"]]),
    ["Abbrechen", null],
    ["Speichern", async () => { const d = daten(); await versuch(() => neu ? api("POST", "/api/servers", d) : api("PUT", `/api/servers/${s.id}`, d), "gespeichert"); }, "p"]]);
  await ladeModule(); zeige();
}

// ------------------------------------------------------------------ HAProxy on OPNsense (os-haproxy)
const OPN_ARTEN = {
  frontend: { titel: "Frontends", eins: "Frontend", wichtig: ["enabled", "name", "description", "bind", "mode", "defaultBackend", "ssl_enabled", "ssl_certificates", "ssl_default_certificate", "linkedActions", "forwardFor", "connectionBehaviour"],
    spalten: ["name", "bind", "mode", "defaultBackend", "description"] },
  backend: { titel: "Backends", eins: "Backend", wichtig: ["enabled", "name", "description", "mode", "algorithm", "linkedServers", "healthCheckEnabled", "healthCheck", "proxyProtocol", "linkedActions"],
    spalten: ["name", "mode", "algorithm", "linkedServers", "description"] },
  server: { titel: "Server", eins: "Server", wichtig: ["enabled", "name", "description", "address", "port", "mode", "ssl", "sslVerify", "sslSNI", "weight", "checkport"],
    spalten: ["name", "address", "port", "mode", "ssl", "description"] },
  acl: { titel: "ACLs", eins: "ACL", wichtig: ["name", "description", "expression", "negate", "hdr", "hdr_beg", "hdr_end", "path_beg", "path", "src", "ssl_fc_sni", "custom_acl"],
    spalten: ["name", "expression", "description"] },
  action: { titel: "Aktionen", eins: "Aktion", wichtig: ["enabled", "name", "description", "testType", "linkedAcls", "operator", "type", "use_backend", "use_server", "http_request_action", "custom"],
    spalten: ["name", "type", "testType", "linkedAcls", "use_backend", "description"] },
};
const OPN_MULTI = /^(linked|ssl_certificates$|sslCA$|http_method$|basicAuth(Users|Groups)$|allowed(Users|Groups)$|ssl_clientAuth(CAs|CRLs)$)/;
const OPN_BOOL = /(^enabled$|Enabled$|_enabled$|^ssl$|^sslVerify$|^sslSNI_?$|^negate$|^caseSensitive$|^forwardFor$|^forwardedHeader$|^tuning_noport$|^ssl_hsts\w*$|^http2Enabled\w*$|^prometheus_enabled$)/;
const opnWert = (r, k) => { const v = r["%" + k] ?? r[k]; return v === undefined || v === null ? "" : String(v); };

const OPN_SEITE = async (main, q) => {
  const vs = verbAuswahl("srv:haproxy", zeige);
  const sid = vs.id;
  const reiter = [["status", "Status"], ...Object.entries(OPN_ARTEN).map(([k, a]) => [k, a.titel]), ["anwenden", "Prüfen & Übernehmen"]];
  let aktiv = q.get("t") || "status";
  if (!reiter.some(r => r[0] === aktiv)) aktiv = "status";
  const halter = h("div", {});
  const zeichnen = async () => {
    leeren(main, modulKopf(vs, reiter, aktiv, k => { aktiv = k; history.replaceState(null, "", `#haproxy?t=${k}`); zeichnen(); }, marke("OPNsense", "akz")), halter);
    leeren(halter, h("div", { class: "leer" }, "Lädt …"));
    try { await (aktiv === "status" ? opnStatus : aktiv === "anwenden" ? opnAnwenden : opnListe)(halter, sid, aktiv, zeichnen); }
    catch (e) { leeren(halter, h("div", { class: "karte" }, h("h2", {}, punkt("bad"), "Fehler"), h("pre", {}, e.message))); }
  };
  await zeichnen();
};

async function opnStatus(el, sid) {
  const z = await api("GET", `/api/srv/${sid}/opn/status`);
  const c = z.counters || [];
  const sv = c.filter(r => r.svname !== "FRONTEND" && r.svname !== "BACKEND");
  const down = sv.filter(r => !/^(UP|no check|OPEN)/i.test(r.status || ""));
  leeren(el, h("div", { class: "kacheln" },
    h("div", { class: "kachel" }, h("div", { class: "w" }, z.status_text), h("div", { class: "l" }, z.enabled ? "HAProxy (eingeschaltet)" : "HAProxy (in der OPNsense ausgeschaltet)")),
    h("div", { class: "kachel" }, h("div", { class: "w" }, z.info.CurrConns ?? "–"), h("div", { class: "l" }, "Verbindungen jetzt")),
    h("div", { class: "kachel" }, h("div", { class: "w" }, `${sv.length - down.length}/${sv.length}`), h("div", { class: "l" }, "Server erreichbar")),
    h("div", { class: "kachel" }, h("div", { class: "w" }, z.info.Version || "–"), h("div", { class: "l" }, "Version"))),
  h("div", { class: "karte" }, h("h2", {}, "Backends und Server", h("span", { class: "rechts" },
      darf("admin") ? ["start", "restart", "stop"].map(a => h("button", { class: "klein" + (a === "stop" ? " gefahr" : ""), onclick: async () => {
        if (!(await bestaetigen(`HAProxy auf der OPNsense: ${a}?`, a, a === "stop"))) return;
        const r = await versuch(() => api("POST", `/api/srv/${sid}/opn/service/${a}`)); hinweis(r.ok ? `${a}: OK` : `${a}: ${r.antwort}`, !r.ok);
      } }, { start: "Starten", restart: "Neu starten", stop: "Stoppen" }[a])) : null,
      h("a", { href: z.ui_url, target: "_blank", rel: "noopener" }, "OPNsense öffnen ↗"))),
    c.length ? tabelle([{ t: "", w: r => punkt(/^(UP|OPEN)/i.test(r.status) ? "ok" : /no check/i.test(r.status) ? "" : "bad") }, { t: "Proxy", w: r => r.pxname },
      { t: "Server", w: r => r.svname === "BACKEND" || r.svname === "FRONTEND" ? h("b", {}, r.svname.toLowerCase()) : r.svname },
      { t: "Status", w: r => r.status }, { t: "Prüfung", w: r => h("span", { class: "klein-t" }, r.check_status || "") },
      { t: "jetzt", w: r => r.scur, z: true }, { t: "gesamt", w: r => r.stot, z: true }, { t: "5xx", w: r => r.hrsp_5xx, z: true }], c)
      : h("div", { class: "leer" }, z.status === "running" ? "Keine Statistik (Socket nicht lesbar)" : "HAProxy läuft nicht – keine Statistik")));
}

async function opnListe(el, sid, art, neu) {
  const a = OPN_ARTEN[art];
  const d = await api("GET", `/api/srv/${sid}/opn/objects/${art}`);
  const op = darf("operator");
  const hatAn = art !== "acl";
  leeren(el, h("div", { class: "karte" }, h("h2", {}, a.titel, h("span", { class: "rechts" }, op ? h("button", { class: "p klein", onclick: () => opnObjekt(sid, art, null, neu) }, `+ ${a.eins}`) : null)),
    h("p", { class: "leise klein-t" }, "Änderungen werden in der OPNsense gespeichert, aber erst unter „Prüfen & Übernehmen“ wirksam."),
    tabelle([
      ...(hatAn ? [{ t: "", w: r => op ? h("input", { type: "checkbox", checked: r.enabled === "1", title: "ein/aus", onclick: e => e.stopPropagation(),
        onchange: async () => { await versuch(() => api("POST", `/api/srv/${sid}/opn/objects/${art}/${r.uuid}/toggle`), "umgeschaltet (noch nicht übernommen)"); neu(); } }) : punkt(r.enabled === "1" ? "ok" : "") }] : []),
      ...a.spalten.map(k => ({ t: k, w: r => k === "name" ? h("b", {}, opnWert(r, k)) : h("span", { class: "klein-t" }, opnWert(r, k)) }))],
    d.rows, { klick: r => opnObjekt(sid, art, r.uuid, neu), leer: `Keine ${a.titel}` })));
}

async function opnObjekt(sid, art, uuid, neu) {
  const a = OPN_ARTEN[art];
  const d = await api("GET", `/api/srv/${sid}/opn/objects/${art}/${uuid || "neu"}`);
  const f = d.fields;
  const op = darf("operator");
  const feldEl = k => {
    const v = f[k];
    if (v && typeof v === "object") {
      const multi = OPN_MULTI.test(k);
      const sel = h("select", { name: k, multiple: multi, size: multi ? Math.min(8, Math.max(3, v.options.length)) : null, disabled: !op },
        v.options.map(([key, label]) => h("option", { value: key, selected: v.selected.includes(key) }, label)));
      return feld(k, sel, multi ? "Mehrfachauswahl mit Strg/⌘" : null);
    }
    if (OPN_BOOL.test(k) && (v === "0" || v === "1" || v === "")) return haken(k, v === "1", null, k).el;
    return feld(k, eingabe(k, v, { disabled: !op }));
  };
  const wichtig = a.wichtig.filter(k => k in f);
  const rest = Object.keys(f).filter(k => !wichtig.includes(k)).sort();
  const form = h("div", {}, h("div", { class: "raster" }, wichtig.map(feldEl)),
    rest.length ? h("details", {}, h("summary", {}, `Weitere Felder (${rest.length})`), h("div", { class: "raster", style: { marginTop: "10px" } }, rest.map(feldEl))) : null);
  const lesen = () => {
    const out = {};
    form.querySelectorAll("[name]").forEach(i => {
      if (i.type === "checkbox") out[i.name] = i.checked ? "1" : "0";
      else if (i.multiple) out[i.name] = [...i.selectedOptions].map(o => o.value);
      else out[i.name] = i.value;
    });
    return out;
  };
  const r = await dialog(uuid ? `${a.eins} ${f.name || ""}` : `Neues ${a.eins}`, form, [
    ...(uuid && darf("admin") ? [["Löschen", async () => {
      if (!(await bestaetigen(`${a.eins} „${f.name || uuid}“ in der OPNsense löschen? (wirksam nach „Übernehmen“)`, "Löschen"))) return false;
      await versuch(() => api("DELETE", `/api/srv/${sid}/opn/objects/${art}/${uuid}`), "gelöscht (noch nicht übernommen)");
    }, "gefahr"]] : []),
    ["Abbrechen", null],
    ...(op ? [["Speichern", () => versuch(() => uuid ? api("PUT", `/api/srv/${sid}/opn/objects/${art}/${uuid}`, { fields: lesen() })
      : api("POST", `/api/srv/${sid}/opn/objects/${art}`, { fields: lesen() }), "gespeichert (noch nicht übernommen)"), "p"]] : [])]);
  if (r) neu();
}

async function opnAnwenden(el, sid, art, neu) {
  const d = await api("GET", `/api/srv/${sid}/opn/diff`);
  const diff = d.diff || "";
  const aus = h("pre", {}, "Noch nicht geprüft.");
  leeren(el, h("div", { class: "karte" }, h("h2", {}, "Gespeichert ↔ laufend", h("span", { class: "rechts" },
      darf("operator") ? h("button", { class: "klein", onclick: async () => { leeren(aus, "prüfe …"); const r = await versuch(() => api("POST", `/api/srv/${sid}/opn/configtest`)); leeren(aus, (r.ok ? "✓ " : "✗ ") + r.ausgabe); } }, "Prüfen (haproxy -c)") : null,
      darf("admin") ? h("button", { class: "p klein", onclick: async () => {
        if (!(await bestaetigen("Gespeicherte Konfiguration übernehmen? MIRA prüft vorher und übernimmt nur, wenn die Prüfung fehlerfrei ist.", "Übernehmen", false))) return;
        leeren(aus, "übernehme …");
        try { const r = await api("POST", `/api/srv/${sid}/opn/reconfigure`); leeren(aus, `${r.meldung}\n\n${r.pruefung}${r.rest_diff ? "\n\nAbweichung:\n" + r.rest_diff : ""}`); hinweis(r.meldung); }
        catch (e) { leeren(aus, e.message); hinweis("Nicht übernommen", true); }
      } }, "Übernehmen") : null)),
    h("p", { class: "leise klein-t" }, "Unterschied zwischen der aus den gespeicherten Einstellungen erzeugten Konfiguration (staging) und der laufenden. Er wird erst nach „Prüfen“ aktuell erzeugt."),
    h("pre", {}, diff ? diff.split("\n").map(z => h("span", { class: z.startsWith("+") ? "plus" : z.startsWith("-") ? "minus" : z.startsWith("@@") ? "at" : "" }, z + "\n")) : "Keine Unterschiede.")),
  h("div", { class: "karte" }, h("h2", {}, "Ergebnis"), aus),
  h("div", { class: "karte" }, h("details", {}, h("summary", {}, "Erzeugte Konfiguration ansehen"), h("pre", { id: "opn-conf" }, "…"))));
  el.querySelector("details").addEventListener("toggle", async e => {
    if (e.target.open) { const c = await api("GET", `/api/srv/${sid}/opn/config`); leeren(el.querySelector("#opn-conf"), c.text || "–"); }
  }, { once: true });
}

// ------------------------------------------------------------------ overview: server tiles
(() => {
  const alt = ANSICHT.uebersicht.zeigen;
  ANSICHT.uebersicht.zeigen = async main => {
    await alt(main);
    if (!SERVERS.length) return;
    const karte = h("div", { class: "karte" }, h("h2", {}, "Routing-Server", h("span", { class: "rechts" },
      h("button", { class: "klein", onclick: async e => { e.target.disabled = true; await versuch(() => api("POST", "/api/servers/test-all"), "geprüft"); await ladeModule(); zeige(); } }, "Server testen"),
      h("a", { href: "#server" }, "alle Server"))),
      h("div", { class: "kacheln" }, SERVERS.map(s => h("div", { class: "kachel klick", onclick: () => s.enabled ? srvOeffnen(s) : (location.hash = "#server") },
        h("div", { class: "t" }, srvPunkt(s), s.name), h("div", { class: "s" }, `${s.kind_title} · ${s.access_title}`, s.site ? ` · ${s.site}` : ""),
        h("div", { class: "s" }, s.enabled ? (s.status.replace(/^(ok|fehler): /, "") || "noch nicht geprüft") : "ausgeschaltet")))));
    main.children.length > 1 ? main.insertBefore(karte, main.children[1]) : main.append(karte);
  };
})();
