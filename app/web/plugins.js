/* MIRA – plugins: loading, navigation, approval, setup assistant (search → address → login) and plugin views.
 *
 * A plugin script registers itself with
 *   miraPlugin("<id>", ctx => ({ views: { <viewId>: { titel, zeigen(main, q) } } }))
 * ctx (one per connection): { conn, plugin, api(method, path, data, extra), link(path), can(level), rerender(), meId }
 *   path is relative to the app root, e.g. "/api/firewalls" – MIRA forwards it with the stored key.
 */
"use strict";

let PLUGINS = [];
const PLUGIN_FACTORY = {};
const PLUGIN_INST = {};
const PLUGIN_LOADED = new Set();
const LEVEL_ROLE = { read: "leser", write: "operator", admin: "admin" };
const LEVEL_RANK = { read: 1, write: 2, admin: 3 };

function miraPlugin(id, factory) { PLUGIN_FACTORY[id] = factory; Object.keys(PLUGIN_INST).forEach(k => { if (k.startsWith(id + ":")) delete PLUGIN_INST[k]; }); }

function loadAsset(url, css) {
  if (PLUGIN_LOADED.has(url)) return Promise.resolve();
  return new Promise((ok, fail) => {
    const el = css ? h("link", { rel: "stylesheet", href: url }) : h("script", { src: url });
    el.onload = () => { PLUGIN_LOADED.add(url); ok(); };
    el.onerror = () => fail(new Error("Plugin-Datei nicht ladbar: " + url));
    document.head.append(el);
  });
}

async function ladePlugins() {
  try { PLUGINS = (await api("GET", "/api/plugins")).plugins; } catch (e) { PLUGINS = []; return; }
  for (const p of PLUGINS) {
    if (p.state !== "active") continue;
    try {
      await Promise.all((p.styles || []).map(u => loadAsset(u, true)));
      for (const u of p.scripts || []) await loadAsset(u);
    } catch (e) { p.loadError = e.message; }
    ANSICHT[p.id] = { titel: p.name, zeigen: main => pluginStart(main, p.id) };
    for (const v of p.views) {
      ANSICHT[`${p.id}.${v.id}`] = { titel: `${p.name} · ${v.title}`, zeigen: (main, q) => pluginView(main, q, p.id, v.id) };
    }
  }
  for (const p of PLUGINS) if (p.state !== "active") ANSICHT[p.id] = { titel: p.name, zeigen: main => pluginStart(main, p.id) };
}

const pluginById = id => PLUGINS.find(p => p.id === id);

/** Navigation groups: static NAVI + one group per plugin (inserted before the MIRA group). */
function naviGruppen() {
  const extra = [];
  for (const p of PLUGINS) {
    if (p.state === "active" && p.connections.length && !p.loadError) {
      extra.push([p.name, [
        ...p.views.filter(v => !v.hidden).map(v => [`${p.id}.${v.id}`, v.title, null, v.level ? LEVEL_ROLE[v.level] : null]),
        [p.id, "Verbindung", null, "admin"]]]);
    } else if (p.state === "active") {
      extra.push([p.name, [[p.id, p.loadError ? "Fehler" : "Einrichten", null, null, { aus: true, zeichen: "neu", icon: p.icon }]]]);
    } else if (darf("admin") && (p.state === "new" || p.state === "changed")) {
      extra.push(["Plugins", [[p.id, p.name, null, "admin", { aus: true, zeichen: p.state === "new" ? "neu" : "geändert", icon: p.icon }]]]);
    }
  }
  const merged = [];
  for (const [g, e] of extra) { const x = merged.find(m => m[0] === g); if (x) x[1].push(...e); else merged.push([g, e]); }
  const i = NAVI.findIndex(([g]) => g === "MIRA");
  return [...NAVI.slice(0, i), ...merged, ...NAVI.slice(i)];
}

