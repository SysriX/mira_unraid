/* MIRA – plugin page: installed plugins, catalog of the plugin directories (PIA), sources, trusted publishers,
 * and "manage directory" (packages, app keys, users, log, settings of a PIA – as far as the key allows). */
"use strict";

const SCOPE_TEXT = { read: "Lesen", publish: "Veröffentlichen", admin: "Verwalten" };
const sigMark = s => !s || s.state === "none" ? marke("unsigniert")
  : s.state === "invalid" ? marke("Signatur ungültig", "bad")
  : s.trusted ? marke(`signiert · ${s.publisher || s.key_id}`, "ok") : marke(`signiert (${s.key_id}) – Herausgeber unbekannt`, "warn");

function pluginTab() { return new URLSearchParams(location.hash.split("?")[1] || "").get("tab") || "installed"; }

ANSICHT.plugins = { titel: "Plugins", zeigen: async (main, q) => {
  const tab = q.get("tab") || "installed";
  const kopf = tabs([["installed", "Installiert"], ["catalog", "Katalog"], ["sources", "Quellen"], ["trust", "Vertrauen"]], tab,
    k => { location.hash = "#plugins?tab=" + k; });
  const inhalt = h("div");
  leeren(main, h("div", { class: "karte" }, kopf, inhalt));
  await ({ installed: pluginsInstalled, catalog: pluginsCatalog, sources: pluginsSources, trust: pluginsTrust }[tab] || pluginsInstalled)(inhalt);
} };

// ------------------------------------------------------------------ installed
async function pluginsInstalled(el) {
  const d = await api("GET", "/api/plugins");
  PLUGINS = d.plugins;
  const upd = await api("GET", "/api/plugin-catalog").then(c => Object.fromEntries(c.plugins.filter(x => x.update).map(x => [x.id, x]))).catch(() => ({}));
  leeren(el,
    h("div", { class: "leiste" },
      h("span", { class: "leise klein-t" }, "Plugins kommen aus dem ", h("a", { href: "#plugins?tab=catalog" }, "Katalog"),
        " (Plugin-Verzeichnisse) oder als Ordner nach ", h("code", {}, d.folder), "."),
      h("span", { class: "rechts" }, h("button", { class: "klein", onclick: async () => {
        await versuch(() => api("POST", "/api/plugins/rescan"), "Ordner neu eingelesen"); await ladePlugins(); zeige();
      } }, "Ordner neu einlesen"))),
    tabelle([
      { t: "", w: p => p.icon ? h("img", { src: p.icon, alt: "", class: "nav-icon" }) : "" },
      { t: "Plugin", w: p => h("div", {}, h("b", {}, p.name), " ", h("span", { class: "leise klein-t" }, p.version),
        upd[p.id] ? h("span", {}, " ", marke(`Update ${upd[p.id].latest.version}`, "akz")) : null,
        p.description ? h("div", { class: "leise klein-t" }, p.description) : null,
        p.error ? h("div", { class: "klein-t", style: { color: "var(--bad)" } }, p.error) : null) },
      { t: "Status", w: p => h("div", {}, stateMark(p), h("div", {}, p.source === "builtin" ? marke("mitgeliefert") : sigMark(p.signature))) },
      { t: "Herkunft", w: p => h("span", { class: "klein-t" }, p.source === "builtin" ? "mit MIRA geliefert" : p.install ? `Katalog · ${p.install.version}` : "Ordner",
        h("div", { class: "leise" }, p.hash)) },
      { t: "Verbindungen", w: p => p.connections.length ? p.connections.map(c => h("div", { class: "klein-t" }, punkt(c.status.startsWith("ok") ? "ok" : "bad"), " ", c.name)) : h("span", { class: "leise" }, "–") },
      { t: "", w: p => h("span", { class: "chips" },
        upd[p.id] ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); pluginInstallieren(upd[p.id]); } }, "Aktualisieren") : null,
        ["new", "changed", "disabled"].includes(p.state) ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); pluginFreigeben(p); } }, p.state === "disabled" ? "Einschalten" : "Freigeben") : null,
        p.state === "active" ? h("button", { class: "klein", onclick: async e => {
          e.stopPropagation();
          if (!(await bestaetigen(`Plugin „${p.name}“ ausschalten? Die Verbindungen bleiben gespeichert.`, "Ausschalten"))) return;
          await versuch(() => api("POST", `/api/plugins/${p.id}/disable`), "Ausgeschaltet"); await ladePlugins(); zeige();
        } }, "Ausschalten") : null,
        p.removable ? h("button", { class: "klein", onclick: e => { e.stopPropagation(); pluginMenue(p); } }, "…") : null) }],
    d.plugins, { klick: p => { location.hash = "#" + p.id; }, leer: "Noch keine Plugins – im Katalog installieren oder einen Plugin-Ordner kopieren." }));
}

