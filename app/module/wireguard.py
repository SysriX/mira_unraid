"""Module WireGuard – manages WireGuard on OPNsense (road-warrior peers + site-to-site links).

Logic lives in the shared package wgcore (canonical copy: VERA repository, app/wgcore).
One MIRA connection = one OPNsense. Data is stored in MIRA's database (tables wg_*).
"""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends

import kern
from anmeldung import rolle
from module import Modul, prot, registrieren, werte
from wgcore.api import build_router, build_share_router
from wgcore.models import Store
from wgcore.service import Service

LEVEL = {"read": "leser", "write": "operator", "admin": "admin"}
LEVEL_OF_ROLE = {"leser": "read", "operator": "write", "admin": "admin"}


class WireGuard(Modul):
    typ = "wireguard"
    titel = "WireGuard"
    beschreibung = "WireGuard auf der OPNsense: Zugänge importieren, neue Geräte anlegen und aktivieren, Standorte verbinden."
    felder = [
        {"name": "url", "titel": "Adresse der OPNsense", "art": "text", "pflicht": True, "hilfe": "z. B. https://<IP_OPNsense> (Port nur, wenn nicht 443)"},
        {"name": "api_key", "titel": "API-Key", "art": "password", "pflicht": True,
         "hilfe": "System › Zugang › Benutzer › API-Schlüssel. Rechte: „VPN: WireGuard“, für Standortverbindungen zusätzlich „Firewall: Rules: API“ und „Firewall: Alias: Edit“"},
        {"name": "api_secret", "titel": "API-Secret", "art": "password", "pflicht": True},
        {"name": "verify_tls", "titel": "Zertifikat prüfen", "art": "bool", "standard": False, "hilfe": "aus bei selbstsigniertem Zertifikat der OPNsense"},
        {"name": "site_name", "titel": "Standortname", "art": "text", "hilfe": "z. B. Haus, Büro – erscheint in Konfigurationen"},
        {"name": "endpoint_host", "titel": "Öffentliche Adresse für Geräte", "art": "text", "hilfe": "DNS-Name/IP, unter der WireGuard von außen erreichbar ist, z. B. vpn.<Domain>"},
        {"name": "client_allowed_ips", "titel": "Standard: erlaubte Netze für Geräte", "art": "text", "hilfe": "z. B. 192.168.0.0/24, 192.168.10.0/24 – leer = Tunnelnetz"},
        {"name": "client_dns", "titel": "Standard: DNS für Geräte", "art": "text"},
    ]

    def testen(self, w):
        s = Service(_store(), w["_id"], _fw(w))
        try:
            return s.test()
        finally:
            s.close()


def _fw(w: dict) -> dict:
    return {**w, "name": w.get("_name", "")}


_lock = threading.Lock()
_cache: dict = {}


def _store() -> Store:
    eng = kern._engine
    if eng is None:
        raise RuntimeError("Datenbank ist noch nicht eingerichtet")
    with _lock:
        if _cache.get("engine") is not eng:
            _cache["engine"] = eng
            _cache["store"] = Store(eng, kern.verschluesseln, lambda t: kern.entschluesseln(t) or "")
        return _cache["store"]


class _Host:
    def store(self):
        return _store()

    def service(self, fw_id: int) -> Service:
        v = kern.verbindung_laden(int(fw_id))
        if v.typ != "wireguard" or not v.aktiv:
            raise LookupError("OPNsense-Verbindung nicht gefunden oder ausgeschaltet")
        w = werte(v)
        return Service(_store(), v.id, _fw(w))

    def role(self, level: str):
        dep = rolle(LEVEL[level])

        def check(u: dict = Depends(dep)) -> dict:
            return {**u, "level": LEVEL_OF_ROLE.get(u["rolle"], "read")}
        return check

    def audit(self, u, action, target="", ok=True, details=""):
        prot(u, "wireguard", action, target, ok, details)


MODUL = registrieren(WireGuard())
_host = _Host()
router = APIRouter()
router.include_router(build_router(_host, "/api/m/wireguard"))
router.include_router(build_share_router(_host, "/wg-share", "MIRA"))
MODUL.router = router