function activeConn(p) {
  if (!p.connections.length) return null;
  const id = Number(localStorage.getItem("mira.plugin." + p.id));
  return p.connections.find(c => c.id === id) || p.connections[0];
}

function pluginCtx(p, conn) {
  const base = `/api/p/${conn.id}`;
  return {
    conn, plugin: p, meId: null,
    api: (m, path, d, x) => api(m, base + path, d, x),
    link: path => base + path,
    can: level => darf(LEVEL_ROLE[level]) && LEVEL_RANK[level] <= (LEVEL_RANK[conn.role] || 0),
    rerender: () => zeige(),
  };
}

async function pluginView(main, q, pid, vid) {
  const p = pluginById(pid);
  if (!p || p.state !== "active") return pluginStart(main, pid);
  const conn = activeConn(p);
  if (!conn) return pluginStart(main, pid);
  const factory = PLUGIN_FACTORY[pid];
  if (!factory) throw new Error(p.loadError || `Das Plugin ${p.name} hat sich nicht angemeldet (miraPlugin fehlt).`);
  const key = `${pid}:${conn.id}`;
  const inst = PLUGIN_INST[key] || (PLUGIN_INST[key] = factory(pluginCtx(p, conn)));
  const view = inst.views[vid];
  if (!view) throw new Error(`Ansicht „${vid}“ fehlt im Plugin`);
  if (p.connections.length > 1) {
    $("#titel").append(" ", h("select", { class: "klein", onchange: e => { localStorage.setItem("mira.plugin." + pid, e.target.value); zeige(); } },
      p.connections.map(c => h("option", { value: c.id, selected: c.id === conn.id }, c.name))));
  }
  return view.zeigen(main, q);
}

function pluginKopf(p) {
  return h("div", { class: "plugin-kopf" }, p.icon ? h("img", { src: p.icon, alt: "" }) : null,
    h("div", {}, h("h2", {}, p.name, " ", h("span", { class: "leise klein-t" }, p.version)), p.title ? h("div", { class: "leise" }, p.title) : null,
      p.description ? h("p", { class: "klein-t" }, p.description) : null));
}

const stateMark = p => ({ active: marke("aktiv", "ok"), new: marke("neu – nicht freigegeben", "warn"), changed: marke("geändert – neu freigeben", "warn"),
  disabled: marke("ausgeschaltet"), invalid: marke("fehlerhaft", "bad"), untrusted: marke("nicht vertrauenswürdig signiert", "bad") }[p.state]);

async function pluginFreigeben(p) {
  if (!(await bestaetigen(`Plugin „${p.name}“ ${p.version} freigeben? Es bringt eigenen Programmcode mit, der in MIRA mit den Rechten des jeweiligen Benutzers läuft. Nur Plugins aus vertrauenswürdiger Quelle freigeben.`, "Freigeben", false))) return;
  await versuch(() => api("POST", `/api/plugins/${p.id}/approve`), "Freigegeben");
  await ladePlugins(); zeige();
}