async function pluginMenue(p) {
  const quellen = (await api("GET", "/api/plugin-sources")).sources.filter(s => s.scopes.includes("publish") || s.scopes.includes("admin"));
  const wahl = auswahl("sid", quellen.map(s => [s.id, s.name]), quellen[0] && quellen[0].id);
  const ersetzen = haken("Gleiche Version im Verzeichnis ersetzen", false, "nur mit Verwalten-Recht", "replace");
  const inhalt = h("div", {}, pluginKopf(p), h("p", {}, sigMark(p.signature)),
    h("h4", {}, "In ein Plugin-Verzeichnis hochladen"),
    quellen.length ? h("div", {}, feld("Verzeichnis", wahl), ersetzen.el,
      h("p", { class: "leise klein-t" }, p.signature && p.signature.state === "valid" ? "Die Signatur wird mit hochgeladen."
        : "Das Plugin ist nicht signiert – andere MIRA-Installationen müssen es dann von Hand freigeben.")) :
      h("p", { class: "leise klein-t" }, "Keine Quelle mit Recht „Veröffentlichen“ eingetragen."),
    h("h4", {}, "Entfernen"),
    h("p", { class: "leise klein-t" }, "Der Ordner wird nach ", h("code", {}, ".trash"), " im Plugin-Ordner verschoben, nicht gelöscht. Vorher die Verbindungen entfernen."));
  await dialog(`Plugin ${p.name}`, inhalt, [
    ["Entfernen", async () => {
      if (!(await bestaetigen(`Plugin „${p.name}“ entfernen?`, "Entfernen"))) return false;
      await versuch(() => api("DELETE", `/api/plugins/${p.id}`), "Entfernt"); await ladePlugins(); zeige();
    }, "gefahr"],
    ["Schließen", null],
    ...(quellen.length ? [["Hochladen", async () => {
      await versuch(() => api("POST", `/api/plugin-sources/${wahl.value}/publish/${p.id}${ersetzen.i.checked ? "?replace=1" : ""}`), "Hochgeladen");
    }, "p"]] : [])]);
}

// ------------------------------------------------------------------ catalog
async function pluginsCatalog(el) {
  leeren(el, h("p", { class: "leise" }, h("span", { class: "dreher" }), " Katalog wird geladen …"));
  let d = await api("GET", "/api/plugin-catalog");
  if (d.sources.some(s => s.enabled && !s.checked_at)) {          // a source that was never asked: ask now
    await api("POST", "/api/plugin-sources/refresh").catch(() => null);
    d = await api("GET", "/api/plugin-catalog");
  }
  const suche = eingabe("q", "", { placeholder: "Suchen …", type: "search" });
  const liste = h("div", { class: "kacheln katalog" });
  const zeichne = () => {
    const t = suche.value.trim().toLowerCase();
    const sichtbar = d.plugins.filter(p => !t || [p.name, p.title, p.description, p.id, p.author].join(" ").toLowerCase().includes(t));
    leeren(liste, sichtbar.length ? sichtbar.map(katalogKachel) : h("div", { class: "leer" },
      d.sources.length ? "Keine Plugins gefunden." : "Noch keine Quelle eingetragen – unter „Quellen“ ein Plugin-Verzeichnis hinzufügen."));
  };
  suche.addEventListener("input", zeichne);
  const stand = d.sources.filter(s => s.enabled).map(s => `${s.name}: ${s.status.replace(/^(ok|fehler): /, "")}${s.checked_at ? " (" + datum(s.checked_at) + ")" : ""}`).join(" · ");
  leeren(el,
    h("div", { class: "leiste" }, suche,
      h("span", { class: "leise klein-t" }, stand || "keine Quelle"),
      h("span", { class: "rechts" }, h("button", { class: "klein", onclick: async () => {
        await versuch(() => api("POST", "/api/plugin-sources/refresh"), "Quellen abgefragt"); zeige();
      } }, "Jetzt nachsehen"))),
    liste);
  zeichne();
}

