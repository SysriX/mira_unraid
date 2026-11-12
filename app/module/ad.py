"""Modul Verzeichnis: Windows-AD und Samba-AD über LDAP(S).

Benutzer anlegen/ändern/sperren/löschen/verschieben, Passwörter setzen, Gruppen und Mitgliedschaften,
Organisationseinheiten. Passwörter lassen sich im AD NUR über eine verschlüsselte Verbindung setzen
(LDAPS oder StartTLS).
"""
from __future__ import annotations

import ssl

from fastapi import APIRouter, Depends, Request
from ldap3 import (ALL_ATTRIBUTES, BASE, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, SCHEMA, SIMPLE, SUBTREE, Connection,
                   Server, Tls)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn, parse_dn

import kern
from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr, werte

UAC_DEAKTIVIERT = 0x2
UAC_NORMAL = 0x200
UAC_PW_LAEUFT_NIE_AB = 0x10000
IN_KETTE = "1.2.840.113556.1.4.1941"   # verschachtelte Gruppen (AD und Samba)
TREE_DELETE = "1.2.840.113556.1.4.805"

GRUPPENTYP = {  # (Bereich, Sicherheit) -> groupType
    ("global", True): -2147483646, ("domainlocal", True): -2147483644, ("universal", True): -2147483640,
    ("global", False): 2, ("domainlocal", False): 4, ("universal", False): 8,
}

BENUTZER_ATTR = ["sAMAccountName", "displayName", "givenName", "sn", "mail", "userPrincipalName", "userAccountControl",
                 "memberOf", "lockoutTime", "pwdLastSet", "lastLogonTimestamp", "whenCreated", "description",
                 "telephoneNumber", "mobile", "title", "department", "company", "physicalDeliveryOfficeName",
                 "distinguishedName", "objectGUID", "accountExpires", "proxyAddresses", "homeMDB"]
AENDERBAR = {"displayName", "givenName", "sn", "mail", "description", "telephoneNumber", "mobile", "title",
             "department", "company", "physicalDeliveryOfficeName", "userPrincipalName"}