/** Start page of a plugin: approve → set up → connections. */
async function pluginStart(main, pid) {
  await ladePlugins();
  const p = pluginById(pid);
  if (!p) { leeren(main, h("div", { class: "karte leer" }, "Plugin nicht (mehr) vorhanden.")); return; }
  const a = darf("admin");
  if (p.state !== "active") {
    leeren(main, h("div", { class: "karte" }, pluginKopf(p), h("p", {}, stateMark(p)),
      p.error ? h("pre", {}, p.error) : null,
      h("p", { class: "leise klein-t" }, `Quelle: ${p.source === "builtin" ? "mit MIRA geliefert" : "Plugin-Ordner"} · Fingerabdruck ${p.hash}`),
      p.source !== "builtin" ? h("p", {}, sigMark(p.signature), p.signature && p.signature.reason ? h("span", { class: "leise klein-t" }, " ", p.signature.reason) : null) : null,
      p.state === "new" || p.state === "changed" ? h("p", { class: "klein-t" }, "Ein Plugin bringt eigenen Programmcode mit und wird erst nach Freigabe durch einen Admin geladen. Ändern sich seine Dateien, muss es neu freigegeben werden. Plugins vertrauenswürdiger Herausgeber (Signatur) sind sofort aktiv.") : null,
      p.state === "untrusted" ? h("p", { class: "klein-t" }, "MIRA lässt nur signierte Plugins zu (Plugins › Vertrauen).") : null,
      a && !["invalid", "untrusted"].includes(p.state) ? h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => pluginFreigeben(p) }, p.state === "disabled" ? "Einschalten" : "Freigeben")) :
        !["invalid", "untrusted"].includes(p.state) ? h("p", { class: "leise" }, "Ein Admin muss das Plugin freigeben.") : null));
    return;
  }
  if (!p.connections.length) {
    leeren(main, h("div", { class: "karte" }, pluginKopf(p),
      h("p", {}, `${p.name} ist noch nicht eingerichtet – MIRA hat noch keinen Zugang.`),
      p.loadError ? h("pre", {}, p.loadError) : null,
      a ? h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => pluginAssistent(p) }, "Einrichtung starten")) :
        h("p", { class: "leise" }, "Ein Admin muss die Verbindung einrichten.")));
    if (a && !sessionStorage.getItem("mira.assistent." + pid)) { sessionStorage.setItem("mira.assistent." + pid, "1"); pluginAssistent(p); }
    return;
  }
  leeren(main,
    h("div", { class: "karte" }, pluginKopf(p),
      h("div", { class: "kacheln" }, p.views.filter(v => !v.hidden && (!v.level || darf(LEVEL_ROLE[v.level]))).map(v =>
        h("div", { class: "kachel klick", onclick: () => { location.hash = `#${p.id}.${v.id}`; } }, h("div", { class: "t" }, v.title))))),
    h("div", { class: "karte" }, h("h2", {}, "Verbindungen", h("span", { class: "rechts" },
      a ? h("button", { class: "klein", onclick: () => pluginAssistent(p) }, `+ Weitere ${p.name} verbinden`) : null)),
    tabelle([
      { t: "", w: c => punkt(c.status.startsWith("ok") ? "ok" : c.status.startsWith("fehler") ? "bad" : "warn") },
      { t: "Name", w: c => h("b", {}, c.name) }, { t: "Adresse", w: c => c.base_url },
      { t: "Rolle", w: c => marke({ read: "Lesen", write: "Bearbeiten", admin: "Admin" }[c.role]) },
      { t: "Status", w: c => h("span", { class: "klein-t" }, c.status.replace(/^(ok|fehler): /, ""), c.checked_at ? h("div", { class: "leise" }, datum(c.checked_at)) : null) },
      { t: "", w: c => h("button", { class: "klein", onclick: async e => {
        e.stopPropagation();
        const r = await api("POST", `/api/plugins/conns/${c.id}/test`).catch(er => ({ ok: false, meldung: er.message }));
        hinweis(r.meldung, !r.ok); zeige();
      } }, "Testen") }],
    p.connections, { klick: a ? c => pluginVerbindungDialog(p, c) : null })));
}

