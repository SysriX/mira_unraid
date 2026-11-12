/* MIRA – Identität: AD-Benutzer, Gruppen, Ablauf „Neuer Mitarbeiter“ */
"use strict";

const AD = { ous: null, ousFuer: null };
async function adOus(vid) {
  if (AD.ousFuer !== vid) { AD.ous = (await api("GET", `/api/m/ad/${vid}/ous`)).ous; AD.ousFuer = vid; }
  return AD.ous;
}
const cn = dn => (dn.match(/^CN=((?:\\,|[^,])+)/i) || [, dn])[1].replace(/\\,/g, ",");
const ouName = dn => dn.split(/,(?=(?:OU|CN|DC)=)/i).slice(1).filter(x => !/^DC=/i.test(x)).map(x => x.split("=")[1]).join(" › ");

// ------------------------------------------------------------------ Benutzer
ANSICHT.benutzer = { titel: "Benutzer", typ: "ad", zeigen: async (main, q) => {
  const vs = verbAuswahl("ad", zeige);
  const vid = vs.id;
  const suche = eingabe("suche", q.get("suche") || "", { placeholder: "Name, Anmeldename oder E-Mail …" });
  const liste = h("div", {}), detail = h("div", {});
  let gewaehlt = q.get("b");
  const laden = async () => {
    leeren(liste, h("div", { class: "leer" }, "Lädt …"));
    const d = await api("GET", `/api/m/ad/${vid}/benutzer?suche=${encodeURIComponent(suche.value)}`);
    leeren(liste, h("p", { class: "leise klein-t" }, `${d.benutzer.length} Benutzer`),
      tabelle([{ t: "", w: b => punkt(!b.aktiv ? "" : b.gesperrt ? "bad" : "ok") }, { t: "Name", w: b => b.anzeigename || b.sam },
        { t: "Anmeldename", w: b => b.sam }, { t: "E-Mail", w: b => b.mail }, { t: "OU", w: b => h("span", { class: "klein-t leise" }, ouName(b.dn)) }],
      d.benutzer, { klick: b => { gewaehlt = b.sam; zeigeDetail(); laden_markieren(); }, an: b => b.sam === gewaehlt, leer: "Niemand gefunden" }));
  };
  const laden_markieren = () => liste.querySelectorAll("tr.klick").forEach(tr => tr.classList.toggle("an", tr.children[2]?.textContent === gewaehlt));
  const zeigeDetail = async () => {
    if (!gewaehlt) return leeren(detail, h("div", { class: "karte leer" }, "Links einen Benutzer wählen"));
    leeren(detail, h("div", { class: "karte leer" }, "Lädt …"));
    const b = await api("GET", `/api/m/ad/${vid}/benutzer/${encodeURIComponent(gewaehlt)}`);
    leeren(detail, benutzerKarte(vid, b, async (neu) => { if (neu === null) { gewaehlt = null; } await laden(); zeigeDetail(); }));
  };
  let t; suche.addEventListener("input", () => { clearTimeout(t); t = setTimeout(laden, 300); });
  leeren(main, h("div", { class: "leiste" }, vs.el, suche, h("span", { class: "rechts" },
    darf("operator") ? h("button", { class: "p", onclick: () => { location.hash = "#neu"; } }, "+ Neuer Mitarbeiter") : null)),
  h("div", { class: "zwei" }, h("div", { class: "karte" }, liste), detail));
  await laden(); zeigeDetail();
} };