class AD(Modul):
    typ = "ad"
    titel = "Verzeichnis (AD / Samba)"
    beschreibung = "Windows- oder Samba-Active-Directory über LDAPS: Benutzer, Gruppen, Passwörter, OUs."
    felder = [
        {"name": "server", "titel": "Server", "art": "text", "pflicht": True, "hilfe": "Name oder IP des Domänencontrollers"},
        {"name": "sicherheit", "titel": "Verschlüsselung", "art": "select", "standard": "ldaps",
         "optionen": [["ldaps", "LDAPS (636)"], ["starttls", "StartTLS (389)"], ["keine", "keine (nur lesen!)"]]},
        {"name": "port", "titel": "Port", "art": "number", "standard": "", "hilfe": "leer = 636 bzw. 389"},
        {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": True},
        {"name": "ca", "titel": "CA-Zertifikat (PEM)", "art": "textarea", "hilfe": "nur nötig bei eigener CA"},
        {"name": "base_dn", "titel": "Basis-DN", "art": "text", "pflicht": True, "hilfe": "z. B. DC=firma,DC=local"},
        {"name": "bind_user", "titel": "Dienstkonto", "art": "text", "pflicht": True,
         "hilfe": "z. B. svc_mira@firma.local – braucht Rechte zum Anlegen/Ändern in den gewählten OUs"},
        {"name": "bind_pass", "titel": "Passwort", "art": "password", "pflicht": True},
        {"name": "benutzer_ou", "titel": "OU für neue Benutzer", "art": "text", "hilfe": "leer = CN=Users,<Basis-DN>"},
        {"name": "gruppen_ou", "titel": "OU für neue Gruppen", "art": "text", "hilfe": "leer = CN=Users,<Basis-DN>"},
        {"name": "upn_suffix", "titel": "UPN-Suffix", "art": "text", "hilfe": "z. B. firma.local – leer = aus Basis-DN"},
        {"name": "art", "titel": "Art", "art": "select", "standard": "windows",
         "optionen": [["windows", "Windows-AD"], ["samba", "Samba-AD"]]},
    ]

    def testen(self, w):
        with verbinden(w) as c:
            c.search(w["base_dn"], "(objectClass=domain)", BASE, attributes=["name"])
            n = c.response[0]["attributes"].get("name") if c.response else w["base_dn"]
            wer = c.extend.standard.who_am_i() or w["bind_user"]
        sic = w.get("sicherheit", "ldaps")
        return f"Verbunden mit {n} als {wer} ({sic})" + ("" if sic != "keine" else " – Passwörter setzen geht so nicht")


MODUL = registrieren(AD())
router = APIRouter(prefix="/api/m/ad/{vid}", tags=["ad"])
MODUL.router = router
verb = verbindung_dep("ad")


# ------------------------------------------------------------------ Verbindung
class _Verb:
    def __init__(self, w, user=None, pw=None):
        self.w, self.user, self.pw = w, user, pw
        self.c = None

    def __enter__(self) -> Connection:
        w = self.w
        sic = w.get("sicherheit") or "ldaps"
        tls = None
        if sic != "keine":
            tls = Tls(validate=ssl.CERT_REQUIRED if wahr(w.get("tls_pruefen", True)) else ssl.CERT_NONE,
                      ca_certs_data=(w.get("ca") or None), version=ssl.PROTOCOL_TLS_CLIENT)
        port = int(w.get("port") or (636 if sic == "ldaps" else 389))
        srv = Server(w["server"], port=port, use_ssl=sic == "ldaps", tls=tls, get_info=SCHEMA, connect_timeout=8)
        try:
            c = Connection(srv, user=self.user or w["bind_user"], password=self.pw if self.user else w.get("bind_pass", ""),
                           authentication=SIMPLE, receive_timeout=20, raise_exceptions=False, read_only=False)
            if not c.open():
                raise Fehler(f"Server nicht erreichbar: {c.result}")
            if sic == "starttls" and not c.start_tls():
                raise Fehler(f"StartTLS fehlgeschlagen: {c.result.get('message')}")
            if not c.bind():
                r = c.result or {}
                raise PermissionError(_text(r) if self.user else f"Anmeldung des Dienstkontos fehlgeschlagen: {_text(r)}")
        except LDAPException as e:
            raise Fehler(f"LDAP: {e}") from e
        self.c = c
        return c

    def __exit__(self, *a):
        if self.c:
            try:
                self.c.unbind()
            except Exception:  # noqa: BLE001
                pass


def verbinden(w, user=None, pw=None) -> _Verb:
    return _Verb(w, user, pw)


AD_CODES = {
    "52e": "Benutzer oder Passwort falsch", "525": "Benutzer nicht gefunden", "530": "Anmeldung zu dieser Zeit nicht erlaubt",
    "531": "Anmeldung von diesem Rechner nicht erlaubt", "532": "Passwort abgelaufen", "533": "Konto deaktiviert",
    "701": "Konto abgelaufen", "773": "Passwort muss geändert werden", "775": "Konto gesperrt",
    "0000052D": "Passwort erfüllt die Kennwortrichtlinie nicht (Länge, Komplexität oder Verlauf)",
}


def _text(r: dict) -> str:
    m = str(r.get("message") or "")
    for k, t in AD_CODES.items():
        if f"data {k}" in m or k in m.split(":")[0]:
            return t
    return f"{r.get('description', 'Fehler')}: {m}".strip(": ")


def _ok(c: Connection, was: str):
    r = c.result or {}
    if r.get("result", 0) != 0:
        raise Fehler(f"{was}: {_text(r)}")


def _upn_suffix(w):
    if w.get("upn_suffix"):
        return w["upn_suffix"]
    return ".".join(v for k, v, _ in parse_dn(w["base_dn"]) if k.upper() == "DC")


def _ou(w, art):
    return w.get(f"{art}_ou") or f"CN=Users,{w['base_dn']}"


def _wert(a, k, standard=None):
    v = a.get(k, standard)
    if isinstance(v, list) and k not in ("memberOf", "member", "proxyAddresses", "objectClass"):
        v = v[0] if v else standard
    return v


def _json(v):
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if hasattr(v, "isoformat"):
        return None if v.year < 1602 or v.year > 9000 else v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def _benutzer_aus(e) -> dict:
    a = e["attributes"]
    uac = int(_wert(a, "userAccountControl", 0) or 0)
    lock = _wert(a, "lockoutTime")
    gesperrt = bool(lock) and hasattr(lock, "year") and lock.year > 1601
    return {
        "dn": e["dn"], "sam": _wert(a, "sAMAccountName", ""), "anzeigename": _wert(a, "displayName", "") or "",
        "vorname": _wert(a, "givenName", "") or "", "nachname": _wert(a, "sn", "") or "", "mail": _wert(a, "mail", "") or "",
        "upn": _wert(a, "userPrincipalName", "") or "", "aktiv": not (uac & UAC_DEAKTIVIERT), "gesperrt": gesperrt,
        "pw_laeuft_nie_ab": bool(uac & UAC_PW_LAEUFT_NIE_AB),
        "gruppen": [g for g in (a.get("memberOf") or [])], "letzte_anmeldung": _json(_wert(a, "lastLogonTimestamp")),
        "pw_gesetzt": _json(_wert(a, "pwdLastSet")), "erstellt": _json(_wert(a, "whenCreated")),
        "beschreibung": _wert(a, "description", "") or "", "telefon": _wert(a, "telephoneNumber", "") or "",
        "mobil": _wert(a, "mobile", "") or "", "titel": _wert(a, "title", "") or "",
        "abteilung": _wert(a, "department", "") or "", "firma": _wert(a, "company", "") or "",
        "buero": _wert(a, "physicalDeliveryOfficeName", "") or "", "guid": str(_wert(a, "objectGUID", "") or ""),
        "postfach": bool(_wert(a, "homeMDB")), "adressen": list(a.get("proxyAddresses") or []),
    }


def benutzer_dn(c: Connection, w, sam: str) -> str:
    c.search(w["base_dn"], f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={escape_filter_chars(sam)}))",
             SUBTREE, attributes=["distinguishedName"])
    if not c.response or not [e for e in c.response if e.get("type") == "searchResEntry"]:
        raise Fehler(f"Benutzer „{sam}“ nicht gefunden")
    return [e for e in c.response if e.get("type") == "searchResEntry"][0]["dn"]