async function pluginVerbindungDialog(p, c) {
  const f = h("div", {}, h("div", { class: "raster" }, feld("Name", eingabe("name", c.name)), feld("Adresse", eingabe("base_url", c.base_url))),
    haken("Zertifikat prüfen", c.verify_tls, "nur bei HTTPS mit gültigem Zertifikat", "verify_tls").el,
    h("p", { class: "leise klein-t" }, `Verbunden von ${c.created_by} über den ${p.name}-Admin „${c.remote_user}“ · Rolle ${c.role}. Für andere Rechte: entfernen und neu verbinden.`));
  const r = await dialog(`${p.name} · ${c.name}`, f, [
    ["Entfernen", async () => {
      if (!(await bestaetigen(`Verbindung „${c.name}“ entfernen? MIRA widerruft dabei seinen Schlüssel in ${p.name}.`, "Entfernen"))) return false;
      const x = await versuch(() => api("DELETE", `/api/plugins/conns/${c.id}`)); hinweis(x.note || "Entfernt");
    }, "gefahr"],
    ["Abbrechen", null],
    ["Speichern", () => versuch(() => api("PUT", `/api/plugins/conns/${c.id}`, lesenForm(f)), "Gespeichert"), "p"]]);
  if (r) { await ladePlugins(); zeige(); }
}

/** Setup assistant: 1. search the network, 2. or enter the address, 3. log in once with an admin of the app. */
async function pluginAssistent(p) {
  let gewaehlt = null;
  const liste = h("div", { class: "plugin-funde" });
  const status = h("div", { class: "klein-t leise" });
  const netze = eingabe("networks", "", { placeholder: "leer = eigenes Netz · z. B. 192.168.10.0/24 192.168.20.0/24:8443" });
  const eigen = p.connect_type && p.connect_type !== "sysrix";
  const url = eingabe("url", "", { placeholder: eigen ? `z. B. https://<IP_${p.name.replace(/\s+/g, "")}>:4055` : "z. B. http://<IP_VERA>:8080" });
  const urlInfo = h("div", { class: "klein-t" });
  const name = eingabe("name", p.name);
  const login = h("div", {},
    h("div", { class: "raster" }, feld("Name in MIRA", name),
      feld(`${p.name}-Admin`, eingabe("username", "", { autocomplete: "off" })),
      feld("Passwort", eingabe("password", "", { type: "password", autocomplete: "new-password" })),
      feld("Rechte für MIRA", auswahl("role", [["admin", "Admin (alles, wie direkt in " + p.name + ")"], ["write", "Bearbeiten"], ["read", "Nur lesen"]], "admin"))),
    haken("Zertifikat prüfen", false, "nur bei HTTPS mit gültigem Zertifikat", "verify_tls").el,
    h("p", { class: "leise klein-t" }, eigen
      ? `${p.name} kennt keine API-Schlüssel für die Verwaltung. MIRA speichert Benutzer und Passwort deshalb verschlüsselt und meldet sich selbst an (nur lokale Konten). Was ein MIRA-Benutzer darf, richtet sich nach seiner Rolle in MIRA und den Rechten oben.`
      : `Benutzer und Passwort werden nur einmal an ${p.name} geschickt und nicht gespeichert. ${p.name} stellt MIRA dafür einen eigenen Schlüssel aus – widerrufbar in ${p.name} unter „Verbundene Apps“. Was ein MIRA-Benutzer darf, richtet sich zusätzlich nach seiner Rolle in MIRA.`));

  const waehle = (u, el) => {
    gewaehlt = u;
    liste.querySelectorAll(".fund").forEach(x => x.classList.toggle("an", x === el));
    if (el) url.value = "";
  };
  const suchen = async () => {
    leeren(liste); waehle(null);
    leeren(status, h("span", { class: "dreher" }), ` Suche ${p.name} im Netz …`);
    try {
      const r = await api("POST", `/api/plugins/${p.id}/discover`, { networks: netze.value });
      leeren(status, `${r.checked} Adressen geprüft (${r.networks.join(", ") || "nur Namen"}) – ${r.found.length ? r.found.length + " gefunden" : "nichts gefunden, Adresse unten eingeben"}.`);
      for (const x of r.found) {
        const el = h("div", { class: "fund klick", onclick: () => waehle(x.url, el) },
          punkt(x.setup_needed ? "warn" : "ok"), h("b", {}, x.url), " ", h("span", { class: "leise" }, `${x.name} ${x.version}`),
          x.setup_needed ? marke("noch nicht eingerichtet", "warn") : null, x.connected ? marke("schon verbunden") : null,
          x.also ? h("span", { class: "leise klein-t" }, "auch unter " + x.also.join(", ")) : null);
        liste.append(el);
        if (r.found.length === 1 && !x.connected) waehle(x.url, el);
      }
    } catch (e) { leeren(status, punkt("bad"), " ", e.message); }
  };
  url.addEventListener("input", () => { if (url.value) waehle(null); leeren(urlInfo); });
  const pruefen = async () => {
    leeren(urlInfo, "prüfe …");
    try { const r = await api("POST", `/api/plugins/${p.id}/probe`, { url: url.value }); leeren(urlInfo, punkt(r.setup_needed ? "warn" : "ok"), ` ${r.name} ${r.version} gefunden`, r.setup_needed ? " – noch nicht eingerichtet" : ""); }
    catch (e) { leeren(urlInfo, punkt("bad"), " ", e.message); }
  };

  const inhalt = h("div", { class: "plugin-assistent" },
    pluginKopf(p),
    h("h4", {}, `1 · ${p.name} im Netz finden`), status, liste,
    h("div", { class: "leiste" }, netze, h("button", { class: "klein", onclick: suchen }, "Suchen")),
    h("h4", {}, "… oder Adresse selbst eingeben"),
    h("div", { class: "leiste" }, url, h("button", { class: "klein", onclick: pruefen }, "Prüfen")), urlInfo,
    h("h4", {}, `2 · Bei ${p.name} anmelden`), login);
  setTimeout(suchen, 0);
  const r = await dialog(`${p.name} einrichten`, inhalt, [["Abbrechen", null], ["Verbinden", async () => {
    const ziel = url.value.trim() || gewaehlt;
    if (!ziel) { hinweis(`Bitte einen gefundenen ${p.name} auswählen oder die Adresse eingeben`, true); return false; }
    const d = { ...lesenForm(login), url: ziel };
    return await versuch(() => api("POST", `/api/plugins/${p.id}/connect`, d), `${p.name} verbunden`);
  }, "p"]]);
  if (r && r !== true) {
    localStorage.setItem("mira.plugin." + p.id, r.id);
    await ladePlugins();
    const first = pluginById(p.id).views.find(v => !v.hidden);
    location.hash = first ? `#${p.id}.${first.id}` : `#${p.id}`;
    zeige();
  }
}

