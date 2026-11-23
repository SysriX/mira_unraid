"""HAProxy servers with access "opnsense": the os-haproxy plugin on an OPNsense, over its REST API.

Auth: API key + secret (HTTP Basic), System › Access › Users › API keys. The user needs the
privileges "Services: HAProxy" (settings + service) – no full admin needed.
Objects: frontends, backends, servers, ACLs, actions (rules) – search/get/add/set/del/toggle.
Saving only writes config.xml; nothing changes until "Anwenden":
    configtest (haproxy -c on the staging file) → only if clean: service/reconfigure → export/diff empty?
Routes: /api/srv/<server id>/opn/...
Written against os-haproxy 5.x (model 5.0.0); field names are passed through, so the form adapts.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, Request

from anmeldung import rolle
from module import Fehler, prot, wahr
from server_store import server_dep

FIELDS = [
    {"name": "url", "titel": "Adresse der OPNsense", "art": "text", "pflicht": True, "hilfe": "z. B. https://<IP_OPNsense> (ggf. mit Port der Weboberfläche)"},
    {"name": "api_key", "titel": "API-Key", "art": "text", "pflicht": True},
    {"name": "api_secret", "titel": "API-Secret", "art": "password", "pflicht": True,
     "hilfe": "System › Zugang › Benutzer › API-Schlüssel; Rechte „Services: HAProxy“ genügen"},
    {"name": "tls_pruefen", "titel": "Zertifikat prüfen", "art": "bool", "standard": False},
]

# MIRA name → (API object name, plural for search)
OBJECTS = {
    "frontend": ("frontend", "Frontends"),
    "backend": ("backend", "Backends"),
    "server": ("server", "Servers"),
    "acl": ("acl", "Acls"),
    "action": ("action", "Actions"),
}
TOGGLE = {"frontend", "backend", "server", "action"}
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _base(w) -> str:
    u = (w.get("url") or "").strip().rstrip("/")
    if not u:
        raise Fehler("Adresse der OPNsense fehlt")
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u + "/api/haproxy"


def api(w, method: str, path: str, data=None, timeout: float = 30):
    """One API call. POST without data sends {} (an empty body with JSON content type is rejected)."""
    kw = {}
    if method == "POST":
        kw["json"] = data if data is not None else {}
    try:
        with httpx.Client(verify=wahr(w.get("tls_pruefen", False)), timeout=timeout,
                          auth=(w.get("api_key") or "", w.get("api_secret") or "")) as c:
            r = c.request(method, _base(w) + path, **kw)
    except httpx.HTTPError as e:
        raise Fehler(f"OPNsense nicht erreichbar: {type(e).__name__}: {e}") from e
    if r.status_code == 401:
        raise Fehler("OPNsense: API-Key/Secret falsch")
    if r.status_code == 403:
        raise Fehler("OPNsense: dem API-Benutzer fehlen Rechte (Services: HAProxy)")
    if r.status_code == 404:
        raise Fehler("OPNsense: HAProxy-Plugin (os-haproxy) nicht installiert oder Pfad unbekannt")
    try:
        d = r.json() if r.content else None
    except ValueError:
        d = None
    if r.status_code >= 400:
        msg = (d or {}).get("message") or (d or {}).get("errorMessage") if isinstance(d, dict) else None
        raise Fehler(f"OPNsense {r.status_code}: {msg or r.text[:200]}")
    if isinstance(d, dict) and d.get("errorMessage"):
        raise Fehler(f"OPNsense: {d['errorMessage']}")
    return d


def _check_result(d, what: str):
    if isinstance(d, dict) and d.get("result") == "failed":
        val = d.get("validations") or {}
        lines = [f"{k.split('.', 1)[-1]}: {', '.join(v) if isinstance(v, list) else v}" for k, v in val.items()]
        raise Fehler(f"OPNsense lehnt {what} ab" + (":\n" + "\n".join(lines) if lines else ""))
    return d


def configtest(w) -> tuple[bool, str]:
    out = str((api(w, "POST", "/service/configtest", timeout=60) or {}).get("result") or "")
    bad = "[ALERT]" in out or "Fatal" in out
    return not bad, out.strip() or "keine Ausgabe"


def test(w) -> str:
    st = (api(w, "GET", "/service/status") or {}).get("status", "unknown")
    n = (api(w, "GET", "/settings/searchFrontends?rowCount=-1") or {}).get("total", 0)
    return f"HAProxy {_status_text(st)}, {n} Frontends"


def _status_text(st):
    return {"running": "läuft", "stopped": "gestoppt", "disabled": "ausgeschaltet"}.get(st, st)


def summary(w) -> dict:
    st = (api(w, "GET", "/service/status") or {}).get("status", "unknown")
    counters = _counters(w)
    sv = [r for r in counters if r.get("svname") not in ("FRONTEND", "BACKEND")]
    down = [r for r in sv if not str(r.get("status", "")).upper().startswith(("UP", "NO CHECK", "OPEN"))]
    return {"running": st == "running", "status": _status_text(st), "servers_up": len(sv) - len(down), "servers": len(sv)}


def _counters(w) -> list:
    try:
        d = api(w, "GET", "/statistics/counters")
    except Fehler:
        return []
    return [r for r in d if isinstance(r, dict) and r.get("id")] if isinstance(d, list) else []


def reconfigure(w) -> dict:
    ok, out = configtest(w)
    if not ok:
        raise Fehler("Prüfung fehlgeschlagen – nichts übernommen:\n" + out)
    r = api(w, "POST", "/service/reconfigure", timeout=120) or {}
    if r.get("status") != "ok":
        raise Fehler(f"Übernehmen fehlgeschlagen: {r.get('status_msg') or r}")
    diff = str((api(w, "GET", "/export/diff") or {}).get("response") or "").strip()
    return {"ok": True, "pruefung": out, "rest_diff": diff,
            "meldung": "übernommen" if not diff else "übernommen, aber die laufende Konfiguration weicht noch ab (siehe Unterschiede)"}


# ------------------------------------------------------------------ routes
router = APIRouter(prefix="/api/srv/{sid}/opn", tags=["opnsense-haproxy"])
verb = server_dep("haproxy", ("opnsense",))


def _obj(kind: str) -> tuple[str, str]:
    if kind not in OBJECTS:
        raise Fehler("unbekannte Objektart")
    return OBJECTS[kind]


def _uuid(uuid: str) -> str:
    if not UUID_RE.match(uuid):
        raise Fehler("ungültige UUID")
    return uuid


def _flat(obj: dict) -> dict:
    """get-shape → {field: value} for the form: option fields become {"options": [[key,label],...], "selected": [...]}"""
    out = {}
    for k, v in (obj or {}).items():
        if isinstance(v, dict) and all(isinstance(x, dict) and "selected" in x for x in v.values()):
            out[k] = {"options": [[key, str(x.get("value", key))] for key, x in v.items()],
                      "selected": [key for key, x in v.items() if str(x.get("selected")) == "1"]}
        elif isinstance(v, (str, int, float)) or v is None:
            out[k] = "" if v is None else str(v)
    return out


@router.get("/status")
def status(w=Depends(verb), u=Depends(rolle("leser"))):
    st = (api(w, "GET", "/service/status") or {}).get("status", "unknown")
    info = {}
    try:
        x = api(w, "GET", "/statistics/info")
        info = x if isinstance(x, dict) else {}
    except Fehler:
        pass
    general = ((api(w, "GET", "/settings/get") or {}).get("haproxy") or {}).get("general") or {}
    return {"status": st, "status_text": _status_text(st), "enabled": str(general.get("enabled")) == "1",
            "info": {k: info.get(k) for k in ("Version", "Uptime", "Uptime_sec", "CurrConns", "MaxConn", "CumReq", "Nbthread") if k in info},
            "counters": _counters(w), "ui_url": (w.get("url") or "").rstrip("/") + "/ui/haproxy"}


@router.get("/objects/{kind}")
def objects(kind: str, w=Depends(verb), u=Depends(rolle("leser"))):
    _, plural = _obj(kind)
    d = api(w, "GET", f"/settings/search{plural}?rowCount=-1") or {}
    return {"rows": d.get("rows") or [], "total": d.get("total", 0)}


@router.get("/objects/{kind}/{uuid}")
def object_get(kind: str, uuid: str, w=Depends(verb), u=Depends(rolle("leser"))):
    name, _ = _obj(kind)
    path = f"/settings/get{name.capitalize()}" + ("" if uuid == "neu" else "/" + _uuid(uuid))
    d = api(w, "GET", path)
    if not isinstance(d, dict) or name not in d:
        raise LookupError(f"{kind} {uuid} gibt es nicht")
    return {"fields": _flat(d[name])}


def _clean(data: dict) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if not re.match(r"^[A-Za-z0-9_]+$", str(k)):
            continue
        if isinstance(v, bool):
            v = "1" if v else "0"
        elif isinstance(v, list):
            v = ",".join(str(x) for x in v)
        out[k] = "" if v is None else str(v)
    return out


@router.post("/objects/{kind}")
async def object_add(kind: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    name, _ = _obj(kind)
    data = _clean((await req.json()).get("fields"))
    r = _check_result(api(w, "POST", f"/settings/add{name.capitalize()}", {name: data}), f"{kind} anlegen")
    prot(u, "haproxy", f"OPNsense: {kind} angelegt (gespeichert, nicht übernommen)", f"{w['_name']}: {data.get('name', '')}")
    return {"ok": True, "uuid": r.get("uuid")}


@router.put("/objects/{kind}/{uuid}")
async def object_set(kind: str, uuid: str, req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    name, _ = _obj(kind)
    data = _clean((await req.json()).get("fields"))
    _check_result(api(w, "POST", f"/settings/set{name.capitalize()}/{_uuid(uuid)}", {name: data}), f"{kind} speichern")
    prot(u, "haproxy", f"OPNsense: {kind} geändert (gespeichert, nicht übernommen)", f"{w['_name']}: {data.get('name', uuid)}")
    return {"ok": True}


@router.delete("/objects/{kind}/{uuid}")
def object_delete(kind: str, uuid: str, w=Depends(verb), u=Depends(rolle("admin"))):
    name, _ = _obj(kind)
    r = api(w, "POST", f"/settings/del{name.capitalize()}/{_uuid(uuid)}") or {}
    if r.get("result") != "deleted":
        raise Fehler(f"Löschen nicht möglich ({r.get('result') or r}) – wird das Objekt noch benutzt?")
    prot(u, "haproxy", f"OPNsense: {kind} gelöscht (nicht übernommen)", f"{w['_name']}: {uuid}")
    return {"ok": True}


@router.post("/objects/{kind}/{uuid}/toggle")
def object_toggle(kind: str, uuid: str, w=Depends(verb), u=Depends(rolle("operator"))):
    name, _ = _obj(kind)
    if kind not in TOGGLE:
        raise Fehler("Dieses Objekt lässt sich nicht ein-/ausschalten")
    r = api(w, "POST", f"/settings/toggle{name.capitalize()}/{_uuid(uuid)}") or {}   # always flips
    prot(u, "haproxy", f"OPNsense: {kind} {r.get('result', 'umgeschaltet')}", f"{w['_name']}: {uuid}")
    return {"ok": True, "result": r.get("result")}


@router.post("/configtest")
def configtest_api(w=Depends(verb), u=Depends(rolle("operator"))):
    ok, out = configtest(w)
    return {"ok": ok, "ausgabe": out}


@router.get("/diff")
def diff(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"diff": str((api(w, "GET", "/export/diff") or {}).get("response") or "")}


@router.get("/config")
def config(w=Depends(verb), u=Depends(rolle("leser"))):
    return {"text": str((api(w, "GET", "/export/config") or {}).get("response") or "")}


@router.post("/reconfigure")
def reconfigure_api(w=Depends(verb), u=Depends(rolle("admin"))):
    try:
        r = reconfigure(w)
    except Fehler as e:
        prot(u, "haproxy", "OPNsense: Übernehmen", w["_name"], False, str(e))
        raise
    prot(u, "haproxy", "OPNsense: übernommen (reconfigure)", w["_name"], details=r["pruefung"][:2000])
    return r


@router.post("/service/{action}")
def service(action: str, w=Depends(verb), u=Depends(rolle("admin"))):
    if action not in ("start", "stop", "restart"):
        raise Fehler("unbekannte Aktion")
    r = api(w, "POST", f"/service/{action}", timeout=120) or {}
    prot(u, "haproxy", f"OPNsense: HAProxy {action}", w["_name"], details=str(r))
    return {"ok": str(r.get("response", "")).strip().upper() == "OK", "antwort": r.get("response")}