def gruppe_dn(c: Connection, w, name: str) -> str:
    if "=" in name and "," in name:
        return name
    c.search(w["base_dn"], f"(&(objectClass=group)(|(sAMAccountName={escape_filter_chars(name)})(cn={escape_filter_chars(name)})))",
             SUBTREE, attributes=["distinguishedName"])
    t = [e for e in c.response if e.get("type") == "searchResEntry"]
    if not t:
        raise Fehler(f"Gruppe „{name}“ nicht gefunden")
    return t[0]["dn"]


def _eintraege(c):
    return [e for e in (c.response or []) if e.get("type") == "searchResEntry"]


def _seiten(c: Connection, base, filt, attrs, scope=SUBTREE, max_=2000):
    gen = c.extend.standard.paged_search(base, filt, scope, attributes=attrs, paged_size=500, generator=True)
    out = []
    for e in gen:
        if e.get("type") == "searchResEntry":
            out.append(e)
            if len(out) >= max_:
                break
    return out


# ------------------------------------------------------------------ für andere Teile von MIRA
def anmeldung_pruefen(vid: int, sam: str, passwort: str | None, gruppe: str) -> dict:
    """Prüft: Konto vorhanden, aktiv, (optional) Mitglied der Gruppe, (optional) Passwort richtig."""
    w = werte(kern.verbindung_laden(vid))
    with verbinden(w) as c:
        try:
            dn = benutzer_dn(c, w, sam)
        except Fehler as e:
            raise PermissionError("Konto im Verzeichnis nicht gefunden") from e
        c.search(dn, "(objectClass=*)", BASE, attributes=BENUTZER_ATTR)
        b = _benutzer_aus(_eintraege(c)[0])
        if not b["aktiv"]:
            raise PermissionError("Konto ist im Verzeichnis deaktiviert")
        if gruppe:
            gdn = gruppe_dn(c, w, gruppe)
            c.search(dn, f"(memberOf:{IN_KETTE}:={escape_filter_chars(gdn)})", BASE, attributes=["cn"])
            if not _eintraege(c):
                raise PermissionError(f"Nicht Mitglied der Zugangsgruppe „{gruppe}“")
    if passwort is not None:
        if not passwort:
            raise PermissionError("Benutzer oder Passwort falsch")
        try:
            with verbinden(w, dn, passwort):
                pass
        except PermissionError as e:
            raise PermissionError(str(e) if "falsch" not in str(e) else "Benutzer oder Passwort falsch") from e
    return b