function benutzerKarte(vid, b, neuLaden) {
  const op = darf("operator");
  const felder = [["anzeigename", "Anzeigename"], ["vorname", "Vorname"], ["nachname", "Nachname"], ["mail", "E-Mail"], ["upn", "UPN (Anmeldung)"],
    ["telefon", "Telefon"], ["mobil", "Mobil"], ["titel", "Position"], ["abteilung", "Abteilung"], ["firma", "Firma"], ["buero", "Büro"], ["beschreibung", "Beschreibung"]];
  const form = h("div", { class: "raster" }, felder.map(([k, t]) => feld(t, eingabe(k, b[k], { disabled: !op }))));
  const nieAb = haken("Passwort läuft nie ab", b.pw_laeuft_nie_ab, null, "pw_laeuft_nie_ab");
  const gruppen = h("div", { class: "chips" }, b.gruppen.map(g => h("span", { class: "chip", title: g }, cn(g),
    op ? h("button", { title: "entfernen", onclick: async () => { await versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/gruppen`, { weg: [g] }), "entfernt"); neuLaden(); } }, "×") : null)));
  const aktion = (t, fn, art) => op ? h("button", { class: "klein " + (art || ""), onclick: fn }, t) : null;
  return h("div", {},
    h("div", { class: "karte" },
      h("h2", {}, punkt(!b.aktiv ? "" : b.gesperrt ? "bad" : "ok"), b.anzeigename || b.sam, h("span", { class: "rechts" },
        b.gesperrt ? marke("gesperrt", "bad") : null, b.aktiv ? marke("aktiv", "ok") : marke("deaktiviert"), b.postfach ? marke("Postfach", "akz") : null)),
      h("p", { class: "leise klein-t" }, b.dn),
      h("p", { class: "klein-t" }, `Anmeldename ${b.sam} · zuletzt angemeldet ${datum(b.letzte_anmeldung) || "nie"} · Passwort gesetzt ${datum(b.pw_gesetzt) || "–"} · angelegt ${datum(b.erstellt)}`),
      h("div", { class: "leiste" },
        aktion("Passwort setzen", () => passwortDialog(vid, b)),
        aktion(b.aktiv ? "Deaktivieren" : "Aktivieren", async () => { await versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/status`, { aktiv: !b.aktiv }), "gespeichert"); neuLaden(); }),
        b.gesperrt ? aktion("Entsperren", async () => { await versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/entsperren`), "entsperrt"); neuLaden(); }) : null,
        aktion("Verschieben", () => verschiebenDialog(vid, b, neuLaden)),
        op ? aktion("Mitarbeiter sperren …", () => sperrenDialog(vid, b, neuLaden)) : null,
        darf("admin") ? aktion("Löschen", async () => {
          if (!(await bestaetigen(`${b.anzeigename || b.sam} endgültig aus dem AD löschen? Das lässt sich nicht rückgängig machen (außer über den AD-Papierkorb).`, "Endgültig löschen"))) return;
          await versuch(() => api("DELETE", `/api/m/ad/${vid}/benutzer/${b.sam}`), "gelöscht"); neuLaden(null);
        }, "gefahr") : null)),
    h("div", { class: "karte" }, h("h2", {}, "Gruppen", h("span", { class: "rechts" }, op ? h("button", { class: "klein", onclick: () => gruppeWaehlen(vid, async g => {
      await versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/gruppen`, { hinzu: [g] }), "hinzugefügt"); neuLaden(); }) }, "+ Gruppe") : null)),
    b.gruppen.length ? gruppen : h("p", { class: "leise" }, "Nur Standardgruppe (Domänen-Benutzer)")),
    h("div", { class: "karte" }, h("h2", {}, "Eigenschaften"), form, nieAb.el,
      op ? h("div", { class: "leiste" }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: async () => {
        await versuch(() => api("PATCH", `/api/m/ad/${vid}/benutzer/${b.sam}`, { ...lesenForm(form), pw_laeuft_nie_ab: nieAb.i.checked }), "gespeichert"); neuLaden();
      } }, "Speichern"))) : null),
    b.adressen.length ? h("div", { class: "karte" }, h("h2", {}, "E-Mail-Adressen (proxyAddresses)"), h("div", { class: "chips" }, b.adressen.map(a => h("span", { class: "chip" }, a)))) : null);
}

