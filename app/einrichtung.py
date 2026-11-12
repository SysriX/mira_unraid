"""Einrichtungs-Assistent beim ersten Start.

Geschützt durch einen Einrichtungs-Code, der beim Start ins Container-Log geschrieben wird
(oder MIRA_SETUP_TOKEN). Ablauf:
  1. Datenbank: eingebaut (SQLite) oder MariaDB (vorhanden – oder mit Root-Zugang anlegen)
  2. Anmeldung: lokal / Verzeichnis (AD) / Authentik – fehlende Verbindungen direkt eingeben und testen
  3. Admin: lokaler Notfall-Admin + optional Verzeichnis-Admin (vorhandenen übernehmen oder neu anlegen)
  4. Abschluss: alles speichern, Gruppe/Authentik-Anwendung anlegen, Protokoll der Schritte zurückgeben
"""
from __future__ import annotations

import hmac
import os
import re
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Request

import kern
from anmeldung import pw_hash
from kern import Benutzer, Verbindung, verschluesseln
from module import Fehler, REGISTER

router = APIRouter(prefix="/api/einrichtung")
CODE_DATEI = os.path.join(kern.DATA, "einrichtungs-code.txt")
_code: str | None = None


def eingerichtet() -> bool:
    return bool(kern.konfig_lesen().get("eingerichtet")) and kern.db_bereit()


def code() -> str:
    global _code
    if _code:
        return _code
    _code = os.environ.get("MIRA_SETUP_TOKEN", "").strip()
    if not _code:
        if os.path.exists(CODE_DATEI):
            _code = open(CODE_DATEI, encoding="utf-8").read().strip()
        else:
            import secrets
            abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            _code = "-".join("".join(secrets.choice(abc) for _ in range(4)) for _ in range(3))
            os.makedirs(kern.DATA, exist_ok=True)
            with open(CODE_DATEI, "w", encoding="utf-8") as f:
                f.write(_code)
    return _code


def code_melden():
    if not eingerichtet():
        c = code()
        kern.log("=" * 60)
        kern.log(f"EINRICHTUNG: Code für den Assistenten:  {c}")
        kern.log(f"(steht auch in {CODE_DATEI})")
        kern.log("=" * 60)


def _pruefen(req: Request):
    if eingerichtet():
        raise HTTPException(409, "MIRA ist bereits eingerichtet")
    if not hmac.compare_digest(req.headers.get("x-mira-einrichtung", ""), code()):
        raise HTTPException(403, "Einrichtungs-Code falsch – er steht im Log des Containers")


@router.get("/status")
def status():
    k = kern.konfig_lesen()
    return {"eingerichtet": eingerichtet(), "db": (k.get("db") or {}).get("typ"), "version": kern.VERSION,
            "module": [m.beschreibung_json() for m in REGISTER.values()]}


@router.post("/code")
async def code_pruefen(req: Request):
    _pruefen(req)
    return {"ok": True, "db_vorhanden": kern.db_bereit()}


# ------------------------------------------------------------------ 1. Datenbank
def _mariadb_url(d, benutzer=None, pw=None, db=True):
    return (f"mysql+pymysql://{quote_plus(benutzer or d['benutzer'])}:{quote_plus(pw if pw is not None else d.get('passwort', ''))}"
            f"@{d['host']}:{int(d.get('port') or 3306)}/{d['name'] if db else ''}?charset=utf8mb4")