def benutzer_anlegen(w, d: dict) -> dict:
    sam = str(d.get("sam", "")).strip()
    if not sam or len(sam) > 20 or any(ch in sam for ch in '"/\\[]:;|=,+*?<>@ '):
        raise Fehler("Anmeldename: max. 20 Zeichen, keine Sonderzeichen wie / \\ [ ] : ; | = , + * ? < > @ oder Leerzeichen")
    vor, nach = str(d.get("vorname", "")).strip(), str(d.get("nachname", "")).strip()
    anzeige = str(d.get("anzeigename") or f"{vor} {nach}".strip() or sam).strip()
    ou = d.get("ou") or _ou(w, "benutzer")
    dn = f"CN={escape_rdn(anzeige)},{ou}"
    upn = d.get("upn") or f"{sam}@{_upn_suffix(w)}"
    attrs = {"objectClass": ["top", "person", "organizationalPerson", "user"], "sAMAccountName": sam,
             "userPrincipalName": upn, "displayName": anzeige, "userAccountControl": UAC_NORMAL | UAC_DEAKTIVIERT}
    for k, a in (("vorname", "givenName"), ("nachname", "sn"), ("mail", "mail"), ("beschreibung", "description"),
                 ("telefon", "telephoneNumber"), ("mobil", "mobile"), ("titel", "title"), ("abteilung", "department"),
                 ("firma", "company"), ("buero", "physicalDeliveryOfficeName")):
        if d.get(k):
            attrs[a] = str(d[k]).strip()
    pw = str(d.get("passwort") or "")
    with verbinden(w) as c:
        c.search(w["base_dn"], f"(sAMAccountName={escape_filter_chars(sam)})", SUBTREE, attributes=["cn"])
        if _eintraege(c):
            raise Fehler(f"„{sam}“ gibt es schon")
        c.add(dn, attributes=attrs)
        _ok(c, "Anlegen")
        try:
            if pw:
                if (w.get("sicherheit") or "ldaps") == "keine":
                    raise Fehler("Passwort setzen geht nur mit LDAPS/StartTLS")
                c.extend.microsoft.modify_password(dn, pw)
                _ok(c, "Passwort")
            uac = UAC_NORMAL | (UAC_PW_LAEUFT_NIE_AB if wahr(d.get("pw_laeuft_nie_ab")) else 0)
            if not wahr(d.get("aktiv", True)) or not pw:
                uac |= UAC_DEAKTIVIERT
            c.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [uac])]})
            _ok(c, "Aktivieren")
            if wahr(d.get("muss_aendern")):
                c.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, [0])]})
                _ok(c, "Passwortwechsel erzwingen")
        except Exception:
            c.delete(dn)    # halb angelegte Konten nicht liegen lassen
            raise
        gruppen = [gruppe_dn(c, w, g) for g in (d.get("gruppen") or []) if g]
        if gruppen:
            c.extend.microsoft.add_members_to_groups([dn], gruppen)
            _ok(c, "Gruppen")
    return {"dn": dn, "sam": sam, "upn": upn, "anzeigename": anzeige, "aktiv": not (uac & UAC_DEAKTIVIERT)}


def gruppe_sicherstellen(w, name: str, beschreibung: str = "", ou: str | None = None) -> tuple[str, bool]:
    with verbinden(w) as c:
        try:
            return gruppe_dn(c, w, name), False
        except Fehler:
            pass
        dn = f"CN={escape_rdn(name)},{ou or _ou(w, 'gruppen')}"
        attrs = {"objectClass": ["top", "group"], "sAMAccountName": name, "groupType": GRUPPENTYP[("global", True)]}
        if beschreibung:
            attrs["description"] = beschreibung
        c.add(dn, attributes=attrs)
        _ok(c, "Gruppe anlegen")
        return dn, True


def mitglied_hinzu(w, gruppe: str, sam: str) -> None:
    with verbinden(w) as c:
        g = gruppe_dn(c, w, gruppe)
        u = benutzer_dn(c, w, sam)
        c.search(u, f"(memberOf={escape_filter_chars(g)})", BASE, attributes=["cn"])
        if _eintraege(c):
            return
        c.extend.microsoft.add_members_to_groups([u], [g])
        _ok(c, "Mitglied hinzufügen")


def benutzer_info(w, sam: str) -> dict:
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        c.search(dn, "(objectClass=*)", BASE, attributes=BENUTZER_ATTR)
        return _benutzer_aus(_eintraege(c)[0])


