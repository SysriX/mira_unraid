/* MIRA – Oberfläche: Kern, Navigation, Anmeldung, Einrichtung, Verwaltung */
"use strict";

// ------------------------------------------------------------------ Hilfen
const $ = (s, r = document) => r.querySelector(s);
const ANSICHT = {};
let ICH = null, MODULE = [], ARTEN = {};

function h(tag, attrs, ...kinder) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "multiple") el[k] = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kinder.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
const leeren = (el, ...neu) => { el.replaceChildren(...neu.flat().filter(x => x !== null && x !== undefined && x !== false)); return el; };

async function api(methode, pfad, daten, extra = {}) {
  const opt = { method: methode, headers: { "X-MIRA": "1", ...(extra.headers || {}) }, credentials: "same-origin" };
  if (daten instanceof FormData) opt.body = daten;
  else if (daten !== undefined) { opt.body = JSON.stringify(daten); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(pfad, opt);
  let d = null;
  try { d = await r.json(); } catch { d = null; }
  if (r.status === 401 && !extra.still) { ICH = null; zeigeAnmeldung(); throw new Error("Bitte anmelden"); }
  if (!r.ok) {
    let t = d && d.detail;
    if (Array.isArray(t)) t = t.map(x => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("\n");
    throw new Error(t || `Fehler ${r.status}`);
  }
  return d;
}

let hinweisZeit;
function hinweis(text, fehler = false) {
  const el = $("#hinweis");
  el.textContent = text; el.className = fehler ? "bad" : ""; el.style.display = "block";
  clearTimeout(hinweisZeit);
  hinweisZeit = setTimeout(() => (el.style.display = "none"), fehler ? 9000 : 3500);
}

async function versuch(fn, ok) {
  try { const r = await fn(); if (ok) hinweis(ok); return r; }
  catch (e) { hinweis(e.message, true); throw e; }
}

function dialog(titel, inhalt, knoepfe = [["Schließen", null]]) {
  return new Promise(res => {
    const hg = h("div", { class: "dialog-hg" });
    const zu = v => { hg.remove(); res(v); };
    hg.addEventListener("mousedown", e => { if (e.target === hg) zu(null); });
    const kn = knoepfe.map(([t, wert, art]) => h("button", { class: art || "", onclick: async () => {
      if (typeof wert === "function") { try { const r = await wert(); if (r !== false) zu(r ?? true); } catch (e) { hinweis(e.message, true); } }
      else zu(wert);
    } }, t));
    hg.append(h("div", { class: "dialog" }, h("h3", {}, titel), inhalt, h("div", { class: "knoepfe" }, kn)));
    document.body.append(hg);
    const f = hg.querySelector("input,select,textarea"); if (f) f.focus();
  });
}
const bestaetigen = (text, knopf = "Ja", gefahr = true) =>
  dialog("Bitte bestätigen", h("p", {}, text), [["Abbrechen", false], [knopf, true, gefahr ? "p gefahr" : "p"]]);

function feld(titel, eingabe, hilfe) {
  return h("label", { class: "feld" }, h("span", {}, titel), eingabe, hilfe ? h("small", {}, hilfe) : null);
}
function haken(titel, an, hilfe, name) {
  const i = h("input", { type: "checkbox", checked: an, name });
  return { el: h("label", { class: "haken" }, i, h("span", {}, titel, hilfe ? h("small", {}, hilfe) : null)), i };
}
function eingabe(name, wert = "", attrs = {}) { return h("input", { name, value: wert ?? "", ...attrs }); }
function auswahl(name, optionen, wert) {
  return h("select", { name }, optionen.map(o => { const [v, t] = Array.isArray(o) ? o : [o, o]; return h("option", { value: v, selected: String(v) === String(wert) }, t); }));
}
function lesenForm(el) {
  const d = {};
  el.querySelectorAll("[name]").forEach(i => {
    if (i.type === "checkbox") d[i.name] = i.checked;
    else if (i.type === "radio") { if (i.checked) d[i.name] = i.value; }
    else d[i.name] = i.value;
  });
  return d;
}

/** Formular aus dem Feld-Schema eines Moduls. Passwörter: leer = unverändert (wenn gesetzt). */
function schemaFormular(felder, werte = {}, gesetzt = []) {
  const el = h("div", { class: "raster" });
  for (const f of felder) {
    const w = werte[f.name] ?? f.standard ?? "";
    let i;
    if (f.art === "bool") { const hk = haken(f.titel, w === true || w === "true", f.hilfe, f.name); el.append(hk.el); continue; }
    if (f.art === "select") i = auswahl(f.name, f.optionen, w);
    else if (f.art === "textarea") i = h("textarea", { name: f.name, rows: 3 }, w);
    else i = eingabe(f.name, f.art === "password" ? "" : w, { type: f.art === "password" ? "password" : f.art === "number" ? "number" : "text",
      placeholder: f.art === "password" && gesetzt.includes(f.name) ? "•••••• (unverändert)" : "", autocomplete: f.art === "password" ? "new-password" : "off" });
    el.append(feld(f.titel + (f.pflicht ? " *" : ""), i, f.hilfe));
    if (f.art === "textarea") el.lastChild.style.gridColumn = "1/-1";
  }
  return el;
}

/** Tabelle: spalten = [{t, w: zeile => wert|Node, z: rechtsbündig}] */
function tabelle(spalten, zeilen, { klick, leer = "Keine Einträge", an } = {}) {
  if (!zeilen || !zeilen.length) return h("div", { class: "leer" }, leer);
  return h("div", { class: "tab-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, spalten.map(s => h("th", { class: s.z ? "z" : "" }, s.t)))),
    h("tbody", {}, zeilen.map(z => h("tr", { class: (klick ? "klick " : "") + (an && an(z) ? "an" : ""), onclick: klick ? () => klick(z) : null },
      spalten.map(s => { const v = s.w(z); return h("td", { class: s.z ? "z" : "" }, v === null || v === undefined ? "" : v); }))))));
}
const marke = (t, art = "") => h("span", { class: "marke " + art }, t);
const punkt = art => h("span", { class: "punkt " + art });
const datum = s => { if (!s) return ""; const d = new Date(s); return isNaN(d) ? s : d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" }); };
const groesse = n => n == null ? "" : n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : n > 1e3 ? (n / 1e3).toFixed(0) + " kB" : n + " B";
const darf = r => ICH && ({ leser: 1, operator: 2, admin: 3 }[ICH.rolle] || 0) >= ({ leser: 1, operator: 2, admin: 3 }[r]);
function zufallsPasswort(n = 16) {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!$%&*+-?";
  const a = new Uint32Array(n); crypto.getRandomValues(a);
  let p = Array.from(a, x => abc[x % abc.length]).join("");
  return /[A-Z]/.test(p) && /[a-z]/.test(p) && /\d/.test(p) && /[^A-Za-z0-9]/.test(p) ? p : zufallsPasswort(n);
}
function pwFeld(name, titel = "Passwort", hilfe) {
  const i = eingabe(name, "", { type: "text", autocomplete: "new-password" });
  return feld(titel, h("div", { style: { display: "flex", gap: "6px" } }, i,
    h("button", { type: "button", onclick: () => { i.value = zufallsPasswort(); } }, "Erzeugen")), hilfe);
}
function tabs(namen, aktiv, wechsel) {
  return h("div", { class: "tabs" }, namen.map(([k, t]) => h("button", { class: k === aktiv ? "an" : "", onclick: () => wechsel(k) }, t)));
}

/** Verbindungs-Auswahl eines Moduls (merkt sich die letzte). */
function verbindungen(typ) { return ((MODULE.find(m => m.typ === typ) || {}).verbindungen || []).filter(v => v.aktiv); }
function verbAuswahl(typ, beiWechsel) {
  const vs = verbindungen(typ);
  if (!vs.length) return { id: null, el: null };
  let id = Number(localStorage.getItem("mira.verb." + typ));
  if (!vs.some(v => v.id === id)) id = vs[0].id;
  const el = vs.length > 1 ? h("select", { onchange: e => { localStorage.setItem("mira.verb." + typ, e.target.value); beiWechsel(); } },
    vs.map(v => h("option", { value: v.id, selected: v.id === id }, v.name))) : h("span", { class: "marke" }, vs[0].name);
  return { id, el };
}
function keineVerbindung(typ) {
  const m = MODULE.find(x => x.typ === typ) || { titel: typ, beschreibung: "" };
  return h("div", { class: "karte leer" }, h("p", {}, `Für ${m.titel} ist noch keine Verbindung eingerichtet.`), h("p", { class: "leise" }, m.beschreibung),
    darf("admin") ? h("button", { class: "p", onclick: () => verbindungDialog({ typ }) }, `${m.titel} verbinden`) : h("p", {}, "Ein Admin muss die Verbindung anlegen."));
}

function themeWahl(klasse = "") {
  return h("select", { class: "theme-wahl " + klasse, title: "Aussehen", onchange: e => themeSetzen(e.target.value) },
    window.MIRA_THEMES.map(([k, t]) => h("option", { value: k, selected: k === themeAktuell() }, t)));
}

// ------------------------------------------------------------------ Navigation / Rahmen
const NAVI = [
  ["", [["uebersicht", "Übersicht"]]],
  ["Identität", [["benutzer", "Benutzer", "ad"], ["gruppen", "Gruppen", "ad"], ["neu", "Neuer Mitarbeiter", "ad"]]],
  ["Dienste", [["authentik", "Authentik", "authentik"], ["haproxy", "HAProxy", "haproxy"], ["nginx", "nginx", "nginx"],
               ["grommunio", "grommunio", "grommunio"], ["exchange", "Exchange", "exchange"]]],
  ["MIRA", [["verbindungen", "Verbindungen"], ["zugang", "Zugang", null, "admin"], ["einstellungen", "Einstellungen", null, "admin"],
            ["protokoll", "Protokoll", null, "operator"]]],
];

function aktuelleRoute() { const [n, q] = location.hash.slice(1).split("?"); return { name: n || "uebersicht", q: new URLSearchParams(q || "") }; }

async function ladeModule() { MODULE = (await api("GET", "/api/module")).module; }

function rahmen() {
  const r = aktuelleRoute();
  const navi = h("aside", { id: "navi" },
    h("div", { class: "logo" }, h("img", { src: "/static/logo-kurz-dunkel.svg", alt: "MIRA", class: "wortmarke nur-dunkel" }), h("img", { src: "/static/logo-kurz-hell.svg", alt: "MIRA", class: "wortmarke nur-hell" }), h("small", {}, "Mail · Identity · Routing · Access")),
    NAVI.map(([gruppe, eintraege]) => [gruppe ? h("h4", {}, gruppe) : null,
      eintraege.filter(([, , , rolle]) => !rolle || darf(rolle)).map(([k, t, typ]) => {
        const n = typ ? verbindungen(typ).length : null;
        return h("a", { class: "nav" + (r.name === k ? " an" : "") + (typ && !n ? " aus" : ""), href: "#" + k,
          onclick: () => $("#navi").classList.remove("offen") }, t, typ && !n ? h("span", { class: "zahl" }, "–") : null);
      })]));
  const oben = h("div", { class: "oben" },
    h("button", { class: "menu-knopf", onclick: () => $("#navi").classList.toggle("offen") }, "☰"),
    h("h1", { id: "titel" }, ""),
    h("div", { class: "rechts" }, themeWahl(), h("span", { class: "leise klein-t" }, ICH.anzeigename), marke(ICH.rolle, "akz"),
      ICH.quelle === "lokal" ? h("button", { class: "klein", onclick: eigenesPasswort }, "Passwort") : null,
      h("button", { class: "klein", onclick: async () => { await api("POST", "/api/abmelden"); ICH = null; start(); } }, "Abmelden")));
  const main = h("main", { id: "inhalt" });
  leeren($("#app"), h("div", { class: "rahmen" }, navi, h("div", {}, oben, main)));
  return main;
}

async function zeige() {
  if (!ICH) return;
  const r = aktuelleRoute();
  const a = ANSICHT[r.name] || ANSICHT.uebersicht;
  const main = rahmen();
  $("#titel").textContent = a.titel;
  document.title = `${a.titel} – MIRA`;
  if (a.typ && !verbindungen(a.typ).length) { main.append(keineVerbindung(a.typ)); return; }
  main.append(h("div", { class: "leer" }, "Lädt …"));
  try { await a.zeigen(main, r.q); }
  catch (e) { leeren(main, h("div", { class: "karte" }, h("h2", {}, punkt("bad"), "Fehler"), h("pre", {}, e.message))); }
}
window.addEventListener("hashchange", () => { if (ICH && !location.hash.startsWith("#anmelden")) zeige(); });

async function start() {
  try {
    ARTEN = await api("GET", "/api/anmeldung/arten", undefined, { still: true });
    if (!ARTEN.eingerichtet) return zeigeEinrichtung();
    ICH = await api("GET", "/api/ich", undefined, { still: true });
    await ladeModule();
    if (location.hash.startsWith("#anmelden")) location.hash = "#uebersicht";
    zeige();
  } catch (e) { zeigeAnmeldung(); }
}

// ------------------------------------------------------------------ Anmeldung
function zeigeAnmeldung() {
  const fehler = new URLSearchParams(location.hash.split("?")[1] || "").get("fehler");
  const f = h("form", { onsubmit: async e => {
    e.preventDefault();
    try { await api("POST", "/api/anmeldung", lesenForm(f), { still: true }); location.hash = "#uebersicht"; start(); }
    catch (er) { hinweis(er.message, true); }
  } },
    feld("Benutzer", eingabe("benutzer", "", { autocomplete: "username", required: true })),
    feld("Passwort", eingabe("passwort", "", { type: "password", autocomplete: "current-password", required: true })),
    h("button", { class: "p voll", type: "submit" }, "Anmelden"));
  leeren($("#app"), h("div", { class: "ecke" }, themeWahl()), h("div", { class: "mitte" },
    h("div", { class: "logo-gross" }, h("img", { src: "/static/icon.svg", alt: "", class: "symbol" }), h("img", { src: "/static/logo-dunkel.svg", alt: "MIRA – Mail · Identity · Routing · Access", class: "wortmarke nur-dunkel" }), h("img", { src: "/static/logo-hell.svg", alt: "MIRA – Mail · Identity · Routing · Access", class: "wortmarke nur-hell" })),
    h("div", { class: "karte" },
      fehler ? h("p", { style: { color: "var(--bad)" } }, fehler) : null,
      ARTEN.oidc ? [h("a", { href: "/auth/oidc/start" }, h("button", { class: "p voll", type: "button" }, `Anmelden mit ${ARTEN.oidc_name || "Authentik"}`)),
        h("div", { class: "oder" }, ARTEN.verzeichnis ? "oder mit Domänen-Konto" : "oder lokaler Notfall-Zugang")] : null,
      f)));
}

async function eigenesPasswort() {
  const f = h("div", {}, feld("Altes Passwort", eingabe("alt", "", { type: "password" })), pwFeld("neu", "Neues Passwort", "mindestens 12 Zeichen"));
  await dialog("Eigenes Passwort ändern", f, [["Abbrechen", null], ["Speichern", () => versuch(() => api("POST", "/api/ich/passwort", lesenForm(f)), "Passwort geändert"), "p"]]);
}

// ------------------------------------------------------------------ Einrichtung
const E = { code: "", schritt: 0, db: null, module: [], anmeldung: { lokal: true, verzeichnis: false, authentik: false },
  ad: { werte: {} }, adId: null, ak: { werte: {} }, akId: null, vorhanden: [], admin: { modus: "keiner" }, adTest: null, akTest: null };

async function eapi(m, p, d) { return api(m, "/api/einrichtung" + p, d, { headers: { "X-MIRA-Einrichtung": E.code }, still: true }); }

async function zeigeEinrichtung() {
  const st = await api("GET", "/api/einrichtung/status", undefined, { still: true });
  E.module = st.module;
  const titel = ["Code", "Datenbank", "Anmeldung", "Verbindungen", "Admin", "Fertig"];
  const box = h("div", { class: "karte" });
  const zeichnen = () => {
    leeren($("#app"), h("div", { class: "ecke" }, themeWahl()), h("div", { class: "mitte breit" },
      h("div", { class: "logo-gross" }, h("img", { src: "/static/icon.svg", alt: "", class: "symbol" }), h("img", { src: "/static/logo-dunkel.svg", alt: "MIRA", class: "wortmarke nur-dunkel" }), h("img", { src: "/static/logo-hell.svg", alt: "MIRA", class: "wortmarke nur-hell" }), h("div", {}, `Einrichtung · Version ${st.version}`)),
      h("div", { class: "schritte" }, titel.map((t, i) => h("div", { class: (i === E.schritt ? "an " : "") + (i < E.schritt ? "fertig" : "") }, h("b", {}, t), `Schritt ${i + 1}`))),
      box));
    leeren(box, SCHRITT[E.schritt](zeichnen));
  };
  zeichnen();
}

const weiter = (z) => { E.schritt++; z(); };
const zurueck = (z) => h("button", { onclick: () => { E.schritt--; z(); } }, "Zurück");

const SCHRITT = [
  // 0 – Code
  z => {
    const i = eingabe("code", E.code, { placeholder: "XXXX-XXXX-XXXX", style: { fontFamily: "monospace", fontSize: "18px", textAlign: "center" } });
    return [h("h2", {}, "Willkommen"),
      h("p", {}, "MIRA wird zum ersten Mal gestartet. Zum Schutz vor fremden Zugriffen steht ein Einrichtungs-Code im Log des Containers (Unraid: Docker › MIRA › Logs) und in /config/einrichtungs-code.txt."),
      feld("Einrichtungs-Code", i),
      h("div", { class: "leiste" }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: async () => {
        E.code = i.value.trim().toUpperCase();
        const r = await versuch(() => eapi("POST", "/code"));
        if (r.db_vorhanden) { E.db = "vorhanden"; E.vorhanden = (await eapi("GET", "/verbindungen")).verbindungen; }
        weiter(z);
      } }, "Weiter")))];
  },
  // 1 – Datenbank
  z => {
    const typ = E.dbTyp || "sqlite";
    const wahlEl = h("div", { class: "wahl" },
      [["sqlite", "Eingebaut (SQLite)", "Datei in /config – nichts weiter nötig. Gut für den Anfang."],
       ["mariadb", "MariaDB / MySQL", "Vorhandene Datenbank (z. B. der MariaDB-Container auf Unraid). Empfohlen, wenn die Rechte-Datenbank mitgenutzt werden soll."]]
        .map(([v, t, b]) => h("label", {}, h("input", { type: "radio", name: "typ", value: v, checked: typ === v, onchange: () => { E.dbTyp = v; z(); } }), h("span", { class: "t" }, t), h("div", { class: "b" }, b))));
    const md = h("div", { class: typ === "mariadb" ? "" : "versteckt" },
      h("div", { class: "raster" }, feld("Server", eingabe("host", "", { placeholder: "<IP_MariaDB>" })), feld("Port", eingabe("port", "3306")),
        feld("Datenbank", eingabe("name", "mira")), feld("Benutzer", eingabe("benutzer", "mira")), feld("Passwort", eingabe("passwort", "", { type: "password" }))));
    const anl = haken("Datenbank und Benutzer jetzt anlegen (mit Root-Zugang)", false, "Nur wenn es sie noch nicht gibt. Der Root-Zugang wird nicht gespeichert.", "anlegen");
    const root = h("div", { class: "raster versteckt" }, feld("Root-Benutzer", eingabe("root_benutzer", "root")), feld("Root-Passwort", eingabe("root_passwort", "", { type: "password" })));
    anl.i.addEventListener("change", () => root.classList.toggle("versteckt", !anl.i.checked));
    md.append(anl.el, root);
    const daten = () => ({ typ, ...lesenForm(md) });
    return [h("h2", {}, "Wo soll MIRA seine Daten speichern?"),
      h("p", { class: "leise" }, "Benutzer mit Zugang, Verbindungen (Passwörter verschlüsselt), Einstellungen und das Protokoll."),
      wahlEl, md,
      E.db === "vorhanden" ? h("p", { class: "marke ok" }, "Es ist bereits eine Datenbank eingerichtet – „Weiter“ übernimmt sie.") : null,
      h("div", { class: "leiste" }, zurueck(z), h("span", { class: "rechts" },
        typ === "mariadb" ? h("button", { onclick: () => versuch(() => eapi("POST", "/db", { ...daten(), nur_testen: true }), "Verbindung klappt") }, "Testen") : null,
        h("button", { class: "p", onclick: async () => {
          if (E.db === "vorhanden" && !E.dbTyp) return weiter(z);
          const r = await versuch(() => eapi("POST", "/db", daten()));
          E.db = typ;
          E.vorhanden = (await eapi("GET", "/verbindungen")).verbindungen;
          if (r.vorhanden && r.vorhanden.benutzer) hinweis(`In der Datenbank sind schon ${r.vorhanden.benutzer} Benutzer und ${r.vorhanden.verbindungen} Verbindungen – werden übernommen.`);
          weiter(z);
        } }, "Weiter")))];
  },
  // 2 – Anmeldung
  z => {
    const a = E.anmeldung;
    const opt = (k, t, b, fest) => h("label", {}, h("input", { type: "checkbox", checked: a[k], disabled: fest, onchange: e => { a[k] = e.target.checked; } }),
      h("span", { class: "t" }, t), h("div", { class: "b" }, b));
    return [h("h2", {}, "Wie sollen sich Benutzer an MIRA anmelden?"),
      h("div", { class: "wahl" },
        opt("lokal", "Lokal (Notfall-Zugang)", "Benutzer nur in der MIRA-Datenbank. Ein lokaler Admin wird immer angelegt, damit man nie ausgesperrt ist.", true),
        opt("verzeichnis", "Verzeichnis (AD / Samba)", "Domänen-Konten. Erlaubt ist nur, wer in MIRA freigegeben ist UND im AD aktiv und in der Zugangsgruppe ist."),
        opt("authentik", "Authentik (Single Sign-on)", "Anmeldung über Authentik (OIDC, mit MFA). MIRA legt die Anwendung dort selbst an.")),
      h("p", { class: "leise klein-t" }, "Empfohlen: alle drei. Für Authentik + AD prüft MIRA bei jeder Anmeldung zusätzlich im AD."),
      h("div", { class: "leiste" }, zurueck(z), h("span", { class: "rechts" }, h("button", { class: "p", onclick: () => weiter(z) }, "Weiter")))];
  },
  // 3 – Verbindungen
  z => {
    const teile = [];
    const block = (typ, zust, idKey, testKey, titel) => {
      const m = E.module.find(x => x.typ === typ);
      const vorh = E.vorhanden.filter(v => v.typ === typ);
      const k = h("div", { class: "karte" }, h("h2", {}, titel));
      const formHalter = h("div", {});
      const nameI = eingabe("_name", zust.name || m.titel);
      const zeichneForm = () => leeren(formHalter, E[idKey] ? null : [feld("Name der Verbindung", nameI), schemaFormular(m.felder, zust.werte)]);
      if (vorh.length) k.append(feld("Vorhandene Verbindung", auswahl("_vorh", [["", "– neue eingeben –"], ...vorh.map(v => [v.id, v.name])], E[idKey] || ""),
        "Schon in der Datenbank gespeichert – kann direkt benutzt werden"));
      const sel = k.querySelector("select[name=_vorh]");
      if (sel) sel.addEventListener("change", () => { E[idKey] = sel.value ? Number(sel.value) : null; zeichneForm(); });
      zeichneForm();
      const erg = h("div", { class: "klein-t" }, E[testKey] ? [punkt(E[testKey].ok ? "ok" : "bad"), " ", E[testKey].meldung] : null);
      k.append(formHalter, h("div", { class: "leiste" }, h("button", { onclick: async () => {
        merken();
        const r = await eapi("POST", "/verbindung/testen", E[idKey] ? { typ, werte: {} } : { typ, werte: zust.werte });
        E[testKey] = E[idKey] ? { ok: true, meldung: "vorhandene Verbindung" } : r;
        leeren(erg, punkt(E[testKey].ok ? "ok" : "bad"), " ", E[testKey].meldung);
      } }, "Verbindung testen"), erg));
      const merken = () => { if (!E[idKey]) { zust.werte = lesenForm(formHalter); zust.name = zust.werte._name; delete zust.werte._name; } };
      teile.push({ merken, typ, idKey, testKey });
      return k;
    };
    const inhalt = [h("h2", {}, "Verbindungen eingeben")];
    if (E.anmeldung.verzeichnis) inhalt.push(block("ad", E.ad, "adId", "adTest", "Verzeichnis (AD / Samba)"));
    if (E.anmeldung.authentik) inhalt.push(block("authentik", E.ak, "akId", "akTest", "Authentik"));
    if (!E.anmeldung.verzeichnis && !E.anmeldung.authentik) inhalt.push(h("p", { class: "leise" }, "Nur lokale Anmeldung – hier ist nichts nötig. Weitere Dienste (HAProxy, nginx, grommunio, Exchange) richtest du später unter „Verbindungen“ ein."));
    else inhalt.push(h("p", { class: "leise klein-t" }, "Weitere Dienste (HAProxy, nginx, grommunio, Exchange) kommen nach der Einrichtung unter „Verbindungen“ dazu."));
    inhalt.push(h("div", { class: "leiste" }, zurueck(z), h("span", { class: "rechts" }, h("button", { class: "p", onclick: async () => {
      for (const t of teile) {
        t.merken();
        if (!E[t.idKey] && !(E[t.testKey] && E[t.testKey].ok)) {
          const r = await eapi("POST", "/verbindung/testen", { typ: t.typ, werte: (t.typ === "ad" ? E.ad : E.ak).werte });
          E[t.testKey] = r;
          if (!r.ok) { hinweis(`${t.typ}: ${r.meldung}`, true); return z(); }
        }
      }
      weiter(z);
    } }, "Weiter"))));
    return inhalt;
  },
  // 4 – Admin
  z => {
    const k = h("div", {});
    const lokal = h("div", { class: "raster" }, feld("Name", eingabe("name", E.lokal?.name || "admin")),
      pwFeld("passwort", "Passwort", "mindestens 12 Zeichen – gut aufheben, das ist der Notfall-Zugang"));
    if (E.lokal?.passwort) lokal.querySelector("[name=passwort]").value = E.lokal.passwort;
    k.append(h("div", { class: "karte" }, h("h2", {}, "Lokaler Notfall-Admin"), lokal));
    const basisI = eingabe("basis_url", E.basis || location.origin);
    let vzTeil = null, vzForm = null, akTeil = null;
    if (E.anmeldung.verzeichnis) {
      const a = E.admin;
      vzForm = h("div", {});
      const gruppeI = eingabe("gruppe", E.gruppe ?? "grp_MIRA_Admins");
      const anl = haken("Gruppe anlegen, falls es sie noch nicht gibt", E.gruppeAnlegen ?? true, null, "gruppe_anlegen");
      const zeichne = () => {
        leeren(vzForm);
        if (a.modus === "vorhanden") {
          const suche = eingabe("suche", "", { placeholder: "Name oder Anmeldename" });
          const liste = h("div", {});
          suche.addEventListener("input", async () => {
            if (suche.value.length < 2) return;
            const r = await eapi("POST", "/verzeichnis/suche", { verbindung: E.adId ? { id: E.adId } : { typ: "ad", werte: E.ad.werte }, suche: suche.value }).catch(e => ({ treffer: [], e }));
            leeren(liste, r.e ? h("p", { class: "leise" }, r.e.message) : tabelle([{ t: "Anmeldename", w: x => x.sam }, { t: "Name", w: x => x.anzeigename }, { t: "E-Mail", w: x => x.mail }],
              r.treffer, { klick: x => { a.sam = x.sam; zeichne(); }, an: x => x.sam === a.sam }));
          });
          vzForm.append(feld("AD-Benutzer suchen", suche), liste, a.sam ? h("p", {}, "Gewählt: ", h("b", {}, a.sam)) : null);
        } else if (a.modus === "neu") {
          vzForm.append(h("div", { class: "raster" }, feld("Vorname", eingabe("vorname", a.vorname)), feld("Nachname", eingabe("nachname", a.nachname)),
            feld("Anmeldename", eingabe("sam", a.sam, { placeholder: "z. B. adm.mwitte" })), feld("E-Mail", eingabe("mail", a.mail)),
            pwFeld("passwort", "Passwort", "muss der Kennwortrichtlinie des AD entsprechen")));
          vzForm.querySelectorAll("input").forEach(i => i.addEventListener("input", () => { a[i.name] = i.value; }));
        }
      };
      const wahl = h("div", { class: "wahl" }, [["vorhanden", "Vorhandenen AD-Benutzer übernehmen", "z. B. dein eigenes Konto"],
        ["neu", "Neuen AD-Benutzer anlegen", "eigenes Admin-Konto nur für MIRA"], ["keiner", "Später", "erst nur mit dem lokalen Admin"]]
        .map(([v, t, b]) => h("label", {}, h("input", { type: "radio", name: "modus", value: v, checked: a.modus === v, onchange: () => { a.modus = v; zeichne(); } }),
          h("span", { class: "t" }, t), h("div", { class: "b" }, b))));
      zeichne();
      vzTeil = h("div", { class: "karte" }, h("h2", {}, "Admin aus dem Verzeichnis"),
        h("div", { class: "raster" }, feld("Zugangsgruppe", gruppeI, "Nur Mitglieder dürfen sich anmelden (auch verschachtelt)")), anl.el, wahl, vzForm);
      vzTeil.lesen = () => { E.gruppe = gruppeI.value.trim(); E.gruppeAnlegen = anl.i.checked; };
    }
    if (E.anmeldung.authentik) {
      const anwI = haken("Anwendung „MIRA“ in Authentik automatisch anlegen", E.akAnlegen ?? true, "OAuth2/OIDC-Anbieter mit Rücksprung-Adresse und Zugriff nur für die Zugangsgruppe", "anlegen");
      const hand = h("div", { class: "raster" + ((E.akAnlegen ?? true) ? " versteckt" : "") }, feld("Issuer", eingabe("issuer", E.akHand?.issuer, { placeholder: "https://auth…/application/o/mira/" })),
        feld("Client-ID", eingabe("client_id", E.akHand?.client_id)), feld("Client-Secret", eingabe("client_secret", "", { type: "password" })));
      anwI.i.addEventListener("change", () => hand.classList.toggle("versteckt", anwI.i.checked));
      akTeil = h("div", { class: "karte" }, h("h2", {}, "Authentik"), anwI.el, hand,
        h("p", { class: "leise klein-t" }, "Die Adresse von MIRA (unten) muss genau so von außen erreichbar sein – daraus wird die Rücksprung-Adresse …/auth/oidc/rueckruf."));
      akTeil.lesen = () => { E.akAnlegen = anwI.i.checked; E.akHand = lesenForm(hand); };
    }
    k.append(vzTeil, akTeil, h("div", { class: "karte" }, h("h2", {}, "Adresse von MIRA"), feld("Basis-Adresse", basisI, "So wie MIRA im Browser aufgerufen wird, z. B. https://mira.firma.de")));
    const merken = () => { E.lokal = lesenForm(lokal); E.basis = basisI.value.trim(); vzTeil?.lesen(); akTeil?.lesen(); };
    return [h("h2", {}, "Admin-Zugang"), k, h("div", { class: "leiste" }, zurueck(z), h("span", { class: "rechts" }, h("button", { class: "p", onclick: async () => {
      merken();
      if ((E.lokal.passwort || "").length < 12) return hinweis("Lokales Passwort: mindestens 12 Zeichen", true);
      const d = { basis_url: E.basis, lokal_admin: E.lokal, verzeichnis: null, authentik: null };
      if (E.anmeldung.verzeichnis) {
        d.verzeichnis = { verbindung: E.adId ? { id: E.adId } : { typ: "ad", name: E.ad.name, werte: E.ad.werte }, gruppe: E.gruppe, gruppe_anlegen: E.gruppeAnlegen, login_direkt: true,
          admin: E.admin.modus === "keiner" ? null : { ...E.admin } };
        if (d.verzeichnis.admin && !d.verzeichnis.admin.sam) return hinweis("Bitte einen AD-Benutzer wählen oder eingeben", true);
      }
      if (E.anmeldung.authentik) {
        d.authentik = { verbindung: E.akId ? { id: E.akId } : { typ: "authentik", name: E.ak.name, werte: E.ak.werte }, anwendung_anlegen: E.akAnlegen, name: "MIRA",
          gruppen_zugriff: true, verzeichnis_pruefen: true, ...(E.akAnlegen ? {} : E.akHand) };
      }
      const r = await versuch(() => eapi("POST", "/abschliessen", d));
      E.ergebnis = r;
      weiter(z);
    } }, "Einrichten")))];
  },
  // 5 – Fertig
  z => {
    const r = E.ergebnis || { schritte: [] };
    return [h("h2", {}, r.ok ? [punkt("ok"), " Fertig"] : [punkt("bad"), " Nicht alles hat geklappt"]),
      h("div", { class: "log" }, r.schritte.map(s => h("div", { class: s.ok ? "ok" : "bad" }, h("div", {}, s.text, s.details ? h("small", {}, s.details) : null)))),
      h("div", { class: "leiste" }, r.ok ? null : zurueck(z), h("span", { class: "rechts" },
        r.ok ? h("button", { class: "p", onclick: () => { location.hash = "#anmelden"; start(); } }, "Zur Anmeldung") : null))];
  },
];

