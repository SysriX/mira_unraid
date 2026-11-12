"""Modul grommunio (grommunio-admin-api, /api/v1).

Domänen, Postfächer (anlegen, ändern, Speicherplatz, Passwort, löschen), Aliase, LDAP-Import
und Systemstatus. Anmeldung mit dem Admin-Konto der grommunio-Oberfläche (JWT + CSRF).
UNGETESTET gegen einen echten grommunio-Server – Endpunkte nach Dokumentation der admin-api.
"""
from __future__ import annotations

import threading
import time

import httpx
from fastapi import APIRouter, Depends, Request

from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr


class Grommunio(Modul):
    typ = "grommunio"
    titel = "grommunio"
    beschreibung = "Mailserver grommunio: Domänen, Postfächer, Aliase, Speicherplatz, LDAP-Import, Status."
    felder = [
        {"name": "url", "titel": "Adresse der Admin-Oberfläche", "art": "text", "pflicht": True,
         "hilfe": "z. B. https://mail.firma.de:8443"},
        {"name": "benutzer", "titel": "Admin-Benutzer", "art": "text", "standard": "admin", "pflicht": True},
        {"name": "passwort", "titel": "Passwort", "art": "password", "pflicht": True},
        {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": True},
    ]

    def testen(self, w):
        d = Client.fuer(w).get("/system/domains")
        return f"Verbunden, {len(d.get('data') or [])} Domäne(n)"


MODUL = registrieren(Grommunio())
router = APIRouter(prefix="/api/m/grommunio/{vid}", tags=["grommunio"])
MODUL.router = router
verb = verbindung_dep("grommunio")


class Client:
    _cache: dict[tuple, "Client"] = {}
    _lock = threading.Lock()

    def __init__(self, w):
        self.w = w
        self.basis = w["url"].rstrip("/") + "/api/v1"
        self.c = httpx.Client(timeout=30, verify=wahr(w.get("tls_pruefen", True)))
        self.csrf, self.seit = None, 0

    @classmethod
    def fuer(cls, w):
        k = (w["_id"], w["url"], w["benutzer"], w["passwort"])
        with cls._lock:
            if k not in cls._cache:
                cls._cache = {kk: v for kk, v in cls._cache.items() if kk[0] != w["_id"]}
                cls._cache[k] = Client(w)
            return cls._cache[k]

    def _anmelden(self):
        r = self.c.post(self.basis + "/login", data={"user": self.w["benutzer"], "pass": self.w["passwort"]})
        if r.status_code != 200:
            raise Fehler(f"grommunio: Anmeldung fehlgeschlagen ({r.status_code}: {_text(r)})")
        d = r.json()
        if d.get("grommunioAuthJwt"):
            self.c.cookies.set("grommunioAuthJwt", d["grommunioAuthJwt"])
        self.csrf, self.seit = d.get("csrf"), time.time()

    def anfrage(self, methode, pfad, **kw):
        try:
            if not self.csrf or time.time() - self.seit > 3000:
                self._anmelden()
            h = {"X-Csrf-Token": self.csrf} if self.csrf else {}
            r = self.c.request(methode, self.basis + pfad, headers=h, **kw)
            if r.status_code in (401, 403) and "csrf" in r.text.lower() or r.status_code == 401:
                self._anmelden()
                r = self.c.request(methode, self.basis + pfad, headers={"X-Csrf-Token": self.csrf}, **kw)
        except httpx.HTTPError as e:
            raise Fehler(f"grommunio nicht erreichbar: {e}") from e
        if r.status_code >= 400:
            raise Fehler(f"grommunio {r.status_code}: {_text(r)}")
        return r.json() if r.content else {}

    def get(self, pfad, **params):
        return self.anfrage("GET", pfad, params=params)


def _text(r):
    try:
        return r.json().get("message") or r.text[:200]
    except Exception:  # noqa: BLE001
        return r.text[:200]


def _c(w):
    return Client.fuer(w)


def domaene_id(w, name_oder_id) -> int:
    if str(name_oder_id).isdigit():
        return int(name_oder_id)
    for d in _c(w).get("/system/domains", limit=1000).get("data") or []:
        if d.get("domainname", "").lower() == str(name_oder_id).lower():
            return d["ID"]
    raise Fehler(f"Domäne „{name_oder_id}“ gibt es in grommunio nicht")


def postfach_anlegen(w, domaene, name: str, anzeigename: str = "", passwort: str = "", quota_mb: int | None = None) -> dict:
    did = domaene_id(w, domaene)
    dname = next((d["domainname"] for d in _c(w).get("/system/domains", limit=1000).get("data") or [] if d["ID"] == did), "")
    props = {"displayname": anzeigename or name}
    if quota_mb:
        kb = int(quota_mb) * 1024
        props.update({"storagequotalimit": kb, "prohibitsendquota": int(kb * 0.97), "prohibitreceivequota": kb})
    body = {"username": name if "@" in name else f"{name}@{dname}", "properties": props, "status": 0}
    r = _c(w).anfrage("POST", f"/domains/{did}/users", json=body)
    if passwort and r.get("ID"):
        _c(w).anfrage("PUT", f"/domains/{did}/users/{r['ID']}/password", json={"new": passwort})
    return r


# ------------------------------------------------------------------ API
@router.get("/domaenen")
def domaenen(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"domaenen": [{"id": d["ID"], "name": d.get("domainname"), "anzeige": d.get("displayname") or d.get("title"),
                          "max_benutzer": d.get("maxUser"), "benutzer": d.get("activeUsers"), "status": d.get("domainStatus")}
                         for d in _c(w).get("/system/domains", limit=1000, level=1).get("data") or []]}


@router.post("/domaenen")
async def domaene_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("admin"))):
    d = await req.json()
    r = _c(w).anfrage("POST", "/system/domains", params={"createRole": "true"},
                      json={"domainname": d["name"], "maxUser": int(d.get("max_benutzer") or 100),
                            "title": d.get("anzeige") or d["name"], "domainStatus": 0})
    prot(u, "grommunio", "Domäne angelegt", d["name"])
    return r


