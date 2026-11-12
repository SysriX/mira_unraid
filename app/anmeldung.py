"""Anmeldung an MIRA: lokal (Notfall-Admin), Verzeichnis (AD/Samba per LDAP) und Authentik (OpenID Connect).

Wer sich anmelden darf, steht IMMER in der MIRA-Datenbank (Tabelle mira_benutzer, mit Rolle).
Bei Verzeichnis-Benutzern prüft MIRA zusätzlich bei jeder Anmeldung im AD: Konto vorhanden, aktiv
und Mitglied der Zugangsgruppe (auch verschachtelt).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

import kern
from kern import Benutzer, Sitzung, einstellung, jetzt, protokoll, sitzung

COOKIE = "mira_sitzung"
DAUER = timedelta(hours=12)
ROLLEN = {"leser": 1, "operator": 2, "admin": 3}
_ph = PasswordHasher()
_fehl: dict[str, list] = {}          # ip -> [anzahl, seit]
_oidc_offen: dict[str, dict] = {}    # state -> {nonce, verifier, zeit}

router = APIRouter()


# ------------------------------------------------------------------ Hilfen
def pw_hash(pw: str) -> str:
    return _ph.hash(pw)


def pw_pruefen(hash_: str | None, pw: str) -> bool:
    if not hash_:
        return False
    try:
        return _ph.verify(hash_, pw)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001
        return False


def client_ip(req: Request) -> str:
    return req.client.host if req.client else ""


def https(req: Request) -> bool:
    return req.url.scheme == "https" or req.headers.get("x-forwarded-proto", "").lower() == "https"


def _bremse(ip: str) -> None:
    n, seit = _fehl.get(ip, [0, time.time()])
    if n >= 5 and time.time() - seit < 300:
        raise HTTPException(429, "Zu viele Fehlversuche – bitte 5 Minuten warten")


def _fehler(ip: str) -> None:
    n, seit = _fehl.get(ip, [0, time.time()])
    if time.time() - seit >= 300:
        n, seit = 0, time.time()
    _fehl[ip] = [n + 1, seit]


def anmeldung_konfig() -> dict:
    return einstellung("anmeldung", {}) or {}


def _sitzung_erzeugen(resp, req: Request, b: Benutzer, art: str):
    tok = secrets.token_urlsafe(32)
    with sitzung() as s:
        s.add(Sitzung(token_hash=hashlib.sha256(tok.encode()).hexdigest(), benutzer_id=b.id,
                      ablauf=jetzt() + DAUER, ip=client_ip(req), art=art))
        bb = s.get(Benutzer, b.id)
        bb.letzte_anmeldung = jetzt()
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=https(req), max_age=int(DAUER.total_seconds()),
                    path="/")
    _fehl.pop(client_ip(req), None)
    protokoll(b.name, "anmeldung", f"angemeldet ({art})", ip=client_ip(req))
    return resp


def benutzer_suchen(name: str) -> Benutzer | None:
    with sitzung() as s:
        b = s.scalars(select(Benutzer).where(func.lower(Benutzer.name) == name.strip().lower())).first()
        if b:
            s.expunge(b)
        return b


def _verzeichnis_pruefen(b: Benutzer, passwort: str | None = None) -> None:
    """Prüft einen Verzeichnis-Benutzer im AD (und optional sein Passwort). Wirft HTTPException."""
    from module import ad
    cfg = anmeldung_konfig().get("verzeichnis") or {}
    vid = b.verbindung_id or cfg.get("verbindung_id")
    if not vid:
        raise HTTPException(403, "Keine AD-Verbindung für die Anmeldung eingerichtet")
    try:
        ad.anmeldung_pruefen(int(vid), b.name, passwort, cfg.get("gruppe") or "")
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Verzeichnis nicht erreichbar: {e}") from e


# ------------------------------------------------------------------ Abhängigkeiten
def aktueller(req: Request) -> dict:
    if not kern.db_bereit():
        raise HTTPException(409, "MIRA ist noch nicht eingerichtet")
    tok = req.cookies.get(COOKIE)
    if not tok:
        raise HTTPException(401, "nicht angemeldet")
    h = hashlib.sha256(tok.encode()).hexdigest()
    with sitzung() as s:
        si = s.get(Sitzung, h)
        if not si or si.ablauf.replace(tzinfo=si.ablauf.tzinfo or kern.timezone.utc) < jetzt():
            if si:
                s.delete(si)
            raise HTTPException(401, "Sitzung abgelaufen")
        b = s.get(Benutzer, si.benutzer_id)
        if not b or not b.aktiv:
            s.delete(si)
            raise HTTPException(401, "Benutzer gesperrt")
        # gleitend verlängern (höchstens alle 10 Minuten schreiben)
        neu = jetzt() + DAUER
        if (neu - si.ablauf.replace(tzinfo=si.ablauf.tzinfo or kern.timezone.utc)).total_seconds() > 600:
            si.ablauf = neu
        return {"id": b.id, "name": b.name, "anzeigename": b.anzeigename or b.name, "rolle": b.rolle,
                "quelle": b.quelle, "ip": client_ip(req)}


def rolle(mindest: str):
    def pruefen(u: dict = Depends(aktueller)) -> dict:
        if ROLLEN.get(u["rolle"], 0) < ROLLEN[mindest]:
            raise HTTPException(403, f"Dafür ist die Rolle „{mindest}“ nötig")
        return u
    return pruefen


# ------------------------------------------------------------------ Routen
@router.get("/api/anmeldung/arten")
def arten():
    if not kern.db_bereit():
        return {"eingerichtet": False}
    cfg = anmeldung_konfig()
    oidc = cfg.get("oidc") or {}
    return {
        "eingerichtet": True,
        "lokal": True,
        "verzeichnis": bool((cfg.get("verzeichnis") or {}).get("aktiv")),
        "oidc": bool(oidc.get("aktiv")),
        "oidc_name": oidc.get("name") or "Authentik",
    }


@router.post("/api/anmeldung")
async def anmelden(req: Request):
    ip = client_ip(req)
    _bremse(ip)
    d = await req.json()
    name, pw = str(d.get("benutzer", "")).strip(), str(d.get("passwort", ""))
    if "\\" in name:
        name = name.split("\\", 1)[1]
    if "@" in name:
        name = name.split("@", 1)[0]
    b = benutzer_suchen(name) if name else None
    if not b or not b.aktiv:
        _fehler(ip)
        protokoll(name, "anmeldung", "fehlgeschlagen", ok=False, details="unbekannt oder gesperrt", ip=ip)
        raise HTTPException(403, "Benutzer oder Passwort falsch")
    if b.quelle == "lokal":
        if not pw_pruefen(b.pw_hash, pw):
            _fehler(ip)
            protokoll(name, "anmeldung", "fehlgeschlagen", ok=False, details="Passwort", ip=ip)
            raise HTTPException(403, "Benutzer oder Passwort falsch")
        return _sitzung_erzeugen(JSONResponse({"ok": True}), req, b, "lokal")
    cfg = anmeldung_konfig().get("verzeichnis") or {}
    if not cfg.get("aktiv"):
        raise HTTPException(403, "Anmeldung über das Verzeichnis ist ausgeschaltet – bitte über Authentik anmelden")
    try:
        _verzeichnis_pruefen(b, pw)
    except HTTPException as e:
        _fehler(ip)
        protokoll(name, "anmeldung", "fehlgeschlagen", ok=False, details=str(e.detail), ip=ip)
        raise
    return _sitzung_erzeugen(JSONResponse({"ok": True}), req, b, "verzeichnis")


@router.post("/api/abmelden")
def abmelden(req: Request):
    tok = req.cookies.get(COOKIE)
    if tok and kern.db_bereit():
        with sitzung() as s:
            si = s.get(Sitzung, hashlib.sha256(tok.encode()).hexdigest())
            if si:
                s.delete(si)
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


@router.get("/api/ich")
def ich(u: dict = Depends(aktueller)):
    return u


@router.post("/api/ich/passwort")
async def eigenes_passwort(req: Request, u: dict = Depends(aktueller)):
    d = await req.json()
    if u["quelle"] != "lokal":
        raise HTTPException(400, "Verzeichnis-Benutzer ändern ihr Passwort im AD")
    neu = str(d.get("neu", ""))
    if len(neu) < 12:
        raise HTTPException(400, "Mindestens 12 Zeichen")
    with sitzung() as s:
        b = s.get(Benutzer, u["id"])
        if not pw_pruefen(b.pw_hash, str(d.get("alt", ""))):
            raise HTTPException(403, "Altes Passwort falsch")
        b.pw_hash = pw_hash(neu)
    protokoll(u["name"], "anmeldung", "eigenes Passwort geändert", ip=u["ip"])
    return {"ok": True}


# ------------------------------------------------------------------ OpenID Connect (Authentik)
def _oidc() -> dict:
    cfg = anmeldung_konfig().get("oidc") or {}
    if not cfg.get("aktiv"):
        raise HTTPException(404, "Anmeldung über Authentik ist nicht eingerichtet")
    return cfg


_disco: dict[str, tuple[float, dict]] = {}


def oidc_discovery(issuer: str, pruefen_tls: bool = True) -> dict:
    issuer = issuer.rstrip("/") + "/"
    c = _disco.get(issuer)
    if c and time.time() - c[0] < 3600:
        return c[1]
    r = httpx.get(issuer + ".well-known/openid-configuration", timeout=10, verify=pruefen_tls)
    r.raise_for_status()
    d = r.json()
    _disco[issuer] = (time.time(), d)
    return d


def basis_url(req: Request) -> str:
    b = einstellung("basis_url") or ""
    return (b or str(req.base_url)).rstrip("/")


def rueckruf_url(req: Request) -> str:
    return basis_url(req) + "/auth/oidc/rueckruf"


@router.get("/auth/oidc/start")
def oidc_start(req: Request):
    cfg = _oidc()
    d = oidc_discovery(cfg["issuer"], cfg.get("tls_pruefen", True))
    for k in [k for k, v in _oidc_offen.items() if time.time() - v["zeit"] > 600]:
        _oidc_offen.pop(k, None)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    _oidc_offen[state] = {"nonce": nonce, "verifier": verifier, "zeit": time.time()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    q = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": rueckruf_url(req),
         "scope": "openid profile email", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return RedirectResponse(d["authorization_endpoint"] + "?" + urlencode(q))


def _fehlerseite(text: str) -> RedirectResponse:
    return RedirectResponse("/#anmelden?fehler=" + urlencode({"t": text})[2:])


@router.get("/auth/oidc/rueckruf")
def oidc_rueckruf(req: Request, code: str = "", state: str = "", error: str = ""):
    ip = client_ip(req)
    if error:
        return _fehlerseite(f"Authentik: {error}")
    offen = _oidc_offen.pop(state, None)
    if not offen or not code:
        return _fehlerseite("Anmeldung abgelaufen – bitte erneut versuchen")
    cfg = _oidc()
    tls = cfg.get("tls_pruefen", True)
    try:
        d = oidc_discovery(cfg["issuer"], tls)
        geheim = kern.entschluesseln(cfg.get("client_secret_enc")).get("s", "")
        r = httpx.post(d["token_endpoint"], timeout=15, verify=tls, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": rueckruf_url(req),
            "client_id": cfg["client_id"], "client_secret": geheim, "code_verifier": offen["verifier"]})
        r.raise_for_status()
        idt = r.json()["id_token"]
        jwks = jwt.PyJWKClient(d["jwks_uri"], ssl_context=None if tls else _ohne_tls())
        key = jwks.get_signing_key_from_jwt(idt)
        claims = jwt.decode(idt, key.key, algorithms=["RS256", "ES256"],
                            audience=cfg["client_id"], issuer=d.get("issuer"))
        if claims.get("nonce") != offen["nonce"]:
            raise ValueError("nonce passt nicht")
    except Exception as e:  # noqa: BLE001
        protokoll("?", "anmeldung", "Authentik fehlgeschlagen", ok=False, details=str(e), ip=ip)
        return _fehlerseite(f"Anmeldung über Authentik fehlgeschlagen: {e}")
    name = str(claims.get("preferred_username") or claims.get("nickname") or "").split("@")[0]
    b = benutzer_suchen(name) if name else None
    if not b or not b.aktiv:
        protokoll(name, "anmeldung", "Authentik: nicht freigegeben", ok=False, ip=ip)
        return _fehlerseite(f"„{name}“ ist in MIRA nicht freigegeben – ein Admin muss dich unter Zugang eintragen")
    if b.quelle == "verzeichnis" and cfg.get("verzeichnis_pruefen", True):
        try:
            _verzeichnis_pruefen(b, None)
        except HTTPException as e:
            protokoll(name, "anmeldung", "Authentik: Verzeichnisprüfung", ok=False, details=str(e.detail), ip=ip)
            return _fehlerseite(str(e.detail))
    return _sitzung_erzeugen(RedirectResponse("/"), req, b, "authentik")


def _ohne_tls():
    import ssl
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c