// ------------------------------------------------------------------ Übersicht
ANSICHT.uebersicht = { titel: "Übersicht", zeigen: async main => {
  const d = await api("GET", "/api/uebersicht");
  const kacheln = h("div", { class: "kacheln" },
    h("div", { class: "kachel" }, h("div", { class: "w" }, d.verbindungen.length), h("div", { class: "l" }, "Verbindungen")),
    h("div", { class: "kachel" }, h("div", { class: "w" }, d.zugang), h("div", { class: "l" }, "Personen mit Zugang zu MIRA")),
    h("div", { class: "kachel" }, h("div", { class: "w" }, d.fehlanmeldungen_heute), h("div", { class: "l" }, "Fehlanmeldungen heute")));
  const ziel = { ad: "benutzer", authentik: "authentik", haproxy: "haproxy", nginx: "nginx", grommunio: "grommunio", exchange: "exchange" };
  const dienste = h("div", { class: "kacheln" }, d.verbindungen.map(v => {
    const ok = v.status.startsWith("ok"), fehl = v.status.startsWith("fehler");
    return h("div", { class: "kachel klick", onclick: () => { localStorage.setItem("mira.verb." + v.typ, v.id); location.hash = "#" + (ziel[v.typ] || "verbindungen"); } },
      h("div", { class: "t" }, punkt(!v.aktiv ? "" : ok ? "ok" : fehl ? "bad" : "warn"), v.name),
      h("div", { class: "s" }, v.titel, v.aktiv ? "" : " · ausgeschaltet"), h("div", { class: "s" }, v.status.replace(/^(ok|fehler): /, "") || "noch nicht geprüft"),
      v.geprueft ? h("div", { class: "s" }, "geprüft " + datum(v.geprueft)) : null);
  }));
  leeren(main, kacheln,
    h("div", { class: "karte" }, h("h2", {}, "Dienste", h("span", { class: "rechts" }, h("button", { class: "klein", onclick: async e => {
      e.target.disabled = true; await versuch(() => api("POST", "/api/uebersicht/pruefen")); zeige();
    } }, "Alle prüfen"), darf("admin") ? h("button", { class: "klein", onclick: () => verbindungDialog({}) }, "+ Verbindung") : null)),
      d.verbindungen.length ? dienste : h("div", { class: "leer" }, "Noch keine Verbindungen.")),
    darf("operator") ? h("div", { class: "karte" }, h("h2", {}, "Zuletzt", h("span", { class: "rechts" }, h("a", { href: "#protokoll" }, "Protokoll"))), protokollTabelle(d.letzte)) : null);
} };