@router.get("/domaenen/{did}/postfaecher")
def postfaecher(did: int, suche: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    out = []
    for b in _c(w).get(f"/domains/{did}/users", limit=5000, level=2, match=suche or None).get("data") or []:
        p = b.get("properties") or {}
        out.append({"id": b["ID"], "name": b.get("username"), "anzeigename": p.get("displayname", ""),
                    "status": b.get("status"), "ldap": bool(b.get("ldapID")),
                    "quota_mb": (p.get("storagequotalimit") or 0) // 1024 or None,
                    "belegt_mb": round((p.get("messagesizeextended") or 0) / 1048576, 1),
                    "art": {0: "Benutzer", 4: "Freigegeben", 7: "Raum", 8: "Gerät"}.get(p.get("displaytypeex", 0), "Benutzer")})
    return {"postfaecher": out}


@router.post("/domaenen/{did}/postfaecher")
async def postfach_neu(did: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    r = postfach_anlegen(w, did, d["name"], d.get("anzeigename", ""), d.get("passwort", ""), d.get("quota_mb"))
    prot(u, "grommunio", "Postfach angelegt", d["name"])
    return r


@router.patch("/domaenen/{did}/postfaecher/{bid}")
async def postfach_aendern(did: int, bid: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    props = {}
    if "anzeigename" in d:
        props["displayname"] = d["anzeigename"]
    if d.get("quota_mb") is not None:
        kb = int(d["quota_mb"]) * 1024
        props.update({"storagequotalimit": kb, "prohibitsendquota": int(kb * 0.97), "prohibitreceivequota": kb})
    body = {"properties": props} if props else {}
    if "status" in d:
        body["status"] = int(d["status"])
    r = _c(w).anfrage("PATCH", f"/domains/{did}/users/{bid}", json=body)
    prot(u, "grommunio", "Postfach geändert", bid, details=str(list(d)))
    return r


@router.post("/domaenen/{did}/postfaecher/{bid}/passwort")
async def postfach_passwort(did: int, bid: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    _c(w).anfrage("PUT", f"/domains/{did}/users/{bid}/password", json={"new": (await req.json())["passwort"]})
    prot(u, "grommunio", "Passwort gesetzt", bid)
    return {"ok": True}


@router.get("/domaenen/{did}/postfaecher/{bid}/aliase")
def aliase(did: int, bid: int, w=Depends(verb), u=Depends(rolle("leser"))):
    return {"aliase": _c(w).get(f"/domains/{did}/users/{bid}/aliases").get("data") or []}


@router.post("/domaenen/{did}/postfaecher/{bid}/aliase")
async def alias_neu(did: int, bid: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    a = (await req.json())["alias"]
    r = _c(w).anfrage("POST", f"/domains/{did}/users/{bid}/aliases", json=a)
    prot(u, "grommunio", "Alias angelegt", a)
    return r


@router.delete("/domaenen/{did}/postfaecher/{bid}")
def postfach_loeschen(did: int, bid: int, dateien: bool = True, w=Depends(verb), u=Depends(rolle("admin"))):
    _c(w).anfrage("DELETE", f"/domains/{did}/users/{bid}", params={"deleteFiles": str(dateien).lower()})
    prot(u, "grommunio", "Postfach gelöscht", bid)
    return {"ok": True}


@router.get("/ldap/suche")
def ldap_suche(q: str, w=Depends(verb), u=Depends(rolle("leser"))):
    return {"treffer": _c(w).get("/domains/ldap/search", query=q).get("data") or []}


@router.post("/domaenen/{did}/ldap-import")
async def ldap_import(did: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    ldap_id = (await req.json())["id"]
    r = _c(w).anfrage("POST", f"/domains/{did}/ldap/importUser", params={"ID": ldap_id, "force": "false"})
    prot(u, "grommunio", "Aus LDAP übernommen", ldap_id)
    return r


@router.get("/status")
def status(w=Depends(verb), u=Depends(rolle("leser"))):
    d = _c(w).get("/system/dashboard")
    try:
        d["dienste"] = _c(w).get("/system/dashboard/services").get("services")
    except Fehler:
        pass
    return d
