/* WireGuard management (shared by VERA and MIRA).
 * Needs the host helpers h, leeren, api, hinweis, versuch, dialog, bestaetigen, feld, haken, eingabe,
 * auswahl, lesenForm, tabelle, marke, punkt, datum, groesse, tabs – and window.WG_HOST:
 *   { base, shareBase, firewalls(): [{id, name}], can(level: "read"|"write"|"admin"): bool, noFirewall(el) }
 */
"use strict";

const WG = {
  url: (fw, p = "") => `${WG_HOST.base}/${fw}${p}`,
  fw: null, data: null, tab: "peers", filter: { q: "", inst: "" },
};

function wgSelectFirewall(onChange) {
  const fws = WG_HOST.firewalls();
  let id = Number(localStorage.getItem("wg.fw"));
  if (!fws.some(f => f.id === id)) id = fws.length ? fws[0].id : null;
  WG.fw = id;
  const el = fws.length > 1 ? h("select", { onchange: e => { localStorage.setItem("wg.fw", e.target.value); WG.fw = Number(e.target.value); onChange(); } },
    fws.map(f => h("option", { value: f.id, selected: f.id === id }, f.name))) : fws.length ? marke(fws[0].name, "akz") : null;
  return el;
}

async function wgView(main, q) {
  if (!WG_HOST.firewalls().length) { leeren(main); WG_HOST.noFirewall(main); return; }
  WG.tab = (q && q.get && q.get("t")) || WG.tab;
  const body = h("div", {});
  const draw = async (withStatus = true) => {
    const sel = wgSelectFirewall(() => draw());
    leeren(main,
      h("div", { class: "leiste" }, sel, h("span", { id: "wg-status", class: "leise klein-t" }),
        h("span", { class: "rechts" },
          h("button", { onclick: () => draw(true) }, "Status aktualisieren"),
          WG_HOST.can("write") ? h("button", { class: "p", title: "Instanzen und Peers der OPNsense in die Datenbank übernehmen", onclick: async () => {
            const r = await versuch(() => api("POST", WG.url(WG.fw, "/sync")));
            hinweis(`Import: ${r.instances} Instanz(en), ${r.peers} Peer(s) – ${r.new} neu, ${r.missing} nicht mehr vorhanden` + (r.key_changed ? `, ${r.key_changed} mit geändertem Schlüssel` : ""));
            draw();
          } }, "Aus OPNsense importieren") : null)),
      tabs([["peers", "Zugänge"], ["instances", "Instanzen"], ["sites", "Standortverbindungen"]], WG.tab, k => { WG.tab = k; draw(false); }),
      body);
    leeren(body, h("div", { class: "leer" }, "Lädt …"));
    try {
      if (WG.tab === "sites") return await wgSites(body, draw);
      WG.data = await api("GET", WG.url(WG.fw, `/overview?status=${withStatus ? 1 : 0}`));
      const st = document.getElementById("wg-status");
      if (st) st.textContent = WG.data.status_error ? "Status nicht abrufbar: " + WG.data.status_error : "";
      if (WG.tab === "instances") wgInstances(body, draw); else wgPeers(body, draw);
    } catch (e) { leeren(body, h("div", { class: "karte" }, h("h2", {}, punkt("bad"), "Fehler"), h("pre", {}, e.message))); }
  };
  await draw();
}

const wgInst = id => (WG.data.instances.find(i => i.id === id) || null);
const wgBytes = n => n ? groesse(n) : "–";
function wgDownload(url) { const a = h("a", { href: url, download: "" }); document.body.append(a); a.click(); a.remove(); }
async function wgCopy(text) {
  try { await navigator.clipboard.writeText(text); hinweis("In die Zwischenablage kopiert"); }
  catch { const t = h("textarea", {}, text); document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); hinweis("Kopiert"); }
}