/** Plugin symbol from the directory; a letter badge if there is none. */
function katalogIcon(p, klasse = "katalog-icon") {
  const ersatz = () => h("span", { class: klasse + " katalog-ersatz" }, (p.name || p.id || "?").slice(0, 1).toUpperCase());
  if (p.has_icon === false) return ersatz();
  return h("img", { src: p.icon, alt: "", class: klasse, onerror: e => e.target.replaceWith(ersatz()) });
}

function katalogKachel(p) {
  const i = p.installed;
  const v = p.latest;
  const knopf = !p.compatible ? h("span", { class: "leise klein-t" }, `braucht MIRA ${v.mira_min}`)
    : !i ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); pluginInstallieren(p); } }, "Installieren")
    : p.update ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); pluginInstallieren(p); } }, `Aktualisieren auf ${v.version}`)
    : h("button", { class: "klein", onclick: e => { e.stopPropagation(); location.hash = "#" + p.id; } }, "Öffnen");
  return h("div", { class: "kachel klick katalog-eintrag", onclick: () => katalogDetail(p) },
    h("div", { class: "t" }, katalogIcon(p),
      h("div", {}, p.name, h("div", { class: "leise klein-t", style: { textTransform: "none", letterSpacing: 0, fontWeight: 400 } }, p.title))),
    h("div", { class: "s" }, p.description),
    h("div", { class: "chips", style: { marginTop: "8px" } }, marke(v.version), v.signed ? (v.trusted ? marke("signiert", "ok") : marke("signiert · unbekannt", "warn")) : marke("unsigniert"),
      i ? marke(i.state === "active" ? `installiert ${i.version}` : `installiert ${i.version} · ${i.state}`, i.state === "active" ? "akz" : "warn") : null),
    h("div", { class: "katalog-fuss" }, h("span", { class: "leise klein-t" }, p.author ? p.author + " · " : "", p.source_name), knopf));
}

async function katalogDetail(p) {
  const vers = [...p.versions].sort((a, b) => (b.published_at || "").localeCompare(a.published_at || ""));
  const inhalt = h("div", {},
    h("div", { class: "plugin-kopf" }, katalogIcon(p, ""),
      h("div", {}, h("h2", {}, p.name, " ", h("span", { class: "leise klein-t" }, p.latest.version)), h("div", { class: "leise" }, p.title),
        h("p", { class: "klein-t" }, p.description),
        p.homepage ? h("a", { href: p.homepage, target: "_blank", rel: "noopener" }, p.homepage) : null)),
    h("h4", {}, "Versionen"),
    tabelle([
      { t: "Version", w: v => h("b", {}, v.version) },
      { t: "Veröffentlicht", w: v => h("span", { class: "klein-t" }, datum(v.published_at), v.published_by ? h("div", { class: "leise" }, v.published_by) : null) },
      { t: "Signatur", w: v => v.signed ? marke(v.key_id, "ok") : marke("unsigniert") },
      { t: "MIRA ab", w: v => v.mira_min || "–" },
      { t: "Größe", w: v => groesse(v.size), z: true },
      { t: "Änderungen", w: v => h("span", { class: "klein-t" }, v.changelog || "") },
      { t: "", w: v => v.version === (p.installed && p.installed.version) ? marke("installiert", "akz")
        : h("button", { class: "klein", onclick: () => pluginInstallieren(p, v.version) }, "Diese installieren") }], vers));
  await dialog(p.name, inhalt);
}