async function passwortDialog(vid, b) {
  const f = h("div", {}, pwFeld("passwort", "Neues Passwort"), haken("Muss bei der nächsten Anmeldung geändert werden", true, null, "muss_aendern").el,
    haken("Konto dabei entsperren", true, null, "entsperren").el);
  await dialog(`Passwort für ${b.anzeigename || b.sam}`, f, [["Abbrechen", null], ["Setzen", () => versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/passwort`, lesenForm(f)), "Passwort gesetzt"), "p"]]);
}

async function verschiebenDialog(vid, b, neuLaden) {
  const ous = await adOus(vid);
  const s = auswahl("ou", ous.map(o => [o.dn, ouName("X=x," + o.dn) || o.name]), "");
  await dialog(`${b.anzeigename} verschieben`, feld("Ziel-OU", s), [["Abbrechen", null], ["Verschieben", () => versuch(() => api("POST", `/api/m/ad/${vid}/benutzer/${b.sam}/verschieben`, { ou: s.value }), "verschoben"), "p"]]);
  neuLaden();
}

async function gruppeWaehlen(vid, fertig) {
  const s = eingabe("s", "", { placeholder: "Gruppe suchen …" });
  const l = h("div", {});
  let wahl = null;
  const such = async () => { const d = await api("GET", `/api/m/ad/${vid}/gruppen?suche=${encodeURIComponent(s.value)}`);
    leeren(l, tabelle([{ t: "Gruppe", w: g => g.name }, { t: "Beschreibung", w: g => h("span", { class: "klein-t" }, g.beschreibung) }], d.gruppen.slice(0, 50),
      { klick: g => { wahl = g.dn; l.querySelectorAll("tr").forEach(tr => tr.classList.toggle("an", tr.children[0]?.textContent === g.name)); } })); };
  let t; s.addEventListener("input", () => { clearTimeout(t); t = setTimeout(such, 250); });
  such();
  const r = await dialog("Gruppe wählen", h("div", {}, s, l), [["Abbrechen", null], ["Hinzufügen", () => wahl || false, "p"]]);
  if (r && wahl) await fertig(wahl);
}

async function sperrenDialog(vid, b, neuLaden) {
  const aks = verbindungen("authentik"), exs = verbindungen("exchange");
  const f = h("div", {}, h("p", {}, `${b.anzeigename || b.sam} überall sperren:`), h("p", { class: "klein-t leise" }, "AD-Konto deaktivieren (wird nicht gelöscht)."),
    aks.length ? feld("Authentik-Konto sperren", auswahl("authentik", [["", "nein"], ...aks.map(v => [v.id, v.name])], aks[0].id)) : null,
    exs.length && b.postfach ? feld("Exchange: aus dem Adressbuch ausblenden", auswahl("exchange", [["", "nein"], ...exs.map(v => [v.id, v.name])], exs[0].id)) : null,
    haken("Zugang zu MIRA sperren (falls vorhanden)", true, null, "mira").el);
  const r = await dialog("Mitarbeiter sperren", f, [["Abbrechen", null], ["Sperren", async () => {
    const d = lesenForm(f);
    return api("POST", "/api/ablauf/benutzer-sperren", { ad: vid, sam: b.sam, authentik: d.authentik || null, exchange: d.exchange ? { vid: d.exchange } : null, mira: d.mira });
  }, "p gefahr"]]);
  if (r && r.schritte) await dialog(r.ok ? "Gesperrt" : "Nicht alles hat geklappt", ablaufLog(r));
  neuLaden();
}

const ablaufLog = r => h("div", { class: "log" }, r.schritte.map(s => h("div", { class: s.ok ? "ok" : "bad" }, h("div", {}, s.text, s.details ? h("small", {}, s.details) : null))));

// ------------------------------------------------------------------ Gruppen
ANSICHT.gruppen = { titel: "Gruppen", typ: "ad", zeigen: async (main) => {
  const vs = verbAuswahl("ad", zeige);
  const vid = vs.id;
  const suche = eingabe("suche", "", { placeholder: "Gruppe suchen …" });
  const liste = h("div", {}), detail = h("div", {});
  let gewaehlt = null;
  const op = darf("operator");
  const laden = async () => {
    const d = await api("GET", `/api/m/ad/${vid}/gruppen?suche=${encodeURIComponent(suche.value)}`);
    leeren(liste, tabelle([{ t: "Gruppe", w: g => g.name }, { t: "Art", w: g => marke((g.sicherheit ? "Sicherheit" : "Verteiler") + " · " + g.bereich) },
      { t: "Mitglieder", w: g => g.mitglieder, z: true }, { t: "Beschreibung", w: g => h("span", { class: "klein-t" }, g.beschreibung) }],
    d.gruppen, { klick: g => { gewaehlt = g.dn; zeigeDetail(); }, an: g => g.dn === gewaehlt }));
  };
  const zeigeDetail = async () => {
    if (!gewaehlt) return leeren(detail, h("div", { class: "karte leer" }, "Links eine Gruppe wählen"));
    const g = await api("GET", `/api/m/ad/${vid}/gruppen/${encodeURIComponent(gewaehlt)}`);
    const beschr = eingabe("beschreibung", g.beschreibung, { disabled: !op });
    const mitgl = async (art, x) => { await versuch(() => api("POST", `/api/m/ad/${vid}/gruppen/${encodeURIComponent(g.dn)}/mitglieder`, { [art]: [x] }), "gespeichert"); zeigeDetail(); laden(); };
    leeren(detail, h("div", { class: "karte" }, h("h2", {}, g.name, h("span", { class: "rechts" }, marke(g.sicherheit ? "Sicherheitsgruppe" : "Verteiler"), marke(g.bereich))),
      h("p", { class: "leise klein-t" }, g.dn),
      h("div", { class: "leiste" }, beschr, op ? h("button", { onclick: () => versuch(() => api("PATCH", `/api/m/ad/${vid}/gruppen/${encodeURIComponent(g.dn)}`, { beschreibung: beschr.value }), "gespeichert") }, "Speichern") : null,
        darf("admin") ? h("button", { class: "gefahr", onclick: async () => {
          if (!(await bestaetigen(`Gruppe ${g.name} endgültig löschen?`, "Löschen"))) return;
          await versuch(() => api("DELETE", `/api/m/ad/${vid}/gruppen/${encodeURIComponent(g.dn)}`), "gelöscht"); gewaehlt = null; laden(); zeigeDetail();
        } }, "Löschen") : null)),
    h("div", { class: "karte" }, h("h2", {}, `Mitglieder (${g.liste.length})`, h("span", { class: "rechts" }, op ? h("button", { class: "klein", onclick: async () => {
      const i = eingabe("x", "", { placeholder: "Anmeldename oder Gruppenname" });
      const r = await dialog("Mitglied hinzufügen", feld("Benutzer oder Gruppe", i), [["Abbrechen", null], ["Hinzufügen", () => i.value.trim() || false, "p"]]);
      if (r) mitgl("hinzu", r);
    } }, "+ Mitglied") : null)),
    tabelle([{ t: "", w: m => m.art === "gruppe" ? marke("Gruppe") : "" }, { t: "Name", w: m => m.anzeigename }, { t: "Anmeldename", w: m => m.name },
      { t: "", w: m => op ? h("button", { class: "klein", onclick: e => { e.stopPropagation(); mitgl("weg", m.dn); } }, "Entfernen") : "" }], g.liste, { leer: "Keine Mitglieder" })));
  };
  const neu = async () => {
    const ous = await adOus(vid);
    const f = h("div", { class: "raster" }, feld("Name", eingabe("name", "", { placeholder: "grp_Dienst_Recht" })), feld("Beschreibung", eingabe("beschreibung")),
      feld("Art", auswahl("art", [["sicherheit", "Sicherheitsgruppe"], ["verteiler", "Verteilergruppe"]], "sicherheit")),
      feld("Bereich", auswahl("bereich", [["global", "Global"], ["universal", "Universal"], ["domainlocal", "Lokal (Domäne)"]], "global")),
      feld("OU", auswahl("ou", [["", "Standard aus der Verbindung"], ...ous.map(o => [o.dn, ouName("X=x," + o.dn) || o.name])], "")));
    const r = await dialog("Neue Gruppe", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/ad/${vid}/gruppen`, lesenForm(f)), "angelegt"), "p"]]);
    if (r) { gewaehlt = r.dn; laden(); zeigeDetail(); }
  };
  let t; suche.addEventListener("input", () => { clearTimeout(t); t = setTimeout(laden, 300); });
  leeren(main, h("div", { class: "leiste" }, vs.el, suche, h("span", { class: "rechts" }, op ? h("button", { class: "p", onclick: neu }, "+ Neue Gruppe") : null,
    op ? h("button", { onclick: async () => {
      const ous = await adOus(vid);
      const f = h("div", {}, feld("Name", eingabe("name")), feld("Unter", auswahl("uebergeordnet", [["", "Domäne (oberste Ebene)"], ...ous.filter(o => /^OU=/i.test(o.dn)).map(o => [o.dn, ouName("X=x," + o.dn) || o.name])], "")),
        feld("Beschreibung", eingabe("beschreibung")));
      await dialog("Neue Organisationseinheit", f, [["Abbrechen", null], ["Anlegen", () => versuch(() => api("POST", `/api/m/ad/${vid}/ous`, lesenForm(f)), "OU angelegt"), "p"]]);
      AD.ousFuer = null;
    } }, "+ OU") : null)),
  h("div", { class: "zwei" }, h("div", { class: "karte" }, liste), detail));
  await laden(); zeigeDetail();
} };