function protokollTabelle(e) {
  return tabelle([{ t: "Zeit", w: x => datum(x.zeit) }, { t: "Wer", w: x => x.benutzer }, { t: "Bereich", w: x => marke(x.bereich) },
    { t: "Aktion", w: x => h("span", {}, x.ok ? null : [punkt("bad"), " "], x.aktion) }, { t: "Ziel", w: x => h("span", { class: "klein-t" }, x.ziel) }],
  e, { klick: x => dialog(x.aktion, h("div", {}, h("p", {}, `${datum(x.zeit)} · ${x.benutzer} · ${x.ip}`), h("p", {}, x.ziel), x.details ? h("pre", {}, x.details) : null)) });
}

// ------------------------------------------------------------------ Verbindungen
async function verbindungDialog(v) {
  const neu = !v.id;
  let typ = v.typ || (MODULE[0] && MODULE[0].typ);
  const halter = h("div", {});
  const name = eingabe("name", v.name || "");
  const aktiv = haken("Eingeschaltet", v.aktiv ?? true);
  const erg = h("div", { class: "klein-t" });
  const zeichne = () => {
    const m = MODULE.find(x => x.typ === typ);
    if (!name.value || MODULE.some(x => x.titel === name.value)) name.value = v.name || m.titel;
    leeren(halter, h("p", { class: "leise klein-t" }, m.beschreibung), schemaFormular(m.felder, v.einstellungen || {}, v.geheim_gesetzt || []));
  };
  const typSel = neu ? auswahl("typ", MODULE.map(m => [m.typ, m.titel]), typ) : null;
  if (typSel) typSel.addEventListener("change", () => { typ = typSel.value; zeichne(); });
  zeichne();
  const daten = () => ({ id: v.id, typ, name: name.value, aktiv: aktiv.i.checked, werte: lesenForm(halter) });
  await dialog(neu ? "Neue Verbindung" : `Verbindung „${v.name}“`, h("div", {},
    h("div", { class: "raster" }, typSel ? feld("Dienst", typSel) : null, feld("Name", name)), aktiv.el, halter,
    h("div", { class: "leiste" }, h("button", { onclick: async () => { leeren(erg, "prüfe …"); const r = await api("POST", "/api/verbindungen/testen", daten()).catch(e => ({ ok: false, meldung: e.message })); leeren(erg, punkt(r.ok ? "ok" : "bad"), " ", r.meldung); } }, "Testen"), erg)),
  [...(neu ? [] : [["Löschen", async () => { if (!(await bestaetigen(`Verbindung „${v.name}“ löschen?`))) return false; await versuch(() => api("DELETE", `/api/verbindungen/${v.id}`), "gelöscht"); }, "gefahr"]]),
    ["Abbrechen", null],
    ["Speichern", async () => { const d = daten(); await versuch(() => neu ? api("POST", "/api/verbindungen", d) : api("PUT", `/api/verbindungen/${v.id}`, d), "gespeichert"); }, "p"]]);
  await ladeModule(); zeige();
}