// ------------------------------------------------------------------ peers
function wgPeers(el, redraw) {
  const d = WG.data, w = WG_HOST.can("write");
  if (!d.instances.length) {
    leeren(el, h("div", { class: "karte leer" }, h("p", {}, "Noch nichts aus dieser OPNsense übernommen."),
      w ? h("p", {}, "Oben auf „Aus OPNsense importieren“ klicken – vorhandene Instanzen und Zugänge werden übernommen.") : null));
    return;
  }
  const s = eingabe("q", WG.filter.q, { placeholder: "Suchen (Name, Besitzer, IP, Gerät) …" });
  const instSel = auswahl("inst", [["", "alle Instanzen"], ...d.instances.map(i => [i.id, `${i.name} (${i.interface || "?"})`])], WG.filter.inst);
  const list = h("div", {});
  const render = () => {
    WG.filter.q = s.value; WG.filter.inst = instSel.value;
    const q = s.value.toLowerCase();
    const rows = d.peers.filter(p => (!instSel.value || String(p.instance_id) === instSel.value) &&
      (!q || [p.name, p.owner, p.device, p.tunnel_address, p.description].join(" ").toLowerCase().includes(q)));
    leeren(list, tabelle([
      { t: "", w: p => punkt(!p.present ? "bad" : !p.enabled ? "" : p.online ? "ok" : "warn") },
      { t: "Name", w: p => h("div", {}, h("b", {}, p.name), p.owner || p.device ? h("div", { class: "leise klein-t" }, [p.owner, p.device].filter(Boolean).join(" · ")) : null) },
      { t: "Instanz", w: p => (wgInst(p.instance_id) || {}).name || "–" },
      { t: "Tunnel-IP", w: p => h("span", { class: "klein-t" }, p.tunnel_address) },
      { t: "Zuletzt online", w: p => p.online ? marke("online", "ok") : p.last_handshake ? datum(p.last_handshake) : "nie" },
      { t: "↓ / ↑", w: p => h("span", { class: "klein-t" }, `${wgBytes(p.tx_bytes)} / ${wgBytes(p.rx_bytes)}`) },
      { t: "", w: p => h("div", { class: "chips" },
        p.kind === "site" ? marke("Standort", "akz") : null,
        p.origin === "import" ? marke("importiert") : marke("neu angelegt", "akz"),
        !p.enabled ? marke("aus") : null, !p.present ? marke("fehlt in OPNsense", "bad") : null,
        p.kind === "client" && !p.has_private_key ? marke("Schlüssel fehlt", "warn") : null,
        p.config_changed ? marke("neue Konfiguration nötig", "warn") : null) },
    ], rows, { klick: p => wgPeerDialog(p, redraw), leer: "Keine Zugänge" }));
  };
  let t; s.addEventListener("input", () => { clearTimeout(t); t = setTimeout(render, 200); });
  instSel.addEventListener("change", render);
  leeren(el, h("div", { class: "karte" },
    h("div", { class: "leiste" }, s, instSel, h("span", { class: "rechts" },
      w ? h("button", { onclick: () => wgImportConfigDialog(redraw) }, "Vorhandene Konfiguration übernehmen") : null,
      w ? h("button", { class: "p", onclick: () => wgNewPeerDialog(redraw) }, "+ Neues Gerät") : null)),
    list,
    h("p", { class: "leise klein-t" }, `${d.peers.length} Zugänge · ${d.peers.filter(p => p.online).length} online · Status alle ${3} Minuten als „online“ gewertet`)));
  render();
}

function wgClientInstances() { return WG.data.instances.filter(i => i.present && i.role !== "site"); }