async function pluginInstallieren(p, version) {
  const v = version ? p.versions.find(x => x.version === version) : p.latest;
  const warnung = !v.signed ? "Das Paket ist nicht signiert. Nur installieren, wenn das Verzeichnis vertrauenswürdig ist."
    : !v.trusted ? `Das Paket ist mit dem Schlüssel „${v.key_id}“ signiert, der in MIRA nicht als vertrauenswürdig eingetragen ist.` : null;
  const alt = p.installed ? ` (installiert: ${p.installed.version})` : "";
  const ok = await dialog(`${p.name} ${v.version} installieren`, h("div", {},
    h("p", {}, `${p.name} ${v.version}${alt} aus „${p.source_name}“ installieren? MIRA lädt das Paket, prüft Prüfsumme und Signatur und legt es im Plugin-Ordner ab. Eine vorhandene Version wird nach `, h("code", {}, ".backup"), " verschoben."),
    v.changelog ? h("pre", {}, v.changelog) : null,
    warnung ? h("p", { class: "klein-t", style: { color: "var(--warn)" } }, warnung) : null,
    h("p", { class: "leise klein-t" }, "Ein Plugin bringt eigenen Programmcode mit, der in MIRA mit den Rechten des jeweiligen Benutzers läuft.")),
    [["Abbrechen", false], ["Installieren", true, "p"]]);
  if (!ok) return;
  const r = await versuch(() => api("POST", "/api/plugin-catalog/install", { source_id: p.source_id, id: p.id, version: v.version }),
    `${p.name} ${v.version} installiert`);
  await ladePlugins(); zeige();
  return r;
}

// ------------------------------------------------------------------ sources
async function pluginsSources(el) {
  const d = await api("GET", "/api/plugin-sources");
  leeren(el,
    h("p", { class: "leise klein-t" }, "Eine Quelle ist ein Plugin-Verzeichnis (PIA) mit einem App-Schlüssel. MIRA sieht regelmäßig nach, welche Plugins es dort gibt, und bietet sie im Katalog an. Mit dem Recht „Veröffentlichen“ oder „Verwalten“ lässt sich das Verzeichnis direkt aus MIRA pflegen."),
    h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => quelleDialog() }, "+ Quelle hinzufügen")),
    tabelle([
      { t: "", w: s => punkt(!s.enabled ? "" : s.status.startsWith("ok") ? "ok" : "bad") },
      { t: "Name", w: s => h("div", {}, h("b", {}, s.name), h("div", { class: "leise klein-t" }, s.url)) },
      { t: "Verzeichnis", w: s => h("span", { class: "klein-t" }, s.server || "–") },
      { t: "Rechte", w: s => h("span", { class: "chips" }, s.scopes.map(x => marke(SCOPE_TEXT[x] || x))) },
      { t: "Stand", w: s => h("span", { class: "klein-t" }, s.status.replace(/^(ok|fehler): /, ""), h("div", { class: "leise" }, s.checked_at ? datum(s.checked_at) : "", ` · alle ${s.interval_h} h`)) },
      { t: "", w: s => h("span", { class: "chips" },
        s.scopes.some(x => x === "publish" || x === "admin") ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); location.hash = `#pluginverzeichnis?id=${s.id}`; } }, "Verwalten") : null,
        h("button", { class: "klein", onclick: async e => { e.stopPropagation(); await versuch(() => api("POST", `/api/plugin-sources/${s.id}/refresh`), "Abgefragt"); zeige(); } }, "Abfragen")) }],
    d.sources, { klick: s => quelleDialog(s), leer: "Noch keine Quelle." }));
}

async function quelleDialog(s) {
  const f = h("div", {},
    h("div", { class: "raster" },
      feld("Name", eingabe("name", s ? s.name : "", { placeholder: "z. B. SysriX-Plugins" })),
      feld("Adresse", eingabe("url", s ? s.url : "", { placeholder: "https://plugins.example.org" })),
      feld("App-Schlüssel", eingabe("token", "", { type: "password", autocomplete: "new-password", placeholder: s ? "•••••• (unverändert)" : "pia_…" }),
        "im Plugin-Verzeichnis unter „App-Schlüssel“ erzeugen"),
      feld("Nachsehen alle … Stunden", eingabe("interval_h", s ? s.interval_h : 6, { type: "number", min: 1, max: 168 }))),
    haken("Zertifikat prüfen", s ? s.verify_tls : true, "aus nur für interne Adressen mit eigenem Zertifikat", "verify_tls").el,
    s ? haken("Quelle benutzen", s.enabled, null, "enabled").el : null);
  const r = await dialog(s ? `Quelle ${s.name}` : "Quelle hinzufügen", f, [
    ...(s ? [["Entfernen", async () => {
      if (!(await bestaetigen(`Quelle „${s.name}“ entfernen? Installierte Plugins bleiben erhalten.`, "Entfernen"))) return false;
      await versuch(() => api("DELETE", `/api/plugin-sources/${s.id}`), "Entfernt");
    }, "gefahr"]] : []),
    ["Abbrechen", null],
    [s ? "Speichern" : "Prüfen und hinzufügen", () => versuch(() => s ? api("PUT", `/api/plugin-sources/${s.id}`, lesenForm(f)) : api("POST", "/api/plugin-sources", lesenForm(f)),
      s ? "Gespeichert" : "Quelle hinzugefügt"), "p"]]);
  if (r) zeige();
}

