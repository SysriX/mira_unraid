"""Modul Authentik (API v3, Token mit Admin-Rechten).

Benutzer, Gruppen, Anwendungen (OAuth2/OIDC oder Proxy), Zugriff je Gruppe (Policy-Bindings),
Outposts und LDAP-Quellen. Legt bei der Einrichtung auch die Anwendung „MIRA“ für die Anmeldung an.
"""
from __future__ import annotations

import re
import secrets

import httpx
from fastapi import APIRouter, Depends, Request

from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr


class Authentik(Modul):
    typ = "authentik"
    titel = "Authentik"
    beschreibung = "Single Sign-on: Benutzer, Gruppen, Anwendungen, Zugriff je Gruppe, Outposts."
    felder = [
        {"name": "url", "titel": "Adresse", "art": "text", "pflicht": True, "hilfe": "z. B. https://auth.firma.de"},
        {"name": "token", "titel": "API-Token", "art": "password", "pflicht": True,
         "hilfe": "Admin › Verzeichnis › Tokens › Erstellen (Zweck: API), Benutzer mit Admin-Rechten"},
        {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": True},
    ]

    def testen(self, w):
        me = api(w, "GET", "core/users/me/")
        v = api(w, "GET", "admin/version/")
        n = (me.get("user") or {}).get("username", "?")
        return f"Verbunden als {n}, Authentik {v.get('version_current', '?')}"


MODUL = registrieren(Authentik())
router = APIRouter(prefix="/api/m/authentik/{vid}", tags=["authentik"])
MODUL.router = router
verb = verbindung_dep("authentik")


def api(w, methode: str, pfad: str, **kw):
    url = w["url"].rstrip("/") + "/api/v3/" + pfad.lstrip("/")
    try:
        r = httpx.request(methode, url, headers={"Authorization": f"Bearer {w['token']}", "Accept": "application/json"},
                          timeout=20, verify=wahr(w.get("tls_pruefen", True)), **kw)
    except httpx.HTTPError as e:
        raise Fehler(f"Authentik nicht erreichbar: {e}") from e
    if r.status_code == 204:
        return {}
    if r.status_code >= 400:
        try:
            d = r.json()
            text = d.get("detail") or "; ".join(f"{k}: {v}" for k, v in d.items())
        except Exception:  # noqa: BLE001
            text = r.text[:300]
        raise Fehler(f"Authentik {r.status_code}: {text}")
    return r.json() if r.content else {}


def alle(w, pfad: str, **params) -> list:
    out, seite = [], 1
    while True:
        d = api(w, "GET", pfad, params={**params, "page": seite, "page_size": 200})
        out += d.get("results", [])
        if not (d.get("pagination") or {}).get("next"):
            return out
        seite += 1


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "app"


def _flow(w, slug_: str, bezeichnung: str) -> str:
    t = api(w, "GET", "flows/instances/", params={"slug": slug_}).get("results") or []
    if not t:
        t = api(w, "GET", "flows/instances/", params={"designation": bezeichnung}).get("results") or []
    if not t:
        raise Fehler(f"Kein Flow „{slug_}“ in Authentik gefunden")
    return t[0]["pk"]


def _scope_mappings(w) -> list[str]:
    for pfad in ("propertymappings/provider/scope/", "propertymappings/scope/"):
        try:
            res = alle(w, pfad)
        except Fehler:
            continue
        pks = [m["pk"] for m in res if m.get("scope_name") in ("openid", "email", "profile")
               and (m.get("managed") or "").startswith("goauthentik.io/providers/oauth2/scope-")]
        if pks:
            return pks
    return []


def _signier_schluessel(w) -> str | None:
    t = alle(w, "crypto/certificatekeypairs/", has_key="true")
    for k in t:
        if "Self-signed" in k.get("name", ""):
            return k["pk"]
    return t[0]["pk"] if t else None


def oidc_anwendung_anlegen(w, name: str, redirect_uris: list[str], start_url: str = "", gruppen: list[str] | None = None) -> dict:
    """Legt OAuth2-Anbieter + Anwendung an (oder aktualisiert sie). Gibt issuer, client_id, client_secret zurück."""
    s = slug(name)
    autor = _flow(w, "default-provider-authorization-implicit-consent", "authorization")
    try:
        invalid = _flow(w, "default-provider-invalidation-flow", "invalidation")
    except Fehler:
        invalid = None
    geheim = secrets.token_urlsafe(48)
    prov = {
        "name": f"{name} (OIDC)", "authorization_flow": autor, "client_type": "confidential",
        "client_secret": geheim, "redirect_uris": [{"matching_mode": "strict", "url": u} for u in redirect_uris],
        "property_mappings": _scope_mappings(w), "signing_key": _signier_schluessel(w), "sub_mode": "user_username",
        "include_claims_in_id_token": True,
    }
    if invalid:
        prov["invalidation_flow"] = invalid
    vorhanden = [p for p in alle(w, "providers/oauth2/", search=prov["name"]) if p["name"] == prov["name"]]
    if vorhanden:
        p = api(w, "PATCH", f"providers/oauth2/{vorhanden[0]['pk']}/", json=prov)
    else:
        p = api(w, "POST", "providers/oauth2/", json=prov)
    app = {"name": name, "slug": s, "provider": p["pk"], "meta_launch_url": start_url, "policy_engine_mode": "any"}
    apps = [a for a in alle(w, "core/applications/", search=name) if a["slug"] == s]
    if apps:
        api(w, "PATCH", f"core/applications/{s}/", json=app)
        app_pk = apps[0]["pk"]
    else:
        app_pk = api(w, "POST", "core/applications/", json=app)["pk"]
    for g in gruppen or []:
        zugriff_gruppe(w, app_pk, g)
    return {"issuer": w["url"].rstrip("/") + f"/application/o/{s}/", "client_id": p["client_id"],
            "client_secret": geheim, "slug": s}


def gruppe_pk(w, name: str) -> str:
    t = [g for g in alle(w, "core/groups/", search=name) if g["name"].lower() == name.lower()]
    if not t:
        raise Fehler(f"Gruppe „{name}“ gibt es in Authentik nicht (noch nicht aus dem AD abgeglichen?)")
    return t[0]["pk"]


def zugriff_gruppe(w, app_pk: str, gruppe: str) -> None:
    gpk = gruppe_pk(w, gruppe)
    vorhanden = alle(w, "policies/bindings/", target=app_pk)
    if any(b.get("group") == gpk for b in vorhanden):
        return
    api(w, "POST", "policies/bindings/", json={"target": app_pk, "group": gpk, "order": len(vorhanden), "enabled": True,
                                               "negate": False, "timeout": 30})


# ------------------------------------------------------------------ API
def _benutzer(b):
    return {"pk": b["pk"], "name": b["username"], "anzeigename": b.get("name", ""), "mail": b.get("email", ""),
            "aktiv": b.get("is_active", True), "typ": b.get("type", ""), "letzte_anmeldung": b.get("last_login"),
            "gruppen": [g["name"] for g in b.get("groups_obj") or []], "pfad": b.get("path", ""),
            "quelle": (b.get("attributes") or {}).get("distinguishedName") and "ldap" or ""}


@router.get("/benutzer")
def benutzer(suche: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    return {"benutzer": [_benutzer(b) for b in alle(w, "core/users/", search=suche, include_groups="true")]}


@router.post("/benutzer")
async def benutzer_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    neu = api(w, "POST", "core/users/", json={"username": d["name"], "name": d.get("anzeigename") or d["name"],
                                                "email": d.get("mail", ""), "is_active": wahr(d.get("aktiv", True)),
                                                "path": d.get("pfad") or "users", "type": d.get("typ") or "internal"})
    if d.get("passwort"):
        api(w, "POST", f"core/users/{neu['pk']}/set_password/", json={"password": d["passwort"]})
    for g in d.get("gruppen") or []:
        api(w, "POST", f"core/groups/{gruppe_pk(w, g)}/add_user/", json={"pk": neu["pk"]})
    prot(u, "authentik", "Benutzer angelegt", d["name"])
    return _benutzer(neu)


@router.patch("/benutzer/{pk}")
async def benutzer_aendern(pk: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    karte = {"anzeigename": "name", "mail": "email", "aktiv": "is_active", "name": "username"}
    neu = {karte[k]: v for k, v in d.items() if k in karte}
    r = api(w, "PATCH", f"core/users/{pk}/", json=neu)
    prot(u, "authentik", "Benutzer geändert", r.get("username", pk), details=str(list(neu)))
    return {"ok": True}


@router.post("/benutzer/{pk}/passwort")
async def benutzer_passwort(pk: int, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    api(w, "POST", f"core/users/{pk}/set_password/", json={"password": (await req.json())["passwort"]})
    prot(u, "authentik", "Passwort gesetzt", pk)
    return {"ok": True}


@router.post("/benutzer/{pk}/wiederherstellung")
def benutzer_link(pk: int, w=Depends(verb), u=Depends(rolle("operator"))):
    r = api(w, "POST", f"core/users/{pk}/recovery/")
    prot(u, "authentik", "Wiederherstellungs-Link erzeugt", pk)
    return {"link": r.get("link")}


@router.delete("/benutzer/{pk}")
def benutzer_loeschen(pk: int, w=Depends(verb), u=Depends(rolle("admin"))):
    api(w, "DELETE", f"core/users/{pk}/")
    prot(u, "authentik", "Benutzer gelöscht", pk)
    return {"ok": True}


@router.get("/gruppen")
def gruppen(suche: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    return {"gruppen": [{"pk": g["pk"], "name": g["name"], "admin": g.get("is_superuser", False),
                         "mitglieder": len(g.get("users") or []), "eltern": (g.get("parent_name") or ""),
                         "attribute": g.get("attributes") or {}}
                        for g in alle(w, "core/groups/", search=suche)]}


@router.get("/gruppen/{pk}")
def gruppe(pk: str, w=Depends(verb), u=Depends(rolle("leser"))):
    g = api(w, "GET", f"core/groups/{pk}/", params={"include_users": "true"})
    return {"pk": g["pk"], "name": g["name"], "admin": g.get("is_superuser", False),
            "liste": [{"pk": x["pk"], "name": x["username"], "anzeigename": x.get("name", "")} for x in g.get("users_obj") or []]}


@router.post("/gruppen")
async def gruppe_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    g = api(w, "POST", "core/groups/", json={"name": d["name"], "is_superuser": wahr(d.get("admin"))})
    prot(u, "authentik", "Gruppe angelegt", d["name"])
    return {"pk": g["pk"]}


@router.post("/gruppen/{pk}/mitglieder")
async def gruppe_mitglieder(pk: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    for b in d.get("hinzu") or []:
        api(w, "POST", f"core/groups/{pk}/add_user/", json={"pk": int(b)})
    for b in d.get("weg") or []:
        api(w, "POST", f"core/groups/{pk}/remove_user/", json={"pk": int(b)})
    prot(u, "authentik", "Gruppenmitglieder geändert", pk, details=str(d))
    return {"ok": True}


@router.delete("/gruppen/{pk}")
def gruppe_loeschen(pk: str, w=Depends(verb), u=Depends(rolle("admin"))):
    api(w, "DELETE", f"core/groups/{pk}/")
    prot(u, "authentik", "Gruppe gelöscht", pk)
    return {"ok": True}


@router.get("/anwendungen")
def anwendungen(w=Depends(verb), u=Depends(rolle("leser"))):
    apps = alle(w, "core/applications/", superuser_full_list="true")
    bindungen = alle(w, "policies/bindings/")
    gruppen_ = {g["pk"]: g["name"] for g in alle(w, "core/groups/")}
    out = []
    for a in apps:
        bs = [b for b in bindungen if b.get("target") == a["pk"]]
        out.append({"pk": a["pk"], "name": a["name"], "slug": a["slug"], "start_url": a.get("launch_url") or "",
                    "anbieter": (a.get("provider_obj") or {}).get("name", ""),
                    "anbieter_typ": (a.get("provider_obj") or {}).get("component", "").replace("ak-provider-", "").replace("-form", ""),
                    "gruppen": [{"binding": b["pk"], "name": gruppen_.get(b.get("group"), "?")} for b in bs if b.get("group")],
                    "andere_regeln": len([b for b in bs if not b.get("group")])})
    return {"anwendungen": out}


@router.post("/anwendungen")
async def anwendung_anlegen(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    """art = oidc (redirect_uris) oder proxy (extern_url, intern_url, modus proxy|forward_single)."""
    d = await req.json()
    name, art = d["name"].strip(), d.get("art", "oidc")
    if art == "oidc":
        r = oidc_anwendung_anlegen(w, name, [x.strip() for x in d.get("redirect_uris", []) if x.strip()],
                                   d.get("start_url", ""), d.get("gruppen") or [])
        prot(u, "authentik", "OIDC-Anwendung angelegt", name)
        return r
    autor = _flow(w, "default-provider-authorization-implicit-consent", "authorization")
    prov = {"name": f"{name} (Proxy)", "authorization_flow": autor, "external_host": d["extern_url"],
            "mode": d.get("modus") or "proxy", "internal_host": d.get("intern_url", ""),
            "internal_host_ssl_validation": wahr(d.get("intern_tls_pruefen", False)),
            "basic_auth_enabled": wahr(d.get("basic_auth")), "intercept_header_auth": wahr(d.get("header_auth", True))}
    try:
        prov["invalidation_flow"] = _flow(w, "default-provider-invalidation-flow", "invalidation")
    except Fehler:
        pass
    p = api(w, "POST", "providers/proxy/", json=prov)
    app = api(w, "POST", "core/applications/", json={"name": name, "slug": slug(name), "provider": p["pk"],
                                                     "meta_launch_url": d.get("start_url") or d["extern_url"]})
    for g in d.get("gruppen") or []:
        zugriff_gruppe(w, app["pk"], g)
    if wahr(d.get("outpost_eingebettet", True)):
        for o in alle(w, "outposts/instances/"):
            if o.get("managed") == "goauthentik.io/outposts/embedded":
                api(w, "PATCH", f"outposts/instances/{o['pk']}/", json={"providers": list({*o.get("providers", []), p["pk"]})})
    prot(u, "authentik", "Proxy-Anwendung angelegt", name)
    return {"slug": app["slug"]}


@router.post("/anwendungen/{slug_}/zugriff")
async def anwendung_zugriff(slug_: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    app = api(w, "GET", f"core/applications/{slug_}/")
    if d.get("gruppe"):
        zugriff_gruppe(w, app["pk"], d["gruppe"])
    if d.get("binding_weg"):
        api(w, "DELETE", f"policies/bindings/{d['binding_weg']}/")
    prot(u, "authentik", "Zugriff geändert", slug_, details=str(d))
    return {"ok": True}


@router.delete("/anwendungen/{slug_}")
def anwendung_loeschen(slug_: str, anbieter_mit: bool = True, w=Depends(verb), u=Depends(rolle("admin"))):
    app = api(w, "GET", f"core/applications/{slug_}/")
    api(w, "DELETE", f"core/applications/{slug_}/")
    if anbieter_mit and app.get("provider"):
        try:
            api(w, "DELETE", f"providers/all/{app['provider']}/")
        except Fehler:
            pass
    prot(u, "authentik", "Anwendung gelöscht", slug_)
    return {"ok": True}


@router.get("/outposts")
def outposts(w=Depends(verb), u=Depends(rolle("leser"))):
    out = []
    for o in alle(w, "outposts/instances/"):
        try:
            h = api(w, "GET", f"outposts/instances/{o['pk']}/health/")
        except Fehler:
            h = []
        out.append({"pk": o["pk"], "name": o["name"], "typ": o.get("type"), "anbieter": len(o.get("providers") or []),
                    "verbunden": [{"version": x.get("version"), "letzter_kontakt": x.get("last_seen"),
                                   "host": x.get("hostname")} for x in h or []]})
    return {"outposts": out}


@router.get("/quellen")
def quellen(w=Depends(verb), u=Depends(rolle("leser"))):
    out = []
    for q in alle(w, "sources/ldap/"):
        st = {}
        try:
            st = api(w, "GET", f"sources/ldap/{q['slug']}/sync/status/")
        except Fehler:
            pass
        out.append({"name": q["name"], "slug": q["slug"], "aktiv": q.get("enabled"), "server": q.get("server_uri"),
                    "basis": q.get("base_dn"), "sync_benutzer": q.get("sync_users"), "sync_gruppen": q.get("sync_groups"),
                    "status": st})
    return {"quellen": out}
