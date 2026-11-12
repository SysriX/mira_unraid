"""Modul Exchange (on-premises, 2016/2019/SE) über PowerShell-Remoting (WinRM, Bibliothek pypsrp).

Zwei Wege:
  snapin    – WinRM zum Exchange-Server (wsman, NTLM), dort Exchange-Snap-In laden. Funktioniert mit
              Benutzer+Passwort von Linux aus; WinRM muss auf dem Server an sein (winrm quickconfig).
  remoting  – offizieller Endpunkt http(s)://<server>/PowerShell (Konfiguration Microsoft.Exchange).
              Verlangt meist Kerberos; mit Basic nur, wenn am PowerShell-vDir eingeschaltet.
UNGETESTET gegen einen echten Exchange – Befehle sind Standard-Cmdlets der Exchange Management Shell.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr


class Exchange(Modul):
    typ = "exchange"
    titel = "Exchange"
    beschreibung = "Exchange Server: Postfächer, Speicherplatz, Adressen, Berechtigungen, Verteiler, Datenbanken, Warteschlangen."
    felder = [
        {"name": "server", "titel": "Exchange-Server", "art": "text", "pflicht": True, "hilfe": "FQDN, z. B. ex01.firma.local"},
        {"name": "weg", "titel": "Verbindungsart", "art": "select", "standard": "snapin",
         "optionen": [["snapin", "WinRM + Snap-In (NTLM)"], ["remoting", "Exchange-Remoting /PowerShell"]]},
        {"name": "ssl", "titel": "HTTPS (5986)", "art": "bool", "standard": False,
         "hilfe": "ohne HTTPS verschlüsselt WinRM die Nachrichten trotzdem (NTLM/Kerberos)"},
        {"name": "port", "titel": "Port", "art": "number", "standard": "", "hilfe": "leer = 5985/5986 bzw. 80/443 bei Remoting"},
        {"name": "auth", "titel": "Anmeldeverfahren", "art": "select", "standard": "ntlm",
         "optionen": [["ntlm", "NTLM"], ["negotiate", "Negotiate"], ["kerberos", "Kerberos"], ["basic", "Basic"]]},
        {"name": "benutzer", "titel": "Benutzer", "art": "text", "pflicht": True, "hilfe": "DOMÄNE\\admin oder admin@firma.local"},
        {"name": "passwort", "titel": "Passwort", "art": "password", "pflicht": True},
        {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": True},
    ]

    def testen(self, w):
        s = ausfuehren(w, "Get-ExchangeServer", {}, ["Name", "AdminDisplayVersion", "Edition"])
        return "Verbunden: " + ", ".join(f"{x.get('Name')} ({x.get('AdminDisplayVersion')})" for x in s)


MODUL = registrieren(Exchange())
router = APIRouter(prefix="/api/m/exchange/{vid}", tags=["exchange"])
MODUL.router = router
verb = verbindung_dep("exchange")


# ------------------------------------------------------------------ PowerShell
NULL = object()   # ausdrücklich $null übergeben (None = Parameter weglassen)


def _ps(v) -> str:
    """Python-Wert → PowerShell-Literal (sicher gequotet)."""
    if v is NULL:
        return "$null"
    if v is True:
        return "$true"
    if v is False:
        return "$false"
    if v is None:
        return "$null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "@(" + ",".join(_ps(x) for x in v) + ")"
    if isinstance(v, dict):
        return "@{" + ";".join(f"{_ps(k)}={_ps(x)}" for k, x in v.items()) + "}"
    return "'" + str(v).replace("'", "''").replace("‘", "‘‘").replace("’", "’’") + "'"


def _skript(befehl: str, params: dict, props: list[str] | None) -> str:
    p = " ".join(f"-{k}" if v is True and k in _SCHALTER else f"-{k}:{_ps(v)}"
                 for k, v in params.items() if v is not None)
    sel = f" | Select-Object {','.join(props)}" if props else ""
    return ("$ErrorActionPreference='Stop'\n"
            "if (-not (Get-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn -ErrorAction SilentlyContinue)) "
            "{ Add-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn }\n"
            f"$r = @({befehl} {p}{sel})\n"
            "ConvertTo-Json -InputObject @($r | ForEach-Object { $o=[ordered]@{}; foreach($pp in $_.PSObject.Properties)"
            "{ $val=$pp.Value; if($val -ne $null -and -not ($val -is [string] -or $val -is [bool] -or $val -is [int] -or $val -is [long] -or $val -is [datetime]))"
            "{ if($val -is [System.Collections.IEnumerable]){ $val=@($val | ForEach-Object { \"$_\" }) } else { $val=\"$val\" } };"
            " $o[$pp.Name]=$val }; $o }) -Depth 4 -Compress")


_SCHALTER = {"Shared", "Room", "Equipment", "Status", "Confirm", "Force", "Archive", "BypassSecurityGroupManagerCheck"}


def _client(w):
    from pypsrp.client import Client
    ssl = wahr(w.get("ssl"))
    port = int(w.get("port") or (5986 if ssl else 5985))
    return Client(w["server"], username=w["benutzer"], password=w["passwort"], ssl=ssl, port=port,
                  auth=w.get("auth") or "ntlm", cert_validation=wahr(w.get("tls_pruefen", True)),
                  connection_timeout=15, operation_timeout=60, encryption="auto" if ssl else "always")


def _remoting(w, befehl, params, props):
    from pypsrp.powershell import PowerShell, RunspacePool
    from pypsrp.wsman import WSMan
    ssl = wahr(w.get("ssl"))
    wsman = WSMan(w["server"], username=w["benutzer"], password=w["passwort"], ssl=ssl,
                  port=int(w.get("port") or (443 if ssl else 80)), path="PowerShell", auth=w.get("auth") or "kerberos",
                  cert_validation=wahr(w.get("tls_pruefen", True)), connection_timeout=15,
                  encryption="auto" if ssl else "always")
    with RunspacePool(wsman, configuration_name="Microsoft.Exchange") as pool:
        ps = PowerShell(pool)
        ps.add_cmdlet(befehl)
        for k, v in params.items():
            if v is True and k in _SCHALTER:
                ps.add_parameter(k)
            elif v is NULL:
                ps.add_parameter(k, None)
            elif v is not None:
                ps.add_parameter(k, v)
        if props:
            ps.add_cmdlet("Select-Object").add_parameter("Property", props)
        aus = ps.invoke()
        if ps.had_errors:
            raise Fehler("Exchange: " + "; ".join(str(e) for e in ps.streams.error)[:1000])
    out = []
    for o in aus:
        d = {}
        for quelle in (getattr(o, "adapted_properties", {}) or {}, getattr(o, "extended_properties", {}) or {}):
            for k, v in quelle.items():
                d[k] = v if isinstance(v, (str, int, float, bool)) or v is None else (
                    [str(x) for x in v] if isinstance(v, list) else str(v))
        out.append(d if d else {"Wert": str(o)})
    return out


def ausfuehren(w, befehl: str, params: dict, props: list[str] | None = None) -> list[dict]:
    try:
        if (w.get("weg") or "snapin") == "remoting":
            return _remoting(w, befehl, params, props)
        out, streams, fehler = _client(w).execute_ps(_skript(befehl, params, props))
    except Fehler:
        raise
    except Exception as e:  # noqa: BLE001
        raise Fehler(f"Exchange nicht erreichbar oder Anmeldung fehlgeschlagen: {e}") from e
    if fehler:
        raise Fehler("Exchange: " + "; ".join(str(x) for x in streams.error)[:1000])
    out = (out or "").strip()
    if not out:
        return []
    d = json.loads(out)
    return d if isinstance(d, list) else [d]


def skript_ausfuehren(w, skript: str) -> str:
    if (w.get("weg") or "snapin") == "remoting":
        raise Fehler("Freie Skripte gehen nur mit Verbindungsart „WinRM + Snap-In“")
    s = ("if (-not (Get-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn -ErrorAction SilentlyContinue)) "
         "{ Add-PSSnapin Microsoft.Exchange.Management.PowerShell.SnapIn }\n" + skript + " | Out-String -Width 200")
    out, streams, fehler = _client(w).execute_ps(s)
    fehl = "\n".join(str(x) for x in streams.error)
    return (out or "") + (("\nFEHLER:\n" + fehl) if fehler else "")


# ------------------------------------------------------------------ für Abläufe
def postfach_aktivieren(w, identitaet: str, datenbank: str | None = None, alias: str | None = None) -> dict:
    r = ausfuehren(w, "Enable-Mailbox", {"Identity": identitaet, "Database": datenbank or None, "Alias": alias or None},
                   ["DisplayName", "PrimarySmtpAddress", "Database"])
    return r[0] if r else {}


# ------------------------------------------------------------------ API
POSTFACH = ["DisplayName", "Alias", "SamAccountName", "PrimarySmtpAddress", "RecipientTypeDetails", "Database",
            "IssueWarningQuota", "ProhibitSendQuota", "ProhibitSendReceiveQuota", "UseDatabaseQuotaDefaults",
            "HiddenFromAddressListsEnabled", "EmailAddresses", "ForwardingSmtpAddress", "DeliverToMailboxAndForward",
            "Guid"]


@router.get("/postfaecher")
def postfaecher(suche: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    return {"postfaecher": ausfuehren(w, "Get-Mailbox", {"ResultSize": 2000, "Anr": suche or None}, POSTFACH)}


@router.get("/postfaecher/{ident}/statistik")
def statistik(ident: str, w=Depends(verb), u=Depends(rolle("leser"))):
    r = ausfuehren(w, "Get-MailboxStatistics", {"Identity": ident},
                   ["DisplayName", "TotalItemSize", "TotalDeletedItemSize", "ItemCount", "LastLogonTime", "LastLoggedOnUserAccount"])
    return r[0] if r else {}


@router.get("/postfaecher/{ident}/berechtigungen")
def berechtigungen(ident: str, w=Depends(verb), u=Depends(rolle("leser"))):
    voll = ausfuehren(w, "Get-MailboxPermission", {"Identity": ident}, ["User", "AccessRights", "IsInherited", "Deny"])
    return {"vollzugriff": [p for p in voll if str(p.get("IsInherited")).lower() in ("false", "0")
                            and "NT AUTHORITY\\SELF" not in str(p.get("User"))]}


@router.post("/postfaecher/aktivieren")
async def aktivieren(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    r = postfach_aktivieren(w, d["identitaet"], d.get("datenbank"), d.get("alias"))
    prot(u, "exchange", "Postfach aktiviert", d["identitaet"])
    return r


@router.post("/postfaecher/freigegeben")
async def freigegeben(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    art = d.get("art", "Shared")
    if art not in ("Shared", "Room", "Equipment"):
        raise Fehler("Art: Shared, Room oder Equipment")
    r = ausfuehren(w, "New-Mailbox", {art: True, "Name": d["name"], "Alias": d.get("alias") or None,
                                      "PrimarySmtpAddress": d.get("adresse") or None, "Database": d.get("datenbank") or None,
                                      "OrganizationalUnit": d.get("ou") or None}, ["DisplayName", "PrimarySmtpAddress"])
    prot(u, "exchange", f"{art}-Postfach angelegt", d["name"])
    return r[0] if r else {}


@router.post("/postfaecher/{ident}/deaktivieren")
def deaktivieren(ident: str, w=Depends(verb), u=Depends(rolle("admin"))):
    ausfuehren(w, "Disable-Mailbox", {"Identity": ident, "Confirm": False})
    prot(u, "exchange", "Postfach deaktiviert (AD-Konto bleibt)", ident)
    return {"ok": True}


@router.patch("/postfaecher/{ident}")
async def aendern(ident: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    p = {"Identity": ident}
    if any(k in d for k in ("quota_warnung", "quota_senden", "quota_empfangen")):
        if all(not d.get(k) for k in ("quota_warnung", "quota_senden", "quota_empfangen")):
            p["UseDatabaseQuotaDefaults"] = True
        else:
            p.update({"UseDatabaseQuotaDefaults": False, "IssueWarningQuota": d.get("quota_warnung") or "Unlimited",
                      "ProhibitSendQuota": d.get("quota_senden") or "Unlimited",
                      "ProhibitSendReceiveQuota": d.get("quota_empfangen") or "Unlimited"})
    if "ausblenden" in d:
        p["HiddenFromAddressListsEnabled"] = wahr(d["ausblenden"])
    if "weiterleitung" in d:
        p["ForwardingSmtpAddress"] = ("smtp:" + d["weiterleitung"]) if d["weiterleitung"] else NULL
        p["DeliverToMailboxAndForward"] = wahr(d.get("auch_behalten", True))
    adr = {}
    if d.get("adressen_hinzu"):
        adr["add"] = [("smtp:" + a) if ":" not in a else a for a in d["adressen_hinzu"]]
    if d.get("adressen_weg"):
        adr["remove"] = [("smtp:" + a) if ":" not in a else a for a in d["adressen_weg"]]
    if adr:
        p["EmailAddresses"] = adr
    if d.get("primaer"):
        p["PrimarySmtpAddress"] = d["primaer"]
        p["EmailAddressPolicyEnabled"] = False
    ausfuehren(w, "Set-Mailbox", p)
    prot(u, "exchange", "Postfach geändert", ident, details=str(list(d)))
    return {"ok": True}


@router.post("/postfaecher/{ident}/berechtigung")
async def berechtigung(ident: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    recht, wer, weg = d.get("recht", "FullAccess"), d["benutzer"], wahr(d.get("entfernen"))
    if recht == "FullAccess":
        ausfuehren(w, "Remove-MailboxPermission" if weg else "Add-MailboxPermission",
                   {"Identity": ident, "User": wer, "AccessRights": "FullAccess", "Confirm": False if weg else None,
                    "AutoMapping": None if weg else wahr(d.get("automapping", True))})
    elif recht == "SendAs":
        ausfuehren(w, "Remove-ADPermission" if weg else "Add-ADPermission",
                   {"Identity": ident, "User": wer, "ExtendedRights": "Send As", "Confirm": False if weg else None})
    else:
        raise Fehler("Recht: FullAccess oder SendAs")
    prot(u, "exchange", f"Berechtigung {recht} {'entfernt' if weg else 'erteilt'}", ident, details=wer)
    return {"ok": True}


@router.get("/datenbanken")
def datenbanken(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"datenbanken": ausfuehren(w, "Get-MailboxDatabase", {"Status": True},
                                      ["Name", "Server", "Mounted", "DatabaseSize", "AvailableNewMailboxSpace",
                                       "IssueWarningQuota", "ProhibitSendQuota", "LastFullBackup"])}


@router.get("/domaenen")
def domaenen(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"domaenen": ausfuehren(w, "Get-AcceptedDomain", {}, ["Name", "DomainName", "DomainType", "Default"])}


@router.get("/verteiler")
def verteiler(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"verteiler": ausfuehren(w, "Get-DistributionGroup", {"ResultSize": 1000},
                                    ["DisplayName", "Alias", "PrimarySmtpAddress", "GroupType", "ManagedBy"])}


@router.get("/verteiler/{ident}/mitglieder")
def verteiler_mitglieder(ident: str, w=Depends(verb), u=Depends(rolle("leser"))):
    return {"mitglieder": ausfuehren(w, "Get-DistributionGroupMember", {"Identity": ident, "ResultSize": 2000},
                                     ["DisplayName", "PrimarySmtpAddress", "RecipientType"])}


@router.post("/verteiler")
async def verteiler_neu(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    r = ausfuehren(w, "New-DistributionGroup", {"Name": d["name"], "Alias": d.get("alias") or None,
                                                "PrimarySmtpAddress": d.get("adresse") or None,
                                                "OrganizationalUnit": d.get("ou") or None,
                                                "Type": d.get("typ") or "Distribution"}, ["DisplayName", "PrimarySmtpAddress"])
    prot(u, "exchange", "Verteiler angelegt", d["name"])
    return r[0] if r else {}


@router.post("/verteiler/{ident}/mitglieder")
async def verteiler_aendern(ident: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    for m in d.get("hinzu") or []:
        ausfuehren(w, "Add-DistributionGroupMember", {"Identity": ident, "Member": m, "BypassSecurityGroupManagerCheck": True})
    for m in d.get("weg") or []:
        ausfuehren(w, "Remove-DistributionGroupMember", {"Identity": ident, "Member": m, "Confirm": False,
                                                         "BypassSecurityGroupManagerCheck": True})
    prot(u, "exchange", "Verteiler-Mitglieder geändert", ident, details=str(d))
    return {"ok": True}


@router.get("/server")
def server(w=Depends(verb), u=Depends(rolle("leser"))):
    return {
        "server": ausfuehren(w, "Get-ExchangeServer", {}, ["Name", "Edition", "AdminDisplayVersion", "ServerRole", "Site"]),
        "zertifikate": ausfuehren(w, "Get-ExchangeCertificate", {}, ["Thumbprint", "Subject", "NotAfter", "Services", "Status"]),
    }


@router.get("/warteschlangen")
def warteschlangen(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"warteschlangen": ausfuehren(w, "Get-Queue", {}, ["Identity", "DeliveryType", "Status", "MessageCount",
                                                               "NextHopDomain", "LastError"])}


@router.post("/skript")
async def skript(req: Request, w=Depends(verb), u=Depends(rolle("admin"))):
    s = str((await req.json()).get("skript", ""))
    if not s.strip():
        raise Fehler("Skript fehlt")
    try:
        aus = skript_ausfuehren(w, s)
    except Fehler:
        raise
    except Exception as e:  # noqa: BLE001
        raise Fehler(f"Exchange: {e}") from e
    prot(u, "exchange", "Skript ausgeführt", w["_name"], details=s[:4000])
    return {"ausgabe": aus}