// ------------------------------------------------------------------ trust
async function pluginsTrust(el) {
  const d = await api("GET", "/api/plugins/trust");
  let keys = d.keys.map(k => ({ ...k }));
  const liste = h("div");
  const zeichne = () => leeren(liste, tabelle([
    { t: "Herausgeber", w: k => h("b", {}, k.name || k.key_id) },
    { t: "Kennung", w: k => k.key_id },
    { t: "Öffentlicher Schlüssel", w: k => h("code", { class: "klein-t" }, k.public_key) },
    { t: "", w: k => k.builtin ? marke("eingebaut") : h("button", { class: "klein gefahr", onclick: () => { keys = keys.filter(x => x !== k); zeichne(); } }, "Entfernen") }],
    [...d.builtin_keys, ...keys]));
  zeichne();
  const neuName = eingabe("name", "", { placeholder: "Name, z. B. Meine Firma" });
  const neuKey = eingabe("public_key", "", { placeholder: "kennung:BASE64… (Ausgabe von mira_plugin.py keygen)" });
  const auto = haken("Signierte Plugins vertrauenswürdiger Herausgeber automatisch freigeben", d.auto_approve,
    "sonst muss jedes Plugin einmal von Hand freigegeben werden", "auto_approve");
  const nurSig = haken("Nur signierte Plugins zulassen", d.require_signature,
    "unsignierte oder fremd signierte Plugins werden weder installiert noch geladen", "require_signature");
  leeren(el,
    h("p", { class: "leise klein-t" }, "Plugins können mit einem Ed25519-Schlüssel signiert sein (Datei signature.json). Ist der Schlüssel hier eingetragen, weiß MIRA, von wem das Plugin stammt und dass es seither nicht verändert wurde."),
    liste,
    h("div", { class: "leiste", style: { marginTop: "10px" } }, neuName, neuKey, h("button", { class: "klein", onclick: () => {
      if (!neuKey.value.trim()) return;
      keys.push({ name: neuName.value.trim(), public_key: neuKey.value.trim(), key_id: "" }); neuName.value = neuKey.value = ""; zeichne();
    } }, "Hinzufügen")),
    auto.el, nurSig.el,
    h("div", { class: "leiste" }, h("button", { class: "p", onclick: async () => {
      await versuch(() => api("PUT", "/api/plugins/trust", { keys, auto_approve: auto.i.checked, require_signature: nurSig.i.checked }), "Gespeichert");
      await ladePlugins(); zeige();
    } }, "Speichern")));
}

// ------------------------------------------------------------------ manage a directory (PIA) from MIRA
ANSICHT.pluginverzeichnis = { titel: "Plugin-Verzeichnis", zeigen: async (main, q) => {
  const sid = Number(q.get("id"));
  const tab = q.get("tab") || "packages";
  const src = (await api("GET", "/api/plugin-sources")).sources.find(s => s.id === sid);
  if (!src) { leeren(main, h("div", { class: "karte leer" }, "Quelle nicht gefunden.")); return; }
  const r = (m, p, d) => api(m, `/api/plugin-sources/${sid}/remote/${p}`, d);
  const me = await r("GET", "me");
  const sc = me.scopes || [];
  $("#titel").textContent = `Plugin-Verzeichnis · ${src.name}`;
  const reiter = [["packages", "Pakete"]];
  if (sc.includes("admin")) reiter.push(["keys", "App-Schlüssel"], ["users", "Benutzer"], ["log", "Protokoll"], ["settings", "Einstellungen"]);
  const inhalt = h("div");
  leeren(main, h("div", { class: "karte" },
    h("h2", {}, src.name, h("span", { class: "leise klein-t", style: { textTransform: "none", letterSpacing: 0, fontWeight: 400 } }, `${src.url} · ${me.server ? me.server.name + " " + me.server.version : ""} · angemeldet als ${me.name}`),
      h("span", { class: "rechts" }, h("button", { class: "klein", onclick: () => { location.hash = "#plugins?tab=sources"; } }, "Zurück"))),
    tabs(reiter, tab, k => { location.hash = `#pluginverzeichnis?id=${sid}&tab=${k}`; }), inhalt));
  await ({ packages: pvPakete, keys: pvSchluessel, users: pvBenutzer, log: pvProtokoll, settings: pvEinstellungen }[tab] || pvPakete)(inhalt, r, sid, me);
} };