async function wgNewPeerDialog(redraw) {
  const insts = wgClientInstances();
  if (!insts.length) return hinweis("Keine Instanz für Geräte vorhanden – zuerst importieren", true);
  const ip = eingabe("tunnel_address", "", { placeholder: "wird automatisch vergeben" });
  const allowed = eingabe("client_allowed_ips", "");
  const dns = eingabe("client_dns", "");
  const instSel = auswahl("instance_id", insts.map(i => [i.id, `${i.name} (${i.interface}, ${i.tunnel_address})`]), insts[0].id);
  const fill = async () => {
    const i = wgInst(Number(instSel.value));
    allowed.placeholder = i.client_allowed_ips || WG.data.defaults.client_allowed_ips || "Tunnelnetz der Instanz";
    dns.placeholder = i.client_dns || WG.data.defaults.client_dns || "kein DNS";
    try { ip.value = (await api("GET", WG.url(WG.fw, `/instances/${i.id}/next-ip`))).tunnel_address; } catch (e) { ip.value = ""; hinweis(e.message, true); }
  };
  instSel.addEventListener("change", fill);
  const psk = haken("Pre-Shared Key verwenden (empfohlen)", true, null, "use_psk");
  const en = haken("Sofort aktiv (auf der OPNsense scharf schalten)", true, null, "enabled");
  const f = h("div", {},
    h("div", { class: "raster" }, feld("Instanz *", instSel), feld("Name *", eingabe("name", "", { placeholder: "z. B. Laptop-Max" }), "erscheint so auch in der OPNsense (Sonderzeichen werden zu _)"),
      feld("Besitzer", eingabe("owner")), feld("Gerät", eingabe("device", "", { placeholder: "z. B. iPhone, Windows-PC" })),
      feld("Tunnel-IP", ip, "frei = nächste freie Adresse"), feld("Erlaubte Netze (AllowedIPs)", allowed, "leer = Standard der Instanz; 0.0.0.0/0 = alles durch den Tunnel"),
      feld("DNS", dns), feld("Keepalive (s)", eingabe("keepalive", "25", { type: "number" }))),
    feld("Notiz", h("textarea", { name: "description", rows: 2 })), psk.el, en.el);
  await fill();
  const res = await dialog("Neues Gerät für WireGuard", f, [["Abbrechen", null], ["Anlegen und aktivieren", async () => {
    const d = lesenForm(f); d.instance_id = Number(d.instance_id);
    return await versuch(() => api("POST", WG.url(WG.fw, "/peers"), d), "Angelegt – auf der OPNsense aktiv");
  }, "p"]]);
  if (res && res.id) { await redraw(false); wgConfigDialog(res, true); }
}

async function wgImportConfigDialog(redraw) {
  const insts = wgClientInstances();
  if (!insts.length) return hinweis("Keine Instanz vorhanden – zuerst importieren", true);
  const ta = h("textarea", { name: "config", rows: 10, placeholder: "[Interface]\nPrivateKey = …\nAddress = …\n\n[Peer]\nPublicKey = …" });
  const datei = h("input", { type: "file", accept: ".conf,.txt", onchange: async e => { const file = e.target.files[0]; if (file) ta.value = await file.text(); } });
  const info = h("div", { class: "klein-t leise" });
  const f = h("div", {},
    h("p", { class: "leise klein-t" }, "Eine fertige WireGuard-Konfiguration (.conf) eines Geräts einlesen. Gibt es den Zugang auf der OPNsense schon (gleicher Schlüssel), wird nur der private Schlüssel nachgetragen – sonst wird der Zugang neu angelegt und sofort aktiviert."),
    h("div", { class: "raster" }, feld("Instanz *", auswahl("instance_id", insts.map(i => [i.id, `${i.name} (${i.interface})`]), insts[0].id)),
      feld("Name (bei neuem Zugang)", eingabe("name")), feld("Besitzer", eingabe("owner")), feld("Gerät", eingabe("device"))),
    feld("Datei", datei), feld("oder Text einfügen", ta), info);
  const res = await dialog("Vorhandene Konfiguration übernehmen", f, [["Abbrechen", null],
    ["Prüfen", async () => {
      const r = await versuch(() => api("POST", WG.url(WG.fw, "/parse-config"), { config: ta.value }));
      const known = WG.data.peers.find(p => p.public_key === r.public_key);
      leeren(info, `Öffentlicher Schlüssel: ${r.public_key} · Adresse: ${r.tunnel_address || "–"} · AllowedIPs: ${r.client_allowed_ips || "–"} · `,
        known ? marke(`gehört zu „${known.name}“ – Schlüssel wird nachgetragen`, "ok") : marke("neu – wird auf der OPNsense angelegt", "akz"));
      return false;
    }],
    ["Übernehmen", async () => {
      const d = lesenForm(f); d.instance_id = Number(d.instance_id);
      return await versuch(() => api("POST", WG.url(WG.fw, "/peers/import-config"), d));
    }, "p"]]);
  if (res && res.id) { hinweis(res.merged ? "Schlüssel nachgetragen" : "Übernommen und aktiviert"); await redraw(false); }
}