# ------------------------------------------------------------------ API: Benutzer
@router.get("/benutzer")
def liste(suche: str = "", ou: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    f = "(&(objectCategory=person)(objectClass=user)"
    if suche:
        s = escape_filter_chars(suche)
        f += f"(|(sAMAccountName=*{s}*)(displayName=*{s}*)(mail=*{s}*)(givenName=*{s}*)(sn=*{s}*))"
    f += ")"
    with verbinden(w) as c:
        return {"benutzer": sorted((_benutzer_aus(e) for e in _seiten(c, ou or w["base_dn"], f, BENUTZER_ATTR)),
                                   key=lambda b: (b["anzeigename"] or b["sam"]).lower())}


@router.get("/benutzer/{sam}")
def ein_benutzer(sam: str, w=Depends(verb), u=Depends(rolle("leser"))):
    return benutzer_info(w, sam)


@router.post("/benutzer")
async def anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    try:
        r = benutzer_anlegen(w, d)
    except Exception as e:
        prot(u, "ad", "Benutzer anlegen", d.get("sam", ""), False, str(e))
        raise
    prot(u, "ad", "Benutzer angelegt", r["dn"], details=f"Gruppen: {d.get('gruppen') or []}")
    return r


@router.patch("/benutzer/{sam}")
async def aendern(sam: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    karte = {"anzeigename": "displayName", "vorname": "givenName", "nachname": "sn", "mail": "mail",
             "beschreibung": "description", "telefon": "telephoneNumber", "mobil": "mobile", "titel": "title",
             "abteilung": "department", "firma": "company", "buero": "physicalDeliveryOfficeName", "upn": "userPrincipalName"}
    mods = {}
    for k, v in d.items():
        a = karte.get(k)
        if not a:
            continue
        v = str(v or "").strip()
        mods[a] = [(MODIFY_REPLACE, [v])] if v else [(MODIFY_REPLACE, [])]
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        if mods:
            c.modify(dn, mods)
            _ok(c, "Ändern")
        if "pw_laeuft_nie_ab" in d:
            c.search(dn, "(objectClass=*)", BASE, attributes=["userAccountControl"])
            uac = int(_wert(_eintraege(c)[0]["attributes"], "userAccountControl", UAC_NORMAL))
            uac = uac | UAC_PW_LAEUFT_NIE_AB if wahr(d["pw_laeuft_nie_ab"]) else uac & ~UAC_PW_LAEUFT_NIE_AB
            c.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [uac])]})
            _ok(c, "Ändern")
    prot(u, "ad", "Benutzer geändert", dn, details=", ".join(mods))
    return {"ok": True}


@router.post("/benutzer/{sam}/passwort")
async def passwort(sam: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    pw = str(d.get("passwort", ""))
    if not pw:
        raise Fehler("Passwort fehlt")
    if (w.get("sicherheit") or "ldaps") == "keine":
        raise Fehler("Passwort setzen geht nur mit LDAPS/StartTLS")
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        c.extend.microsoft.modify_password(dn, pw)
        _ok(c, "Passwort")
        c.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, [0 if wahr(d.get("muss_aendern")) else -1])]})
        _ok(c, "Passwortwechsel")
        if wahr(d.get("entsperren", True)):
            c.extend.microsoft.unlock_account(dn)
    prot(u, "ad", "Passwort gesetzt", dn, details="muss ändern" if wahr(d.get("muss_aendern")) else "")
    return {"ok": True}


@router.post("/benutzer/{sam}/status")
async def status(sam: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    aktiv = wahr((await req.json()).get("aktiv"))
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        c.search(dn, "(objectClass=*)", BASE, attributes=["userAccountControl"])
        uac = int(_wert(_eintraege(c)[0]["attributes"], "userAccountControl", UAC_NORMAL))
        uac = uac & ~UAC_DEAKTIVIERT if aktiv else uac | UAC_DEAKTIVIERT
        c.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [uac])]})
        _ok(c, "Status")
    prot(u, "ad", "Benutzer aktiviert" if aktiv else "Benutzer deaktiviert", dn)
    return {"ok": True}


@router.post("/benutzer/{sam}/entsperren")
def entsperren(sam: str, w=Depends(verb), u=Depends(rolle("operator"))):
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        c.extend.microsoft.unlock_account(dn)
        _ok(c, "Entsperren")
    prot(u, "ad", "Benutzer entsperrt", dn)
    return {"ok": True}


