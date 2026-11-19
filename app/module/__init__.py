"""Module von MIRA. Jedes Modul beschreibt seine Verbindungsfelder, kann eine Verbindung testen
und bringt eigene API-Routen unter /api/m/<typ>/<verbindung_id>/... mit."""
from __future__ import annotations

from typing import Any

from kern import Verbindung, entschluesseln


class Modul:
    typ = ""
    titel = ""
    beschreibung = ""
    # Felder des Verbindungsformulars:
    # {name, titel, art: text|password|number|bool|select|textarea, standard, hilfe, optionen, pflicht}
    # art=password wird verschlüsselt gespeichert und nie wieder an den Browser geschickt.
    felder: list[dict] = []
    router = None

    def geheime_felder(self) -> set[str]:
        return {f["name"] for f in self.felder if f.get("art") == "password"}

    def testen(self, w: dict) -> str:
        """Verbindung prüfen; gibt eine kurze Erfolgsmeldung zurück oder wirft eine Ausnahme."""
        raise NotImplementedError

    def beschreibung_json(self) -> dict:
        return {"typ": self.typ, "titel": self.titel, "beschreibung": self.beschreibung, "felder": self.felder}


def werte(v: Verbindung) -> dict[str, Any]:
    """Einstellungen + entschlüsselte Geheimnisse einer Verbindung zusammen."""
    w = dict(v.einstellungen or {})
    w.update(entschluesseln(v.geheim))
    w["_id"] = v.id
    w["_name"] = v.name
    return w


def wahr(x) -> bool:
    return x is True or str(x).lower() in ("1", "true", "ja", "on", "yes")


def verbindung_dep(typ: str):
    """FastAPI-Abhängigkeit: lädt die Verbindung aus dem Pfad-Parameter vid (muss zum Typ passen)."""
    from fastapi import HTTPException

    import kern

    def laden(vid: int) -> dict:
        try:
            v = kern.verbindung_laden(vid)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        if v.typ != typ:
            raise HTTPException(400, "Verbindung hat einen anderen Typ")
        if not v.aktiv:
            raise HTTPException(409, f"Verbindung „{v.name}“ ist ausgeschaltet")
        return werte(v)
    return laden


def prot(u: dict, bereich: str, aktion: str, ziel: str = "", ok: bool = True, details: str = "") -> None:
    import kern
    kern.protokoll(u.get("name", "?"), bereich, aktion, ziel, ok, details, u.get("ip", ""))


class Fehler(Exception):
    """Fachlicher Fehler eines Moduls – wird als 400 mit Text an die Oberfläche gegeben."""


REGISTER: dict[str, Modul] = {}


def registrieren(m: Modul) -> Modul:
    REGISTER[m.typ] = m
    return m


def laden():
    # Reihenfolge = Reihenfolge in der Oberfläche
    from module import ad, authentik, haproxy, nginx, grommunio, exchange  # noqa: F401
    return REGISTER