async function pvPakete(el, r, sid) {
  const d = await r("GET", "plugins");
  const datei = h("input", { type: "file", accept: ".zip,application/zip", style: { display: "none" } });
  const ersetzen = haken("gleiche Version ersetzen", false, null, "replace");
  datei.addEventListener("change", async () => {
    const f = datei.files[0]; if (!f) return;
    await versuch(() => api("POST", `/api/plugin-sources/${sid}/remote/plugins${ersetzen.i.checked ? "?replace=1" : ""}`, f), `${f.name} hochgeladen`);
    await api("POST", `/api/plugin-sources/${sid}/refresh`).catch(() => null); zeige();
  });
  const zeilen = [];
  for (const p of d.plugins) for (const v of p.versions) zeilen.push({ p, v });
  leeren(el,
    h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => datei.click() }, "Paket hochladen (.zip)"), ersetzen.el, datei,
      h("span", { class: "leise klein-t" }, "Paket bauen: ", h("code", {}, "py tools/mira_plugin.py pack plugin_mira --key …"))),
    tabelle([
      { t: "", w: x => katalogIcon({ ...x.p, icon: `/api/plugin-sources/${sid}/remote/plugins/${x.p.id}/icon` }, "nav-icon") },
      { t: "Plugin", w: x => h("div", {}, h("b", {}, x.p.name), " ", h("span", { class: "leise klein-t" }, x.p.id), x.p.hidden ? h("span", {}, " ", marke("versteckt", "warn")) : null) },
      { t: "Version", w: x => h("span", {}, x.v.version, x.v.yanked ? h("span", {}, " ", marke("zurückgezogen", "warn")) : null) },
      { t: "Signatur", w: x => x.v.signed ? marke(x.v.key_id + (x.v.signature_valid === false ? " – ungültig" : ""), x.v.signature_valid === false ? "bad" : "ok") : marke("unsigniert") },
      { t: "Veröffentlicht", w: x => h("span", { class: "klein-t" }, datum(x.v.published_at), h("div", { class: "leise" }, x.v.published_by || "")) },
      { t: "Downloads", w: x => x.v.downloads ?? "", z: true },
      { t: "Größe", w: x => groesse(x.v.size), z: true },
      { t: "", w: x => h("span", { class: "chips" },
        h("button", { class: "klein", onclick: async () => {
          await versuch(() => r("PATCH", `plugins/${x.p.id}/versions/${x.v.version}`, { yanked: !x.v.yanked }), x.v.yanked ? "Wieder freigegeben" : "Zurückgezogen"); zeige();
        } }, x.v.yanked ? "Freigeben" : "Zurückziehen"),
        h("button", { class: "klein gefahr", onclick: async () => {
          if (!(await bestaetigen(`${x.p.name} ${x.v.version} endgültig aus dem Verzeichnis löschen? Das Paket ist danach weg.`, "Endgültig löschen"))) return;
          await versuch(() => r("DELETE", `plugins/${x.p.id}/versions/${x.v.version}`), "Gelöscht"); zeige();
        } }, "Löschen")) }],
    zeilen, { leer: "Noch keine Pakete im Verzeichnis." }));
}