async function wgPeerDialog(p, redraw) {
  const w = WG_HOST.can("write");
  const insts = WG.data.instances.filter(i => i.present);
  const client = p.kind === "client";
  const knoepfe = h("div", { class: "leiste" },
    client ? h("button", { onclick: () => wgConfigDialog(p, false) }, "Ansehen (ohne Schlüssel)") : null,
    client && w ? h("button", { class: "p", disabled: !p.has_private_key, onclick: () => wgConfigDialog(p, true) }, "Konfiguration + QR-Code") : null,
    client && w ? h("button", { disabled: !p.has_private_key, onclick: () => wgDownload(WG.url(WG.fw, `/peers/${p.id}/download?format=conf`)) }, "Herunterladen (.conf)") : null,
    client && w ? h("button", { disabled: !p.has_private_key, onclick: () => wgDownload(WG.url(WG.fw, `/peers/${p.id}/download?format=qr`)) }, "QR als Bild") : null,
    client && w ? h("button", { disabled: !p.has_private_key, onclick: () => wgShareDialog(p) }, "Freigabelink") : null);
  const f = h("div", { class: "raster" },
    feld("Name", eingabe("name", p.name)), feld("Besitzer", eingabe("owner", p.owner)), feld("Gerät", eingabe("device", p.device)),
    feld("Instanz", auswahl("instance_id", insts.map(i => [i.id, `${i.name} (${i.interface})`]), p.instance_id)),
    feld("Tunnel-IP / Allowed IPs (OPNsense)", eingabe("tunnel_address", p.tunnel_address)),
    client ? feld("AllowedIPs im Gerät", eingabe("client_allowed_ips", p.client_allowed_ips, { placeholder: "Standard der Instanz" })) : null,
    client ? feld("DNS im Gerät", eingabe("client_dns", p.client_dns)) : null,
    !client ? feld("Endpoint (Gegenseite)", eingabe("endpoint_host", p.endpoint_host)) : null,
    !client ? feld("Port Gegenseite", eingabe("endpoint_port", p.endpoint_port || "")) : null,
    feld("Keepalive (s)", eingabe("keepalive", p.keepalive ?? "")));
  const en = haken("Aktiv", p.enabled, null, "enabled");
  const notiz = feld("Notiz", h("textarea", { name: "description", rows: 2 }, p.description || ""));
  const schluessel = client && !p.has_private_key ? h("div", { class: "karte" },
    h("p", { class: "klein-t" }, "Für diesen importierten Zugang ist kein privater Schlüssel gespeichert (OPNsense kennt nur den öffentlichen). Zum Anzeigen/Herunterladen die vorhandene .conf des Geräts einlesen, den privaten Schlüssel eintragen – oder neue Schlüssel erzeugen (dann braucht das Gerät die neue Konfiguration)."),
    feld("Privater Schlüssel des Geräts", eingabe("private_key", "", { autocomplete: "off", placeholder: "Base64, 44 Zeichen" }))) : null;
  const info = h("p", { class: "leise klein-t" }, `Öffentlicher Schlüssel: ${p.public_key}`, h("br"),
    `Herkunft: ${p.origin === "import" ? "aus OPNsense importiert" : "hier angelegt"} · zuletzt geändert ${datum(p.updated_at)}` + (p.remote_endpoint ? ` · zuletzt von ${p.remote_endpoint}` : ""));
  const form = h("div", {}, knoepfe, info, w ? [f, notiz, en.el, schluessel] : null);
  if (!w) form.append(h("p", { class: "leise" }, "Nur Ansicht – zum Ändern fehlen die Rechte."));
  const kn = [["Schließen", null]];
  if (w) {
    kn.unshift(["Löschen", async () => {
      if (!(await bestaetigen(`Zugang „${p.name}“ auf der OPNsense löschen? Das Gerät kann sich danach nicht mehr verbinden.`, "Löschen"))) return false;
      await versuch(() => api("DELETE", WG.url(WG.fw, `/peers/${p.id}`)), "Gelöscht"); redraw(false);
    }, "gefahr"]);
    if (client) kn.unshift(["Neue Schlüssel", async () => {
      if (!(await bestaetigen("Neue Schlüssel erzeugen? Die alte Konfiguration auf dem Gerät funktioniert danach nicht mehr.", "Erneuern"))) return false;
      const r = await versuch(() => api("POST", WG.url(WG.fw, `/peers/${p.id}/rotate`)), "Neue Schlüssel aktiv");
      await redraw(false); wgConfigDialog(r, true);
    }]);
    kn.push(["Speichern", async () => {
      const d = { ...lesenForm(f), ...lesenForm(notiz), enabled: en.i.checked };
      if (schluessel) Object.assign(d, lesenForm(schluessel));
      d.instance_id = Number(d.instance_id);
      if (!d.private_key) delete d.private_key;
      await versuch(() => api("PUT", WG.url(WG.fw, `/peers/${p.id}`), d), "Gespeichert und übernommen"); redraw(false);
    }, "p"]);
  }
  await dialog(`Zugang ${p.name}`, form, kn);
}

