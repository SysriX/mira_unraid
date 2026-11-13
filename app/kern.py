"""MIRA – Kern: Startkonfiguration, Verschlüsselung, Datenbank, Protokoll.

Startkonfiguration  <MIRA_DATA>/mira.json      (welche Datenbank, ob eingerichtet)
Schlüssel           <MIRA_DATA>/schluessel.key  (oder Umgebungsvariable MIRA_KEY)
Alles andere (Benutzer, Verbindungen, Einstellungen, Protokoll) liegt in der Datenbank.
Geheimnisse (Passwörter, Tokens) werden vor dem Speichern mit Fernet (AES-128-CBC + HMAC) verschlüsselt.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, select, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

VERSION = "0.1.2"
DATA = os.environ.get("MIRA_DATA", "/config")
KONFIG_DATEI = os.path.join(DATA, "mira.json")
SCHLUESSEL_DATEI = os.path.join(DATA, "schluessel.key")


def jetzt() -> datetime:
    return datetime.now(timezone.utc)


def log(*a):
    print("[mira]", *a, flush=True)


# ------------------------------------------------------------------ Startkonfiguration
_konfig_lock = threading.Lock()


def konfig_lesen() -> dict:
    try:
        with open(KONFIG_DATEI, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def konfig_schreiben(k: dict) -> None:
    with _konfig_lock:
        os.makedirs(DATA, exist_ok=True)
        tmp = KONFIG_DATEI + ".neu"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(k, f, indent=2, ensure_ascii=False)
        os.replace(tmp, KONFIG_DATEI)
        try:
            os.chmod(KONFIG_DATEI, 0o600)
        except OSError:
            pass


# ------------------------------------------------------------------ Verschlüsselung
_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    key = os.environ.get("MIRA_KEY", "").strip()
    if not key:
        if os.path.exists(SCHLUESSEL_DATEI):
            with open(SCHLUESSEL_DATEI, encoding="utf-8") as f:
                key = f.read().strip()
        else:
            os.makedirs(DATA, exist_ok=True)
            key = Fernet.generate_key().decode()
            with open(SCHLUESSEL_DATEI, "w", encoding="utf-8") as f:
                f.write(key + "\n")
            try:
                os.chmod(SCHLUESSEL_DATEI, 0o600)
            except OSError:
                pass
            log("Neuer Schlüssel angelegt:", SCHLUESSEL_DATEI, "– mit sichern, sonst sind gespeicherte Passwörter verloren!")
    _fernet = Fernet(key.encode())
    return _fernet


def verschluesseln(daten: Any) -> str:
    return fernet().encrypt(json.dumps(daten).encode()).decode()


def entschluesseln(token: str | None) -> Any:
    if not token:
        return {}
    try:
        return json.loads(fernet().decrypt(token.encode()))
    except InvalidToken as e:
        raise RuntimeError("Gespeicherte Geheimnisse lassen sich nicht entschlüsseln – falscher Schlüssel?") from e


# ------------------------------------------------------------------ Datenbank
class Basis(DeclarativeBase):
    pass


class Benutzer(Basis):
    __tablename__ = "mira_benutzer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)   # sAMAccountName bzw. lokaler Name
    anzeigename: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    quelle: Mapped[str] = mapped_column(String(16), default="lokal")          # lokal | verzeichnis
    rolle: Mapped[str] = mapped_column(String(16), default="leser")           # admin | operator | leser
    pw_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)   # nur lokal
    verbindung_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # AD-Verbindung (bei verzeichnis)
    dn: Mapped[str] = mapped_column(String(512), default="")
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    erstellt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)
    letzte_anmeldung: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Sitzung(Basis):
    __tablename__ = "mira_sitzung"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    benutzer_id: Mapped[int] = mapped_column(ForeignKey("mira_benutzer.id", ondelete="CASCADE"))
    ablauf: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str] = mapped_column(String(64), default="")
    art: Mapped[str] = mapped_column(String(16), default="")


class Verbindung(Basis):
    __tablename__ = "mira_verbindung"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    typ: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    einstellungen: Mapped[dict] = mapped_column(JSON, default=dict)
    geheim: Mapped[str] = mapped_column(Text, default="")       # verschlüsselt (JSON)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(512), default="")
    geprueft: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    geaendert: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)


class Einstellung(Basis):
    __tablename__ = "mira_einstellung"
    schluessel: Mapped[str] = mapped_column(String(64), primary_key=True)
    wert: Mapped[Any] = mapped_column(JSON)


class Protokoll(Basis):
    __tablename__ = "mira_protokoll"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zeit: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt, index=True)
    benutzer: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    bereich: Mapped[str] = mapped_column(String(32), default="")
    aktion: Mapped[str] = mapped_column(String(128), default="")
    ziel: Mapped[str] = mapped_column(String(512), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[str] = mapped_column(Text, default="")


_engine = None
_SessionLocal: sessionmaker | None = None


def db_url(k: dict | None = None) -> str | None:
    k = konfig_lesen() if k is None else k
    db = k.get("db")
    if not db:
        return None
    if db.get("typ") == "sqlite":
        return "sqlite:///" + os.path.join(DATA, db.get("datei") or "mira.db")
    if db.get("typ") == "mariadb":
        from urllib.parse import quote_plus
        pw = entschluesseln(db.get("passwort_enc")).get("p", "") if db.get("passwort_enc") else ""
        return (f"mysql+pymysql://{quote_plus(db['benutzer'])}:{quote_plus(pw)}@{db['host']}:{int(db.get('port') or 3306)}/"
                f"{db['name']}?charset=utf8mb4")
    raise RuntimeError("unbekannter Datenbanktyp")


def engine_bauen(url: str):
    args = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        args["connect_args"] = {"check_same_thread": False}
    else:
        args["pool_recycle"] = 1800
    return create_engine(url, **args)


def db_starten() -> bool:
    """Verbindet mit der konfigurierten Datenbank und legt fehlende Tabellen an. False = noch keine DB."""
    global _engine, _SessionLocal
    url = db_url()
    if not url:
        return False
    _engine = engine_bauen(url)
    Basis.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    return True


def db_bereit() -> bool:
    return _SessionLocal is not None


def db_testen(url: str) -> str:
    e = engine_bauen(url)
    try:
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        return "ok"
    finally:
        e.dispose()


@contextmanager
def sitzung() -> Session:
    if not _SessionLocal:
        raise RuntimeError("Datenbank ist noch nicht eingerichtet")
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def einstellung(schluessel: str, standard: Any = None) -> Any:
    with sitzung() as s:
        e = s.get(Einstellung, schluessel)
        return e.wert if e else standard


def einstellung_setzen(schluessel: str, wert: Any) -> None:
    with sitzung() as s:
        e = s.get(Einstellung, schluessel)
        if e:
            e.wert = wert
        else:
            s.add(Einstellung(schluessel=schluessel, wert=wert))


def protokoll(benutzer: str, bereich: str, aktion: str, ziel: str = "", ok: bool = True, details: str = "",
              ip: str = "") -> None:
    try:
        with sitzung() as s:
            s.add(Protokoll(benutzer=benutzer, bereich=bereich, aktion=aktion, ziel=str(ziel)[:512], ok=ok,
                            details=str(details)[:8000], ip=ip))
    except Exception as e:  # noqa: BLE001  – Protokoll darf nie die eigentliche Aktion abbrechen
        log("Protokoll fehlgeschlagen:", e)


# ------------------------------------------------------------------ Verbindungen
def verbindung_laden(vid: int) -> Verbindung:
    with sitzung() as s:
        v = s.get(Verbindung, vid)
        if not v:
            raise LookupError("Verbindung gibt es nicht")
        s.expunge(v)
        return v


def verbindungen(typ: str | None = None, nur_aktiv: bool = False) -> list[Verbindung]:
    with sitzung() as s:
        q = select(Verbindung).order_by(Verbindung.typ, Verbindung.name)
        if typ:
            q = q.where(Verbindung.typ == typ)
        if nur_aktiv:
            q = q.where(Verbindung.aktiv.is_(True))
        out = list(s.scalars(q))
        for v in out:
            s.expunge(v)
        return out


def zufall(n: int = 32) -> str:
    return secrets.token_urlsafe(n)
