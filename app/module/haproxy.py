"""Modul HAProxy – bindet haproxy-ui an (eigenständiges Produkt, ghcr.io/sysrix/haproxy-ui).

MIRA spricht mit der API von haproxy-ui; dort bleiben Prüfung, Übernahme mit Rücksprung und der
Assistent „Neue Adresse“. In MIRA: Übersicht/Statistik, Dienste-Tabelle, Zertifikate, Unterschiede,
Prüfen/Übernehmen/Zurück, Regeln (Baukasten) ansehen und löschen, Dateien lesen/schreiben.
"""
from __future__ import annotations

import threading

import httpx
from fastapi import APIRouter, Depends, Request

from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr


class HAProxy(Modul):
    typ = "haproxy"
    titel = "HAProxy"
    beschreibung = "Reverse-Proxy über haproxy-ui: Dienste, Statistik, Zertifikate, sicheres Übernehmen."
    felder = [
        {"name": "url", "titel": "Adresse von haproxy-ui", "art": "text", "pflicht": True, "hilfe": "z. B. http://<IP_haproxy-ui>:8080"},
        {"name": "passwort", "titel": "Passwort (UI_PASS)", "art": "password", "pflicht": True},
        {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": True},
    ]

    def testen(self, w):
        z = Client.fuer(w).get("/api/zustand")
        s = z.get("stats") or {}
        return "Verbunden" + (f" – Statistik: {s['fehler']}" if s.get("fehler") else f", {len(s.get('zeilen') or [])} Statistik-Zeilen")


MODUL = registrieren(HAProxy())
router = APIRouter(prefix="/api/m/haproxy/{vid}", tags=["haproxy"])
MODUL.router = router
verb = verbindung_dep("haproxy")


class Client:
    _cache: dict[tuple, "Client"] = {}
    _lock = threading.Lock()

    def __init__(self, w):
        self.basis = w["url"].rstrip("/")
        self.pw = w["passwort"]
        self.c = httpx.Client(timeout=20, verify=wahr(w.get("tls_pruefen", True)), headers={"X-HapUI": "1"})
        self.angemeldet = False

    @classmethod
    def fuer(cls, w) -> "Client":
        k = (w["_id"], w["url"], w["passwort"])
        with cls._lock:
            if k not in cls._cache:
                cls._cache = {kk: v for kk, v in cls._cache.items() if kk[0] != w["_id"]}
                cls._cache[k] = Client(w)
            return cls._cache[k]

    def _anmelden(self):
        r = self.c.post(self.basis + "/api/anmelden", json={"passwort": self.pw})
        if r.status_code != 200:
            raise Fehler(f"haproxy-ui: Anmeldung fehlgeschlagen ({r.status_code}: {_fehler(r)})")
        self.angemeldet = True

    def anfrage(self, methode, pfad, **kw):
        try:
            if not self.angemeldet:
                self._anmelden()
            r = self.c.request(methode, self.basis + pfad, **kw)
            if r.status_code == 401:
                self._anmelden()
                r = self.c.request(methode, self.basis + pfad, **kw)
        except httpx.HTTPError as e:
            raise Fehler(f"haproxy-ui nicht erreichbar: {e}") from e
        if r.status_code >= 400:
            raise Fehler(f"haproxy-ui: {_fehler(r)}")
        return r.json()

    def get(self, pfad, **params):
        return self.anfrage("GET", pfad, params=params)

    def post(self, pfad, daten):
        return self.anfrage("POST", pfad, json=daten)


def _fehler(r):
    try:
        return r.json().get("fehler") or r.text[:200]
    except Exception:  # noqa: BLE001
        return r.text[:200]


@router.get("/zustand")
def zustand(w=Depends(verb), u=Depends(rolle("leser"))):
    z = Client.fuer(w).get("/api/zustand")
    z["ui_url"] = w["url"]
    return z


@router.get("/dienste")
def dienste(w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/dienste")


@router.post("/dienste")
async def dienste_speichern(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    r = Client.fuer(w).post("/api/dienste", {"dienste": d.get("dienste", [])})
    prot(u, "haproxy", "Dienste gespeichert (Entwurf)", w["_name"], details=f"{len(d.get('dienste', []))} Einträge")
    return r


@router.get("/diff")
def diff(w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/diff")


@router.post("/verwerfen")
def verwerfen(w=Depends(verb), u=Depends(rolle("operator"))):
    r = Client.fuer(w).post("/api/verwerfen", {})
    prot(u, "haproxy", "Entwurf verworfen", w["_name"])
    return r


@router.post("/auftrag")
async def auftrag(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    aktion = (await req.json()).get("aktion", "")
    if aktion not in ("pruefen", "uebernehmen", "zurueck", "zertifikate"):
        raise Fehler("unbekannte Aktion")
    if aktion in ("uebernehmen", "zurueck") and u["rolle"] != "admin":
        raise Fehler("Übernehmen und Zurückspringen dürfen nur Admins")
    r = Client.fuer(w).post("/api/auftrag", {"aktion": aktion})
    prot(u, "haproxy", f"Auftrag {aktion}", w["_name"], details=str(r.get("id")))
    return r


@router.get("/auftrag")
def auftrag_stand(w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/auftrag")


@router.get("/dateien")
def dateien(w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/dateien")


@router.get("/datei")
def datei(pfad: str, w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/datei", pfad=pfad)


@router.post("/datei")
async def datei_schreiben(req: Request, w=Depends(verb), u=Depends(rolle("admin"))):
    d = await req.json()
    r = Client.fuer(w).post("/api/datei", {"pfad": d["pfad"], "text": d["text"]})
    prot(u, "haproxy", "Datei geschrieben (Entwurf)", d["pfad"])
    return r


@router.get("/regeln")
def regeln(w=Depends(verb), u=Depends(rolle("leser"))):
    return Client.fuer(w).get("/api/baukasten")


@router.post("/regeln/loeschen")
async def regel_loeschen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    name = (await req.json()).get("name", "")
    r = Client.fuer(w).post("/api/baukasten/loeschen", {"name": name})
    prot(u, "haproxy", "Adresse gelöscht (Entwurf)", name)
    return r