@router.post("/db")
async def db_einrichten(req: Request):
    _pruefen(req)
    d = await req.json()
    typ = d.get("typ", "sqlite")
    nur_testen = bool(d.get("nur_testen"))
    if typ == "sqlite":
        cfg = {"typ": "sqlite", "datei": "mira.db"}
    elif typ == "mariadb":
        for f in ("host", "name", "benutzer"):
            if not d.get(f):
                raise HTTPException(400, f"Feld „{f}“ fehlt")
        if not re.match(r"^[A-Za-z0-9_]+$", d["name"]) or not re.match(r"^[A-Za-z0-9_.-]+$", d["benutzer"]):
            raise HTTPException(400, "Datenbank-/Benutzername: nur Buchstaben, Zahlen, _ . -")
        if d.get("anlegen"):
            # mit Root-Zugang Datenbank + Benutzer anlegen
            from sqlalchemy import text
            e = kern.engine_bauen(_mariadb_url(d, d.get("root_benutzer") or "root", d.get("root_passwort", ""), db=False))
            try:
                with e.begin() as c:
                    c.execute(text(f"CREATE DATABASE IF NOT EXISTS `{d['name']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                    if not nur_testen:
                        c.execute(text("CREATE USER IF NOT EXISTS :u@'%' IDENTIFIED BY :p"), {"u": d["benutzer"], "p": d.get("passwort", "")})
                        c.execute(text("ALTER USER :u@'%' IDENTIFIED BY :p"), {"u": d["benutzer"], "p": d.get("passwort", "")})
                        c.execute(text(f"GRANT ALL PRIVILEGES ON `{d['name']}`.* TO :u@'%'"), {"u": d["benutzer"]})
                        c.execute(text("FLUSH PRIVILEGES"))
            except Exception as ex:  # noqa: BLE001
                raise HTTPException(400, f"Anlegen mit Root-Zugang fehlgeschlagen: {ex}") from ex
            finally:
                e.dispose()
        try:
            kern.db_testen(_mariadb_url(d))
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(400, f"Verbindung zur MariaDB fehlgeschlagen: {ex}") from ex
        cfg = {"typ": "mariadb", "host": d["host"], "port": int(d.get("port") or 3306), "name": d["name"],
               "benutzer": d["benutzer"], "passwort_enc": verschluesseln({"p": d.get("passwort", "")})}
    else:
        raise HTTPException(400, "Typ: sqlite oder mariadb")
    if nur_testen:
        return {"ok": True, "meldung": "Verbindung klappt"}
    k = kern.konfig_lesen()
    k["db"] = cfg
    kern.konfig_schreiben(k)
    kern.db_starten()
    with kern.sitzung() as s:
        from sqlalchemy import func, select
        n_benutzer = s.scalar(select(func.count()).select_from(Benutzer))
        n_verb = s.scalar(select(func.count()).select_from(Verbindung))
    return {"ok": True, "meldung": "Datenbank bereit", "vorhanden": {"benutzer": n_benutzer, "verbindungen": n_verb}}


# ------------------------------------------------------------------ 2. Verbindungen testen/speichern
def _verbindung_aus(d) -> tuple[dict, dict]:
    m = REGISTER.get(d.get("typ"))
    if not m:
        raise HTTPException(400, "unbekannter Verbindungstyp")
    werte = d.get("werte") or {}
    geheim = {k: werte[k] for k in m.geheime_felder() if werte.get(k)}
    einst = {k: v for k, v in werte.items() if k not in m.geheime_felder()}
    return einst, geheim


@router.post("/verbindung/testen")
async def verbindung_testen(req: Request):
    _pruefen(req)
    d = await req.json()
    m = REGISTER.get(d.get("typ"))
    einst, geheim = _verbindung_aus(d)
    try:
        return {"ok": True, "meldung": m.testen({**einst, **geheim, "_id": 0, "_name": "test"})}
    except (Fehler, PermissionError) as e:
        return {"ok": False, "meldung": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "meldung": f"{type(e).__name__}: {e}"}


@router.get("/verbindungen")
def vorhandene(req: Request):
    _pruefen(req)
    if not kern.db_bereit():
        return {"verbindungen": []}
    return {"verbindungen": [{"id": v.id, "typ": v.typ, "name": v.name, "einstellungen": v.einstellungen}
                             for v in kern.verbindungen()]}


@router.post("/verzeichnis/suche")
async def verzeichnis_suche(req: Request):
    """Benutzer im AD suchen (für „vorhandenen Admin übernehmen“), mit den Formulardaten der Verbindung."""
    _pruefen(req)
    d = await req.json()
    from ldap3 import SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    from module import ad
    w = _werte_aus_auswahl(d.get("verbindung"))
    q = escape_filter_chars(d.get("suche", ""))
    with ad.verbinden(w) as c:
        c.search(w["base_dn"], f"(&(objectCategory=person)(objectClass=user)(|(sAMAccountName=*{q}*)(displayName=*{q}*)))",
                 SUBTREE, attributes=["sAMAccountName", "displayName", "mail", "userAccountControl"], size_limit=50)
        return {"treffer": [{"sam": ad._wert(e["attributes"], "sAMAccountName"), "anzeigename": ad._wert(e["attributes"], "displayName", ""),
                             "mail": ad._wert(e["attributes"], "mail", "") or ""} for e in ad._eintraege(c)]}


def _werte_aus_auswahl(v: dict | None) -> dict:
    """v = {"id": vorhandene} oder {"typ":..., "werte": {...}} (neu, noch nicht gespeichert)."""
    if not v:
        raise HTTPException(400, "Verbindung fehlt")
    if v.get("id"):
        from module import werte
        return werte(kern.verbindung_laden(int(v["id"])))
    einst, geheim = _verbindung_aus(v)
    return {**einst, **geheim, "_id": 0, "_name": v.get("name", "")}


def _verbindung_speichern(v: dict) -> int:
    if v.get("id"):
        return int(v["id"])
    einst, geheim = _verbindung_aus(v)
    with kern.sitzung() as s:
        neu = Verbindung(typ=v["typ"], name=v.get("name") or REGISTER[v["typ"]].titel, einstellungen=einst,
                         geheim=verschluesseln(geheim), status="bei Einrichtung getestet")
        s.add(neu)
        s.flush()
        return neu.id


# ------------------------------------------------------------------ 3./4. Abschluss
@router.post("/abschliessen")
async def abschliessen(req: Request):
    """Erwartet:
    {
      basis_url: "https://mira.firma.de",
      lokal_admin: {name, passwort, anzeigename}              # immer (Notfall-Zugang)
      verzeichnis: null | {verbindung: {...}, gruppe: "grp_MIRA_Admins", gruppe_anlegen: true,
                           login_direkt: true,
                           admin: null | {modus: "vorhanden"|"neu", sam, vorname, nachname, passwort, mail}}
      authentik: null | {verbindung: {...}, anwendung_anlegen: true, name: "MIRA", gruppen_zugriff: true,
                         issuer?, client_id?, client_secret? (falls Anwendung schon da), verzeichnis_pruefen: true}
      weitere: [ {typ, name, werte}, ... ]                       # optionale weitere Verbindungen
    }
    """
    _pruefen(req)
    if not kern.db_bereit():
        raise HTTPException(400, "Erst die Datenbank einrichten")
    d = await req.json()
    schritte: list[dict] = []

    def schritt(text, ok=True, details=""):
        schritte.append({"text": text, "ok": ok, "details": details})

    la = d.get("lokal_admin") or {}
    if not la.get("name") or len(la.get("passwort", "")) < 12:
        raise HTTPException(400, "Lokaler Admin: Name und Passwort (mind. 12 Zeichen) sind Pflicht")
    basis = (d.get("basis_url") or str(req.base_url)).rstrip("/")
    anmeldung = {"verzeichnis": {"aktiv": False}, "oidc": {"aktiv": False}}

    try:
        # --- lokaler Notfall-Admin
        from anmeldung import benutzer_suchen
        with kern.sitzung() as s:
            b = benutzer_suchen(la["name"])
            if b:
                bb = s.get(Benutzer, b.id)
                bb.pw_hash, bb.rolle, bb.aktiv, bb.quelle = pw_hash(la["passwort"]), "admin", True, "lokal"
                schritt(f"Lokaler Admin „{la['name']}“ aktualisiert")
            else:
                s.add(Benutzer(name=la["name"], anzeigename=la.get("anzeigename") or la["name"], quelle="lokal",
                               rolle="admin", pw_hash=pw_hash(la["passwort"])))
                schritt(f"Lokaler Admin „{la['name']}“ angelegt")

        # --- Verzeichnis
        vz = d.get("verzeichnis")
        if vz:
            from module import ad
            w = _werte_aus_auswahl(vz["verbindung"])
            vid = _verbindung_speichern(vz["verbindung"])
            schritt(f"AD-Verbindung gespeichert (Nr. {vid})")
            gruppe = (vz.get("gruppe") or "").strip()
            if gruppe and vz.get("gruppe_anlegen"):
                dn, neu = ad.gruppe_sicherstellen(w, gruppe, "Zugang zu MIRA (Admin-Oberfläche)")
                schritt(f"Gruppe {gruppe} " + ("angelegt" if neu else "war schon da"), details=dn)
            a = vz.get("admin")
            if a:
                if a.get("modus") == "neu":
                    r = ad.benutzer_anlegen(w, {"sam": a["sam"], "vorname": a.get("vorname", ""), "nachname": a.get("nachname", ""),
                                                "mail": a.get("mail", ""), "passwort": a["passwort"], "aktiv": True,
                                                "gruppen": [gruppe] if gruppe else []})
                    schritt(f"AD-Benutzer {r['sam']} angelegt", details=r["dn"])
                else:
                    if gruppe:
                        ad.mitglied_hinzu(w, gruppe, a["sam"])
                        schritt(f"{a['sam']} ist Mitglied von {gruppe}")
                info = ad.benutzer_info(w, a["sam"])
                with kern.sitzung() as s:
                    b = benutzer_suchen(info["sam"])
                    if b:
                        bb = s.get(Benutzer, b.id)
                        bb.quelle, bb.rolle, bb.aktiv, bb.verbindung_id, bb.dn = "verzeichnis", "admin", True, vid, info["dn"]
                    else:
                        s.add(Benutzer(name=info["sam"], anzeigename=info["anzeigename"], email=info["mail"],
                                       quelle="verzeichnis", rolle="admin", verbindung_id=vid, dn=info["dn"]))
                schritt(f"{info['sam']} als Admin in MIRA eingetragen")
            anmeldung["verzeichnis"] = {"aktiv": bool(vz.get("login_direkt", True)), "verbindung_id": vid, "gruppe": gruppe}

        # --- Authentik
        ak = d.get("authentik")
        if ak:
            from module import authentik
            w = _werte_aus_auswahl(ak["verbindung"])
            aid = _verbindung_speichern(ak["verbindung"])
            schritt(f"Authentik-Verbindung gespeichert (Nr. {aid})")
            if ak.get("anwendung_anlegen", True):
                gr = []
                g = (anmeldung.get("verzeichnis") or {}).get("gruppe")
                if ak.get("gruppen_zugriff") and g:
                    gr = [g]
                try:
                    r = authentik.oidc_anwendung_anlegen(w, ak.get("name") or "MIRA", [basis + "/auth/oidc/rueckruf"], basis, gr)
                    if gr:
                        schritt(f"Zugriff in Authentik nur für Gruppe {g}")
                except Fehler as e:
                    if gr and "gibt es in Authentik nicht" in str(e):
                        r = authentik.oidc_anwendung_anlegen(w, ak.get("name") or "MIRA", [basis + "/auth/oidc/rueckruf"], basis, [])
                        schritt(f"Gruppe {g} ist (noch) nicht in Authentik – Zugriff dort später setzen", ok=False)
                    else:
                        raise
                schritt(f"Authentik-Anwendung „{ak.get('name') or 'MIRA'}“ angelegt/aktualisiert", details=r["issuer"])
            else:
                r = {"issuer": ak["issuer"], "client_id": ak["client_id"], "client_secret": ak["client_secret"]}
            anmeldung["oidc"] = {"aktiv": True, "authentik_id": aid, "issuer": r["issuer"], "client_id": r["client_id"],
                                 "client_secret_enc": verschluesseln({"s": r["client_secret"]}), "name": "Authentik",
                                 "tls_pruefen": bool(w.get("tls_pruefen", True)),
                                 "verzeichnis_pruefen": bool(ak.get("verzeichnis_pruefen", True))}

        # --- weitere Verbindungen
        for v in d.get("weitere") or []:
            vid = _verbindung_speichern(v)
            schritt(f"Verbindung „{v.get('name') or v['typ']}“ gespeichert (Nr. {vid})")

        kern.einstellung_setzen("anmeldung", anmeldung)
        kern.einstellung_setzen("basis_url", basis)
    except HTTPException:
        raise
    except (Fehler, PermissionError) as e:
        schritt(str(e), ok=False)
        return {"ok": False, "schritte": schritte}
    except Exception as e:  # noqa: BLE001
        schritt(f"{type(e).__name__}: {e}", ok=False)
        return {"ok": False, "schritte": schritte}

    k = kern.konfig_lesen()
    k["eingerichtet"] = True
    kern.konfig_schreiben(k)
    try:
        os.remove(CODE_DATEI)
    except OSError:
        pass
    kern.protokoll(la["name"], "einrichtung", "Einrichtung abgeschlossen", details="; ".join(s["text"] for s in schritte),
                   ip=req.client.host if req.client else "")
    schritt("Einrichtung abgeschlossen – jetzt anmelden")
    return {"ok": True, "schritte": schritte}