// ------------------------------------------------------------------ Neuer Mitarbeiter (Ablauf)
ANSICHT.neu = { titel: "Neuer Mitarbeiter", typ: "ad", zeigen: async (main) => {
  if (!darf("operator")) return leeren(main, h("div", { class: "karte leer" }, "Dafür ist die Rolle Operator nötig."));
  const vs = verbAuswahl("ad", zeige);
  const vid = vs.id;
  const [ous, dom] = await Promise.all([adOus(vid), api("GET", `/api/m/ad/${vid}/domaene`).catch(() => ({}))]);
  const p = h("div", { class: "raster" }, feld("Vorname", eingabe("vorname")), feld("Nachname", eingabe("nachname")),
    feld("Anmeldename", eingabe("sam", "", { placeholder: "wird vorgeschlagen" }), "max. 20 Zeichen"),
    feld("Anzeigename", eingabe("anzeigename", "", { placeholder: "Vorname Nachname" })),
    feld("E-Mail", eingabe("mail", "", { placeholder: `name@${dom.upn_suffix || "firma.de"}` })),
    feld("Abteilung", eingabe("abteilung")), feld("Position", eingabe("titel")), feld("Telefon", eingabe("telefon")),
    feld("OU", auswahl("ou", [["", "Standard aus der Verbindung"], ...ous.map(o => [o.dn, ouName("X=x," + o.dn) || o.name])], "")));
  const [vor, nach, sam, anz, mail] = ["vorname", "nachname", "sam", "anzeigename", "mail"].map(n => p.querySelector(`[name=${n}]`));
  let samHand = false, mailHand = false;
  sam.addEventListener("input", () => (samHand = true)); mail.addEventListener("input", () => (mailHand = true));
  const umlaut = s => s.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss").normalize("NFD").replace(/[^a-z0-9]/g, "");
  const vorschlag = () => {
    anz.placeholder = `${vor.value} ${nach.value}`.trim() || "Vorname Nachname";
    if (!samHand) sam.value = (umlaut(vor.value).slice(0, 1) + umlaut(nach.value)).slice(0, 20);
    if (!mailHand && dom.upn_suffix && vor.value && nach.value) mail.value = `${umlaut(vor.value)}.${umlaut(nach.value)}@${dom.upn_suffix}`;
  };
  vor.addEventListener("input", vorschlag); nach.addEventListener("input", vorschlag);

  const pw = h("div", {}, pwFeld("passwort", "Start-Passwort", dom.pw_min_laenge ? `Richtlinie: mind. ${dom.pw_min_laenge} Zeichen${dom.pw_komplex ? ", komplex" : ""}` : ""),
    haken("Muss bei der ersten Anmeldung geändert werden", true, null, "muss_aendern").el, haken("Konto sofort aktiv", true, null, "aktiv").el);
  pw.querySelector("[name=passwort]").value = zufallsPasswort();

  const gruppenListe = [];
  const chips = h("div", { class: "chips" });
  const zeigeChips = () => leeren(chips, gruppenListe.map((g, i) => h("span", { class: "chip" }, cn(g), h("button", { onclick: () => { gruppenListe.splice(i, 1); zeigeChips(); } }, "×"))));
  const gruppenK = h("div", { class: "karte" }, h("h2", {}, "Gruppen", h("span", { class: "rechts" },
    h("button", { class: "klein", onclick: () => gruppeWaehlen(vid, g => { if (!gruppenListe.includes(g)) gruppenListe.push(g); zeigeChips(); }) }, "+ Gruppe"))), chips,
  h("p", { class: "leise klein-t" }, "Über die Gruppen bekommt die Person Zugriff auf Dienste (Authentik übernimmt sie aus dem AD)."));

  const exs = verbindungen("exchange"), grs = verbindungen("grommunio");
  const pfArt = auswahl("art", [["", "kein Postfach"], ...(exs.length ? [["exchange", "Exchange"]] : []), ...(grs.length ? [["grommunio", "grommunio"]] : [])], exs.length ? "exchange" : grs.length ? "grommunio" : "");
  const pfMehr = h("div", {});
  const pfZeichnen = async () => {
    leeren(pfMehr);
    if (pfArt.value === "exchange") {
      const v = auswahl("vid", exs.map(x => [x.id, x.name]), exs[0].id);
      const dbs = await api("GET", `/api/m/exchange/${exs[0].id}/datenbanken`).then(d => d.datenbanken).catch(() => []);
      pfMehr.append(h("div", { class: "raster" }, exs.length > 1 ? feld("Exchange", v) : h("input", { type: "hidden", name: "vid", value: exs[0].id }),
        feld("Datenbank", auswahl("datenbank", [["", "automatisch"], ...dbs.map(d => [d.Name, d.Name])], ""))));
    } else if (pfArt.value === "grommunio") {
      const doms = await api("GET", `/api/m/grommunio/${grs[0].id}/domaenen`).then(d => d.domaenen).catch(() => []);
      pfMehr.append(h("div", { class: "raster" }, h("input", { type: "hidden", name: "vid", value: grs[0].id }),
        feld("Domäne", auswahl("domaene", doms.map(d => [d.id, d.name]), doms[0]?.id)), feld("Speicherplatz (MB)", eingabe("quota_mb", "", { type: "number", placeholder: "Standard" }))),
      haken("Aus dem LDAP übernehmen (empfohlen, wenn grommunio am AD hängt)", true, null, "ldap").el);
    }
  };
  pfArt.addEventListener("change", pfZeichnen); pfZeichnen();
  const pfK = (exs.length || grs.length) ? h("div", { class: "karte" }, h("h2", {}, "Postfach"), feld("Postfach anlegen in", pfArt), pfMehr) : null;
  const miraK = darf("admin") ? h("div", { class: "karte" }, h("h2", {}, "Zugang zu MIRA"),
    feld("Rolle in MIRA", auswahl("rolle", [["", "kein Zugang"], ["leser", "Leser"], ["operator", "Operator"], ["admin", "Admin"]], ""))) : null;
  const ergebnis = h("div", {});

  leeren(main, h("div", { class: "leiste" }, vs.el, h("span", { class: "leise klein-t" }, "Legt die Person im AD an und – wenn gewählt – Postfach und Zugang zu MIRA in einem Rutsch.")),
    h("div", { class: "zwei" }, h("div", {}, h("div", { class: "karte" }, h("h2", {}, "Person"), p), h("div", { class: "karte" }, h("h2", {}, "Anmeldung"), pw)),
      h("div", {}, gruppenK, pfK, miraK, h("div", { class: "leiste" }, h("span", { class: "rechts" }, h("button", { class: "p", onclick: async e => {
        const b = { ...lesenForm(p), ...lesenForm(pw), gruppen: gruppenListe };
        if (!b.anzeigename) b.anzeigename = `${b.vorname} ${b.nachname}`.trim();
        if (!b.sam) return hinweis("Anmeldename fehlt", true);
        const pf = pfK && pfArt.value ? { art: pfArt.value, ...lesenForm(pfMehr) } : null;
        const rolle = miraK ? lesenForm(miraK).rolle : "";
        e.target.disabled = true;
        try {
          const r = await api("POST", "/api/ablauf/neuer-benutzer", { ad: vid, benutzer: b, postfach: pf, mira: rolle ? { rolle } : null });
          leeren(ergebnis, h("div", { class: "karte" }, h("h2", {}, punkt(r.ok ? "ok" : "bad"), r.ok ? "Angelegt" : "Abgebrochen"), ablaufLog(r),
            r.ok ? h("p", {}, "Start-Passwort: ", h("code", {}, b.passwort), " – jetzt weitergeben, es wird nicht gespeichert.") : null,
            r.ok ? h("button", { onclick: () => { location.hash = `#benutzer?b=${encodeURIComponent(b.sam)}&suche=${encodeURIComponent(b.sam)}`; } }, "Zum Benutzer") : null));
        } catch (er) { hinweis(er.message, true); }
        e.target.disabled = false;
      } }, "Anlegen"))), ergebnis)));
} };