// admin page (installed / catalog / sources / trust): plugin-catalog.js

// ------------------------------------------------------------------ overview: plugin tiles
(() => {
  const alt = ANSICHT.uebersicht.zeigen;
  ANSICHT.uebersicht.zeigen = async main => {
    await alt(main);
    const sichtbar = PLUGINS.filter(p => p.state === "active" || darf("admin"));
    if (!sichtbar.length) return;
    const karte = h("div", { class: "karte" }, h("h2", {}, "Programme (Plugins)"),
      h("div", { class: "kacheln" }, sichtbar.map(p => {
        const c = p.connections;
        const ok = c.length && c.every(x => x.status.startsWith("ok"));
        const text = p.state !== "active" ? { new: "neu – freigeben", changed: "geändert – neu freigeben", disabled: "ausgeschaltet", invalid: "fehlerhaft", untrusted: "nicht vertrauenswürdig" }[p.state]
          : c.length ? c.map(x => x.name).join(", ") : "noch nicht eingerichtet";
        return h("div", { class: "kachel klick", onclick: () => { location.hash = "#" + p.id; } },
          h("div", { class: "t" }, p.icon ? h("img", { src: p.icon, alt: "", class: "nav-icon" }) : null, punkt(p.state !== "active" || !c.length ? "warn" : ok ? "ok" : "bad"), p.name),
          h("div", { class: "s" }, text));
      })));
    main.children.length > 1 ? main.insertBefore(karte, main.children[1]) : main.append(karte);
  };
})();