async function wgConfigDialog(p, reveal) {
  const r = await versuch(() => api("GET", WG.url(WG.fw, `/peers/${p.id}/config?reveal=${reveal ? 1 : 0}`)));
  const box = h("div", { class: "wg-config" },
    r.qr ? h("img", { src: r.qr, alt: "QR-Code", class: "wg-qr" }) : null,
    h("pre", {}, r.text));
  const kn = [["Schließen", null]];
  if (reveal) {
    kn.unshift(["Kopieren", () => { wgCopy(r.text); return false; }],
      ["Herunterladen", () => { wgDownload(WG.url(WG.fw, `/peers/${p.id}/download?format=conf`)); return false; }, "p"]);
  }
  await dialog(reveal ? `Konfiguration ${r.peer.name}` : `Konfiguration ${r.peer.name} (Schlüssel verborgen)`,
    h("div", {}, reveal ? h("p", { class: "leise klein-t" }, "Mit der WireGuard-App den QR-Code scannen oder die Datei importieren. Enthält den privaten Schlüssel – vertraulich behandeln.") : null, box), kn);
}

async function wgShareDialog(p) {
  const liste = h("div", {});
  const laden = async () => {
    const r = await api("GET", WG.url(WG.fw, `/peers/${p.id}/shares`));
    leeren(liste, tabelle([{ t: "", w: s => punkt(s.valid ? "ok" : "") }, { t: "Erstellt", w: s => `${datum(s.created_at)} · ${s.created_by}` },
      { t: "Gültig bis", w: s => datum(s.expires_at) }, { t: "Aufrufe", w: s => `${s.uses} / ${s.max_uses || "∞"}` },
      { t: "", w: s => h("button", { class: "klein gefahr", onclick: async () => { await api("DELETE", WG.url(WG.fw, `/peers/${p.id}/shares/${s.id}`)); laden(); } }, "Widerrufen") }],
      r.shares, { leer: "Noch keine Links" }));
  };
  const f = h("div", { class: "raster" },
    feld("Gültig für", auswahl("hours", [[1, "1 Stunde"], [4, "4 Stunden"], [24, "1 Tag"], [72, "3 Tage"], [168, "7 Tage"]], 24)),
    feld("Darf geöffnet werden", auswahl("max_uses", [[1, "1×"], [3, "3×"], [0, "beliebig oft"]], 1)));
  const ergebnis = h("div", {});
  await laden();
  await dialog(`Freigabelink für ${p.name}`, h("div", {},
    h("p", { class: "leise klein-t" }, "Ein Link ohne Anmeldung, über den das Gerät seine Konfiguration (QR-Code + Datei) abholen kann – z. B. am Handy öffnen. Läuft automatisch ab."),
    f, ergebnis, h("h4", {}, "Bisherige Links"), liste),
    [["Schließen", null], ["Link erzeugen", async () => {
      const d = lesenForm(f);
      const r = await versuch(() => api("POST", WG.url(WG.fw, `/peers/${p.id}/shares`), { hours: Number(d.hours), max_uses: Number(d.max_uses) }));
      const url = `${location.origin}${WG_HOST.shareBase}/${r.token}`;
      leeren(ergebnis, h("div", { class: "karte" }, h("p", { class: "klein-t" }, `Gültig bis ${datum(r.expires_at)} – wird nur jetzt angezeigt:`),
        h("div", { class: "leiste" }, eingabe("url", url, { readonly: true, style: { flex: 1 } }), h("button", { onclick: () => wgCopy(url) }, "Kopieren"))));
      laden(); return false;
    }, "p"]]);
}

