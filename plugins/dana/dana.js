/* DANA – MySQL/MariaDB administration for MIRA (plugin "dana", connector type "mysql").
 * Every MIRA connection of this plugin is one database server; MIRA keeps the login and answers /api/db/... itself.
 * Views: overview (server + databases), database (tables, SQL, export, import, settings), table (data, structure,
 * SQL, operations), sql (console), users (accounts + grants), status (processes, variables, status). */
"use strict";

miraPlugin("dana", ctx => {
  const B = "/api/db";
  const enc = encodeURIComponent;
  const get = p => ctx.api("GET", B + p);
  const send = (m, p, d) => ctx.api(m, B + p, d === undefined ? {} : d);
  const write = ctx.can("write"), admin = ctx.can("admin");
  const kopf = (titel, ...rechts) => h("h2", {}, titel, h("span", { class: "rechts" }, rechts));
  const dbP = db => `/databases/${enc(db)}`;
  const tP = (db, t) => `${dbP(db)}/tables/${enc(t)}`;
  const zahl = n => n == null || n === "" ? "" : Number(n).toLocaleString("de-DE");
  const href = (view, params) => `#dana.${view}` + (params ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")) : "");
  const go = (view, params) => { location.hash = href(view, params); };
  const lade = (link) => { const a = h("a", { href: link, download: "" }); document.body.append(a); a.click(); a.remove(); };
  const qs = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")).toString();
  const dauer = s => {
    s = Number(s) || 0;
    const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return d ? `${d} T ${hh} h` : hh ? `${hh} h ${m} min` : `${m} min`;
  };
  let CHARSETS = null;
  const charsets = async () => CHARSETS || (CHARSETS = await get("/charsets"));

  function pfad(...teile) {
    const out = [h("a", { href: href("overview") }, ctx.conn.name)];
    for (const [t, l] of teile) out.push(h("span", { class: "leise" }, " › "), l ? h("a", { href: l }, t) : h("b", {}, t));
    return h("div", { class: "dana-pfad" }, out);
  }

  /** a value as shown in lists */
  function zelle(v) {
    if (v === null || v === undefined) return h("span", { class: "dana-null" }, "NULL");
    if (typeof v === "object") {
      if (v.$hex !== undefined) return h("span", { class: "dana-bin", title: `${v.len} Byte` }, `0x${v.$hex.slice(0, 40)}${v.$hex.length > 40 || v.$trunc ? "…" : ""}`);
      if (v.$trunc !== undefined) return h("span", { class: "dana-wert", title: `${v.len} Zeichen – vollständig im Bearbeiten-Dialog` }, v.$trunc.slice(0, 200) + "…");
    }
    if (typeof v === "boolean") return v ? "1" : "0";
    const s = String(v);
    return h("span", { class: "dana-wert", title: s.length > 60 ? s.slice(0, 1000) : null }, s.length > 200 ? s.slice(0, 200) + "…" : s);
  }

  /** collation select (charset groups) */
  async function sortierung(name, wert, leer = "(Standard)") {
    const cs = await charsets();
    return h("select", { name }, h("option", { value: "" }, leer),
      cs.charsets.map(c => h("optgroup", { label: c.name }, c.collations.map(x => h("option", { value: x, selected: x === wert }, x + (x === c.default ? " ★" : ""))))));
  }

  // ================================================================ overview
  async function overview(main) {
    const [s, d] = await Promise.all([get("/server"), get("/databases")]);
    const sys = localStorage.getItem("dana.sys") === "1";
    const st = s.status || {};
    const liste = d.databases.filter(x => sys || !x.system);
    leeren(main,
      h("div", { class: "kacheln" },
        h("div", { class: "kachel" }, h("div", { class: "w" }, `${s.kind} ${s.version}`), h("div", { class: "l" }, s.comment || s.full_version)),
        h("div", { class: "kachel" }, h("div", { class: "w" }, s.user_databases), h("div", { class: "l" }, `Datenbanken (${s.databases} mit System) · ${zahl(s.tables)} Tabellen`)),
        h("div", { class: "kachel" }, h("div", { class: "w" }, groesse(s.size)), h("div", { class: "l" }, "Daten + Indizes")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, `${st.Threads_connected || "?"} / ${s.max_connections}`), h("div", { class: "l" }, `Verbindungen (höchstens ${st.Max_used_connections || "?"} gleichzeitig)`)),
        h("div", { class: "kachel" }, h("div", { class: "w" }, dauer(st.Uptime)), h("div", { class: "l" }, `Laufzeit · ${zahl(st.Questions)} Abfragen`)),
        h("div", { class: "kachel" }, h("div", { class: "w" }, s.tls ? "TLS" : "ohne TLS"), h("div", { class: "l" }, s.tls ? s.tls : "Verbindung MIRA → Server unverschlüsselt"))),
      h("div", { class: "karte" },
        kopf("Datenbanken",
          h("label", { class: "klein-t leise dana-schalter" }, h("input", { type: "checkbox", checked: sys, onchange: e => { localStorage.setItem("dana.sys", e.target.checked ? "1" : "0"); ctx.rerender(); } }), " Systemdatenbanken"),
          h("button", { class: "klein", onclick: () => go("sql") }, "SQL"),
          write ? h("button", { class: "p klein", onclick: () => neueDb() }, "+ Datenbank") : null),
        h("p", { class: "leise klein-t" }, `${s.address} · angemeldet als ${s.user} · Zeichensatz ${s.charset} / ${s.collation}`),
        tabelle([
          { t: "Name", w: x => h("b", {}, x.name, x.system ? h("span", { class: "leise" }, " ", marke("System")) : null) },
          { t: "Tabellen", w: x => zahl(x.tables), z: true },
          { t: "Zeilen ≈", w: x => zahl(x.rows), z: true },
          { t: "Größe", w: x => groesse(x.data + x.index), z: true },
          { t: "Sortierung", w: x => h("span", { class: "klein-t" }, x.collation) },
          { t: "", w: x => h("span", { class: "dana-knoepfe" },
            h("button", { class: "klein", title: "SQL-Export herunterladen", onclick: e => { e.stopPropagation(); lade(ctx.link(`${B}${dbP(x.name)}/export?` + qs({ structure: 1, data: 1, drop: 1, routines: 1, gzip: 1 }))); } }, "Export"),
            admin && !x.system ? h("button", { class: "klein gefahr", onclick: e => { e.stopPropagation(); dbLoeschen(x.name); } }, "Löschen") : null) }],
        liste, { klick: x => go("database", { db: x.name }), leer: "Keine Datenbanken" })));
  }

  async function neueDb() {
    const f = h("div", { class: "raster" }, feld("Name", eingabe("name", "", { autocomplete: "off" })),
      feld("Sortierung", await sortierung("collation", "", "(Server-Standard)")));
    await dialog("Neue Datenbank", f, [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesenForm(f);
      if (d.collation) d.charset = d.collation.split("_")[0];
      await versuch(() => send("POST", "/databases", d), `Datenbank „${d.name}“ angelegt`);
      go("database", { db: d.name });
    }, "p"]]);
  }

  async function dbLoeschen(db) {
    const i = eingabe("ok", "", { autocomplete: "off", placeholder: db });
    const r = await dialog("Datenbank löschen", h("div", {},
      h("p", {}, `Die Datenbank „${db}“ mit allen Tabellen und Daten wird endgültig gelöscht. Vorher einen Export ziehen!`),
      feld("Zur Bestätigung den Namen eingeben", i)), [["Abbrechen", null], ["Endgültig löschen", () => {
      if (i.value !== db) { hinweis("Name stimmt nicht", true); return false; }
      return true;
    }, "p gefahr"]]);
    if (!r) return;
    await versuch(() => send("DELETE", dbP(db)), `Datenbank „${db}“ gelöscht`);
    go("overview");
    ctx.rerender();
  }

  // ================================================================ database
  async function database(main, q) {
    const db = q.get("db");
    const tab = q.get("tab") || "tables";
    if (!db) return go("overview");
    const inhalt = h("div", {});
    leeren(main, pfad([db]),
      tabs([["tables", "Tabellen"], ["sql", "SQL"], ["export", "Export"], ...(write ? [["import", "Import"]] : []), ["settings", "Einstellungen"]], tab,
        k => go("database", { db, tab: k })), inhalt);
    if (tab === "sql") return sqlKonsole(inhalt, { db });
    const d = await get(`${dbP(db)}/tables`);
    if (tab === "export") return exportForm(inhalt, db, d.tables);
    if (tab === "import") return importForm(inhalt, db);
    if (tab === "settings") return dbEinstellungen(inhalt, db, d);
    tabellenListe(inhalt, db, d);
  }

  function tabellenListe(el, db, d) {
    const wahl = new Set();
    const zaehler = h("span", { class: "leise klein-t" });
    const sammel = h("span", { class: "dana-knoepfe" });
    const neu = () => {
      leeren(zaehler, wahl.size ? `${wahl.size} gewählt` : "");
      sammel.style.visibility = wahl.size ? "visible" : "hidden";
    };
    const views = new Set(d.tables.filter(t => t.type === "view").map(t => t.name));
    const mehrere = async (was) => {
      const namen = [...wahl];
      if (was === "export") return lade(ctx.link(`${B}${dbP(db)}/export?` + qs({ tables: namen.join(","), structure: 1, data: 1, drop: 1, routines: 1 })));
      if (was === "optimize") {
        for (const t of namen.filter(t => !views.has(t))) await versuch(() => send("POST", `${tP(db, t)}/maintenance`, { op: "optimize" }));
        hinweis(`${namen.length} Tabelle(n) optimiert`); return ctx.rerender();
      }
      const text = was === "truncate" ? `${namen.length} Tabelle(n) leeren (TRUNCATE – alle Zeilen weg, AUTO_INCREMENT zurück)?`
        : `${namen.length} Tabelle(n)/Ansicht(en) endgültig löschen: ${namen.join(", ")}?`;
      if (!(await bestaetigen(text, was === "truncate" ? "Leeren" : "Löschen"))) return;
      for (const t of namen) {
        const extra = was === "truncate" ? "?mode=truncate" : views.has(t) ? "?view=1" : "";
        if (was === "truncate" && views.has(t)) continue;
        await versuch(() => ctx.api("DELETE", `${B}${tP(db, t)}${extra}`));
      }
      hinweis("Erledigt"); ctx.rerender();
    };
    leeren(sammel, h("button", { class: "klein", onclick: () => mehrere("export") }, "Export"),
      write ? h("button", { class: "klein", onclick: () => mehrere("optimize") }, "Optimieren") : null,
      admin ? h("button", { class: "klein gefahr", onclick: () => mehrere("truncate") }, "Leeren") : null,
      admin ? h("button", { class: "klein gefahr", onclick: () => mehrere("drop") }, "Löschen") : null);
    neu();
    const alle = h("input", { type: "checkbox", onclick: e => { e.stopPropagation(); el.querySelectorAll("input.dana-wahl").forEach(i => { i.checked = e.target.checked; i.checked ? wahl.add(i.value) : wahl.delete(i.value); }); neu(); } });
    const summe = d.tables.reduce((a, t) => a + (t.data || 0) + (t.index || 0), 0);
    leeren(el, h("div", { class: "karte" },
      kopf(`Tabellen (${d.tables.length})`, zaehler, sammel, write ? h("button", { class: "p klein", onclick: () => neueTabelle(db, d) }, "+ Tabelle") : null),
      h("p", { class: "leise klein-t" }, `${groesse(summe)} · ${d.charset} / ${d.collation}`),
      d.tables.length ? h("div", { class: "tab-wrap" }, h("table", { class: "dana-liste" },
        h("thead", {}, h("tr", {}, h("th", {}, alle), ["Name", "Art", "Zeilen ≈", "Größe", "Sortierung", "Kommentar", "geändert", ""].map((t, i) => h("th", { class: [2, 3].includes(i) ? "z" : "" }, t)))),
        h("tbody", {}, d.tables.map(t => h("tr", { class: "klick", onclick: () => go("table", { db, t: t.name }) },
          h("td", {}, h("input", { type: "checkbox", class: "dana-wahl", value: t.name, onclick: e => { e.stopPropagation(); e.target.checked ? wahl.add(t.name) : wahl.delete(t.name); neu(); } })),
          h("td", {}, h("b", {}, t.name)),
          h("td", {}, t.type === "view" ? marke("Ansicht", "akz") : h("span", { class: "klein-t leise" }, t.engine || "")),
          h("td", { class: "z" }, zahl(t.rows)),
          h("td", { class: "z" }, t.type === "view" ? "" : groesse((t.data || 0) + (t.index || 0))),
          h("td", { class: "klein-t" }, t.collation || ""),
          h("td", { class: "klein-t leise" }, t.comment || ""),
          h("td", { class: "klein-t leise" }, t.updated ? datum(t.updated) : ""),
          h("td", {}, h("span", { class: "dana-knoepfe" },
            h("button", { class: "klein", onclick: e => { e.stopPropagation(); go("table", { db, t: t.name, tab: "structure" }); } }, "Struktur"))))))))
        : h("div", { class: "leer" }, "Noch keine Tabellen")));
  }

  async function dbEinstellungen(el, db, d) {
    const f = h("div", { class: "raster" }, feld("Sortierung (Standard für neue Tabellen)", await sortierung("collation", d.collation)));
    leeren(el, h("div", { class: "karte" }, kopf("Einstellungen"), f,
      h("p", { class: "leise klein-t" }, "Ändert nur den Standard der Datenbank – vorhandene Tabellen behalten ihre Sortierung (je Tabelle unter Vorgänge umstellbar)."),
      write ? h("div", { class: "leiste" }, h("button", { class: "p", onclick: async () => {
        const v = lesenForm(f);
        await versuch(() => send("PUT", dbP(db), { collation: v.collation, charset: v.collation.split("_")[0] }), "Gespeichert");
        ctx.rerender();
      } }, "Speichern")) : null),
      admin ? h("div", { class: "karte" }, kopf("Gefahrenbereich"),
        h("p", { class: "klein-t" }, "Löscht die Datenbank mit allen Tabellen, Ansichten, Prozeduren und Daten."),
        h("button", { class: "gefahr", onclick: () => dbLoeschen(db) }, `Datenbank „${db}“ löschen`)) : null);
  }

  // ---------------------------------------------------------------- export / import
  function exportForm(el, db, tables, only) {
    const wahl = h("select", { multiple: true, size: Math.min(12, Math.max(4, tables.length)), class: "dana-mehrfach" },
      tables.map(t => h("option", { value: t.name, selected: !only || t.name === only }, t.name + (t.type === "view" ? " (Ansicht)" : ""))));
    const opt = h("div", { class: "dana-haken" },
      haken("Struktur (CREATE TABLE)", true, null, "structure").el,
      haken("Daten (INSERT)", true, null, "data").el,
      haken("DROP … IF EXISTS davor", true, null, "drop").el,
      haken("Trigger, Prozeduren, Funktionen", !only, null, "routines").el,
      haken("gzip-komprimiert (.sql.gz)", false, null, "gzip").el);
    const fmt = auswahl("format", [["sql", "SQL"], ["csv", "CSV (eine Tabelle, ; getrennt, UTF-8)"]], "sql");
    leeren(el, h("div", { class: "karte" }, kopf("Export"),
      h("div", { class: "raster" }, feld("Tabellen", wahl, "Strg/Umschalt für mehrere"), h("div", {}, feld("Format", fmt), opt)),
      h("p", { class: "leise klein-t" }, "MIRA baut die Datei im Speicher – für sehr große Datenbanken (mehrere GB) besser mysqldump bzw. mariadb-dump direkt am Server benutzen."),
      h("div", { class: "leiste" }, h("button", { class: "p", onclick: () => {
        const namen = [...wahl.selectedOptions].map(o => o.value);
        if (!namen.length) return hinweis("Keine Tabelle gewählt", true);
        const o = lesenForm(opt);
        const f = fmt.value;
        if (f === "csv" && namen.length !== 1) return hinweis("CSV gibt es nur für genau eine Tabelle", true);
        const p = { format: f, structure: o.structure ? 1 : 0, data: o.data ? 1 : 0, drop: o.drop ? 1 : 0, routines: o.routines ? 1 : 0, gzip: o.gzip ? 1 : 0 };
        lade(f === "csv" ? ctx.link(`${B}${tP(db, namen[0])}/export?format=csv`)
          : ctx.link(`${B}${dbP(db)}/export?` + qs({ ...p, tables: namen.length === tables.length ? "" : namen.join(",") })));
      } }, "Herunterladen"))));
  }

  function importForm(el, db) {
    const datei = h("input", { type: "file", accept: ".sql,.gz,.txt,application/sql,application/gzip" });
    const weiter = haken("Bei Fehlern weitermachen (höchstens 50 Fehler)", false);
    const erg = h("div", {});
    leeren(el, h("div", { class: "karte" }, kopf("Import"),
      h("p", { class: "klein-t" }, `Führt eine SQL-Datei (.sql oder .sql.gz, z. B. aus mysqldump/mariadb-dump oder dem Export oben) in der Datenbank „${db}“ aus. DELIMITER-Blöcke für Prozeduren/Trigger werden verstanden.`),
      feld("Datei", datei), weiter.el,
      h("div", { class: "leiste" }, h("button", { class: "p", onclick: async e => {
        const f = datei.files[0];
        if (!f) return hinweis("Bitte eine Datei wählen", true);
        if (!(await bestaetigen(`„${f.name}“ (${groesse(f.size)}) in „${db}“ ausführen? Vorhandene Tabellen können dabei überschrieben werden (DROP TABLE im Dump).`, "Importieren", false))) return;
        e.target.disabled = true;
        leeren(erg, h("p", {}, h("span", { class: "dreher" }), " Import läuft …"));
        try {
          const r = await ctx.api("POST", `${B}/import?` + qs({ database: db, continue: weiter.i.checked ? 1 : "" }), new Blob([await f.arrayBuffer()], { type: "application/octet-stream" }));
          leeren(erg, h("p", {}, punkt(r.errors.length ? "bad" : "ok"), ` ${r.executed} von ${r.statements} Anweisungen ausgeführt · ${zahl(r.affected)} Zeilen · ${r.seconds} s`),
            r.errors.length ? tabelle([{ t: "#", w: x => x.index }, { t: "Fehler", w: x => h("span", { class: "bad" }, x.error) }, { t: "Anweisung", w: x => h("code", { class: "klein-t" }, x.statement.slice(0, 200)) }], r.errors) : null);
        } catch (er) { leeren(erg, h("p", {}, punkt("bad"), " ", er.message)); }
        finally { e.target.disabled = false; }
      } }, "Importieren")), erg));
  }

  // ================================================================ table
  async function table(main, q) {
    const db = q.get("db"), t = q.get("t");
    const tab = q.get("tab") || "data";
    if (!db || !t) return go("overview");
    const inhalt = h("div", {});
    leeren(main, pfad([db, href("database", { db })], [t]),
      tabs([["data", "Daten"], ["structure", "Struktur"], ["sql", "SQL"], ["ops", "Vorgänge"]], tab, k => go("table", { db, t, tab: k })), inhalt);
    if (tab === "structure") return struktur(inhalt, db, t);
    if (tab === "sql") return sqlKonsole(inhalt, { db, vorlage: `SELECT * FROM \`${t.replace(/`/g, "``")}\` WHERE 1 LIMIT 100;` });
    if (tab === "ops") return vorgaenge(inhalt, db, t);
    return daten(inhalt, db, t, q);
  }

  async function daten(el, db, t, q) {
    const st = { offset: Number(q.get("offset") || 0), limit: Number(q.get("limit") || localStorage.getItem("dana.limit") || 50),
      order: q.get("order") || "", dir: q.get("dir") || "", s: q.get("s") || "", where: q.get("where") || "" };
    const nav = o => go("table", { db, t, ...st, ...o, tab: "data" });
    const d = await get(`${tP(db, t)}/rows?` + qs(st));
    const editierbar = write && !d.view;
    const keyOf = row => {
      const cols = d.key.length ? d.key : d.names;
      const k = {};
      for (const c of cols) { const i = d.names.indexOf(c); k[c] = row[i]; }
      return k;
    };
    const wahl = new Set();
    const suche = eingabe("s", st.s, { placeholder: "in allen Spalten suchen …", type: "search" });
    const wo = eingabe("where", st.where, { placeholder: "WHERE … (SQL, z. B. id > 100 AND name LIKE 'a%')", class: "dana-mono" });
    const loeschen = h("button", { class: "klein gefahr", style: { visibility: "hidden" }, onclick: async () => {
      if (!(await bestaetigen(`${wahl.size} Zeile(n) löschen?`, "Löschen"))) return;
      const keys = [...wahl].map(i => keyOf(d.rows[i]));
      const r = await versuch(() => send("POST", `${tP(db, t)}/rows/delete`, { keys }));
      hinweis(`${r.affected} Zeile(n) gelöscht`); ctx.rerender();
    } }, "Gewählte löschen");
    const seiten = Math.max(1, Math.ceil(d.total / d.limit)), seite = Math.floor(d.offset / d.limit) + 1;
    const pager = h("span", { class: "dana-pager" },
      h("button", { class: "klein", disabled: seite <= 1, onclick: () => nav({ offset: 0 }) }, "«"),
      h("button", { class: "klein", disabled: seite <= 1, onclick: () => nav({ offset: Math.max(0, d.offset - d.limit) }) }, "‹"),
      h("span", { class: "klein-t" }, ` Seite ${seite} von ${zahl(seiten)} · ${d.approx ? "≈ " : ""}${zahl(d.total)} Zeilen `),
      h("button", { class: "klein", disabled: seite >= seiten, onclick: () => nav({ offset: d.offset + d.limit }) }, "›"),
      h("button", { class: "klein", disabled: seite >= seiten || d.approx, onclick: () => nav({ offset: (seiten - 1) * d.limit }) }, "»"),
      auswahl("limit", [25, 50, 100, 250, 500], d.limit));
    pager.querySelector("select").onchange = e => { localStorage.setItem("dana.limit", e.target.value); nav({ limit: e.target.value, offset: 0 }); };
    const sortKnopf = c => {
      const an = st.order === c;
      return h("th", { class: "klick dana-sort", title: "Sortieren", onclick: () => nav({ order: c, dir: an && st.dir !== "desc" ? "desc" : "asc", offset: 0 }) },
        c, an ? (st.dir === "desc" ? " ▼" : " ▲") : "",
        h("div", { class: "dana-typ" }, (d.columns.find(x => x.name === c) || {}).type || ""));
    };
    leeren(el, h("div", { class: "karte" },
      h("div", { class: "leiste" },
        h("form", { class: "dana-suche", onsubmit: e => { e.preventDefault(); nav({ s: suche.value, where: wo.value, offset: 0 }); } },
          suche, wo, h("button", { class: "klein" }, "Filtern"),
          st.s || st.where ? h("button", { type: "button", class: "klein", onclick: () => nav({ s: "", where: "", offset: 0 }) }, "Zurücksetzen") : null),
        h("span", { class: "rechts" },
          editierbar ? loeschen : null,
          h("button", { class: "klein", onclick: () => lade(ctx.link(`${B}${tP(db, t)}/export?format=csv`)) }, "CSV"),
          h("button", { class: "klein", onclick: () => lade(ctx.link(`${B}${tP(db, t)}/export?` + qs({ structure: 1, data: 1, drop: 1, routines: 0 }))) }, "SQL"),
          editierbar ? h("button", { class: "p klein", onclick: () => zeile(db, t, d, null) }, "+ Zeile") : null)),
      !d.key.length && editierbar ? h("p", { class: "klein-t warn" }, "Die Tabelle hat keinen Primärschlüssel – Zeilen werden über alle Spaltenwerte gefunden (LIMIT 1). Besser einen Primärschlüssel anlegen.") : null,
      d.rows.length ? h("div", { class: "tab-wrap dana-raster" }, h("table", {},
        h("thead", {}, h("tr", {}, editierbar ? h("th", {}, h("input", { type: "checkbox", onclick: e => {
          el.querySelectorAll("input.dana-wahl").forEach((i, n) => { i.checked = e.target.checked; e.target.checked ? wahl.add(n) : wahl.delete(n); });
          loeschen.style.visibility = wahl.size ? "visible" : "hidden";
        } })) : null, d.names.map(sortKnopf))),
        h("tbody", {}, d.rows.map((r, n) => h("tr", { class: "klick", onclick: () => zeile(db, t, d, r) },
          editierbar ? h("td", {}, h("input", { type: "checkbox", class: "dana-wahl", onclick: e => {
            e.stopPropagation(); e.target.checked ? wahl.add(n) : wahl.delete(n);
            loeschen.style.visibility = wahl.size ? "visible" : "hidden";
          } })) : null,
          r.map((v, i) => h("td", { class: d.key.includes(d.names[i]) ? "dana-key" : "" }, zelle(v)))))))) :
        h("div", { class: "leer" }, st.s || st.where ? "Keine Treffer" : "Die Tabelle ist leer"),
      h("div", { class: "leiste dana-unten" }, pager)));
  }

  const istText = typ => /text|json|blob|binary|geometry/i.test(typ);
  const istBinaer = typ => /blob|binary/i.test(typ);

  /** insert (row = null) or edit a row */
  async function zeile(db, t, d, row) {
    let werte = row;
    let eindeutig = true;
    if (row) {
      const k = {};
      const cols = d.key.length ? d.key : d.names;
      for (const c of cols) k[c] = row[d.names.indexOf(c)];
      try {
        const full = await send("POST", `${tP(db, t)}/rows/get`, { key: k });
        werte = d.names.map(n => full.row[full.names.indexOf(n)]);
        eindeutig = full.unique;
      } catch (e) { hinweis(e.message, true); return; }
    }
    const darfAendern = write && !d.view;
    const felder = [];
    const form = h("div", { class: "dana-zeile" });
    for (const [i, c] of d.columns.entries()) {
      const v = werte ? werte[i] : undefined;
      const hex = v && typeof v === "object" && v.$hex !== undefined;
      const anfang = v === null || v === undefined ? "" : hex ? v.$hex : typeof v === "object" ? (v.$trunc ?? "") : String(v);
      const enumW = /^(enum|set)\((.*)\)$/i.exec(c.type);
      let inp;
      if (enumW && /^enum/i.test(c.type)) {
        const opts = [...enumW[2].matchAll(/'((?:[^'\\]|\\.|'')*)'/g)].map(m => m[1].replace(/''/g, "'"));
        inp = h("select", { disabled: !darfAendern }, h("option", { value: "" }, row ? "" : "(Standard)"), opts.map(o => h("option", { value: o, selected: o === anfang }, o)));
      } else if (istText(c.type) || anfang.length > 80 || anfang.includes("\n")) {
        inp = h("textarea", { rows: Math.min(10, Math.max(2, anfang.split("\n").length)), readonly: !darfAendern, class: hex ? "dana-mono" : "" }, anfang);
      } else {
        inp = eingabe("", anfang, { readonly: !darfAendern, placeholder: !row && /auto_increment/i.test(c.extra) ? "automatisch" : !row && c.default !== null ? `Standard: ${c.default}` : "" });
      }
      const nul = c.null ? h("input", { type: "checkbox", checked: row ? v === null : false, disabled: !darfAendern, title: "NULL" }) : null;
      if (nul) nul.onchange = () => { inp.disabled = nul.checked; };
      if (nul && nul.checked) inp.disabled = true;
      const binHinweis = hex ? marke(`binär · ${v.len} Byte · Hex`, "warn") : istBinaer(c.type) && !row ? marke("Text oder Hex mit 0x…", "") : null;
      felder.push({ c, inp, nul, anfang, hex, orig: v });
      form.append(h("div", { class: "dana-feld" },
        h("label", {}, h("b", {}, c.name), d.key.includes(c.name) ? marke("Schlüssel", "akz") : null, h("span", { class: "dana-typ" }, ` ${c.type}${c.extra ? " · " + c.extra : ""}`), c.comment ? h("div", { class: "leise klein-t" }, c.comment) : null),
        h("div", { class: "dana-eingabe" }, inp, nul ? h("label", { class: "klein-t dana-null-wahl" }, nul, " NULL") : null, binHinweis)));
    }
    const lesen = (nurGeaendert) => {
      const out = {};
      for (const f of felder) {
        const neu = f.nul && f.nul.checked ? null : f.inp.value;
        if (nurGeaendert) {
          const alt = f.orig === null || f.orig === undefined ? null : f.anfang;
          if (neu === alt) continue;
        } else if (neu === "" && (f.inp.tagName !== "TEXTAREA" || !f.inp.value) && !(f.nul && f.nul.checked)) continue;   // empty = default
        if (neu !== null && (f.hex || (istBinaer(f.c.type) && /^0x[0-9a-f\s]*$/i.test(neu)))) out[f.c.name] = { $hex: neu.replace(/^0x/i, "") };
        else out[f.c.name] = neu;
      }
      return out;
    };
    const knoepfe = [["Schließen", null]];
    if (darfAendern && row) {
      knoepfe.unshift(["Löschen", async () => {
        if (!(await bestaetigen("Diese Zeile löschen?", "Löschen"))) return false;
        const k = {}; const cols = d.key.length ? d.key : d.names;
        for (const c of cols) k[c] = row[d.names.indexOf(c)];
        await versuch(() => send("POST", `${tP(db, t)}/rows/delete`, { key: k }), "Zeile gelöscht");
        ctx.rerender();
      }, "gefahr"]);
      knoepfe.push(["Als neue Zeile", async () => {
        const vals = lesen(false);
        for (const c of d.columns) if (/auto_increment/i.test(c.extra)) delete vals[c.name];
        const r = await versuch(() => send("POST", `${tP(db, t)}/rows`, { values: vals }), "Zeile eingefügt");
        if (r.insert_id) hinweis(`Zeile eingefügt (ID ${r.insert_id})`);
        ctx.rerender();
      }]);
      knoepfe.push(["Speichern", async () => {
        const vals = lesen(true);
        if (!Object.keys(vals).length) { hinweis("Nichts geändert"); return true; }
        const k = {}; const cols = d.key.length ? d.key : d.names;
        for (const c of cols) k[c] = row[d.names.indexOf(c)];
        const r = await versuch(() => send("PUT", `${tP(db, t)}/rows`, { key: k, values: vals }));
        hinweis(r.affected ? "Gespeichert" : "Keine Änderung (Wert war schon so)");
        ctx.rerender();
      }, "p"]);
    } else if (darfAendern) {
      knoepfe.push(["Einfügen", async () => {
        const r = await versuch(() => send("POST", `${tP(db, t)}/rows`, { values: lesen(false) }));
        hinweis(r.insert_id ? `Zeile eingefügt (ID ${r.insert_id})` : "Zeile eingefügt");
        ctx.rerender();
      }, "p"]);
    }
    await dialog(row ? `Zeile in ${t}` : `Neue Zeile in ${t}`, h("div", {},
      row && !eindeutig ? h("p", { class: "klein-t warn" }, "Achtung: mehrere gleiche Zeilen – Speichern/Löschen betrifft nur eine davon.") : null,
      !row ? h("p", { class: "leise klein-t" }, "Leere Felder bekommen den Standardwert der Spalte.") : null, form), knoepfe);
  }

  // ---------------------------------------------------------------- structure
  async function struktur(el, db, t) {
    const s = await get(`${tP(db, t)}/structure`);
    const aend = write && !s.view;
    leeren(el,
      h("div", { class: "karte" }, kopf(s.view ? "Spalten (Ansicht)" : "Spalten",
        aend ? h("button", { class: "p klein", onclick: () => spalte(db, t, s, null) }, "+ Spalte") : null),
      h("p", { class: "leise klein-t" }, s.view ? "Ansicht (VIEW)" : `${s.engine} · ${s.collation} · ≈ ${zahl(s.rows)} Zeilen${s.auto_increment ? " · nächste ID " + s.auto_increment : ""}${s.comment ? " · " + s.comment : ""}`),
      tabelle([
        { t: "Name", w: c => h("b", {}, c.name) },
        { t: "Typ", w: c => h("code", {}, c.type) },
        { t: "Schlüssel", w: c => c.key ? marke({ PRI: "primär", UNI: "eindeutig", MUL: "Index" }[c.key] || c.key, c.key === "PRI" ? "akz" : "") : "" },
        { t: "NULL", w: c => c.null ? "ja" : "" },
        { t: "Standard", w: c => c.default === null ? (c.null ? h("span", { class: "dana-null" }, "NULL") : "") : h("code", {}, String(c.default)) },
        { t: "Extra", w: c => h("span", { class: "klein-t" }, c.extra) },
        { t: "Sortierung", w: c => h("span", { class: "klein-t leise" }, c.collation || "") },
        { t: "Kommentar", w: c => h("span", { class: "klein-t" }, c.comment) },
        { t: "", w: c => admin && !s.view ? h("button", { class: "klein gefahr", onclick: async e => {
          e.stopPropagation();
          if (!(await bestaetigen(`Spalte „${c.name}“ mit allen Werten löschen?`, "Löschen"))) return;
          await versuch(() => ctx.api("DELETE", `${B}${tP(db, t)}/columns/${enc(c.name)}`), "Spalte gelöscht"); ctx.rerender();
        } }, "Löschen") : "" }],
        s.columns, { klick: aend ? c => spalte(db, t, s, c) : null })),
      s.view ? null : h("div", { class: "karte" }, kopf("Indizes", aend ? h("button", { class: "klein", onclick: () => index(db, t, s) }, "+ Index") : null),
        tabelle([
          { t: "Name", w: i => h("b", {}, i.name) },
          { t: "Art", w: i => i.name === "PRIMARY" ? marke("primär", "akz") : i.unique ? marke("eindeutig") : i.type === "FULLTEXT" ? marke("Volltext") : marke("Index") },
          { t: "Spalten", w: i => h("code", {}, i.columns.join(", ")) },
          { t: "Typ", w: i => h("span", { class: "klein-t leise" }, i.type) },
          { t: "", w: i => admin ? h("button", { class: "klein gefahr", onclick: async () => {
            if (!(await bestaetigen(`Index „${i.name}“ löschen?`, "Löschen"))) return;
            await versuch(() => ctx.api("DELETE", `${B}${tP(db, t)}/indexes/${enc(i.name)}`), "Index gelöscht"); ctx.rerender();
          } }, "Löschen") : "" }],
          s.indexes, { leer: "Keine Indizes" })),
      s.foreign_keys.length ? h("div", { class: "karte" }, kopf("Fremdschlüssel"),
        tabelle([{ t: "Name", w: f => f.constraint_name }, { t: "Spalte", w: f => h("code", {}, f.column_name) },
          { t: "verweist auf", w: f => h("a", { href: href("table", { db: f.referenced_table_schema, t: f.referenced_table_name }) }, `${f.referenced_table_schema}.${f.referenced_table_name}.${f.referenced_column_name}`) }], s.foreign_keys)) : null,
      s.triggers.length ? h("div", { class: "karte" }, kopf("Trigger"),
        tabelle([{ t: "Name", w: x => h("b", {}, x.trigger_name) }, { t: "Wann", w: x => `${x.action_timing} ${x.event_manipulation}` },
          { t: "Anweisung", w: x => h("code", { class: "klein-t" }, String(x.action_statement).slice(0, 300)) }], s.triggers)) : null,
      h("div", { class: "karte" }, kopf(s.view ? "CREATE VIEW" : "CREATE TABLE",
        h("button", { class: "klein", onclick: () => navigator.clipboard.writeText(s.create).then(() => hinweis("Kopiert")) }, "Kopieren")),
        h("pre", {}, s.create)));
  }

  const TYPEN = ["INT", "INT UNSIGNED", "BIGINT", "BIGINT UNSIGNED", "TINYINT(1)", "SMALLINT", "MEDIUMINT", "DECIMAL(10,2)", "DOUBLE", "FLOAT",
    "VARCHAR(255)", "CHAR(36)", "TEXT", "MEDIUMTEXT", "LONGTEXT", "JSON", "DATE", "DATETIME", "TIMESTAMP", "TIME", "YEAR",
    "BLOB", "LONGBLOB", "VARBINARY(255)", "BINARY(16)", "ENUM('a','b')", "SET('a','b')", "BOOLEAN", "UUID", "INET6"];
  const typListe = () => h("datalist", { id: "dana-typen" }, TYPEN.map(x => h("option", { value: x })));

  function standardArt(c) {
    if (!c) return ["none", ""];
    if (c.default === null) return [c.null ? "null" : "none", ""];
    if (/^current_timestamp(\(\d*\))?$/i.test(String(c.default))) return ["expr", String(c.default)];
    return ["value", String(c.default)];
  }

  /** add (c = null) or change a column */
  async function spalte(db, t, s, c) {
    const [art, wert] = standardArt(c);
    const stdWert = eingabe("default", wert);
    const stdArt = auswahl("default_kind", [["none", "kein"], ["null", "NULL"], ["value", "Wert"], ["expr", "Ausdruck (CURRENT_TIMESTAMP)"]], art);
    const sync = () => { stdWert.style.display = ["value", "expr"].includes(stdArt.value) ? "" : "none"; };
    stdArt.onchange = sync; sync();
    const f = h("div", {},
      h("div", { class: "raster" },
        feld("Name", eingabe("name", c ? c.name : "", { autocomplete: "off" })),
        feld("Typ", eingabe("type", c ? c.type : "VARCHAR(255)", { list: "dana-typen", class: "dana-mono" }), "z. B. INT UNSIGNED, VARCHAR(100), DECIMAL(10,2), ENUM('a','b')"),
        feld("Standard", h("div", { class: "dana-zeile-inline" }, stdArt, stdWert)),
        feld("Sortierung (Text)", await sortierung("collation", c ? c.collation : "", "(wie Tabelle)")),
        feld("Kommentar", eingabe("comment", c ? c.comment : "")),
        !c ? feld("Position", auswahl("after", [["", "am Ende"], ["^first", "ganz vorne"], ...s.columns.map(x => [x.name, "nach " + x.name])], "")) : null),
      h("div", { class: "dana-haken" },
        haken("NULL erlaubt", c ? c.null : true, null, "null").el,
        haken("AUTO_INCREMENT", c ? /auto_increment/i.test(c.extra) : false, "nur für eine Schlüssel-Spalte", "auto_increment").el,
        haken("ON UPDATE CURRENT_TIMESTAMP", c ? /on update/i.test(c.extra) : false, null, "on_update_ts").el),
      typListe(),
      c ? h("p", { class: "klein-t warn" }, "Typ ändern wandelt alle vorhandenen Werte um – bei großen Tabellen dauert das und sperrt die Tabelle.") : null);
    await dialog(c ? `Spalte „${c.name}“ ändern` : `Neue Spalte in ${t}`, f, [["Abbrechen", null], [c ? "Speichern" : "Hinzufügen", async () => {
      const d = lesenForm(f);
      if (!d.name || !d.type) { hinweis("Name und Typ angeben", true); return false; }
      const r = c ? await versuch(() => send("PUT", `${tP(db, t)}/columns/${enc(c.name)}`, d))
        : await versuch(() => send("POST", `${tP(db, t)}/columns`, d));
      hinweis("Ausgeführt: " + r.sql.slice(0, 160));
      ctx.rerender();
    }, "p"]]);
  }

  async function index(db, t, s) {
    const spalten = eingabe("columns", "", { placeholder: "spalte1, spalte2(10)", class: "dana-mono" });
    const f = h("div", {}, h("div", { class: "raster" },
      feld("Art", auswahl("kind", [["INDEX", "Index"], ["UNIQUE", "eindeutig (UNIQUE)"], ["FULLTEXT", "Volltext"], ...(s.indexes.some(i => i.name === "PRIMARY") ? [] : [["PRIMARY", "Primärschlüssel"]])], "INDEX")),
      feld("Name", eingabe("name", "", { placeholder: "automatisch" }))),
      feld("Spalten", spalten, "Reihenfolge zählt; (n) = nur die ersten n Zeichen"),
      h("div", { class: "chips" }, s.columns.map(c => h("button", { type: "button", class: "klein", onclick: () => { spalten.value = (spalten.value ? spalten.value + ", " : "") + c.name; } }, c.name))));
    await dialog(`Index für ${t}`, f, [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesenForm(f);
      d.columns = d.columns.split(",").map(x => x.trim()).filter(Boolean);
      await versuch(() => send("POST", `${tP(db, t)}/indexes`, d), "Index angelegt");
      ctx.rerender();
    }, "p"]]);
  }

  async function neueTabelle(db, dbInfo) {
    const zeilen = h("tbody", {});
    const neueZeile = (c = {}) => {
      const tr = h("tr", {},
        h("td", {}, eingabe("name", c.name || "", { placeholder: "name" })),
        h("td", {}, eingabe("type", c.type || "VARCHAR(255)", { list: "dana-typen", class: "dana-mono" })),
        h("td", {}, h("input", { type: "checkbox", name: "null", checked: !!c.null })),
        h("td", {}, auswahl("default_kind", [["none", "–"], ["null", "NULL"], ["value", "Wert"], ["expr", "Ausdruck"]], c.default_kind || "none"), eingabe("default", c.default || "", { class: "dana-klein" })),
        h("td", {}, h("input", { type: "checkbox", name: "auto_increment", checked: !!c.auto_increment })),
        h("td", {}, h("input", { type: "checkbox", name: "primary", checked: !!c.primary })),
        h("td", {}, eingabe("comment", c.comment || "", { class: "dana-klein" })),
        h("td", {}, h("button", { type: "button", class: "klein", onclick: () => tr.remove() }, "×")));
      zeilen.append(tr);
    };
    neueZeile({ name: "id", type: "INT UNSIGNED", auto_increment: true, primary: true });
    neueZeile({ name: "", type: "VARCHAR(255)", null: true });
    const kopfF = h("div", { class: "raster" }, feld("Tabellenname", eingabe("name", "", { autocomplete: "off" })),
      feld("Engine", auswahl("engine", ["InnoDB", "Aria", "MyISAM", "MEMORY", "ARCHIVE"], "InnoDB")),
      feld("Sortierung", await sortierung("collation", "", `(wie Datenbank: ${dbInfo.collation})`)),
      feld("Kommentar", eingabe("comment", "")));
    const vorschau = h("pre", { style: { display: "none" } });
    const lesen = () => {
      const d = lesenForm(kopfF);
      if (d.collation) d.charset = d.collation.split("_")[0];
      d.columns = [...zeilen.children].map(tr => lesenForm(tr)).filter(c => c.name);
      return d;
    };
    await dialog(`Neue Tabelle in ${db}`, h("div", { class: "dana-breit" }, kopfF,
      h("div", { class: "tab-wrap" }, h("table", { class: "dana-spalten" }, h("thead", {}, h("tr", {}, ["Spalte", "Typ", "NULL", "Standard", "A_I", "Primär", "Kommentar", ""].map(x => h("th", {}, x)))), zeilen)),
      h("div", { class: "leiste" }, h("button", { type: "button", class: "klein", onclick: () => neueZeile({ null: true }) }, "+ Spalte"),
        h("button", { type: "button", class: "klein", onclick: async () => {
          const r = await versuch(() => send("POST", `${dbP(db)}/tables`, { ...lesen(), preview: true }));
          vorschau.textContent = r.sql; vorschau.style.display = "";
        } }, "SQL-Vorschau")),
      vorschau, typListe()), [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesen();
      await versuch(() => send("POST", `${dbP(db)}/tables`, d), `Tabelle „${d.name}“ angelegt`);
      go("table", { db, t: d.name, tab: "structure" });
    }, "p"]]);
  }

  // ---------------------------------------------------------------- operations
  async function vorgaenge(el, db, t) {
    const s = await get(`${tP(db, t)}/structure`);
    const erg = h("div", {});
    const f = h("div", { class: "raster" },
      feld("Name", eingabe("name", t)),
      !s.view ? feld("Kommentar", eingabe("comment", s.comment || "")) : null,
      !s.view ? feld("Engine", auswahl("engine", ["InnoDB", "Aria", "MyISAM", "MEMORY", "ARCHIVE"], s.engine)) : null,
      !s.view ? feld("Sortierung (wandelt alle Textspalten um)", await sortierung("collation", s.collation)) : null,
      !s.view ? feld("Nächster AUTO_INCREMENT", eingabe("auto_increment", s.auto_increment || "", { type: "number" })) : null);
    const wartung = op => async () => {
      const r = await versuch(() => send("POST", `${tP(db, t)}/maintenance`, { op }));
      leeren(erg, tabelle(Object.keys(r.result[0] || {}).map(k => ({ t: k, w: x => String(x[k] ?? "") })), r.result));
    };
    leeren(el,
      write ? h("div", { class: "karte" }, kopf("Tabelle ändern"), f, h("div", { class: "leiste" }, h("button", { class: "p", onclick: async () => {
        const d = lesenForm(f);
        const neu = {};
        if (d.name && d.name !== t) neu.name = d.name;
        if (!s.view) {
          if (d.comment !== (s.comment || "")) neu.comment = d.comment;
          if (d.engine && d.engine !== s.engine) neu.engine = d.engine;
          if (d.collation && d.collation !== s.collation) neu.collation = d.collation;
          if (d.auto_increment && String(d.auto_increment) !== String(s.auto_increment || "")) neu.auto_increment = d.auto_increment;
        }
        if (!Object.keys(neu).length) return hinweis("Nichts geändert");
        await versuch(() => send("PUT", tP(db, t), neu), "Geändert");
        go("table", { db, t: neu.name || t, tab: "ops" });
      } }, "Speichern"))) : null,
      !s.view && write ? h("div", { class: "karte" }, kopf("Wartung"),
        h("div", { class: "leiste" }, h("button", { onclick: wartung("optimize") }, "Optimieren"), h("button", { onclick: wartung("analyze") }, "Analysieren"),
          h("button", { onclick: wartung("check") }, "Prüfen"), h("button", { onclick: wartung("repair") }, "Reparieren"), h("button", { onclick: wartung("flush") }, "Flush")), erg) : null,
      admin ? h("div", { class: "karte" }, kopf("Gefahrenbereich"), h("div", { class: "leiste" },
        !s.view ? h("button", { class: "gefahr", onclick: async () => {
          if (!(await bestaetigen(`Alle Zeilen aus „${t}“ löschen (TRUNCATE, AUTO_INCREMENT beginnt wieder bei 1)?`, "Leeren"))) return;
          await versuch(() => ctx.api("DELETE", `${B}${tP(db, t)}?mode=truncate`), "Tabelle geleert"); ctx.rerender();
        } }, "Leeren (TRUNCATE)") : null,
        h("button", { class: "gefahr", onclick: async () => {
          if (!(await bestaetigen(`${s.view ? "Ansicht" : "Tabelle"} „${t}“ endgültig löschen?`, "Löschen"))) return;
          await versuch(() => ctx.api("DELETE", `${B}${tP(db, t)}${s.view ? "?view=1" : ""}`), "Gelöscht"); go("database", { db });
        } }, s.view ? "Ansicht löschen" : "Tabelle löschen"))) : null,
      !write ? h("div", { class: "karte leer" }, "Nur mit Bearbeiten-Rechten.") : null);
  }

  // ================================================================ SQL console
  const HIST = `dana.hist.${ctx.conn.id}`;
  const verlauf = () => { try { return JSON.parse(localStorage.getItem(HIST) || "[]"); } catch { return []; } };

  async function sqlKonsole(el, { db, vorlage } = {}) {
    const dbs = (await get("/databases")).databases;
    const dbWahl = h("select", {}, h("option", { value: "" }, "(keine Datenbank)"), dbs.map(x => h("option", { value: x.name, selected: x.name === db }, x.name)));
    const entwurf = `dana.sql.${ctx.conn.id}.${db || ""}`;
    const text = h("textarea", { class: "dana-sql", rows: 9, spellcheck: "false", placeholder: "SELECT …;  (Strg+Enter ausführen)" },
      sessionStorage.getItem(entwurf) || vorlage || "");
    text.addEventListener("input", () => sessionStorage.setItem(entwurf, text.value));
    const maxRows = auswahl("max", [100, 500, 1000, 5000], 1000);
    const weiter = h("input", { type: "checkbox" });
    const hist = h("select", { class: "klein", onchange: e => { if (e.target.value !== "") { text.value = verlauf()[e.target.value]; e.target.value = ""; text.focus(); } } },
      h("option", { value: "" }, "Verlauf …"), verlauf().map((x, i) => h("option", { value: i }, x.replace(/\s+/g, " ").slice(0, 90))));
    const erg = h("div", {});
    const los = async () => {
      const sql = text.value.trim();
      if (!sql) return;
      const v = verlauf().filter(x => x !== sql); v.unshift(sql);
      try { localStorage.setItem(HIST, JSON.stringify(v.slice(0, 40))); } catch { /* full */ }
      leeren(erg, h("p", {}, h("span", { class: "dreher" }), " läuft …"));
      const t0 = performance.now();
      try {
        const r = write
          ? await send("POST", "/query", { sql, database: dbWahl.value, max_rows: Number(maxRows.value), continue: weiter.checked })
          : await get("/select?" + qs({ sql, database: dbWahl.value, max_rows: maxRows.value }));
        ergebnisse(erg, r, performance.now() - t0);
      } catch (e) { leeren(erg, h("div", { class: "karte" }, h("p", {}, punkt("bad"), " ", e.message))); }
    };
    text.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); los(); }
      if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); const p = text.selectionStart; text.setRangeText("  ", p, text.selectionEnd, "end"); }
    });
    leeren(el, h("div", { class: "karte" },
      h("div", { class: "leiste" }, feldInline("Datenbank", dbWahl), feldInline("höchstens Zeilen", maxRows),
        write ? h("label", { class: "klein-t" }, weiter, " bei Fehler weiter") : marke("nur lesen: eine SELECT/SHOW-Abfrage", "warn"),
        h("span", { class: "rechts" }, hist)),
      text,
      h("div", { class: "leiste" }, h("button", { class: "p", onclick: los }, "Ausführen"), h("span", { class: "leise klein-t" }, "Strg+Enter · mehrere Anweisungen mit ; trennen · DELIMITER wird verstanden"))),
    erg);
    text.focus();
  }
  const feldInline = (t, e) => h("label", { class: "klein-t dana-inline" }, h("span", { class: "leise" }, t + " "), e);

  function ergebnisse(el, r, ms) {
    leeren(el, h("p", { class: "klein-t leise" }, `${r.done} von ${r.count} Anweisung(en) ausgeführt${r.errors ? ` · ${r.errors} Fehler` : ""} · ${Math.round(ms)} ms`),
      r.results.map(x => h("div", { class: "karte dana-ergebnis" },
        h("pre", { class: "dana-anweisung" }, x.statement.length > 600 ? x.statement.slice(0, 600) + " …" : x.statement),
        x.error ? h("p", { class: "bad" }, punkt("bad"), " ", x.error)
          : x.sets.length ? x.sets.map(s => h("div", {},
            h("p", { class: "klein-t leise" }, `${s.rows.length}${s.truncated ? "+" : ""} Zeile(n) · ${x.ms} ms`, s.truncated ? marke("gekürzt – LIMIT benutzen", "warn") : null),
            s.rows.length ? h("div", { class: "tab-wrap dana-raster" }, h("table", {},
              h("thead", {}, h("tr", {}, s.columns.map(c => h("th", {}, c)))),
              h("tbody", {}, s.rows.map(row => h("tr", {}, row.map(v => h("td", {}, zelle(v)))))))) : null))
            : h("p", { class: "klein-t" }, punkt("ok"), ` ${zahl(x.affected)} Zeile(n) betroffen`, x.insert_id ? ` · neue ID ${x.insert_id}` : "", ` · ${x.ms} ms`),
        x.warnings ? h("pre", { class: "klein-t warn" }, x.warnings.join("\n")) : null)));
  }

  async function sql(main, q) {
    leeren(main, pfad(["SQL"]));
    const el = h("div", {});
    main.append(el);
    return sqlKonsole(el, { db: q.get("db") || "" });
  }

  // ================================================================ users
  async function users(main) {
    const d = await get("/users");
    leeren(main, pfad(["Benutzer"]), h("div", { class: "karte" },
      kopf(`Benutzer (${d.users.length})`, admin ? h("button", { class: "p klein", onclick: () => neuerBenutzer() }, "+ Benutzer") : null),
      h("p", { class: "leise klein-t" }, "Konten des Datenbank-Servers (mysql.user). Rechte ändern: Zeile anklicken."),
      tabelle([
        { t: "Benutzer", w: u => h("b", {}, u.user || h("span", { class: "leise" }, "(anonym)")) },
        { t: "Host", w: u => h("code", {}, u.host) },
        { t: "Anmeldung", w: u => h("span", { class: "klein-t leise" }, u.plugin) },
        { t: "", w: u => h("span", {}, u.system ? marke("System") : null, u.role ? marke("Rolle", "akz") : null, u.locked ? marke("gesperrt", "bad") : null, u.expired ? marke("Passwort abgelaufen", "warn") : null,
          !u.has_password && !u.role && !u.system && !/socket|gssapi|pam/.test(u.plugin) ? marke("ohne Passwort", "bad") : null, u.super ? marke("Super", "warn") : null) }],
      d.users, { klick: u => benutzer(u) })));
  }

  async function rechteForm(start = "*.*") {
    const p = await get("/privileges");
    const dbs = (await get("/databases")).databases;
    const ziel = h("select", { name: "on" }, h("option", { value: "*.*", selected: start === "*.*" }, "alle Datenbanken (*.*)"),
      dbs.map(x => h("option", { value: `\`${x.name.replace(/`/g, "``")}\`.*`, selected: start === x.name }, `${x.name}.*`)));
    const alle = h("input", { type: "checkbox", name: "all" });
    const boxen = h("div", { class: "dana-rechte" }, p.privileges.map(x => h("label", { class: "klein-t", "data-global": p.global_only.includes(x) ? "1" : null },
      h("input", { type: "checkbox", value: x, class: "dana-recht" }), " ", x)));
    const sync = () => {
      const glob = ziel.value === "*.*";
      boxen.querySelectorAll("label[data-global]").forEach(l => { l.style.display = glob ? "" : "none"; if (!glob) l.querySelector("input").checked = false; });
      boxen.style.opacity = alle.checked ? ".4" : "1";
    };
    ziel.onchange = sync; alle.onchange = sync; sync();
    const schnell = (liste) => () => { alle.checked = false; boxen.querySelectorAll("input").forEach(i => { i.checked = liste.includes(i.value); }); sync(); };
    const el = h("div", {},
      h("div", { class: "raster" }, feld("Gilt für", ziel), h("div", {}, h("label", { class: "haken" }, alle, h("span", {}, h("b", {}, "ALL PRIVILEGES"))),
        h("label", { class: "haken" }, h("input", { type: "checkbox", name: "grant_option" }), h("span", {}, "darf Rechte weitergeben (GRANT OPTION)")))),
      h("div", { class: "leiste" }, h("span", { class: "klein-t leise" }, "Schnellwahl:"),
        h("button", { type: "button", class: "klein", onclick: schnell(["SELECT", "SHOW VIEW"]) }, "nur lesen"),
        h("button", { type: "button", class: "klein", onclick: schnell(["SELECT", "INSERT", "UPDATE", "DELETE", "SHOW VIEW", "EXECUTE"]) }, "Daten"),
        h("button", { type: "button", class: "klein", onclick: schnell(["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "INDEX", "ALTER", "CREATE TEMPORARY TABLES", "LOCK TABLES", "EXECUTE", "CREATE VIEW", "SHOW VIEW", "CREATE ROUTINE", "ALTER ROUTINE", "EVENT", "TRIGGER", "REFERENCES"]) }, "Anwendung (Daten + Struktur)")),
      boxen);
    el.lesen = () => ({ on: ziel.value, grant_option: el.querySelector("[name=grant_option]").checked,
      privileges: alle.checked ? ["ALL"] : [...boxen.querySelectorAll("input:checked")].map(i => i.value) });
    return el;
  }

  async function neuerBenutzer() {
    const f = h("div", { class: "raster" }, feld("Benutzer", eingabe("user", "", { autocomplete: "off" })),
      feld("Host", eingabe("host", "%"), "% = von überall, localhost, 192.168.10.% …"),
      pwFeld("password", "Passwort"));
    const db = haken("Gleichnamige Datenbank anlegen und alle Rechte darauf geben", false);
    const mitRechten = haken("Außerdem Rechte vergeben", false);
    const rechte = await rechteForm();
    rechte.style.display = "none";
    mitRechten.i.onchange = () => { rechte.style.display = mitRechten.i.checked ? "" : "none"; };
    await dialog("Neuer Datenbank-Benutzer", h("div", {}, f, db.el, mitRechten.el, rechte), [["Abbrechen", null], ["Anlegen", async () => {
      const d = { ...lesenForm(f), create_db: db.i.checked };
      if (mitRechten.i.checked) Object.assign(d, rechte.lesen());
      if (!d.password && !(await bestaetigen("Benutzer ohne Passwort anlegen?", "Trotzdem anlegen"))) return false;
      await versuch(() => send("POST", "/users", d), `Benutzer ${d.user}@${d.host} angelegt`);
      ctx.rerender();
    }, "p"]]);
  }

  async function benutzer(u) {
    const g = await get("/users/grants?" + qs({ user: u.user, host: u.host })).catch(e => ({ grants: [], fehler: e.message }));
    const liste = h("div", {}, g.fehler ? h("p", { class: "bad" }, g.fehler) : null,
      g.grants.map(x => h("div", { class: "dana-grant" }, h("code", {}, x),
        admin && !/^GRANT USAGE ON \*\.\* TO/i.test(x) && /^GRANT /.test(x) && !/^GRANT PROXY/i.test(x) ? h("button", { class: "klein gefahr", onclick: async () => {
          if (!(await bestaetigen(`Entziehen:\n${x}`, "Entziehen"))) return;
          await versuch(() => send("POST", "/users/revoke", { user: u.user, host: u.host, grant: x }), "Recht entzogen");
          document.querySelectorAll(".dialog-hg").forEach(d => d.remove()); benutzer(u);
        } }, "Entziehen") : null)));
    const rechte = admin ? await rechteForm() : null;
    const konto = h("div", { class: "raster" }, pwFeld("password", "Neues Passwort", "leer = unverändert"),
      feld("Benutzer umbenennen", eingabe("new_user", u.user)), feld("Host", eingabe("new_host", u.host)),
      feld("max. gleichzeitige Verbindungen", eingabe("max_connections", u.max_connections ?? "", { type: "number", min: 0 }), "0 = unbegrenzt"));
    const sperre = haken("Konto gesperrt (ACCOUNT LOCK)", u.locked);
    const inhalt = h("div", { class: "dana-breit" },
      h("h4", {}, "Rechte"), liste,
      admin ? h("details", {}, h("summary", {}, "Rechte vergeben"), rechte, h("div", { class: "leiste" }, h("button", { class: "p klein", onclick: async () => {
        const d = rechte.lesen();
        if (!d.privileges.length) return hinweis("Keine Rechte gewählt", true);
        await versuch(() => send("POST", "/users/grant", { user: u.user, host: u.host, ...d }), "Rechte vergeben");
        document.querySelectorAll(".dialog-hg").forEach(x => x.remove()); benutzer(u);
      } }, "Vergeben"))) : null,
      admin ? h("details", {}, h("summary", {}, "Konto ändern"), konto, sperre.el) : null);
    await dialog(`${u.user || "(anonym)"}@${u.host}`, inhalt, [
      ...(admin ? [["Löschen", async () => {
        if (!(await bestaetigen(`Benutzer ${u.user}@${u.host} löschen? Anwendungen, die sich so anmelden, funktionieren danach nicht mehr.`, "Löschen"))) return false;
        await versuch(() => ctx.api("DELETE", `${B}/users?` + qs({ user: u.user, host: u.host })), "Benutzer gelöscht");
        ctx.rerender();
      }, "gefahr"]] : []),
      ["Schließen", null],
      ...(admin ? [["Konto speichern", async () => {
        const d = lesenForm(konto);
        const out = { user: u.user, host: u.host };
        if (d.password) out.password = d.password;
        if (d.new_user !== u.user) out.new_user = d.new_user;
        if (d.new_host !== u.host) out.new_host = d.new_host;
        if (String(d.max_connections) !== String(u.max_connections ?? "")) out.max_connections = d.max_connections;
        if (sperre.i.checked !== u.locked) out.locked = sperre.i.checked;
        if (Object.keys(out).length === 2) { hinweis("Nichts geändert"); return false; }
        await versuch(() => send("PUT", "/users", out), "Konto gespeichert");
        ctx.rerender();
      }, "p"]] : [])]);
  }

  // ================================================================ status
  async function status(main, q) {
    const tab = q.get("tab") || "processes";
    const el = h("div", {});
    leeren(main, pfad(["Status"]), tabs([["processes", "Prozesse"], ["variables", "Variablen"], ["status", "Statuswerte"]], tab, k => go("status", { tab: k })), el);
    if (tab === "processes") {
      const d = await get("/processes");
      const nurAktiv = localStorage.getItem("dana.aktiv") === "1";
      const liste = d.processes.filter(p => !nurAktiv || (p.command !== "Sleep"));
      return leeren(el, h("div", { class: "karte" },
        kopf(`Prozesse (${d.processes.length})`,
          h("label", { class: "klein-t leise dana-schalter" }, h("input", { type: "checkbox", checked: nurAktiv, onchange: e => { localStorage.setItem("dana.aktiv", e.target.checked ? "1" : "0"); ctx.rerender(); } }), " ohne Sleep"),
          h("button", { class: "klein", onclick: () => ctx.rerender() }, "Aktualisieren")),
        tabelle([
          { t: "ID", w: p => p.id, z: true },
          { t: "Benutzer", w: p => p.user },
          { t: "Host", w: p => h("span", { class: "klein-t" }, p.host) },
          { t: "DB", w: p => p.db || "" },
          { t: "Befehl", w: p => p.command },
          { t: "Zeit", w: p => p.time, z: true },
          { t: "Zustand", w: p => h("span", { class: "klein-t leise" }, p.state || "") },
          { t: "Anweisung", w: p => p.info ? h("code", { class: "klein-t dana-wert" }, String(p.info).slice(0, 200)) : "" },
          { t: "", w: p => admin && p.id !== d.own && p.command !== "Daemon" ? h("button", { class: "klein gefahr", onclick: async () => {
            if (!(await bestaetigen(`Prozess ${p.id} (${p.user}) beenden?`, "Beenden"))) return;
            await versuch(() => ctx.api("DELETE", `${B}/processes/${p.id}`), "Beendet"); ctx.rerender();
          } }, "Beenden") : p.id === d.own ? marke("MIRA") : "" }],
          liste, { leer: "Keine Prozesse" })));
    }
    const such = eingabe("like", q.get("like") || "", { placeholder: "Filter, z. B. innodb_buffer", type: "search" });
    const d = await get("/variables?" + qs({ kind: tab === "status" ? "status" : "variables", like: q.get("like") || "" }));
    leeren(el, h("div", { class: "karte" },
      h("form", { class: "leiste", onsubmit: e => { e.preventDefault(); go("status", { tab, like: such.value }); } }, such, h("button", { class: "klein" }, "Filtern"),
        h("span", { class: "leise klein-t" }, `${d.items.length} Einträge`)),
      tabelle([{ t: "Name", w: x => h("code", {}, x[0]) }, { t: "Wert", w: x => h("span", { class: "dana-wert" }, String(x[1] ?? "")) }], d.items)));
  }

  return {
    views: {
      overview: { titel: "Server", zeigen: overview },
      database: { titel: "Datenbank", zeigen: database },
      table: { titel: "Tabelle", zeigen: table },
      sql: { titel: "SQL", zeigen: sql },
      users: { titel: "Benutzer", zeigen: users },
      status: { titel: "Status", zeigen: status },
    },
  };
});
