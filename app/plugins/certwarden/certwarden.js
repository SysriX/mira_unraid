/* Cert Warden plugin for MIRA – certificates, orders, ACME accounts and private keys.
 * Talks to Cert Warden through MIRA (ctx.api → /api/p/<conn>/certwarden/api/...); MIRA keeps the login.
 * Cert Warden API (v0.31): timestamps are unix seconds, lists take ?limit=&offset=&sort=<field>.<asc|desc>. */
"use strict";

miraPlugin("certwarden", ctx => {
  const V1 = "/certwarden/api/v1";
  const get = p => ctx.api("GET", V1 + p);
  const send = (m, p, d) => ctx.api(m, V1 + p, d === undefined ? {} : d);
  const ts = s => (s ? new Date(s * 1000).toISOString() : null);
  const daysLeft = s => (s ? Math.floor((s * 1000 - Date.now()) / 86400000) : null);
  const ampel = t => t == null ? "" : t < 0 ? "bad" : t < 14 ? "bad" : t < 30 ? "warn" : "ok";
  const ORDER_STATUS = { valid: ["gültig", "ok"], invalid: ["fehlgeschlagen", "bad"], pending: ["wartet", "warn"], ready: ["bereit", "warn"], processing: ["in Arbeit", "warn"] };
  const statusMark = o => {
    if (o.known_revoked) return marke("widerrufen", "bad");
    const [t, a] = ORDER_STATUS[o.status] || [o.status, ""];
    return marke(t, a);
  };
  const names = c => [c.subject, ...(c.subject_alts || [])].filter(Boolean);
  const kopf = (titel, ...rechts) => h("h2", {}, titel, h("span", { class: "rechts" }, rechts));
  const write = ctx.can("write"), admin = ctx.can("admin");

  async function currentValid() {
    const d = await get("/orders/currentvalid?limit=1000&sort=valid_to.asc");
    const by = {};
    for (const o of d.orders || []) by[o.certificate.id] = o;
    return by;
  }

  // ---------------------------------------------------------------- overview
  async function overview(main) {
    const [certs, valid, work] = await Promise.all([get("/certificates?limit=1000&sort=name.asc"), currentValid(),
      get("/orders/fulfilling/status").catch(() => ({}))]);
    const list = certs.certificates || [];
    const rows = list.map(c => ({ c, o: valid[c.id], t: valid[c.id] ? daysLeft(valid[c.id].valid_to) : null }));
    rows.sort((a, b) => (a.t ?? -1e9) - (b.t ?? -1e9));
    const bald = rows.filter(r => r.t != null && r.t < 30).length, ohne = rows.filter(r => !r.o).length;
    const working = Object.values(work.jobs_working || {}).filter(Boolean).length + (work.jobs_waiting || []).length;
    leeren(main,
      h("div", { class: "kacheln" },
        h("div", { class: "kachel" }, h("div", { class: "w" }, list.length), h("div", { class: "l" }, "Zertifikate")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, bald), h("div", { class: "l" }, "laufen in < 30 Tagen ab")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, ohne), h("div", { class: "l" }, "ohne gültiges Zertifikat")),
        h("div", { class: "kachel" }, h("div", { class: "w" }, working), h("div", { class: "l" }, "Aufträge in Arbeit"))),
      h("div", { class: "karte" }, kopf("Ablaufdaten", h("span", { class: "leise klein-t" }, `${ctx.conn.name} · ${ctx.conn.base_url}`)),
        h("p", { class: "leise klein-t" }, "Cert Warden erneuert selbst (ARI bzw. vor Ablauf). „Jetzt erneuern“ stellt sofort einen neuen Auftrag."),
        tabelle([
          { t: "", w: r => punkt(r.o ? ampel(r.t) : "bad") },
          { t: "Zertifikat", w: r => h("b", {}, r.c.name) },
          { t: "Namen", w: r => h("span", { class: "klein-t" }, names(r.c).join(", ")) },
          { t: "läuft ab", w: r => r.o ? datum(ts(r.o.valid_to)) : marke("kein gültiges", "bad") },
          { t: "Tage", w: r => r.t ?? "", z: true },
          { t: "Konto", w: r => h("span", { class: "klein-t" }, r.c.acme_account.name, r.c.acme_account.acme_server && r.c.acme_account.acme_server.is_staging ? marke("Staging", "warn") : null) },
          { t: "", w: r => write ? h("button", { class: "klein", onclick: e => { e.stopPropagation(); renew(r.c); } }, "Jetzt erneuern") : "" }],
        rows, { klick: r => certDialog(r.c.id), leer: "Noch keine Zertifikate" })));
  }

  async function renew(c) {
    if (!(await bestaetigen(`Für „${c.name}“ jetzt einen neuen Auftrag stellen (Zertifikat neu ausstellen)?`, "Erneuern", false))) return;
    await versuch(() => send("POST", `/certificates/${c.id}/orders`), "Auftrag gestellt – Cert Warden arbeitet ihn ab");
    setTimeout(() => ctx.rerender(), 4000);
  }

  // ---------------------------------------------------------------- certificates
  async function certificates(main) {
    const [d, valid] = await Promise.all([get("/certificates?limit=1000&sort=name.asc"), currentValid()]);
    leeren(main, h("div", { class: "karte" }, kopf("Zertifikate", write ? h("button", { class: "p klein", onclick: newCert }, "+ Zertifikat") : null),
      tabelle([
        { t: "", w: c => punkt(valid[c.id] ? ampel(daysLeft(valid[c.id].valid_to)) : "bad") },
        { t: "Name", w: c => h("div", {}, h("b", {}, c.name), c.description ? h("div", { class: "leise klein-t" }, c.description) : null) },
        { t: "Namen", w: c => h("span", { class: "klein-t" }, names(c).join(", ")) },
        { t: "Schlüssel", w: c => h("span", { class: "klein-t" }, c.private_key.name, " ", marke(c.private_key.algorithm.name)) },
        { t: "Konto", w: c => h("span", { class: "klein-t" }, c.acme_account.name) },
        { t: "läuft ab", w: c => valid[c.id] ? datum(ts(valid[c.id].valid_to)) : "–" },
        { t: "zuletzt abgeholt", w: c => c.last_access ? datum(ts(c.last_access)) : h("span", { class: "leise" }, "nie") }],
      d.certificates, { klick: c => certDialog(c.id), leer: "Noch keine Zertifikate" })));
  }

  async function newCert() {
    const o = (await get("/certificates/-1")).certificate_options || {};
    const accounts = (o.acme_accounts || []).filter(a => a.status === "valid" || a.status === undefined);
    if (!accounts.length) { await dialog("Kein ACME-Konto", h("p", {}, "Zuerst unter „ACME-Konten“ ein Konto anlegen und registrieren.")); return; }
    const keyOpts = [["-1", "neuen Schlüssel erzeugen"], ...(o.private_keys || []).map(k => [k.id, `${k.name} (${k.algorithm.name})`])];
    const algo = auswahl("algorithm_value", (o.key_algorithms || []).map(a => [a.value, a.name]), "ecdsap256");
    const key = auswahl("private_key_id", keyOpts, "-1");
    const algoFeld = feld("Algorithmus für den neuen Schlüssel", algo);
    key.addEventListener("change", () => { algoFeld.style.display = key.value === "-1" ? "" : "none"; });
    const f = h("div", {},
      h("div", { class: "raster" },
        feld("Name", eingabe("name", "", { placeholder: "z. B. cloud-firma-de" }), "nur Buchstaben, Zahlen, - _ ."),
        feld("Beschreibung", eingabe("description")),
        feld("Haupt-Name (CN)", eingabe("subject", "", { placeholder: "cloud.firma.de" })),
        feld("Weitere Namen", eingabe("subject_alts", "", { placeholder: "www.cloud.firma.de *.firma.de" }), "mit Leerzeichen oder Komma getrennt"),
        feld("ACME-Konto", auswahl("acme_account_id", accounts.map(a => [a.id, `${a.name}${a.acme_server && a.acme_server.is_staging ? " (Staging)" : ""}`]), accounts[0].id)),
        feld("Schlüssel", key), algoFeld),
      h("details", {}, h("summary", {}, "Nach dem Ausstellen (Post Processing)"),
        h("div", { class: "raster", style: { marginTop: "10px" } },
          feld("Befehl", eingabe("post_processing_command", "", { placeholder: "/opt/ablage/cw-ablegen.sh" }), "z. B. Ablage auf die Freigabe (certwarden-unraid)"),
          feld("Umgebung", eingabe("post_processing_environment", "", { placeholder: "ZIEL=haproxy NAME=cloud" }), "VAR=wert, mit Leerzeichen getrennt"))));
    await dialog("Neues Zertifikat", f, [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesenForm(f);
      const body = { name: d.name.trim(), description: d.description, subject: d.subject.trim(), acme_account_id: Number(d.acme_account_id),
        private_key_id: Number(d.private_key_id), subject_alts: d.subject_alts.split(/[\s,;]+/).filter(Boolean) };
      if (body.private_key_id === -1) body.algorithm_value = d.algorithm_value;
      if (d.post_processing_command) body.post_processing_command = d.post_processing_command;
      if (d.post_processing_environment) body.post_processing_environment = d.post_processing_environment.split(/\s+/).filter(Boolean);
      const r = await versuch(() => send("POST", "/certificates", body), "Zertifikat angelegt");
      if (await bestaetigen("Gleich den ersten Auftrag stellen (Zertifikat ausstellen lassen)?", "Ausstellen", false))
        await versuch(() => send("POST", `/certificates/${r.certificate.id}/orders`), "Auftrag gestellt");
    }, "p"]]);
    ctx.rerender();
  }

  async function certDialog(id) {
    const [c, o] = await Promise.all([get(`/certificates/${id}`), get(`/certificates/${id}/orders?limit=25&sort=created_at.desc`)]);
    const x = c.certificate;
    const orders = o.orders || [];
    const env = (x.post_processing_environment || []).join(" ");
    const f = h("div", { class: "raster" },
      feld("Beschreibung", eingabe("description", x.description, { disabled: !write })),
      feld("Weitere Namen", eingabe("subject_alts", (x.subject_alts || []).join(" "), { disabled: !write }), "Änderung gilt ab dem nächsten Auftrag"),
      feld("Post-Processing-Befehl", eingabe("post_processing_command", x.post_processing_command, { disabled: !write })),
      feld("Post-Processing-Umgebung", eingabe("post_processing_environment", env, { disabled: !write })));
    const orderTab = tabelle([
      { t: "#", w: r => r.id, z: true }, { t: "Status", w: r => statusMark(r) },
      { t: "gültig ab", w: r => datum(ts(r.valid_from)) }, { t: "bis", w: r => datum(ts(r.valid_to)) },
      { t: "Fehler", w: r => r.error ? h("span", { class: "klein-t" }, r.error.detail || r.error.type) : "" },
      { t: "erstellt", w: r => datum(ts(r.created_at)) },
      { t: "", w: r => h("span", {},
        r.status === "valid" ? h("a", { href: ctx.link(`${V1}/certificates/${id}/orders/${r.id}/download`), class: "klein-t" }, "PEM") : null, " ",
        write && !["valid", "invalid"].includes(r.status) ? h("button", { class: "klein", onclick: async () => { await versuch(() => send("POST", `/certificates/${id}/orders/${r.id}`), "neuer Versuch"); } }, "Erneut versuchen") : null,
        admin && r.status === "valid" && !r.known_revoked && r.valid_to * 1000 > Date.now() ? h("button", { class: "klein gefahr", onclick: () => revoke(id, r) }, "Widerrufen") : null) }],
    orders, { leer: "Noch keine Aufträge" });
    await dialog(`Zertifikat ${x.name}`, h("div", {},
      h("p", {}, h("b", {}, names(x).join(", "))),
      h("p", { class: "klein-t leise" }, `Konto ${x.acme_account.name} · Schlüssel ${x.private_key.name} (${x.private_key.algorithm.name}) · angelegt ${datum(ts(x.created_at))}`),
      f, h("h3", {}, "Aufträge"), orderTab,
      h("p", { class: "klein-t leise" }, "Abholen ohne Anmeldung (z. B. HAProxy/nginx-Skripte): ", h("code", {}, `${ctx.conn.base_url}${V1}/download/certificates/${x.name}`), " mit Kopfzeile X-API-Key – den Schlüssel zeigt Cert Warden selbst.")),
    [...(admin ? [["Löschen", async () => {
      if (!(await bestaetigen(`Zertifikat „${x.name}“ in Cert Warden löschen? Aufträge und Dateien gehen verloren.`, "Löschen"))) return false;
      await versuch(() => ctx.api("DELETE", `${V1}/certificates/${id}`), "gelöscht");
    }, "gefahr"]] : []),
    ...(write ? [["Jetzt erneuern", async () => { await renew(x); }]] : []),
    ["Schließen", null],
    ...(write ? [["Speichern", async () => {
      const d = lesenForm(f);
      await versuch(() => send("PUT", `/certificates/${id}`, { description: d.description, subject_alts: d.subject_alts.split(/[\s,;]+/).filter(Boolean),
        post_processing_command: d.post_processing_command, post_processing_environment: d.post_processing_environment.split(/\s+/).filter(Boolean) }), "gespeichert");
    }, "p"]] : [])]);
    ctx.rerender();
  }

  async function revoke(id, r) {
    const grund = auswahl("reason_code", [["0", "ohne Angabe"], ["1", "Schlüssel kompromittiert"], ["4", "ersetzt (superseded)"], ["5", "nicht mehr benötigt (cessation)"]], "4");
    const ok = await dialog(`Auftrag ${r.id} widerrufen`, h("div", {}, h("p", {}, "Das Zertifikat wird bei der Zertifizierungsstelle widerrufen und ist danach ungültig."), feld("Grund", grund)),
      [["Abbrechen", null], ["Widerrufen", () => true, "p gefahr"]]);
    if (ok) await versuch(() => send("POST", `/certificates/${id}/orders/${r.id}/revoke`, { reason_code: Number(grund.value) }), "widerrufen");
  }

  // ---------------------------------------------------------------- ACME accounts
  async function accounts(main) {
    const [d, s] = await Promise.all([get("/acmeaccounts?limit=1000&sort=name.asc"), get("/acmeservers?limit=1000&sort=name.asc")]);
    leeren(main,
      h("div", { class: "karte" }, kopf("ACME-Konten", write ? h("button", { class: "p klein", onclick: newAccount }, "+ Konto") : null),
        tabelle([
          { t: "", w: a => punkt(a.status === "valid" ? "ok" : a.status === "unknown" ? "warn" : "bad") },
          { t: "Name", w: a => h("div", {}, h("b", {}, a.name), a.description ? h("div", { class: "leise klein-t" }, a.description) : null) },
          { t: "Server", w: a => h("span", {}, a.acme_server.name, a.acme_server.is_staging ? [" ", marke("Staging", "warn")] : null) },
          { t: "E-Mail", w: a => a.email || h("span", { class: "leise" }, "–") },
          { t: "Status", w: a => marke(a.status === "valid" ? "registriert" : a.status === "unknown" ? "nicht registriert" : a.status, a.status === "valid" ? "ok" : "warn") },
          { t: "Schlüssel", w: a => h("span", { class: "klein-t" }, a.private_key.name) },
          { t: "", w: a => h("span", {},
            write && a.status !== "valid" ? h("button", { class: "klein p", onclick: e => { e.stopPropagation(); register(a); } }, "Registrieren") : null,
            admin ? h("button", { class: "klein gefahr", onclick: async e => {
              e.stopPropagation();
              if (!(await bestaetigen(`ACME-Konto „${a.name}“ in Cert Warden löschen?`, "Löschen"))) return;
              await versuch(() => ctx.api("DELETE", `${V1}/acmeaccounts/${a.id}`), "gelöscht"); ctx.rerender();
            } }, "Löschen") : null) }],
        d.acme_accounts, { leer: "Noch keine Konten" })),
      h("div", { class: "karte" }, kopf("ACME-Server"),
        tabelle([{ t: "Name", w: x => x.name }, { t: "Adresse", w: x => h("span", { class: "klein-t" }, x.directory_url) },
          { t: "", w: x => x.is_staging ? marke("Staging", "warn") : marke("Produktiv", "ok") },
          { t: "EAB nötig", w: x => x.external_account_required ? "ja" : "nein" }], s.acme_servers)));
  }

  async function newAccount() {
    const o = (await get("/acmeaccounts/-1")).acme_account_options || {};
    if (!(o.private_keys || []).length) { await dialog("Kein freier Schlüssel", h("p", {}, "Ein ACME-Konto braucht einen eigenen, noch unbenutzten Schlüssel – zuerst unter „Schlüssel“ anlegen.")); return; }
    const tos = haken("Nutzungsbedingungen des ACME-Servers akzeptieren", false, null, "accepted_tos");
    const f = h("div", {}, h("div", { class: "raster" },
      feld("Name", eingabe("name", "", { placeholder: "z. B. letsencrypt-prod" })), feld("Beschreibung", eingabe("description")),
      feld("E-Mail", eingabe("email", "", { placeholder: "admin@firma.de" }), "für Ablauf-Hinweise der Zertifizierungsstelle"),
      feld("ACME-Server", auswahl("acme_server_id", (o.acme_servers || []).map(s => [s.id, s.name + (s.is_staging ? " (Staging)" : "")]))),
      feld("Schlüssel", auswahl("private_key_id", o.private_keys.map(k => [k.id, `${k.name} (${k.algorithm ? k.algorithm.name : ""})`])))), tos.el,
      haken("Gleich registrieren", true, null, "_register").el);
    await dialog("Neues ACME-Konto", f, [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesenForm(f);
      if (!d.accepted_tos) { hinweis("Ohne Zustimmung zu den Nutzungsbedingungen geht es nicht", true); return false; }
      const r = await versuch(() => send("POST", "/acmeaccounts", { name: d.name.trim(), description: d.description, email: d.email.trim(),
        acme_server_id: Number(d.acme_server_id), private_key_id: Number(d.private_key_id), accepted_tos: true }), "Konto angelegt");
      if (d._register) await versuch(() => send("POST", `/acmeaccounts/${r.acme_account.id}/register`, {}), "beim ACME-Server registriert");
    }, "p"]]);
    ctx.rerender();
  }

  async function register(a) {
    const kid = eingabe("eab_kid"), hmac = eingabe("eab_hmac_key");
    const ok = await dialog(`Konto „${a.name}“ registrieren`, h("div", {},
      h("p", { class: "klein-t" }, "Meldet das Konto beim ACME-Server an. Let's Encrypt braucht nichts weiter; manche Anbieter (ZeroSSL, Google) verlangen EAB-Daten."),
      h("div", { class: "raster" }, feld("EAB Key-ID", kid), feld("EAB HMAC-Schlüssel", hmac))),
    [["Abbrechen", null], ["Registrieren", () => true, "p"]]);
    if (!ok) return;
    await versuch(() => send("POST", `/acmeaccounts/${a.id}/register`, { eab_kid: kid.value.trim(), eab_hmac_key: hmac.value.trim() }), "registriert");
    ctx.rerender();
  }

  // ---------------------------------------------------------------- private keys
  async function keys(main) {
    const d = await get("/privatekeys?limit=1000&sort=name.asc");
    leeren(main, h("div", { class: "karte" }, kopf("Schlüssel", write ? h("button", { class: "p klein", onclick: newKey }, "+ Schlüssel") : null),
      h("p", { class: "leise klein-t" }, "Private Schlüssel für Zertifikate und ACME-Konten. Jeder Schlüssel gehört zu genau einem Zertifikat oder Konto."),
      tabelle([
        { t: "Name", w: k => h("div", {}, h("b", {}, k.name), k.description ? h("div", { class: "leise klein-t" }, k.description) : null) },
        { t: "Algorithmus", w: k => marke(k.algorithm.name) },
        { t: "Abruf per API-Key", w: k => k.api_key_disabled ? marke("aus") : marke("an", "warn") },
        { t: "zuletzt abgeholt", w: k => k.last_access ? datum(ts(k.last_access)) : h("span", { class: "leise" }, "nie") },
        { t: "", w: k => admin ? h("button", { class: "klein gefahr", onclick: async e => {
          e.stopPropagation();
          if (!(await bestaetigen(`Schlüssel „${k.name}“ löschen? Geht nur, wenn ihn nichts mehr benutzt.`, "Löschen"))) return;
          await versuch(() => ctx.api("DELETE", `${V1}/privatekeys/${k.id}`), "gelöscht"); ctx.rerender();
        } }, "Löschen") : "" }],
      d.private_keys, { leer: "Noch keine Schlüssel" })));
  }

  async function newKey() {
    const o = (await get("/privatekeys/-1")).private_key_options || {};
    const art = auswahl("_art", [["neu", "neu erzeugen"], ["pem", "vorhandenen importieren (PEM)"]], "neu");
    const algo = feld("Algorithmus", auswahl("algorithm_value", (o.key_algorithms || []).map(a => [a.value, a.name]), "ecdsap256"));
    const pem = feld("Schlüssel (PEM)", h("textarea", { name: "pem", rows: 6, spellcheck: "false" }));
    pem.style.display = "none";
    art.addEventListener("change", () => { algo.style.display = art.value === "neu" ? "" : "none"; pem.style.display = art.value === "pem" ? "" : "none"; });
    const f = h("div", {}, h("div", { class: "raster" }, feld("Name", eingabe("name")), feld("Beschreibung", eingabe("description")), feld("Art", art), algo), pem,
      haken("Abruf per API-Key sperren (empfohlen für Konto-Schlüssel)", true, null, "api_key_disabled").el);
    await dialog("Neuer Schlüssel", f, [["Abbrechen", null], ["Anlegen", async () => {
      const d = lesenForm(f);
      const body = { name: d.name.trim(), description: d.description, api_key_disabled: !!d.api_key_disabled };
      if (art.value === "pem") body.pem = d.pem; else body.algorithm_value = d.algorithm_value;
      await versuch(() => send("POST", "/privatekeys", body), "Schlüssel angelegt");
    }, "p"]]);
    ctx.rerender();
  }

  return { views: {
    overview: { titel: "Übersicht", zeigen: overview },
    certificates: { titel: "Zertifikate", zeigen: certificates },
    accounts: { titel: "ACME-Konten", zeigen: accounts },
    keys: { titel: "Schlüssel", zeigen: keys },
  } };
});
