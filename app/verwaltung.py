"""Verwaltung von MIRA selbst: Verbindungen, Zugang (wer darf MIRA benutzen), Anmelde-Einstellungen,
Protokoll, Übersicht und dienstübergreifende Abläufe (neuer Benutzer, Benutzer sperren)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select

import kern
from anmeldung import ROLLEN, anmeldung_konfig, pw_hash, rolle
from kern import Benutzer, Protokoll, Verbindung, jetzt, verschluesseln
from module import REGISTER, Fehler, prot, wahr, werte

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ Verbindungen
def _verb_json(v: Verbindung) -> dict:
    m = REGISTER.get(v.typ)
    geheim = kern.entschluesseln(v.geheim) if v.geheim else {}
    return {"id": v.id, "typ": v.typ, "titel": m.titel if m else v.typ, "name": v.name, "aktiv": v.aktiv,
            "einstellungen": v.einstellungen or {}, "geheim_gesetzt": sorted(k for k, x in geheim.items() if x),
            "status": v.status, "geprueft": v.geprueft.isoformat() if v.geprueft else None}


@router.get("/module")
def module(u=Depends(rolle("leser"))):
    vs = kern.verbindungen()
    return {"module": [{**m.beschreibung_json(), "verbindungen": [{"id": v.id, "name": v.name, "aktiv": v.aktiv, "status": v.status}
                                                                for v in vs if v.typ == m.typ]}
                       for m in REGISTER.values()]}


@router.get("/verbindungen")
def verbindungen(u=Depends(rolle("leser"))):
    return {"verbindungen": [_verb_json(v) for v in kern.verbindungen()]}


def _aufteilen(typ, wert_dict, alt_geheim=None):
    m = REGISTER.get(typ)
    if not m:
        raise HTTPException(400, "unbekannter Typ")
    gf = m.geheime_felder()
    geheim = dict(alt_geheim or {})
    for k in gf:
        if wert_dict.get(k):           # leer = altes Geheimnis behalten
            geheim[k] = wert_dict[k]
    einst = {k: v for k, v in wert_dict.items() if k not in gf}
    return einst, geheim


def _testen(typ, einst, geheim, vid=0, name=""):
    try:
        return True, REGISTER[typ].testen({**einst, **geheim, "_id": vid, "_name": name})
    except (Fehler, PermissionError) as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


@router.post("/verbindungen/testen")
async def verbindung_formular_testen(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    alt = {}
    if d.get("id"):
        alt = kern.entschluesseln(kern.verbindung_laden(int(d["id"])).geheim)
    einst, geheim = _aufteilen(d["typ"], d.get("werte") or {}, alt)
    ok, text = _testen(d["typ"], einst, geheim, int(d.get("id") or 0), d.get("name", ""))
    return {"ok": ok, "meldung": text}


@router.post("/verbindungen")
async def verbindung_neu(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    einst, geheim = _aufteilen(d["typ"], d.get("werte") or {})
    with kern.sitzung() as s:
        v = Verbindung(typ=d["typ"], name=d.get("name") or REGISTER[d["typ"]].titel, einstellungen=einst,
                       geheim=verschluesseln(geheim), aktiv=wahr(d.get("aktiv", True)))
        s.add(v)
        s.flush()
        vid = v.id
    prot(u, "verbindungen", "Verbindung angelegt", f"{d['typ']}: {d.get('name')}")
    return {"id": vid}


@router.put("/verbindungen/{vid}")
async def verbindung_aendern(vid: int, req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    with kern.sitzung() as s:
        v = s.get(Verbindung, vid)
        if not v:
            raise HTTPException(404, "gibt es nicht")
        einst, geheim = _aufteilen(v.typ, d.get("werte") or {}, kern.entschluesseln(v.geheim))
        for k in d.get("geheim_loeschen") or []:
            geheim.pop(k, None)
        v.einstellungen, v.geheim = einst, verschluesseln(geheim)
        v.name = d.get("name") or v.name
        v.aktiv = wahr(d.get("aktiv", v.aktiv))
        v.geaendert = jetzt()
    prot(u, "verbindungen", "Verbindung geändert", f"{vid}: {d.get('name')}")
    return {"ok": True}


@router.delete("/verbindungen/{vid}")
def verbindung_loeschen(vid: int, u=Depends(rolle("admin"))):
    cfg = anmeldung_konfig()
    if vid in ((cfg.get("verzeichnis") or {}).get("verbindung_id"), (cfg.get("oidc") or {}).get("authentik_id")):
        raise HTTPException(400, "Diese Verbindung wird für die Anmeldung gebraucht – erst unter Einstellungen umstellen")
    with kern.sitzung() as s:
        v = s.get(Verbindung, vid)
        if v:
            s.delete(v)
    prot(u, "verbindungen", "Verbindung gelöscht", vid)
    return {"ok": True}


def _pruefen_und_merken(v: Verbindung) -> dict:
    w = werte(v)
    ok, text = _testen(v.typ, {k: x for k, x in w.items() if not k.startswith("_")}, {}, v.id, v.name)
    with kern.sitzung() as s:
        vv = s.get(Verbindung, v.id)
        vv.status, vv.geprueft = ("ok: " if ok else "fehler: ") + text[:480], jetzt()
    return {"id": v.id, "ok": ok, "meldung": text}


@router.post("/verbindungen/{vid}/testen")
def verbindung_testen(vid: int, u=Depends(rolle("leser"))):
    return _pruefen_und_merken(kern.verbindung_laden(vid))


@router.post("/uebersicht/pruefen")
def alle_pruefen(u=Depends(rolle("leser"))):
    vs = kern.verbindungen(nur_aktiv=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {"ergebnisse": list(ex.map(_pruefen_und_merken, vs))}


@router.get("/uebersicht")
def uebersicht(u=Depends(rolle("leser"))):
    with kern.sitzung() as s:
        n_zugang = s.scalar(select(func.count()).select_from(Benutzer).where(Benutzer.aktiv.is_(True)))
        letzte = list(s.scalars(select(Protokoll).order_by(Protokoll.id.desc()).limit(12)))
        fehl = s.scalar(select(func.count()).select_from(Protokoll).where(
            Protokoll.bereich == "anmeldung", Protokoll.ok.is_(False),
            Protokoll.zeit > jetzt().replace(hour=0, minute=0, second=0, microsecond=0)))
    return {"verbindungen": [_verb_json(v) for v in kern.verbindungen()], "zugang": n_zugang,
            "fehlanmeldungen_heute": fehl, "version": kern.VERSION,
            "letzte": [_prot_json(p) for p in letzte]}


# ------------------------------------------------------------------ Zugang zu MIRA
def _b_json(b: Benutzer) -> dict:
    return {"id": b.id, "name": b.name, "anzeigename": b.anzeigename, "email": b.email, "quelle": b.quelle,
            "rolle": b.rolle, "aktiv": b.aktiv, "verbindung_id": b.verbindung_id, "dn": b.dn,
            "letzte_anmeldung": b.letzte_anmeldung.isoformat() if b.letzte_anmeldung else None}


@router.get("/zugang")
def zugang(u=Depends(rolle("admin"))):
    with kern.sitzung() as s:
        return {"benutzer": [_b_json(b) for b in s.scalars(select(Benutzer).order_by(Benutzer.name))],
                "rollen": list(ROLLEN)}


@router.post("/zugang")
async def zugang_neu(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    r = d.get("rolle", "leser")
    if r not in ROLLEN:
        raise HTTPException(400, "unbekannte Rolle")
    from anmeldung import benutzer_suchen
    if d.get("quelle") == "lokal":
        if len(d.get("passwort", "")) < 12:
            raise HTTPException(400, "Passwort: mindestens 12 Zeichen")
        if benutzer_suchen(d["name"]):
            raise HTTPException(400, "Name gibt es schon")
        b = Benutzer(name=d["name"], anzeigename=d.get("anzeigename") or d["name"], quelle="lokal", rolle=r,
                     pw_hash=pw_hash(d["passwort"]))
    else:
        from module import ad
        vid = int(d.get("verbindung_id") or (anmeldung_konfig().get("verzeichnis") or {}).get("verbindung_id") or 0)
        if not vid:
            raise HTTPException(400, "Keine AD-Verbindung gewählt")
        info = ad.benutzer_info(werte(kern.verbindung_laden(vid)), d["name"])
        if benutzer_suchen(info["sam"]):
            raise HTTPException(400, f"{info['sam']} ist schon eingetragen")
        b = Benutzer(name=info["sam"], anzeigename=info["anzeigename"], email=info["mail"], quelle="verzeichnis",
                     rolle=r, verbindung_id=vid, dn=info["dn"])
        gruppe = (anmeldung_konfig().get("verzeichnis") or {}).get("gruppe")
        if gruppe and wahr(d.get("in_gruppe", True)):
            ad.mitglied_hinzu(werte(kern.verbindung_laden(vid)), gruppe, info["sam"])
    with kern.sitzung() as s:
        s.add(b)
    prot(u, "zugang", "Benutzer freigegeben", b.name, details=f"{b.quelle}, Rolle {r}")
    return {"ok": True}


def _admins_uebrig(s, ohne_id: int) -> int:
    return s.scalar(select(func.count()).select_from(Benutzer).where(
        Benutzer.rolle == "admin", Benutzer.aktiv.is_(True), Benutzer.id != ohne_id))


@router.patch("/zugang/{bid}")
async def zugang_aendern(bid: int, req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    with kern.sitzung() as s:
        b = s.get(Benutzer, bid)
        if not b:
            raise HTTPException(404, "gibt es nicht")
        wird_kein_admin = ("rolle" in d and d["rolle"] != "admin") or ("aktiv" in d and not wahr(d["aktiv"]))
        if b.rolle == "admin" and wird_kein_admin and _admins_uebrig(s, b.id) == 0:
            raise HTTPException(400, "Mindestens ein aktiver Admin muss bleiben")
        if "rolle" in d:
            if d["rolle"] not in ROLLEN:
                raise HTTPException(400, "unbekannte Rolle")
            b.rolle = d["rolle"]
        if "aktiv" in d:
            b.aktiv = wahr(d["aktiv"])
        if d.get("passwort"):
            if b.quelle != "lokal":
                raise HTTPException(400, "Passwort von Verzeichnis-Benutzern im AD ändern")
            if len(d["passwort"]) < 12:
                raise HTTPException(400, "Passwort: mindestens 12 Zeichen")
            b.pw_hash = pw_hash(d["passwort"])
        name = b.name
    prot(u, "zugang", "Zugang geändert", name, details=str({k: v for k, v in d.items() if k != "passwort"}))
    return {"ok": True}


@router.delete("/zugang/{bid}")
def zugang_loeschen(bid: int, u=Depends(rolle("admin"))):
    with kern.sitzung() as s:
        b = s.get(Benutzer, bid)
        if not b:
            return {"ok": True}
        if b.rolle == "admin" and _admins_uebrig(s, b.id) == 0:
            raise HTTPException(400, "Mindestens ein aktiver Admin muss bleiben")
        name = b.name
        s.delete(b)
    prot(u, "zugang", "Zugang entfernt", name)
    return {"ok": True}


# ------------------------------------------------------------------ Einstellungen
@router.get("/einstellungen")
def einstellungen(u=Depends(rolle("admin"))):
    a = anmeldung_konfig()
    o = dict(a.get("oidc") or {})
    o["client_secret_gesetzt"] = bool(o.pop("client_secret_enc", None))
    return {"basis_url": kern.einstellung("basis_url", ""), "verzeichnis": a.get("verzeichnis") or {}, "oidc": o,
            "db": (kern.konfig_lesen().get("db") or {}).get("typ"), "version": kern.VERSION}


@router.put("/einstellungen")
async def einstellungen_setzen(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    a = anmeldung_konfig()
    if "verzeichnis" in d:
        v = d["verzeichnis"] or {}
        a["verzeichnis"] = {"aktiv": wahr(v.get("aktiv")), "verbindung_id": int(v["verbindung_id"]) if v.get("verbindung_id") else None,
                            "gruppe": (v.get("gruppe") or "").strip()}
    if "oidc" in d:
        o, alt = d["oidc"] or {}, a.get("oidc") or {}
        neu = {"aktiv": wahr(o.get("aktiv")), "authentik_id": int(o["authentik_id"]) if o.get("authentik_id") else None,
               "issuer": (o.get("issuer") or "").strip(), "client_id": (o.get("client_id") or "").strip(),
               "name": o.get("name") or "Authentik", "tls_pruefen": wahr(o.get("tls_pruefen", True)),
               "verzeichnis_pruefen": wahr(o.get("verzeichnis_pruefen", True)),
               "client_secret_enc": verschluesseln({"s": o["client_secret"]}) if o.get("client_secret") else alt.get("client_secret_enc")}
        if neu["aktiv"] and not (neu["issuer"] and neu["client_id"] and neu["client_secret_enc"]):
            raise HTTPException(400, "Für Authentik sind Issuer, Client-ID und Client-Secret nötig")
        a["oidc"] = neu
    kern.einstellung_setzen("anmeldung", a)
    if "basis_url" in d:
        kern.einstellung_setzen("basis_url", str(d["basis_url"]).rstrip("/"))
    prot(u, "einstellungen", "Anmelde-Einstellungen geändert")
    return {"ok": True}


@router.post("/einstellungen/authentik-anwendung")
async def authentik_anwendung(req: Request, u=Depends(rolle("admin"))):
    """Legt die MIRA-Anwendung in Authentik (neu) an und schaltet die Anmeldung darüber ein."""
    from module import authentik
    d = await req.json()
    vid = int(d["authentik_id"])
    w = werte(kern.verbindung_laden(vid))
    basis = (kern.einstellung("basis_url") or str(req.base_url)).rstrip("/")
    gruppen = [g for g in [d.get("gruppe")] if g]
    r = authentik.oidc_anwendung_anlegen(w, d.get("name") or "MIRA", [basis + "/auth/oidc/rueckruf"], basis, gruppen)
    a = anmeldung_konfig()
    a["oidc"] = {"aktiv": True, "authentik_id": vid, "issuer": r["issuer"], "client_id": r["client_id"],
                 "client_secret_enc": verschluesseln({"s": r["client_secret"]}), "name": "Authentik",
                 "tls_pruefen": wahr(w.get("tls_pruefen", True)), "verzeichnis_pruefen": True}
    kern.einstellung_setzen("anmeldung", a)
    prot(u, "einstellungen", "Authentik-Anwendung für MIRA angelegt", r["issuer"])
    return {"ok": True, "issuer": r["issuer"]}


# ------------------------------------------------------------------ Protokoll
def _prot_json(p: Protokoll) -> dict:
    return {"id": p.id, "zeit": p.zeit.isoformat(), "benutzer": p.benutzer, "ip": p.ip, "bereich": p.bereich,
            "aktion": p.aktion, "ziel": p.ziel, "ok": p.ok, "details": p.details}


@router.get("/protokoll")
def protokoll(bereich: str = "", suche: str = "", seite: int = 0, u=Depends(rolle("operator"))):
    with kern.sitzung() as s:
        q = select(Protokoll).order_by(Protokoll.id.desc())
        if bereich:
            q = q.where(Protokoll.bereich == bereich)
        if suche:
            like = f"%{suche}%"
            q = q.where(or_(Protokoll.benutzer.like(like), Protokoll.aktion.like(like), Protokoll.ziel.like(like),
                            Protokoll.details.like(like)))
        return {"eintraege": [_prot_json(p) for p in s.scalars(q.offset(seite * 100).limit(100))]}


# ------------------------------------------------------------------ Abläufe über mehrere Dienste
@router.post("/ablauf/neuer-benutzer")
async def neuer_benutzer(req: Request, u=Depends(rolle("operator"))):
    """{ad: vid, benutzer: {...wie AD anlegen...},
        postfach: null | {art: "exchange", vid, datenbank} | {art: "grommunio", vid, domaene, quota_mb, ldap: true},
        mira: null | {rolle}}"""
    from module import ad
    d = await req.json()
    schritte = []

    def ok(t, det=""):
        schritte.append({"text": t, "ok": True, "details": det})
    try:
        w_ad = werte(kern.verbindung_laden(int(d["ad"])))
        b = d["benutzer"]
        r = ad.benutzer_anlegen(w_ad, b)
        ok(f"AD: {r['sam']} angelegt" + ("" if r["aktiv"] else " (deaktiviert)"), r["dn"])
        prot(u, "ablauf", "Neuer Benutzer: AD", r["dn"])
        pf = d.get("postfach")
        if pf and pf.get("art") == "exchange":
            from module import exchange
            x = exchange.postfach_aktivieren(werte(kern.verbindung_laden(int(pf["vid"]))), r["upn"], pf.get("datenbank"))
            ok("Exchange: Postfach aktiviert", str(x.get("PrimarySmtpAddress", "")))
            prot(u, "ablauf", "Neuer Benutzer: Exchange-Postfach", r["sam"])
        elif pf and pf.get("art") == "grommunio":
            from module import grommunio
            wg = werte(kern.verbindung_laden(int(pf["vid"])))
            if wahr(pf.get("ldap", True)):
                c = grommunio.Client.fuer(wg)
                treffer = c.get("/domains/ldap/search", query=r["sam"]).get("data") or []
                if not treffer:
                    raise Fehler("grommunio findet den Benutzer (noch) nicht im LDAP – später unter grommunio › LDAP-Import übernehmen")
                did = grommunio.domaene_id(wg, pf["domaene"])
                c.anfrage("POST", f"/domains/{did}/ldap/importUser", params={"ID": treffer[0]["ID"], "force": "false"})
                ok("grommunio: Postfach aus LDAP übernommen", treffer[0].get("email", ""))
            else:
                x = grommunio.postfach_anlegen(wg, pf["domaene"], b.get("mail") or r["sam"], r["anzeigename"],
                                               b.get("passwort", ""), pf.get("quota_mb"))
                ok("grommunio: Postfach angelegt", str(x.get("username", "")))
            prot(u, "ablauf", "Neuer Benutzer: grommunio-Postfach", r["sam"])
        m = d.get("mira")
        if m and m.get("rolle"):
            if u["rolle"] != "admin":
                raise Fehler("Zugang zu MIRA dürfen nur Admins vergeben")
            with kern.sitzung() as s:
                s.add(Benutzer(name=r["sam"], anzeigename=r["anzeigename"], email=b.get("mail", ""), quelle="verzeichnis",
                               rolle=m["rolle"], verbindung_id=int(d["ad"]), dn=r["dn"]))
            g = (anmeldung_konfig().get("verzeichnis") or {}).get("gruppe")
            if g:
                ad.mitglied_hinzu(w_ad, g, r["sam"])
            ok(f"MIRA: Zugang als {m['rolle']}")
    except (Fehler, PermissionError, LookupError) as e:
        schritte.append({"text": str(e), "ok": False, "details": ""})
        prot(u, "ablauf", "Neuer Benutzer abgebrochen", d.get("benutzer", {}).get("sam", ""), False, str(e))
        return {"ok": False, "schritte": schritte}
    return {"ok": True, "schritte": schritte}


@router.post("/ablauf/benutzer-sperren")
async def benutzer_sperren(req: Request, u=Depends(rolle("operator"))):
    """{ad: vid, sam, authentik: vid|null, mira: true, exchange: {vid}|null (aus Adressbuch ausblenden)}"""
    from module import ad
    d = await req.json()
    sam = d["sam"]
    schritte = []
    try:
        w = werte(kern.verbindung_laden(int(d["ad"])))
        with ad.verbinden(w) as c:
            dn = ad.benutzer_dn(c, w, sam)
            c.search(dn, "(objectClass=*)", "BASE", attributes=["userAccountControl"])
            uac = int(ad._wert(ad._eintraege(c)[0]["attributes"], "userAccountControl", 512))
            c.modify(dn, {"userAccountControl": [("MODIFY_REPLACE", [uac | ad.UAC_DEAKTIVIERT])]})
            ad._ok(c, "Deaktivieren")
        schritte.append({"text": f"AD: {sam} deaktiviert", "ok": True})
        if d.get("authentik"):
            from module import authentik
            wa = werte(kern.verbindung_laden(int(d["authentik"])))
            t = [x for x in authentik.alle(wa, "core/users/", username=sam) if x["username"].lower() == sam.lower()]
            for x in t:
                authentik.api(wa, "PATCH", f"core/users/{x['pk']}/", json={"is_active": False})
            schritte.append({"text": f"Authentik: {len(t)} Konto/Konten gesperrt", "ok": True})
        if (d.get("exchange") or {}).get("vid"):
            from module import exchange
            exchange.ausfuehren(werte(kern.verbindung_laden(int(d["exchange"]["vid"]))), "Set-Mailbox",
                                {"Identity": sam, "HiddenFromAddressListsEnabled": True})
            schritte.append({"text": "Exchange: aus dem Adressbuch ausgeblendet", "ok": True})
        if wahr(d.get("mira", True)):
            with kern.sitzung() as s:
                b = s.scalars(select(Benutzer).where(func.lower(Benutzer.name) == sam.lower())).first()
                if b:
                    if b.rolle == "admin" and _admins_uebrig(s, b.id) == 0:
                        raise Fehler("Letzter Admin – MIRA-Zugang bleibt")
                    b.aktiv = False
                    schritte.append({"text": "MIRA: Zugang gesperrt", "ok": True})
    except (Fehler, PermissionError, LookupError) as e:
        schritte.append({"text": str(e), "ok": False})
        prot(u, "ablauf", "Benutzer sperren abgebrochen", sam, False, str(e))
        return {"ok": False, "schritte": schritte}
    prot(u, "ablauf", "Benutzer gesperrt", sam, details="; ".join(x["text"] for x in schritte))
    return {"ok": True, "schritte": schritte}