// ------------------------------------------------------------------ instances
function wgInstances(el, redraw) {
  const d = WG.data, w = WG_HOST.can("write");
  leeren(el, h("div", { class: "karte" },
    h("p", { class: "leise klein-t" }, "Instanzen kommen aus der OPNsense (VPN › WireGuard › Instanzen). Hier nur die Vorgaben für neue Geräte-Konfigurationen: öffentliche Adresse, DNS und erlaubte Netze."),
    tabelle([{ t: "", w: i => punkt(!i.present ? "bad" : i.enabled ? "ok" : "") }, { t: "Name", w: i => i.name }, { t: "Schnittstelle", w: i => i.interface },
      { t: "Port", w: i => i.listen_port, z: true }, { t: "Tunnelnetz", w: i => i.tunnel_address },
      { t: "Zugänge", w: i => d.peers.filter(p => p.instance_id === i.id).length, z: true },
      { t: "Art", w: i => i.role === "site" ? marke("Standort", "akz") : marke("Geräte") },
      { t: "Endpoint", w: i => i.endpoint || (d.defaults.endpoint_host ? `${d.defaults.endpoint_host}:${i.listen_port}` : marke("fehlt", "warn")) },
      { t: "", w: i => !i.present ? marke("fehlt in OPNsense", "bad") : "" }],
      d.instances, { klick: w ? i => wgInstanceDialog(i, redraw) : null, leer: "Noch nichts importiert" })));
}

async function wgInstanceDialog(i, redraw) {
  const d = WG.data.defaults;
  const f = h("div", { class: "raster" },
    feld("Öffentliche Adresse (Endpoint)", eingabe("endpoint", i.endpoint, { placeholder: d.endpoint_host ? `${d.endpoint_host}:${i.listen_port}` : "vpn.<Domain>:" + i.listen_port }), "Name oder IP, unter der Geräte die OPNsense erreichen; ohne Port = Port der Instanz"),
    feld("DNS für Geräte", eingabe("client_dns", i.client_dns, { placeholder: d.client_dns || "" })),
    feld("Erlaubte Netze für Geräte", eingabe("client_allowed_ips", i.client_allowed_ips, { placeholder: d.client_allowed_ips || "Tunnelnetz" }), "z. B. 192.168.0.0/24, 192.168.10.0/24 oder 0.0.0.0/0"),
    feld("Art", auswahl("role", [["clients", "Geräte (Road-Warrior)"], ["site", "Standortverbindung"]], i.role)));
  await dialog(`Instanz ${i.name}`, h("div", {}, h("p", { class: "leise klein-t" }, `${i.interface} · Port ${i.listen_port} · ${i.tunnel_address} · Schlüssel ${i.public_key}`), f),
    [["Abbrechen", null], ["Speichern", () => versuch(() => api("PUT", WG.url(WG.fw, `/instances/${i.id}`), lesenForm(f)), "Gespeichert"), "p"]]);
  redraw(false);
}

// ------------------------------------------------------------------ site links
async function wgSites(el, redraw) {
  const r = await api("GET", `${WG_HOST.base}/site-links`);
  const fwName = id => (WG_HOST.firewalls().find(f => f.id === id) || { name: `#${id}` }).name;
  const a = WG_HOST.can("admin");
  leeren(el, h("div", { class: "karte" },
    h("h2", {}, "Standortverbindungen", h("span", { class: "rechts" },
      a ? h("button", { class: "p klein", onclick: () => wgSiteWizard(redraw) }, "+ Standortverbindung") : null)),
    h("p", { class: "leise klein-t" }, "Verbindet zwei OPNsense dauerhaft über eine eigene WireGuard-Instanz. Bestehende Zugänge für Geräte laufen unverändert weiter."),
    tabelle([{ t: "Name", w: l => l.name }, { t: "Standort A", w: l => h("div", {}, fwName(l.firewall_a_id), h("div", { class: "leise klein-t" }, l.networks_a)) },
      { t: "Standort B", w: l => h("div", {}, fwName(l.firewall_b_id), h("div", { class: "leise klein-t" }, l.networks_b)) },
      { t: "Tunnel", w: l => `${l.tunnel_net} · UDP ${l.port_a}/${l.port_b}` },
      { t: "Erstellt", w: l => datum(l.created_at) },
      { t: "", w: l => h("span", {}, h("button", { class: "klein", onclick: e => { e.stopPropagation(); wgSiteHints(l); } }, "Hinweise"),
        a ? h("button", { class: "klein gefahr", onclick: async e => {
          e.stopPropagation();
          if (!(await bestaetigen(`Standortverbindung „${l.name}“ auf beiden OPNsense entfernen (Instanzen, Peers, angelegte Regeln)?`, "Entfernen"))) return;
          const x = await versuch(() => api("DELETE", `${WG_HOST.base}/site-links/${l.id}`), "Entfernt");
          if (x.warnings && x.warnings.length) hinweis(x.warnings.join("\n"), true);
          redraw(false);
        } }, "Entfernen") : null) }],
      r.links, { leer: "Noch keine Standortverbindung" })));
}

