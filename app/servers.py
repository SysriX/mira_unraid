"""Server list API: several HAProxy (haproxy-ui or OPNsense) and nginx (local or SSH) instances.

  GET    /api/servers                     list (+ access types with their form fields)
  POST   /api/servers                     create          (admin)
  PUT    /api/servers/{sid}               change          (admin)
  DELETE /api/servers/{sid}               delete          (admin)
  POST   /api/servers/test                test form values before saving (admin)
  POST   /api/servers/{sid}/test          test + remember status
  POST   /api/servers/test-all            test all enabled servers
  GET    /api/servers/{sid}/status        short status (running, servers up, pending changes …)
  POST   /api/servers/{sid}/check         check configuration (nginx -t / haproxy -c / haproxy-ui "prüfen")
  POST   /api/servers/{sid}/reload        apply/reload (nginx reload, OPNsense reconfigure, haproxy-ui "übernehmen")
  POST   /api/servers/{sid}/forget-host-key   SSH: forget the pinned host key (admin)
Type specific pages: module/haproxy.py, module/opnsense_haproxy.py, module/nginx.py.

Migration (schema 2, MIRA 0.4.0): connections of type haproxy/nginx move from mira_verbindung to mira_server.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

import kern
from anmeldung import rolle
from kern import Verbindung, jetzt, verschluesseln
from module import Fehler, prot, wahr
from module import haproxy as m_haproxy
from module import nginx as m_nginx
from module import haproxy_migration as m_mig
from module import opnsense_haproxy as m_opn
from server_store import KINDS, Server, all_servers, load, open_fs, values

SCHEMA_VERSION = 2


# ------------------------------------------------------------------ access types
def _nginx_call(fn):
    def run(w):
        fs = open_fs(w)
        try:
            return fn({**w, "_fs": fs}, fs)
        finally:
            fs.close()
    return run


def _hui_job(w, aktion: str) -> tuple[bool, str]:
    c = m_haproxy.Client.fuer(w)
    c.post("/api/auftrag", {"aktion": aktion})
    for _ in range(45):
        time.sleep(2)
        a = c.get("/api/auftrag")
        if not a.get("laeuft") and a.get("ergebnis"):
            e = a["ergebnis"]
            return e.get("code") == 0, str(e.get("log") or "")
    raise Fehler("Keine Antwort vom HAProxy-Container (läuft hap-waechter?)")


def _nginx_summary(w, fs):
    n = len([f for f in fs.listdir(w["conf_pfad"]) if f.endswith(".conf")]) if fs.isdir(w.get("conf_pfad") or "") else 0
    ok, out = m_nginx.pruefen(w)
    return {"running": ok, "hosts": n, "check": out[-300:]}


def _nginx_check(w, fs):
    return m_nginx.pruefen(w)


def _nginx_reload(w, fs):
    ok, out = m_nginx.pruefen(w)
    if not ok:
        raise Fehler("nginx -t meldet Fehler – nicht neu geladen:\n" + out)
    return {"ok": True, "meldung": m_nginx.neu_laden(w), "ausgabe": out}


ACCESS = {
    "haproxy-ui": {
        "kind": "haproxy", "title": "haproxy-ui (SysriX)", "address": "url", "fields": m_haproxy.FIELDS,
        "description": "HAProxy-Container mit haproxy-ui: Dienste, Statistik, Zertifikate, Übernehmen mit automatischem Rücksprung.",
        "test": m_haproxy.test, "summary": m_haproxy.summary,
        "check": lambda w: _hui_job(w, "pruefen"),
        "reload": lambda w: (lambda r: {"ok": r[0], "meldung": "übernommen" if r[0] else "Fehler – HAProxy ist zurückgesprungen", "ausgabe": r[1]})(_hui_job(w, "uebernehmen")),
    },
    "opnsense": {
        "kind": "haproxy", "title": "OPNsense (os-haproxy)", "address": "url", "fields": m_opn.FIELDS,
        "description": "HAProxy-Plugin der OPNsense über deren API (Key/Secret): Frontends, Backends, Server, ACLs, Aktionen; prüfen und übernehmen.",
        "test": m_opn.test, "summary": m_opn.summary, "check": m_opn.configtest,
        "reload": lambda w: {**m_opn.reconfigure(w), "ausgabe": ""},
    },
    "local": {
        "kind": "nginx", "title": "nginx – Ordner in MIRA + Docker-Socket", "address": "conf_pfad", "fields": m_nginx.FIELDS_LOCAL,
        "description": "nginx-Container auf demselben Unraid: Konfigurationsordner in MIRA eingebunden, Prüfen/Neu laden über den Docker-Socket.",
        "test": _nginx_call(m_nginx.test), "summary": _nginx_call(_nginx_summary),
        "check": _nginx_call(_nginx_check), "reload": _nginx_call(_nginx_reload),
    },
    "ssh": {
        "kind": "nginx", "title": "nginx auf anderem Host (SSH)", "address": "host", "fields": m_nginx.FIELDS_SSH,
        "description": "nginx auf einem anderen Rechner oder in einem Container dort: Dateien per SFTP, Befehle per SSH (optional sudo / docker exec). Host-Schlüssel wird beim ersten Mal gemerkt.",
        "test": _nginx_call(m_nginx.test), "summary": _nginx_call(_nginx_summary),
        "check": _nginx_call(_nginx_check), "reload": _nginx_call(_nginx_reload),
    },
}


def _secret_fields(access: str) -> set[str]:
    return {f["name"] for f in ACCESS[access]["fields"] if f.get("art") == "password"}


def _split(access: str, vals: dict, old_secret: dict | None = None) -> tuple[dict, dict]:
    sf = _secret_fields(access)
    secret = dict(old_secret or {})
    for k in sf:
        if vals.get(k):
            secret[k] = vals[k]
    settings = {k: v for k, v in vals.items() if k not in sf and not k.startswith("_")}
    return settings, secret


def _json(x: Server) -> dict:
    secret = kern.entschluesseln(x.secret_enc) if x.secret_enc else {}
    a = ACCESS.get(x.access, {})
    st = x.settings or {}
    return {"id": x.id, "name": x.name, "kind": x.kind, "kind_title": KINDS.get(x.kind, x.kind), "access": x.access,
            "access_title": a.get("title", x.access), "site": x.site, "enabled": x.enabled, "sort": x.sort,
            "address": str(st.get(a.get("address", ""), "")), "settings": st,
            "secret_set": sorted(k for k, v in secret.items() if v), "status": x.status,
            "checked_at": x.checked_at.isoformat() if x.checked_at else None, "migrated_from": x.migrated_from}


def _run(access: str, op: str, w: dict):
    try:
        return ACCESS[access][op](w)
    except (Fehler, PermissionError) as e:
        raise Fehler(str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise Fehler(f"{type(e).__name__}: {e}") from e


def _test_and_remember(x: Server) -> dict:
    try:
        ok, text = True, _run(x.access, "test", values(x))
    except Fehler as e:
        ok, text = False, str(e)
    with kern.sitzung() as s:
        y = s.get(Server, x.id)
        if y:
            y.status, y.checked_at = ("ok: " if ok else "fehler: ") + text[:480], jetzt()
    return {"id": x.id, "ok": ok, "meldung": text}


# ------------------------------------------------------------------ routes
router = APIRouter(prefix="/api/servers")


@router.get("")
def server_list(u=Depends(rolle("leser"))):
    return {"servers": [_json(x) for x in all_servers()],
            "kinds": [{"kind": k, "title": t} for k, t in KINDS.items()],
            "access": [{"access": k, "kind": a["kind"], "title": a["title"], "description": a["description"], "fields": a["fields"]}
                       for k, a in ACCESS.items()]}


def _validate(d: dict) -> tuple[str, str]:
    access = d.get("access")
    if access not in ACCESS:
        raise HTTPException(400, "unbekannte Zugangsart")
    kind = ACCESS[access]["kind"]
    if d.get("kind") and d["kind"] != kind:
        raise HTTPException(400, "Zugangsart passt nicht zur Server-Art")
    if not (d.get("name") or "").strip():
        raise HTTPException(400, "Name fehlt")
    return kind, access


@router.post("/test")
async def server_form_test(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    access = d.get("access")
    if access not in ACCESS:
        raise HTTPException(400, "unbekannte Zugangsart")
    old_secret, host_key = {}, ""
    if d.get("id"):
        x = load(int(d["id"]))
        if x.access == access:
            old_secret = kern.entschluesseln(x.secret_enc)
            host_key = (x.settings or {}).get("host_key", "")
    settings, secret = _split(access, d.get("values") or {}, old_secret)
    settings.setdefault("host_key", host_key)
    w = {**settings, **secret, "_id": int(d.get("id") or 0), "_name": d.get("name", ""), "_access": access}
    try:
        return {"ok": True, "meldung": _run(access, "test", w)}
    except Fehler as e:
        return {"ok": False, "meldung": str(e)}


@router.post("")
async def server_create(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    kind, access = _validate(d)
    settings, secret = _split(access, d.get("values") or {})
    with kern.sitzung() as s:
        x = Server(name=d["name"].strip()[:128], kind=kind, access=access, site=(d.get("site") or "").strip()[:128],
                   settings=settings, secret_enc=verschluesseln(secret), enabled=wahr(d.get("enabled", True)),
                   sort=int(d.get("sort") or 100))
        s.add(x)
        s.flush()
        sid = x.id
    prot(u, "server", "Server angelegt", f"{KINDS[kind]} ({ACCESS[access]['title']}): {d['name']}")
    return {"id": sid}


@router.put("/{sid}")
async def server_update(sid: int, req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    with kern.sitzung() as s:
        x = s.get(Server, sid)
        if not x:
            raise HTTPException(404, "Server gibt es nicht")
        access = d.get("access") or x.access
        if access not in ACCESS or ACCESS[access]["kind"] != x.kind:
            raise HTTPException(400, "Zugangsart passt nicht zur Server-Art")
        old_secret = kern.entschluesseln(x.secret_enc) if access == x.access else {}
        settings, secret = _split(access, d.get("values") or {}, old_secret)
        for k in d.get("secret_clear") or []:
            secret.pop(k, None)
        if access == x.access and (x.settings or {}).get("host_key") and "host_key" not in settings:
            settings["host_key"] = x.settings["host_key"]       # pinned SSH key survives a form save
        x.access, x.settings, x.secret_enc = access, settings, verschluesseln(secret)
        if d.get("name"):
            x.name = d["name"].strip()[:128]
        if "site" in d:
            x.site = (d.get("site") or "").strip()[:128]
        if "enabled" in d:
            x.enabled = wahr(d["enabled"])
        if "sort" in d:
            x.sort = int(d.get("sort") or 100)
        x.updated_at = jetzt()
        name = x.name
    prot(u, "server", "Server geändert", f"{sid}: {name}")
    return {"ok": True}


@router.delete("/{sid}")
def server_delete(sid: int, u=Depends(rolle("admin"))):
    with kern.sitzung() as s:
        x = s.get(Server, sid)
        if not x:
            return {"ok": True}
        name = x.name
        s.delete(x)
    prot(u, "server", "Server entfernt", f"{sid}: {name}", details="nur aus MIRA – am Server selbst ändert sich nichts")
    return {"ok": True}


@router.post("/test-all")
def server_test_all(u=Depends(rolle("leser"))):
    xs = [x for x in all_servers() if x.enabled]
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {"ergebnisse": list(ex.map(_test_and_remember, xs))}


@router.post("/{sid}/test")
def server_test(sid: int, u=Depends(rolle("leser"))):
    return _test_and_remember(load(sid))


def _enabled(sid: int) -> Server:
    x = load(sid)
    if not x.enabled:
        raise HTTPException(409, f"Server „{x.name}“ ist ausgeschaltet")
    return x


@router.get("/{sid}/status")
def server_status(sid: int, u=Depends(rolle("leser"))):
    x = _enabled(sid)
    return {"id": sid, **_run(x.access, "summary", values(x))}


@router.post("/{sid}/check")
def server_check(sid: int, u=Depends(rolle("operator"))):
    x = _enabled(sid)
    ok, out = _run(x.access, "check", values(x))
    prot(u, "server", "Konfiguration geprüft", x.name, ok, out[-2000:])
    return {"ok": ok, "ausgabe": out}


@router.post("/{sid}/reload")
def server_reload(sid: int, u=Depends(rolle("operator"))):
    x = _enabled(sid)
    if x.kind == "haproxy" and u["rolle"] != "admin":
        raise HTTPException(403, "HAProxy übernehmen dürfen nur Admins")
    try:
        r = _run(x.access, "reload", values(x))
    except Fehler as e:
        prot(u, "server", "Übernehmen/Neu laden", x.name, False, str(e))
        raise
    prot(u, "server", "Übernehmen/Neu laden", x.name, bool(r.get("ok", True)), str(r.get("meldung", ""))[:500])
    return r


@router.post("/{sid}/forget-host-key")
def server_forget_key(sid: int, u=Depends(rolle("admin"))):
    with kern.sitzung() as s:
        x = s.get(Server, sid)
        if not x:
            raise HTTPException(404, "Server gibt es nicht")
        x.settings = {k: v for k, v in (x.settings or {}).items() if k != "host_key"}
        name = x.name
    prot(u, "server", "SSH-Host-Schlüssel vergessen", name)
    return {"ok": True}


# ------------------------------------------------------------------ HAProxy migration assistant
def _hap(sid) -> tuple[Server, dict]:
    x = _enabled(int(sid or 0))
    if x.kind != "haproxy" or x.access not in ("haproxy-ui", "opnsense"):
        raise HTTPException(400, f"„{x.name}“ ist kein HAProxy-Server")
    return x, values(x)


def _migration_plan(d: dict):
    src, ws = _hap(d.get("source"))
    tgt, wt = _hap(d.get("target"))
    if src.id == tgt.id:
        raise HTTPException(400, "Quelle und Ziel sind derselbe Server")
    model, warnings = m_mig.read_source(src.access, ws)
    chosen, w2 = m_mig.apply_choices(model, d.get("choices") or {})
    tnames = m_mig.target_names(tgt.access, wt)
    if tgt.access == "haproxy-ui":
        plan = m_mig.plan_haproxy_ui(chosen, tnames)
        warnings += [r["warn"] for r in plan["routes"] if r.get("warn")]
    else:
        plan = m_mig.plan_opnsense(chosen, tnames, d.get("options") or {})
    return src, tgt, wt, plan, warnings + w2


@router.post("/migration/read")
async def migration_read(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    src, ws = _hap(d.get("source"))
    tgt, wt = _hap(d.get("target"))
    if src.id == tgt.id:
        raise HTTPException(400, "Quelle und Ziel sind derselbe Server")
    try:
        model, warnings = m_mig.read_source(src.access, ws)
        tnames = m_mig.target_names(tgt.access, wt)
        return {"source": _json(src), "target": _json(tgt), "model": model, "warnings": warnings, "target_names": tnames,
                "target_frontends": m_mig.target_frontends(tgt.access, wt), "certificates": m_mig.target_certificates(tgt.access, wt)}
    except (Fehler, HTTPException):
        raise
    except Exception as e:  # noqa: BLE001
        raise Fehler(f"{type(e).__name__}: {e}") from e


@router.post("/migration/preview")
async def migration_preview(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    src, tgt, wt, plan, warnings = _migration_plan(d)
    return {"preview": m_mig.preview_text(tgt.access, plan), "warnings": warnings}


@router.post("/migration/apply")
async def migration_apply(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    src, tgt, wt, plan, warnings = _migration_plan(d)
    log = m_mig.apply_haproxy_ui(wt, plan) if tgt.access == "haproxy-ui" else m_mig.apply_opnsense(wt, plan)
    ok = all(x["ok"] for x in log)
    prot(u, "server", "HAProxy-Migration", f"{src.name} → {tgt.name}", ok,
         "\n".join(("✓ " if x["ok"] else "✗ ") + x["text"] for x in log)[:6000])
    return {"ok": ok, "log": log, "warnings": warnings, "target": _json(tgt)}


ROUTERS = [router, m_haproxy.router, m_opn.router, m_nginx.router]


# ------------------------------------------------------------------ migration
def migrate() -> None:
    """Schema 1 → 2: HAProxy/nginx connections (mira_verbindung) become servers (mira_server).

    Idempotent; the moved rows are kept as a JSON backup in the setting "migration_v2_backup"
    (secrets stay encrypted) and removed from mira_verbindung in the same transaction."""
    current = int(kern.einstellung("schema_version", 1) or 1)
    if current >= SCHEMA_VERSION:
        return
    moved, backup = [], []
    with kern.sitzung() as s:
        done = {x.migrated_from for x in s.scalars(select(Server)) if x.migrated_from}
        for v in s.scalars(select(Verbindung).where(Verbindung.typ.in_(("haproxy", "nginx"))).order_by(Verbindung.id)):
            backup.append({"id": v.id, "typ": v.typ, "name": v.name, "einstellungen": v.einstellungen or {},
                           "geheim": v.geheim, "aktiv": v.aktiv, "status": v.status})
            if v.id not in done:
                access = "haproxy-ui" if v.typ == "haproxy" else "local"
                s.add(Server(name=v.name, kind=v.typ, access=access, settings=dict(v.einstellungen or {}),
                             secret_enc=v.geheim or "", enabled=v.aktiv, status=v.status, checked_at=v.geprueft,
                             migrated_from=v.id, sort=10 + len(moved), site=""))
                moved.append(f"{v.typ} „{v.name}“")
            s.delete(v)
        from kern import Einstellung
        if backup:
            e = s.get(Einstellung, "migration_v2_backup")
            if e:
                e.wert = (e.wert or []) + backup
            else:
                s.add(Einstellung(schluessel="migration_v2_backup", wert=backup))
        e = s.get(Einstellung, "schema_version")
        if e:
            e.wert = SCHEMA_VERSION
        else:
            s.add(Einstellung(schluessel="schema_version", wert=SCHEMA_VERSION))
    if moved:
        kern.protokoll("MIRA", "server", "Migration auf Server-Liste", ", ".join(moved)[:500], True,
                       "HAProxy/nginx-Verbindungen sind jetzt Server (Menü Routing › Server). Sicherung: Einstellung migration_v2_backup")
        kern.log("Migration: Verbindungen -> Server:", ", ".join(moved))


kern.NACH_DB_START.append(migrate)
