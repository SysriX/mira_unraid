"""Plugin catalog: MIRA asks plugin directories (PIA, see SysriX/pia_web) which plugins exist,
installs/updates them into the plugin folder and lets admins manage a directory from MIRA.

A source is a PIA address plus an API key ("pia_…"). Every call carries "Authorization: Bearer <key>"; nothing
of a directory is reachable without it. Scope of the key (set in PIA):
  read     – see the catalog, download packages
  publish  – additionally upload / withdraw / delete plugin versions
  admin    – additionally manage users, app keys, settings and see the log of the directory
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

import kern
import plugin_format as pf
import plugins as pl
from anmeldung import rolle
from kern import Basis, jetzt, sitzung

API = "/api/v1"
CHECK_EVERY = 600            # background loop: look every 10 minutes which sources are due
# paths of the directory API that MIRA forwards for "manage directory" (admin in MIRA + scope of the key)
REMOTE_PATHS = re.compile(r"^(me|plugins(/[a-z][a-z0-9_-]{1,31}(/icon|/versions/[0-9A-Za-z.+-]{1,40}(/download)?)?)?|"
                          r"keys(/\d+)?|users(/\d+)?|log|settings)$")


# ------------------------------------------------------------------ tables
class PluginSource(Basis):
    __tablename__ = "mira_plugin_source"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(String(255), default="")
    token_enc: Mapped[str] = mapped_column(Text, default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_h: Mapped[int] = mapped_column(Integer, default=6)
    scopes: Mapped[str] = mapped_column(String(64), default="")          # "read,publish,admin" as reported by /me
    server: Mapped[str] = mapped_column(String(128), default="")         # name + version of the directory
    status: Mapped[str] = mapped_column(String(512), default="")
    catalog: Mapped[str] = mapped_column(Text, default="")               # last /plugins answer (JSON)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")


class PluginInstall(Basis):
    __tablename__ = "mira_plugin_install"
    plugin_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(40), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    installed_by: Mapped[str] = mapped_column(String(128), default="")
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)


def installs() -> dict:
    if not kern.db_bereit():
        return {}
    with sitzung() as s:
        return {x.plugin_id: {"source_id": x.source_id, "version": x.version, "sha256": x.sha256,
                              "installed_by": x.installed_by, "installed_at": x.installed_at.isoformat()}
                for x in s.scalars(select(PluginInstall))}


def forget_install(pid: str) -> None:
    with sitzung() as s:
        x = s.get(PluginInstall, pid)
        if x:
            s.delete(x)


# ------------------------------------------------------------------ helpers
def _src_json(x: PluginSource) -> dict:
    try:
        n = len((json.loads(x.catalog) or {}).get("plugins", [])) if x.catalog else 0
    except ValueError:
        n = 0
    return {"id": x.id, "name": x.name, "url": x.url, "verify_tls": x.verify_tls, "enabled": x.enabled,
            "interval_h": x.interval_h, "scopes": [s for s in x.scopes.split(",") if s], "server": x.server,
            "status": x.status, "plugins": n, "checked_at": x.checked_at.isoformat() if x.checked_at else None,
            "created_by": x.created_by}


def _sources() -> list[PluginSource]:
    with sitzung() as s:
        out = list(s.scalars(select(PluginSource).order_by(PluginSource.name)))
        for x in out:
            s.expunge(x)
        return out


def _source(sid: int) -> PluginSource:
    with sitzung() as s:
        x = s.get(PluginSource, sid)
        if not x:
            raise LookupError("Plugin-Quelle gibt es nicht")
        s.expunge(x)
        return x


def _norm_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise HTTPException(400, "Adresse fehlt")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": f"MIRA/{kern.VERSION}"}


def _err(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail") or f"HTTP {r.status_code}")
    except ValueError:
        return f"HTTP {r.status_code}"


def _fetch(url: str, token: str, verify: bool) -> tuple[dict, dict]:
    """(me, catalog) of a directory – raises HTTPException with a readable reason."""
    try:
        with httpx.Client(verify=verify, timeout=20, follow_redirects=False) as c:
            me = c.get(url + API + "/me", headers=_headers(token))
            if me.status_code in (401, 403):
                raise HTTPException(400, f"Zugang abgelehnt: {_err(me)}")
            if me.status_code != 200:
                raise HTTPException(400, f"{url} antwortet nicht wie ein Plugin-Verzeichnis ({_err(me)})")
            cat = c.get(url + API + "/plugins", headers=_headers(token))
            if cat.status_code != 200:
                raise HTTPException(400, f"Katalog nicht lesbar: {_err(cat)}")
            return me.json(), cat.json()
    except httpx.HTTPError as e:
        raise HTTPException(400, f"{url} ist nicht erreichbar ({e.__class__.__name__})") from e
    except ValueError as e:
        raise HTTPException(400, f"{url} antwortet nicht wie ein Plugin-Verzeichnis") from e


def refresh(sid: int) -> dict:
    x = _source(sid)
    token = (kern.entschluesseln(x.token_enc) or {}).get("token", "")
    try:
        me, cat = _fetch(x.url, token, x.verify_tls)
        ok, status = True, f"ok: {len(cat.get('plugins', []))} Plugins"
    except HTTPException as e:
        ok, status, me, cat = False, f"fehler: {e.detail}", None, None
    with sitzung() as s:
        y = s.get(PluginSource, sid)
        y.status, y.checked_at = status[:512], jetzt()
        if ok:
            y.catalog = json.dumps(cat)
            y.scopes = ",".join(me.get("scopes") or [])[:64]
            srv = me.get("server") or {}
            y.server = f"{srv.get('name', 'PIA')} {srv.get('version', '')}".strip()[:128]
        out = _src_json(y)
    return out


async def loop():
    """Looks at the directories regularly (interval per source, default 6 h)."""
    await asyncio.sleep(20)
    while True:
        try:
            if kern.db_bereit():
                for x in _sources():
                    due = not x.checked_at or (jetzt() - x.checked_at.replace(tzinfo=x.checked_at.tzinfo or kern.timezone.utc)
                                               >= timedelta(hours=max(1, x.interval_h or 6)))
                    if x.enabled and due:
                        await asyncio.to_thread(refresh, x.id)
        except Exception as e:  # noqa: BLE001 – the loop must survive everything
            kern.log("Plugin-Katalog:", e)
        await asyncio.sleep(CHECK_EVERY)


def catalog() -> list[dict]:
    """All plugins of all enabled sources, newest version wins if several sources have the same id."""
    installed = pl.scan()
    states = pl._states()
    t = pl.trust()
    inst = installs()
    out: dict[str, dict] = {}
    for x in _sources():
        if not x.enabled or not x.catalog:
            continue
        try:
            plist = json.loads(x.catalog).get("plugins", [])
        except ValueError:
            continue
        for p in plist:
            vers = [v for v in p.get("versions", []) if not v.get("yanked")]
            if not vers or not pl.ID_RE.match(str(p.get("id", ""))):
                continue
            compatible = [v for v in vers if not v.get("mira_min") or not pf.newer(v["mira_min"], kern.VERSION)]
            best = max(compatible or vers, key=lambda v: pf.version_tuple(v["version"]))
            e = {"id": p["id"], "name": p.get("name", p["id"]), "title": p.get("title", ""), "description": p.get("description", ""),
                 "author": p.get("author", ""), "group": p.get("group", ""), "homepage": p.get("homepage", ""),
                 "source_id": x.id, "source_name": x.name, "latest": best, "versions": vers,
                 "compatible": bool(compatible), "icon": f"/api/plugin-sources/{x.id}/remote/plugins/{p['id']}/icon",
                 "has_icon": p.get("has_icon", True)}
            prev = out.get(p["id"])
            if prev and not pf.newer(best["version"], prev["latest"]["version"]):
                continue
            out[p["id"]] = e
    for e in out.values():
        local = installed.get(e["id"])
        e["installed"] = None
        if local:
            e["installed"] = {"version": str(local.m.get("version", "")), "source": local.source,
                              "state": pl.state_of(local, states.get(e["id"]), t), "from_catalog": e["id"] in inst}
            e["update"] = pf.newer(e["latest"]["version"], e["installed"]["version"])
        key = e["latest"].get("public_key")
        e["latest"]["trusted"] = bool(e["latest"].get("signed") and any(k["public_key"] == key for k in t["keys"]))
    return sorted(out.values(), key=lambda e: (e["group"], e["name"].lower()))


def install(sid: int, pid: str, version: str | None, user: str) -> dict:
    x = _source(sid)
    try:
        entry = next(p for p in json.loads(x.catalog or "{}").get("plugins", []) if p.get("id") == pid)
    except StopIteration as e:
        raise HTTPException(404, "Plugin ist in dieser Quelle nicht (mehr) vorhanden – Quelle aktualisieren") from e
    vers = [v for v in entry.get("versions", []) if not v.get("yanked")]
    v = next((v for v in vers if v["version"] == version), None) if version else \
        max(vers, key=lambda v: pf.version_tuple(v["version"]), default=None)
    if not v:
        raise HTTPException(404, "Version nicht gefunden")
    if v.get("mira_min") and pf.newer(v["mira_min"], kern.VERSION):
        raise HTTPException(400, f"{pid} {v['version']} braucht MIRA {v['mira_min']} oder neuer")
    token = (kern.entschluesseln(x.token_enc) or {}).get("token", "")
    try:
        with httpx.Client(verify=x.verify_tls, timeout=120, follow_redirects=False) as c:
            r = c.get(f"{x.url}{API}/plugins/{pid}/versions/{v['version']}/download", headers=_headers(token))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Download fehlgeschlagen ({e.__class__.__name__})") from e
    if r.status_code != 200:
        raise HTTPException(502, f"Download fehlgeschlagen: {_err(r)}")
    data = r.content
    sha = hashlib.sha256(data).hexdigest()
    if v.get("sha256") and sha != v["sha256"]:
        raise HTTPException(502, "Prüfsumme des Pakets stimmt nicht mit dem Katalog überein – Installation abgebrochen")
    try:
        entries = pf.safe_entries(data)
        manifest = json.loads(entries["plugin.json"])
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Paket ungültig: {e}") from e
    if manifest.get("id") != pid or str(manifest.get("version")) != v["version"]:
        raise HTTPException(400, "Paket passt nicht zum Katalog (id oder Version weichen ab)")

    os.makedirs(pl.USER_DIR, exist_ok=True)
    staging = os.path.join(pl.USER_DIR, f".staging-{pid}-{os.urandom(4).hex()}")
    pf.write_tree(entries, staging)
    t = pl.trust()
    fp = pf.fingerprint_dir(staging)
    si = pf.check_signature(pf.read_signature(staging), pid, v["version"], fp, t["keys"])
    if si["state"] == "invalid" or (t["require_signature"] and not si["trusted"]):
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, f"Installation abgelehnt – Signatur: {si.get('reason') or 'nicht vertrauenswürdig signiert'}")

    current = pl._plugins.get(pid) or pl.scan().get(pid)
    if current and current.source == "builtin":
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, f"„{pid}“ ist ein mitgeliefertes Plugin und lässt sich nicht aus dem Katalog ersetzen")
    old_version = str(current.m.get("version", "")) if current else ""
    target = current.dir if current else os.path.join(pl.USER_DIR, pid)
    backup = pf.replace_dir(staging, target, os.path.join(pl.USER_DIR, ".backup"))
    with sitzung() as s:
        st = s.get(pl.PluginState, pid) or pl.PluginState(id=pid)
        # installing from a configured source is the admin's approval – the folder now matches the package exactly
        st.approved_hash, st.enabled, st.approved_by, st.approved_at = fp, True, user, jetzt()
        s.merge(st)
        ins = s.get(PluginInstall, pid) or PluginInstall(plugin_id=pid)
        ins.source_id, ins.version, ins.sha256, ins.installed_by, ins.installed_at = sid, v["version"], sha, user, jetzt()
        s.merge(ins)
    pl.scan()
    return {"id": pid, "version": v["version"], "previous": old_version, "backup": backup, "signature": si,
            "source": x.name}


# ------------------------------------------------------------------ routes
router = APIRouter()


def _audit(u: dict, action: str, target: str = "", ok: bool = True, details: str = ""):
    kern.protokoll(u.get("name", "?"), "plugin", action, target, ok, details, u.get("ip", ""))


@router.get("/api/plugin-sources")
def sources_list(u=Depends(rolle("admin"))):
    return {"sources": [_src_json(x) for x in _sources()]}


@router.post("/api/plugin-sources")
async def source_add(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    url, token, verify = _norm_url(d.get("url")), (d.get("token") or "").strip(), bool(d.get("verify_tls", True))
    if not token:
        raise HTTPException(400, "API-Schlüssel fehlt – im Plugin-Verzeichnis unter „App-Schlüssel“ erzeugen")
    me, cat = await asyncio.to_thread(_fetch, url, token, verify)
    srv = me.get("server") or {}
    with sitzung() as s:
        x = PluginSource(name=(d.get("name") or srv.get("name") or "Plugin-Verzeichnis").strip()[:128], url=url,
                         token_enc=kern.verschluesseln({"token": token}), verify_tls=verify,
                         interval_h=max(1, min(168, int(d.get("interval_h") or 6))),
                         scopes=",".join(me.get("scopes") or [])[:64],
                         server=f"{srv.get('name', 'PIA')} {srv.get('version', '')}".strip()[:128],
                         status=f"ok: {len(cat.get('plugins', []))} Plugins", catalog=json.dumps(cat), checked_at=jetzt(),
                         created_by=u["name"])
        s.add(x)
        s.flush()
        out = _src_json(x)
    _audit(u, "Plugin-Quelle hinzugefügt", out["name"], details=f"{url}, Rechte {out['scopes']}")
    return out


@router.put("/api/plugin-sources/{sid}")
async def source_update(sid: int, req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    with sitzung() as s:
        x = s.get(PluginSource, sid)
        if not x:
            raise LookupError("Plugin-Quelle gibt es nicht")
        if d.get("name"):
            x.name = d["name"].strip()[:128]
        if d.get("url"):
            x.url = _norm_url(d["url"])
        if "verify_tls" in d:
            x.verify_tls = bool(d["verify_tls"])
        if "enabled" in d:
            x.enabled = bool(d["enabled"])
        if d.get("interval_h"):
            x.interval_h = max(1, min(168, int(d["interval_h"])))
        if (d.get("token") or "").strip():
            x.token_enc = kern.verschluesseln({"token": d["token"].strip()})
    _audit(u, "Plugin-Quelle geändert", str(sid))
    return await asyncio.to_thread(refresh, sid)


@router.delete("/api/plugin-sources/{sid}")
def source_delete(sid: int, u=Depends(rolle("admin"))):
    x = _source(sid)
    with sitzung() as s:
        y = s.get(PluginSource, sid)
        if y:
            s.delete(y)
    _audit(u, "Plugin-Quelle entfernt", x.name, details="installierte Plugins bleiben erhalten")
    return {"ok": True}


@router.post("/api/plugin-sources/{sid}/refresh")
async def source_refresh(sid: int, u=Depends(rolle("admin"))):
    return await asyncio.to_thread(refresh, sid)


@router.post("/api/plugin-sources/refresh")
async def sources_refresh_all(u=Depends(rolle("admin"))):
    return {"sources": [await asyncio.to_thread(refresh, x.id) for x in _sources() if x.enabled]}


@router.get("/api/plugin-catalog")
def catalog_list(u=Depends(rolle("admin"))):
    return {"plugins": catalog(), "sources": [_src_json(x) for x in _sources()], "mira": kern.VERSION}


@router.post("/api/plugin-catalog/install")
async def catalog_install(req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    pid = str(d.get("id") or "")
    if not pl.ID_RE.match(pid):
        raise HTTPException(400, "ungültige Plugin-id")
    try:
        res = await asyncio.to_thread(install, int(d.get("source_id") or 0), pid, d.get("version"), u["name"])
    except HTTPException as e:
        _audit(u, "Plugin-Installation fehlgeschlagen", pid, ok=False, details=str(e.detail))
        raise
    si = res["signature"]
    _audit(u, "Plugin installiert" if not res["previous"] else "Plugin aktualisiert",
           f"{pid} {res['previous'] + ' → ' if res['previous'] else ''}{res['version']}",
           details=f"Quelle {res['source']}; Signatur {si.get('state')} {si.get('key_id', '')}"
                   f"{' (vertrauenswürdig)' if si.get('trusted') else ''}; Sicherung {res['backup'] or '–'}")
    return res


@router.post("/api/plugin-sources/{sid}/publish/{pid}")
async def source_publish_local(sid: int, pid: str, req: Request, u=Depends(rolle("admin"))):
    """Uploads an installed plugin folder (with its signature.json) to a directory."""
    x = _source(sid)
    p = pl.plugin(pid)
    data = pf.pack_dir(p.dir)
    replace = req.query_params.get("replace") == "1"
    token = (kern.entschluesseln(x.token_enc) or {}).get("token", "")

    def post():
        with httpx.Client(verify=x.verify_tls, timeout=120) as c:
            return c.post(f"{x.url}{API}/plugins" + ("?replace=1" if replace else ""), content=data,
                          headers={**_headers(token), "Content-Type": "application/zip"})
    try:
        r = await asyncio.to_thread(post)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{x.name} ist nicht erreichbar ({e.__class__.__name__})") from e
    if r.status_code >= 400:
        _audit(u, "Plugin hochladen fehlgeschlagen", f"{pid} → {x.name}", ok=False, details=_err(r))
        raise HTTPException(400, f"{x.name}: {_err(r)}")
    _audit(u, "Plugin hochgeladen", f"{pid} {p.m.get('version', '')} → {x.name}")
    await asyncio.to_thread(refresh, sid)
    return r.json()


# ---- manage a directory from MIRA: forwards to <url>/api/v1/<path> with the stored key (admin in MIRA only;
#      what the key may do is decided by the directory itself)
_PASS = {"content-type", "content-disposition", "cache-control", "etag"}


@router.api_route("/api/plugin-sources/{sid}/remote/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def source_remote(sid: int, path: str, req: Request, u=Depends(rolle("admin"))):
    if not REMOTE_PATHS.match(path):
        raise HTTPException(403, "Dieser Pfad des Plugin-Verzeichnisses ist für MIRA nicht freigegeben")
    x = _source(sid)
    token = (kern.entschluesseln(x.token_enc) or {}).get("token", "")
    headers = _headers(token)
    headers["X-Acting-User"] = u["name"]
    if req.headers.get("content-type"):
        headers["Content-Type"] = req.headers["content-type"]
    body = await req.body()
    try:
        async with httpx.AsyncClient(verify=x.verify_tls, timeout=120) as c:
            r = await c.request(req.method, f"{x.url}{API}/{path}", params=list(req.query_params.multi_items()),
                                content=body or None, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{x.name} ist nicht erreichbar ({e.__class__.__name__})") from e
    if r.status_code == 401:
        raise HTTPException(502, f"{x.name} lehnt den Schlüssel ab – unter Plugins › Quellen neu eintragen")
    if req.method != "GET":
        _audit(u, f"Plugin-Verzeichnis: {req.method} {path}", x.name, ok=r.status_code < 400,
               details="" if r.status_code < 400 else _err(r))
    out = {k: v for k, v in r.headers.items() if k.lower() in _PASS}
    if path.endswith("/icon"):
        out["Cache-Control"] = "private, max-age=3600"
    return Response(content=r.content, status_code=r.status_code, headers=out)