ANSICHT.verbindungen = { titel: "Verbindungen", zeigen: async main => {
  const d = await api("GET", "/api/verbindungen");
  leeren(main, h("div", { class: "karte" }, h("h2", {}, "Verbindungen zu den Diensten", h("span", { class: "rechts" },
    darf("admin") ? h("button", { class: "p klein", onclick: () => verbindungDialog({}) }, "+ Neue Verbindung") : null)),
  h("p", { class: "leise klein-t" }, "Zugangsdaten werden verschlüsselt in der Datenbank gespeichert und nie wieder angezeigt. Der Schlüssel liegt in /config/schluessel.key – mit sichern!"),
  tabelle([{ t: "", w: v => punkt(!v.aktiv ? "" : v.status.startsWith("ok") ? "ok" : v.status.startsWith("fehler") ? "bad" : "warn") },
    { t: "Name", w: v => v.name }, { t: "Dienst", w: v => v.titel },
    { t: "Ziel", w: v => h("span", { class: "klein-t" }, v.einstellungen.server || v.einstellungen.url || v.einstellungen.conf_pfad || "") },
    { t: "Status", w: v => h("span", { class: "klein-t" }, v.status || "–") },
    { t: "", w: v => h("button", { class: "klein", onclick: async e => { e.stopPropagation(); const r = await api("POST", `/api/verbindungen/${v.id}/testen`).catch(er => ({ ok: false, meldung: er.message })); hinweis(r.meldung, !r.ok); zeige(); } }, "Testen") }],
  d.verbindungen, { klick: darf("admin") ? v => verbindungDialog(v) : null, leer: "Noch keine Verbindungen" })));
} };