async function pvSchluessel(el, r) {
  const d = await r("GET", "keys");
  leeren(el,
    h("p", { class: "leise klein-t" }, "App-Schlüssel sind die Zugänge für Programme (z. B. jede MIRA-Installation). Der Schlüssel wird nur einmal angezeigt; gespeichert ist nur sein Hash."),
    h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => pvSchluesselDialog(r) }, "+ App-Schlüssel")),
    tabelle([
      { t: "", w: k => punkt(k.enabled ? "ok" : "") },
      { t: "Name", w: k => h("div", {}, h("b", {}, k.name), h("div", { class: "leise klein-t" }, k.prefix + "…")) },
      { t: "Rechte", w: k => h("span", { class: "chips" }, k.scopes.map(x => marke(SCOPE_TEXT[x] || x))) },
      { t: "Plugins", w: k => k.plugins.length ? k.plugins.join(", ") : h("span", { class: "leise" }, "alle") },
      { t: "Netze", w: k => k.ip_allow || h("span", { class: "leise" }, "alle") },
      { t: "Zuletzt", w: k => h("span", { class: "klein-t" }, k.last_used_at ? datum(k.last_used_at) : "nie", h("div", { class: "leise" }, k.last_ip || "")) },
      { t: "Läuft ab", w: k => k.expires_at ? datum(k.expires_at) : "–" }],
    d.keys, { klick: k => pvSchluesselDialog(r, k), leer: "Noch keine App-Schlüssel." }));
}

async function pvSchluesselDialog(r, k) {
  const sc = k ? k.scopes : ["read"];
  const f = h("div", {},
    h("div", { class: "raster" },
      feld("Name", eingabe("name", k ? k.name : "", { placeholder: "z. B. MIRA Büro" })),
      feld("Nur diese Plugins", eingabe("plugins", k ? k.plugins.join(" ") : "", { placeholder: "leer = alle · z. B. vera uma" })),
      feld("Nur aus diesen Netzen", eingabe("ip_allow", k ? k.ip_allow : "", { placeholder: "leer = überall · z. B. 203.0.113.7 10.0.0.0/8" })),
      feld("Läuft ab am", eingabe("expires_at", k && k.expires_at ? k.expires_at.slice(0, 10) : "", { type: "date" }))),
    h("div", { class: "wahl" }, ["read", "publish", "admin"].map(s => h("label", {}, h("input", { type: "checkbox", name: "scope_" + s, checked: sc.includes(s) }),
      h("span", { class: "t" }, SCOPE_TEXT[s]), h("div", { class: "b" }, { read: "Katalog sehen, Pakete laden", publish: "Pakete hochladen, zurückziehen, löschen", admin: "Benutzer, Schlüssel, Einstellungen, Protokoll" }[s])))),
    k ? haken("Aktiv", k.enabled, null, "enabled").el : null);
  const lesen = () => {
    const d = lesenForm(f);
    return { name: d.name, plugins: d.plugins.split(/[\s,;]+/).filter(Boolean), ip_allow: d.ip_allow, expires_at: d.expires_at || null,
      scopes: ["read", "publish", "admin"].filter(s => d["scope_" + s]), ...(k ? { enabled: d.enabled } : {}) };
  };
  const res = await dialog(k ? `App-Schlüssel ${k.name}` : "Neuer App-Schlüssel", f, [
    ...(k ? [["Löschen", async () => {
      if (!(await bestaetigen(`App-Schlüssel „${k.name}“ löschen? Programme mit diesem Schlüssel verlieren sofort den Zugang.`, "Löschen"))) return false;
      await versuch(() => r("DELETE", `keys/${k.id}`), "Gelöscht");
    }, "gefahr"]] : []),
    ["Abbrechen", null],
    [k ? "Speichern" : "Erzeugen", () => versuch(() => k ? r("PATCH", `keys/${k.id}`, lesen()) : r("POST", "keys", lesen())), "p"]]);
  if (res && res.token) {
    await dialog("App-Schlüssel erzeugt", h("div", {}, h("p", {}, "Diesen Schlüssel jetzt kopieren – er wird nicht noch einmal angezeigt:"),
      h("pre", {}, res.token), h("button", { class: "klein", onclick: () => navigator.clipboard.writeText(res.token).then(() => hinweis("Kopiert")) }, "Kopieren")));
  }
  if (res) zeige();
}