function wgSiteHints(l) {
  return dialog(`Hinweise – ${l.name}`, h("ul", {}, (l.hints || []).map(x => h("li", {}, x))));
}

async function wgSiteWizard(redraw) {
  const fws = WG_HOST.firewalls();
  if (fws.length < 2) return hinweis("Dafür müssen zwei OPNsense eingerichtet sein", true);
  const opts = fws.map(f => [f.id, f.name]);
  const f = h("div", {},
    h("div", { class: "raster" },
      feld("Name", eingabe("name", "", { placeholder: "z. B. Haus-Buero" })),
      feld("Tunnelnetz", eingabe("tunnel_net", "10.255.255.0/30"), "eigenes kleines Netz nur für den Tunnel (A = .1, B = .2)")),
    h("div", { class: "zwei" },
      h("div", { class: "karte" }, h("h2", {}, "Standort A"),
        feld("OPNsense", auswahl("firewall_a_id", opts, fws[0].id)),
        feld("Öffentliche Adresse", eingabe("endpoint_a", "", { placeholder: "Standard aus der Firewall-Einstellung" })),
        feld("Port", eingabe("port_a", "51821", { type: "number" }), "neuer Port – nicht der der bestehenden Instanz"),
        feld("Lokale Netze", h("textarea", { name: "networks_a", rows: 3, placeholder: "192.168.1.0/24\n192.168.99.0/24" })),
        feld("WAN-Schnittstelle", eingabe("wan_interface_a", "wan"))),
      h("div", { class: "karte" }, h("h2", {}, "Standort B"),
        feld("OPNsense", auswahl("firewall_b_id", opts, fws[1].id)),
        feld("Öffentliche Adresse", eingabe("endpoint_b", "", { placeholder: "Standard aus der Firewall-Einstellung" })),
        feld("Port", eingabe("port_b", "51821", { type: "number" })),
        feld("Lokale Netze", h("textarea", { name: "networks_b", rows: 3, placeholder: "192.168.110.0/24\n192.168.120.0/24" })),
        feld("WAN-Schnittstelle", eingabe("wan_interface_b", "wan")))),
    h("div", { class: "raster" }, feld("Keepalive (s)", eingabe("keepalive", "25", { type: "number" })), feld("MTU", eingabe("mtu", "1420", { type: "number" }))),
    haken("Road-Warrior-Netze beider Seiten mit routen", true, "Geräte, die per WireGuard an A hängen, erreichen auch B (und umgekehrt).", "include_rw").el,
    haken("Erlaubte Netze der Geräte-Konfigurationen um die Gegenseite erweitern", true, "Betroffene Zugänge werden als „neue Konfiguration nötig“ markiert (nicht bei 0.0.0.0/0).", "extend_rw").el,
    haken("Firewall-Regeln + Aliase automatisch anlegen", true, "Über OPNsense-Automation, mit Sicherungspunkt (Rücksprung nach 60 s, falls die API danach nicht mehr erreichbar ist).", "firewall_rules").el,
    haken("Pre-Shared Key verwenden", true, null, "use_psk").el);
  const res = await dialog("Neue Standortverbindung", f, [["Abbrechen", null], ["Verbinden", async () => {
    const d = lesenForm(f);
    const data = { ...d, firewall_a_id: Number(d.firewall_a_id), firewall_b_id: Number(d.firewall_b_id) };
    return await versuch(() => api("POST", `${WG_HOST.base}/site-links`, data), "Standortverbindung steht");
  }, "p"]]);
  if (res && res.id) { await redraw(false); wgSiteHints(res); }
}