// ------------------------------------------------------------------ Zugang zu MIRA
ANSICHT.zugang = { titel: "Zugang zu MIRA", zeigen: async main => {
  const [d, e] = await Promise.all([api("GET", "/api/zugang"), api("GET", "/api/einstellungen")]);
  const vz = e.verzeichnis || {};
  const neu = async quelle => {
    const f = h("div", {});
    if (quelle === "lokal") f.append(h("div", { class: "raster" }, feld("Name", eingabe("name")), feld("Anzeigename", eingabe("anzeigename")), pwFeld("passwort", "Passwort", "mind. 12 Zeichen")));
    else {
      const ads = verbindungen("ad");
      f.append(h("div", { class: "raster" }, feld("AD-Verbindung", auswahl("verbindung_id", ads.map(v => [v.id, v.name]), vz.verbindung_id)),
        feld("Anmeldename im AD", eingabe("name", "", { placeholder: "sAMAccountName" }))),
        vz.gruppe ? haken(`Auch in die Zugangsgruppe ${vz.gruppe} aufnehmen`, true, null, "in_gruppe").el : null);
    }
    f.append(feld("Rolle", auswahl("rolle", [["leser", "Leser – nur ansehen"], ["operator", "Operator – Benutzer/Hosts pflegen"], ["admin", "Admin – alles inkl. Verbindungen"]], "leser")));
    await dialog(quelle === "lokal" ? "Lokalen Benutzer anlegen" : "Verzeichnis-Benutzer freigeben", f,
      [["Abbrechen", null], ["Speichern", () => versuch(() => api("POST", "/api/zugang", { quelle, ...lesenForm(f) }), "freigegeben"), "p"]]);
    zeige();
  };
  const bearbeiten = async b => {
    const f = h("div", {}, feld("Rolle", auswahl("rolle", d.rollen, b.rolle)), haken("Aktiv", b.aktiv, null, "aktiv").el,
      b.quelle === "lokal" ? pwFeld("passwort", "Neues Passwort", "leer lassen = unverändert") : h("p", { class: "leise klein-t" }, b.dn));
    await dialog(`${b.anzeigename || b.name}`, f, [["Entfernen", async () => { if (!(await bestaetigen(`Zugang für ${b.name} entfernen?`))) return false; await versuch(() => api("DELETE", `/api/zugang/${b.id}`), "entfernt"); }, "gefahr"],
      ["Abbrechen", null], ["Speichern", () => versuch(() => api("PATCH", `/api/zugang/${b.id}`, lesenForm(f)), "gespeichert"), "p"]]);
    zeige();
  };
  leeren(main, h("div", { class: "karte" }, h("h2", {}, "Wer darf MIRA benutzen?", h("span", { class: "rechts" },
    verbindungen("ad").length ? h("button", { class: "p klein", onclick: () => neu("verzeichnis") }, "+ Aus dem AD") : null,
    h("button", { class: "klein", onclick: () => neu("lokal") }, "+ Lokal"))),
  h("p", { class: "leise klein-t" }, "Anmelden darf nur, wer hier eingetragen und aktiv ist. Verzeichnis-Benutzer müssen zusätzlich im AD aktiv sein",
    vz.gruppe ? ` und in der Gruppe ${vz.gruppe} stehen.` : "."),
  tabelle([{ t: "", w: b => punkt(b.aktiv ? "ok" : "") }, { t: "Name", w: b => b.name }, { t: "Anzeigename", w: b => b.anzeigename },
    { t: "Quelle", w: b => marke(b.quelle === "lokal" ? "lokal" : "AD") }, { t: "Rolle", w: b => marke(b.rolle, b.rolle === "admin" ? "akz" : "") },
    { t: "Letzte Anmeldung", w: b => datum(b.letzte_anmeldung) }], d.benutzer, { klick: bearbeiten })));
} };