@router.post("/benutzer/{sam}/verschieben")
async def verschieben(sam: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    ziel = str((await req.json()).get("ou", ""))
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        erster = parse_dn(dn)[0]
        rdn = f"{erster[0]}={erster[1]}"
        c.modify_dn(dn, rdn, new_superior=ziel)
        _ok(c, "Verschieben")
    prot(u, "ad", "Benutzer verschoben", dn, details=ziel)
    return {"ok": True}


@router.post("/benutzer/{sam}/gruppen")
async def benutzer_gruppen(sam: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        hinzu = [gruppe_dn(c, w, g) for g in d.get("hinzu") or []]
        weg = [gruppe_dn(c, w, g) for g in d.get("weg") or []]
        if hinzu:
            c.extend.microsoft.add_members_to_groups([dn], hinzu)
            _ok(c, "Gruppen hinzufügen")
        if weg:
            c.extend.microsoft.remove_members_from_groups([dn], weg)
            _ok(c, "Gruppen entfernen")
    prot(u, "ad", "Gruppen geändert", dn, details=f"+{hinzu} -{weg}")
    return {"ok": True}


@router.delete("/benutzer/{sam}")
def loeschen(sam: str, w=Depends(verb), u=Depends(rolle("admin"))):
    with verbinden(w) as c:
        dn = benutzer_dn(c, w, sam)
        c.delete(dn, controls=[(TREE_DELETE, True, None)])
        _ok(c, "Löschen")
    prot(u, "ad", "Benutzer gelöscht", dn)
    return {"ok": True}


# ------------------------------------------------------------------ API: Gruppen
def _gruppe_aus(e):
    a = e["attributes"]
    gt = int(_wert(a, "groupType", 0) or 0)
    bereich = {2: "global", 4: "domainlocal", 8: "universal"}.get(gt & 0xE, "?")
    return {"dn": e["dn"], "name": _wert(a, "sAMAccountName", "") or _wert(a, "cn", ""), "cn": _wert(a, "cn", ""),
            "beschreibung": _wert(a, "description", "") or "", "mail": _wert(a, "mail", "") or "",
            "sicherheit": gt < 0, "bereich": bereich, "mitglieder": len(a.get("member") or [])}


@router.get("/gruppen")
def gruppen(suche: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    f = "(objectClass=group)"
    if suche:
        s = escape_filter_chars(suche)
        f = f"(&(objectClass=group)(|(cn=*{s}*)(sAMAccountName=*{s}*)(description=*{s}*)))"
    with verbinden(w) as c:
        es = _seiten(c, w["base_dn"], f, ["cn", "sAMAccountName", "description", "groupType", "member", "mail"])
        return {"gruppen": sorted((_gruppe_aus(e) for e in es), key=lambda g: g["name"].lower())}


@router.get("/gruppen/{name}")
def gruppe(name: str, w=Depends(verb), u=Depends(rolle("leser"))):
    with verbinden(w) as c:
        dn = gruppe_dn(c, w, name)
        c.search(dn, "(objectClass=*)", BASE, attributes=["cn", "sAMAccountName", "description", "groupType", "member", "mail"])
        g = _gruppe_aus(_eintraege(c)[0])
        mitglieder = []
        for m in (_eintraege(c)[0]["attributes"].get("member") or []):
            c.search(m, "(objectClass=*)", BASE, attributes=["sAMAccountName", "displayName", "objectClass", "cn"])
            for e in _eintraege(c):
                a = e["attributes"]
                mitglieder.append({"dn": e["dn"], "name": _wert(a, "sAMAccountName", "") or _wert(a, "cn", ""),
                                   "anzeigename": _wert(a, "displayName", "") or _wert(a, "cn", ""),
                                   "art": "gruppe" if "group" in (a.get("objectClass") or []) else "benutzer"})
        g["liste"] = sorted(mitglieder, key=lambda m: (m["art"], m["anzeigename"].lower()))
        return g


@router.post("/gruppen")
async def gruppe_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    name = str(d.get("name", "")).strip()
    if not name:
        raise Fehler("Name fehlt")
    dn = f"CN={escape_rdn(name)},{d.get('ou') or _ou(w, 'gruppen')}"
    attrs = {"objectClass": ["top", "group"], "sAMAccountName": name,
             "groupType": GRUPPENTYP[(d.get("bereich") or "global", d.get("art", "sicherheit") == "sicherheit")]}
    for k, a in (("beschreibung", "description"), ("mail", "mail"), ("info", "info")):
        if d.get(k):
            attrs[a] = str(d[k])
    with verbinden(w) as c:
        c.add(dn, attributes=attrs)
        _ok(c, "Gruppe anlegen")
    prot(u, "ad", "Gruppe angelegt", dn)
    return {"dn": dn}


@router.patch("/gruppen/{name}")
async def gruppe_aendern(name: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    with verbinden(w) as c:
        dn = gruppe_dn(c, w, name)
        mods = {}
        for k, a in (("beschreibung", "description"), ("mail", "mail")):
            if k in d:
                mods[a] = [(MODIFY_REPLACE, [str(d[k])] if d[k] else [])]
        if mods:
            c.modify(dn, mods)
            _ok(c, "Gruppe ändern")
    prot(u, "ad", "Gruppe geändert", dn)
    return {"ok": True}


@router.post("/gruppen/{name}/mitglieder")
async def mitglieder(name: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    with verbinden(w) as c:
        dn = gruppe_dn(c, w, name)

        def aufloesen(x):
            if "=" in x:
                return x
            try:
                return benutzer_dn(c, w, x)
            except Fehler:
                return gruppe_dn(c, w, x)
        hinzu = [aufloesen(x) for x in d.get("hinzu") or []]
        weg = [aufloesen(x) for x in d.get("weg") or []]
        if hinzu:
            c.modify(dn, {"member": [(MODIFY_ADD, hinzu)]})
            _ok(c, "Mitglieder hinzufügen")
        if weg:
            c.modify(dn, {"member": [(MODIFY_DELETE, weg)]})
            _ok(c, "Mitglieder entfernen")
    prot(u, "ad", "Gruppenmitglieder geändert", dn, details=f"+{hinzu} -{weg}")
    return {"ok": True}


@router.delete("/gruppen/{name}")
def gruppe_loeschen(name: str, w=Depends(verb), u=Depends(rolle("admin"))):
    with verbinden(w) as c:
        dn = gruppe_dn(c, w, name)
        c.delete(dn)
        _ok(c, "Gruppe löschen")
    prot(u, "ad", "Gruppe gelöscht", dn)
    return {"ok": True}


# ------------------------------------------------------------------ API: OUs, Domäne
@router.get("/ous")
def ous(w=Depends(verb), u=Depends(rolle("leser"))):
    with verbinden(w) as c:
        es = _seiten(c, w["base_dn"], "(|(objectClass=organizationalUnit)(&(objectClass=container)(cn=Users)))",
                     ["ou", "cn", "description"])
    out = [{"dn": e["dn"], "name": _wert(e["attributes"], "ou") or _wert(e["attributes"], "cn") or e["dn"],
            "beschreibung": _wert(e["attributes"], "description", "") or ""} for e in es]
    return {"ous": sorted(out, key=lambda o: list(reversed([p[1].lower() for p in parse_dn(o["dn"])])))}


@router.post("/ous")
async def ou_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    name = str(d.get("name", "")).strip()
    if not name:
        raise Fehler("Name fehlt")
    dn = f"OU={escape_rdn(name)},{d.get('uebergeordnet') or w['base_dn']}"
    with verbinden(w) as c:
        c.add(dn, attributes={"objectClass": ["top", "organizationalUnit"], **({"description": d["beschreibung"]}
                                                                              if d.get("beschreibung") else {})})
        _ok(c, "OU anlegen")
    prot(u, "ad", "OU angelegt", dn)
    return {"dn": dn}


@router.get("/domaene")
def domaene(w=Depends(verb), u=Depends(rolle("leser"))):
    with verbinden(w) as c:
        c.search(w["base_dn"], "(objectClass=*)", BASE, attributes=ALL_ATTRIBUTES)
        a = _eintraege(c)[0]["attributes"] if _eintraege(c) else {}

        def tage(x):
            try:
                return abs(x.days) if hasattr(x, "days") else abs(int(x)) // 864_000_000_000
            except Exception:  # noqa: BLE001
                return None
        return {
            "name": _wert(a, "name", ""), "dn": w["base_dn"],
            "pw_min_laenge": _wert(a, "minPwdLength"), "pw_verlauf": _wert(a, "pwdHistoryLength"),
            "pw_max_alter_tage": tage(_wert(a, "maxPwdAge")), "pw_komplex": bool(int(_wert(a, "pwdProperties", 0) or 0) & 1),
            "sperre_schwelle": _wert(a, "lockoutThreshold"), "upn_suffix": _upn_suffix(w),
        }