async function pvBenutzer(el, r) {
  const d = await r("GET", "users");
  const ROLLE = { reader: "Lesen", maintainer: "Veröffentlichen", admin: "Verwalten" };
  leeren(el,
    h("p", { class: "leise klein-t" }, "Benutzer melden sich auf der Webseite des Plugin-Verzeichnisses an."),
    h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => pvBenutzerDialog(r) }, "+ Benutzer")),
    tabelle([
      { t: "", w: b => punkt(b.enabled ? "ok" : "") },
      { t: "Benutzer", w: b => h("div", {}, h("b", {}, b.username), b.display_name ? h("div", { class: "leise klein-t" }, b.display_name) : null) },
      { t: "Rolle", w: b => marke(ROLLE[b.role] || b.role) },
      { t: "Letzte Anmeldung", w: b => b.last_login_at ? datum(b.last_login_at) : "–" }],
    d.users, { klick: b => pvBenutzerDialog(r, b) }));
}

async function pvBenutzerDialog(r, b) {
  const f = h("div", { class: "raster" },
    feld("Benutzername", eingabe("username", b ? b.username : "", { disabled: !!b })),
    feld("Anzeigename", eingabe("display_name", b ? b.display_name : "")),
    feld("Rolle", auswahl("role", [["reader", "Lesen"], ["maintainer", "Veröffentlichen"], ["admin", "Verwalten"]], b ? b.role : "reader")),
    pwFeld("password", b ? "Neues Passwort" : "Passwort", b ? "leer = unverändert" : "mindestens 12 Zeichen"),
    b ? haken("Aktiv", b.enabled, null, "enabled").el : null);
  const res = await dialog(b ? `Benutzer ${b.username}` : "Neuer Benutzer", f, [
    ...(b ? [["Löschen", async () => {
      if (!(await bestaetigen(`Benutzer „${b.username}“ löschen?`, "Löschen"))) return false;
      await versuch(() => r("DELETE", `users/${b.id}`), "Gelöscht");
    }, "gefahr"]] : []),
    ["Abbrechen", null],
    [b ? "Speichern" : "Anlegen", () => {
      const d = lesenForm(f); if (b && !d.password) delete d.password; if (b) delete d.username;
      return versuch(() => b ? r("PATCH", `users/${b.id}`, d) : r("POST", "users", d), "Gespeichert");
    }, "p"]]);
  if (res) zeige();
}

async function pvProtokoll(el, r) {
  const d = await r("GET", "log?limit=300");
  leeren(el, tabelle([
    { t: "Zeit", w: e => h("span", { class: "klein-t" }, datum(e.at)) },
    { t: "", w: e => punkt(e.ok ? "ok" : "bad") },
    { t: "Wer", w: e => h("span", { class: "klein-t" }, e.actor, h("div", { class: "leise" }, e.ip)) },
    { t: "Aktion", w: e => e.action },
    { t: "Ziel", w: e => h("span", { class: "klein-t" }, e.target) },
    { t: "Details", w: e => h("span", { class: "klein-t leise" }, e.details) }], d.entries, { leer: "Noch nichts protokolliert." }));
}

async function pvEinstellungen(el, r) {
  const d = await r("GET", "settings");
  const f = h("div", {},
    h("div", { class: "raster" }, feld("Name des Verzeichnisses", eingabe("title", d.title)),
      feld("Paketgröße höchstens (MB)", eingabe("max_package_mb", d.max_package_mb, { type: "number", min: 1, max: 200 }))),
    haken("Nur signierte Pakete annehmen", d.require_signature, d.sodium ? "Signatur muss zu einem der Schlüssel unten passen" : "PHP-Erweiterung sodium fehlt – Signaturen können nicht geprüft werden", "require_signature").el,
    feld("Vertrauenswürdige Herausgeber (eine Zeile je Schlüssel: kennung:BASE64 Name)",
      h("textarea", { name: "trusted_keys", rows: 4 }, (d.trusted_keys || []).map(k => `${k.key_id}:${k.public_key} ${k.name || ""}`.trim()).join("\n"))));
  leeren(el, f, h("div", { class: "leiste" }, h("button", { class: "p", onclick: async () => {
    const v = lesenForm(f);
    v.trusted_keys = v.trusted_keys.split("\n").map(z => z.trim()).filter(Boolean).map(z => {
      const [kp, ...name] = z.split(/\s+/); const [key_id, public_key] = kp.split(":"); return { key_id, public_key, name: name.join(" ") };
    });
    v.max_package_mb = Number(v.max_package_mb);
    await versuch(() => r("PUT", "settings", v), "Gespeichert"); zeige();
  } }, "Speichern")));
}