// ------------------------------------------------------------------ Einstellungen
ANSICHT.einstellungen = { titel: "Einstellungen", zeigen: async main => {
  const e = await api("GET", "/api/einstellungen");
  const ads = verbindungen("ad"), aks = verbindungen("authentik");
  const vz = e.verzeichnis || {}, o = e.oidc || {};
  const fv = h("div", {}, haken("Anmeldung mit Domänen-Konto (Benutzer + Passwort) erlauben", vz.aktiv, null, "aktiv").el,
    h("div", { class: "raster" }, feld("AD-Verbindung", auswahl("verbindung_id", [["", "–"], ...ads.map(v => [v.id, v.name])], vz.verbindung_id)),
      feld("Zugangsgruppe", eingabe("gruppe", vz.gruppe), "leer = keine Gruppenprüfung (nicht empfohlen)")));
  const fo = h("div", {}, haken("Anmeldung über Authentik (OIDC)", o.aktiv, null, "aktiv").el,
    h("div", { class: "raster" }, feld("Authentik-Verbindung", auswahl("authentik_id", [["", "–"], ...aks.map(v => [v.id, v.name])], o.authentik_id)),
      feld("Issuer", eingabe("issuer", o.issuer)), feld("Client-ID", eingabe("client_id", o.client_id)),
      feld("Client-Secret", eingabe("client_secret", "", { type: "password", placeholder: o.client_secret_gesetzt ? "•••••• (unverändert)" : "" }))),
    haken("Zertifikat prüfen", o.tls_pruefen ?? true, null, "tls_pruefen").el,
    haken("Verzeichnis-Benutzer zusätzlich im AD prüfen (aktiv + Gruppe)", o.verzeichnis_pruefen ?? true, null, "verzeichnis_pruefen").el);
  const basis = eingabe("basis_url", e.basis_url);
  leeren(main,
    h("div", { class: "karte" }, h("h2", {}, "Adresse"), feld("Basis-Adresse von MIRA", basis, "Daraus entsteht die Rücksprung-Adresse für Authentik: " + (e.basis_url || location.origin) + "/auth/oidc/rueckruf")),
    h("div", { class: "karte" }, h("h2", {}, "Verzeichnis"), fv),
    h("div", { class: "karte" }, h("h2", {}, "Authentik", h("span", { class: "rechts" }, aks.length ? h("button", { class: "klein", onclick: async () => {
      if (!(await bestaetigen("Anwendung „MIRA“ in Authentik anlegen bzw. neu einrichten? Ein neues Client-Secret wird erzeugt.", "Anlegen", false))) return;
      await versuch(() => api("POST", "/api/einstellungen/authentik-anwendung", { authentik_id: lesenForm(fo).authentik_id || aks[0].id, gruppe: lesenForm(fv).gruppe }), "Anwendung angelegt, Anmeldung über Authentik ist an");
      zeige();
    } }, "Anwendung automatisch anlegen") : null)), fo),
    h("div", { class: "leiste" }, h("span", { class: "leise klein-t" }, `MIRA ${e.version} · Datenbank: ${e.db}`), h("span", { class: "rechts" },
      h("button", { class: "p", onclick: () => versuch(() => api("PUT", "/api/einstellungen", { basis_url: basis.value, verzeichnis: lesenForm(fv), oidc: lesenForm(fo) }), "gespeichert") }, "Speichern"))));
} };

// ------------------------------------------------------------------ Protokoll
ANSICHT.protokoll = { titel: "Protokoll", zeigen: async (main, q) => {
  const seite = Number(q.get("seite") || 0), bereich = q.get("bereich") || "", suche = q.get("suche") || "";
  const d = await api("GET", `/api/protokoll?seite=${seite}&bereich=${encodeURIComponent(bereich)}&suche=${encodeURIComponent(suche)}`);
  const s = eingabe("suche", suche, { placeholder: "Suchen …" });
  const b = auswahl("bereich", [["", "alle Bereiche"], "anmeldung", "ad", "authentik", "haproxy", "nginx", "grommunio", "exchange", "ablauf", "zugang", "verbindungen", "einstellungen", "einrichtung"], bereich);
  const los = (sei = 0) => { location.hash = `#protokoll?seite=${sei}&bereich=${b.value}&suche=${encodeURIComponent(s.value)}`; };
  s.addEventListener("keydown", e => { if (e.key === "Enter") los(); });
  b.addEventListener("change", () => los());
  leeren(main, h("div", { class: "karte" }, h("div", { class: "leiste" }, s, b,
    h("span", { class: "rechts" }, seite > 0 ? h("button", { onclick: () => los(seite - 1) }, "‹ Neuer") : null, d.eintraege.length === 100 ? h("button", { onclick: () => los(seite + 1) }, "Älter ›") : null)),
  protokollTabelle(d.eintraege)));
} };

document.addEventListener("DOMContentLoaded", start);
