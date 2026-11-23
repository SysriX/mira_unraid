"""MIRA plugin system.

A plugin is a folder with a plugin.json (manifest) and its UI files. MIRA looks in
  <app>/plugins/        plugins shipped with MIRA (approved automatically)
  <MIRA_DATA>/plugins/  plugins copied in by the admin (must be approved – they run code in the browser)

Plugin type "sysrix": the target app speaks the SysriX connect standard
  GET  /.well-known/sysrix-app      → {"app", "name", "version", "connect": 1, "pair": "/api/connect/pair"}
  POST <pair> {username, password, client, role}  → {"token", "token_id", "role"}   (password is not stored)
  API calls with "Authorization: Bearer <token>" plus X-Acting-User / X-Acting-Role / X-Acting-IP.
MIRA stores the token encrypted and forwards the plugin's API calls: /api/p/<connection id>/<path of the app>.
Other plugin types use a built-in connector (connectors.py), e.g. "certwarden": MIRA logs in with stored
credentials and forwards the calls the same way. See docs/PLUGINS.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

import kern
from anmeldung import rolle
from connectors import CONNECTORS
from kern import Basis, jetzt, sitzung

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_DIR = os.path.join(APP_DIR, "plugins")
USER_DIR = os.environ.get("MIRA_PLUGINS", os.path.join(kern.DATA, "plugins"))
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
ROLE_TO_LEVEL = {"leser": "read", "operator": "write", "admin": "admin"}
LEVEL_RANK = {"read": 1, "write": 2, "admin": 3}
MAX_SCAN_HOSTS = 1024


# ------------------------------------------------------------------ tables
class PluginState(Basis):
    __tablename__ = "mira_plugin"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    approved_hash: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[str] = mapped_column(String(128), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PluginConnection(Basis):
    __tablename__ = "mira_plugin_conn"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    base_url: Mapped[str] = mapped_column(String(255), default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    token_enc: Mapped[str] = mapped_column(Text, default="")            # {"token", "token_id"} encrypted
    role: Mapped[str] = mapped_column(String(16), default="admin")    # upper limit granted by the app
    remote_user: Mapped[str] = mapped_column(String(128), default="")  # app admin who approved the pairing
    remote_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(512), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)


# ------------------------------------------------------------------ plugin folders
class Plugin:
    def __init__(self, folder: str, source: str):
        self.dir = folder
        self.source = source                      # builtin | folder
        self.error = ""
        self.manifest: dict = {}
        self.hash = ""
        try:
            with open(os.path.join(folder, "plugin.json"), encoding="utf-8") as f:
                self.manifest = json.load(f)
        except Exception as e:  # noqa: BLE001
            self.error = f"plugin.json nicht lesbar: {e}"
        self.id = str(self.manifest.get("id") or os.path.basename(folder)).lower()
        if not self.error:
            if not ID_RE.match(self.id):
                self.error = "ungültige id (a–z, 0–9, _ und -, 2–32 Zeichen)"
            elif (self.manifest.get("connect") or {}).get("type", "sysrix") not in ("sysrix", *CONNECTORS):
                self.error = "Plugin-Typ wird von dieser MIRA-Version nicht unterstützt"
        self.hash = self._hash()

    def _hash(self) -> str:
        """Fingerprint of all files – a changed plugin has to be approved again."""
        h = hashlib.sha256()
        for root, dirs, files in os.walk(self.dir):
            dirs.sort()
            for name in sorted(files):
                p = os.path.join(root, name)
                h.update(os.path.relpath(p, self.dir).replace("\\", "/").encode())
                try:
                    with open(p, "rb") as f:
                        h.update(hashlib.sha256(f.read()).digest())
                except OSError:
                    pass
        return h.hexdigest()

    @property
    def m(self) -> dict:
        return self.manifest

    def connect(self) -> dict:
        c = dict(self.m.get("connect") or {})
        c.setdefault("type", "sysrix")
        c.setdefault("app", self.id)
        c.setdefault("ports", [8080, 443, 80])
        c.setdefault("hostnames", [self.id])
        c.setdefault("proxy", ["/api/"])
        return c

    def file(self, rel: str) -> str | None:
        p = os.path.realpath(os.path.join(self.dir, rel))
        root = os.path.realpath(self.dir)
        if not p.startswith(root + os.sep) or not os.path.isfile(p):
            return None
        return p


_plugins: dict[str, Plugin] = {}


def scan() -> dict[str, Plugin]:
    found: dict[str, Plugin] = {}
    for base, source in ((BUILTIN_DIR, "builtin"), (USER_DIR, "folder")):
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "plugin.json")):
                p = Plugin(d, source)
                if p.id in found and source == "folder" and found[p.id].source == "builtin":
                    p.error = p.error or f"id „{p.id}“ ist schon ein eingebautes Plugin"
                    p.id = f"{p.id}~{name}"[:32]
                found.setdefault(p.id, p)
    _plugins.clear()
    _plugins.update(found)
    return _plugins


def plugin(pid: str) -> Plugin:
    if pid not in _plugins:
        scan()
    p = _plugins.get(pid)
    if not p:
        raise LookupError("Plugin nicht gefunden")
    return p


def _states() -> dict[str, PluginState]:
    with sitzung() as s:
        out = {x.id: x for x in s.scalars(select(PluginState))}
        for x in out.values():
            s.expunge(x)
        return out


def state_of(p: Plugin, st: PluginState | None) -> str:
    if p.error:
        return "invalid"
    if p.source == "builtin":
        return "disabled" if st and not st.enabled else "active"
    if not st or not st.approved_hash:
        return "new"
    if st.approved_hash != p.hash:
        return "changed"
    return "active" if st.enabled else "disabled"


def _active(pid: str) -> Plugin:
    p = plugin(pid)
    if state_of(p, _states().get(pid)) != "active":
        raise HTTPException(409, f"Plugin „{p.m.get('name', pid)}“ ist nicht freigegeben")
    return p


# ------------------------------------------------------------------ connections
def _conn_json(c: PluginConnection) -> dict:
    return {"id": c.id, "plugin_id": c.plugin_id, "name": c.name, "base_url": c.base_url, "verify_tls": c.verify_tls,
            "role": c.role, "remote_user": c.remote_user, "remote_version": c.remote_version, "status": c.status,
            "checked_at": c.checked_at.isoformat() if c.checked_at else None, "created_by": c.created_by}


def _conns(pid: str | None = None) -> list[PluginConnection]:
    with sitzung() as s:
        q = select(PluginConnection).order_by(PluginConnection.plugin_id, PluginConnection.name)
        if pid:
            q = q.where(PluginConnection.plugin_id == pid)
        out = list(s.scalars(q))
        for c in out:
            s.expunge(c)
        return out


def _conn(cid: int) -> PluginConnection:
    with sitzung() as s:
        c = s.get(PluginConnection, cid)
        if not c:
            raise LookupError("Plugin-Verbindung gibt es nicht")
        s.expunge(c)
        return c


def _token(c: PluginConnection) -> str:
    return (kern.entschluesseln(c.token_enc) or {}).get("token", "")


def _norm_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise HTTPException(400, "Adresse fehlt")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _client(verify: bool, timeout: float = 15) -> httpx.Client:
    return httpx.Client(verify=verify, timeout=timeout, follow_redirects=False)


def connector(p: "Plugin"):
    """Built-in connector of a plugin, None for the SysriX standard."""
    return CONNECTORS.get(p.connect()["type"])


def probe(url: str, app: str, verify: bool = False, p: "Plugin | None" = None) -> dict:
    """Reads /.well-known/sysrix-app and checks that the expected app answers (or asks the connector)."""
    if p is not None and connector(p):
        return connector(p).probe(url, verify)
    try:
        with _client(verify, 6) as c:
            r = c.get(url + "/.well-known/sysrix-app")
    except httpx.HTTPError as e:
        raise HTTPException(400, f"{url} ist nicht erreichbar: {e.__class__.__name__}") from e
    if r.status_code != 200:
        raise HTTPException(400, f"{url} antwortet nicht wie eine SysriX-App (HTTP {r.status_code})")
    try:
        info = r.json()
    except ValueError as e:
        raise HTTPException(400, f"{url} antwortet nicht wie eine SysriX-App") from e
    if info.get("app") != app:
        raise HTTPException(400, f"Unter {url} läuft „{info.get('name') or info.get('app')}“, nicht {app.upper()}")
    return info


# ------------------------------------------------------------------ discovery
def _own_networks() -> list[ipaddress.IPv4Network]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))          # no packet is sent – only picks the outgoing interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    nets = []
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local:
            nets.append(ipaddress.ip_network(f"{ip}/24", strict=False))
    return sorted(set(nets), key=str)


def _targets(p: Plugin, networks: list[str]) -> tuple[list[str], list[str]]:
    """Networks may carry an extra port: "192.168.10.0/24:8443" also tries that port."""
    con = p.connect()
    nets, ports = [], [int(x) for x in con["ports"]]
    for n in networks:
        n = n.strip()
        if not n:
            continue
        m = re.match(r"^(.+):(\d{1,5})$", n)
        if m:
            n = m.group(1)
            if int(m.group(2)) not in ports:
                ports.append(int(m.group(2)))
        try:
            nets.append(ipaddress.ip_network(n, strict=False))
        except ValueError as e:
            raise HTTPException(400, f"Netz „{n}“ ist ungültig") from e
    if not nets:
        nets = _own_networks()
    hosts: list[str] = []
    for n in nets:
        if n.version != 4:
            continue
        hosts.extend(str(h) for h in (n.hosts() if n.num_addresses > 2 else [n.network_address]))
        if len(hosts) > MAX_SCAN_HOSTS:
            raise HTTPException(400, f"Zu viele Adressen – höchstens {MAX_SCAN_HOSTS} (z. B. vier /24-Netze)")
    hosts.extend(h for h in con["hostnames"] if h)
    urls = []
    for host in hosts:
        for port in ports:
            port = int(port)
            if port == 443:
                urls.append(f"https://{host}")
            elif port == 80:
                urls.append(f"http://{host}")
            else:
                urls.append(f"http://{host}:{port}")
                if port in (8443, 9443) or port in getattr(connector(p), "https_ports", ()):
                    urls[-1] = f"https://{host}:{port}"
    return urls, [str(n) for n in nets]


async def discover(p: Plugin, networks: list[str]) -> dict:
    urls, nets = _targets(p, networks)
    app = p.connect()["app"]
    sem = asyncio.Semaphore(200)
    found: dict[str, dict] = {}

    conn_type = connector(p)

    async def one(client: httpx.AsyncClient, url: str):
        async with sem:
            if conn_type:
                info = await conn_type.detect(client, url)
                if info:
                    found[url] = {"url": url, "name": info["name"], "version": info.get("version", ""),
                                  "setup_needed": bool(info.get("setup_needed")), "instance": info.get("instance") or url}
                return
            try:
                r = await client.get(url + "/.well-known/sysrix-app")
                if r.status_code == 200:
                    info = r.json()
                    if info.get("app") == app:
                        found[url] = {"url": url, "name": info.get("name", app), "version": info.get("version", ""),
                                      "setup_needed": bool(info.get("setup_needed")), "instance": info.get("instance") or url}
            except Exception:  # noqa: BLE001 – nothing there
                pass

    limits = httpx.Limits(max_connections=200, max_keepalive_connections=0)
    async with httpx.AsyncClient(verify=False, timeout=httpx.Timeout(1.5, connect=0.8), limits=limits) as client:
        await asyncio.gather(*(one(client, u) for u in urls))
    known = {c.base_url for c in _conns(p.id)}
    order = {u: i for i, u in enumerate(urls)}          # one entry per installation: first address wins (hostname last)
    res, seen = [], {}
    for r in sorted(found.values(), key=lambda x: order[x["url"]]):
        if r["instance"] in seen:
            seen[r["instance"]].setdefault("also", []).append(r["url"])
            continue
        seen[r["instance"]] = r
        res.append(r)
    for r in res:
        r["connected"] = r["url"] in known
    return {"found": res, "networks": nets, "checked": len(urls)}


# ------------------------------------------------------------------ routes
router = APIRouter()


def _audit(u: dict, action: str, target: str = "", ok: bool = True, details: str = ""):
    kern.protokoll(u.get("name", "?"), "plugin", action, target, ok, details, u.get("ip", ""))


@router.get("/api/plugins")
def plugins_list(u=Depends(rolle("leser"))):
    scan()
    states = _states()
    conns = _conns()
    out = []
    for pid, p in sorted(_plugins.items(), key=lambda kv: (kv[1].m.get("order", 100), kv[0])):
        st = state_of(p, states.get(pid))
        ui = p.m.get("ui") or {}
        item = {"id": pid, "name": p.m.get("name", pid), "title": p.m.get("title", ""), "version": p.m.get("version", ""),
                "description": p.m.get("description", ""), "group": p.m.get("group", "Plugins"),
                "author": p.m.get("author", ""), "source": p.source, "state": st, "error": p.error,
                "hash": p.hash[:12], "icon": f"/plugins/{pid}/{p.m['icon']}" if p.m.get("icon") else None,
                "app": p.connect()["app"], "connect_type": p.connect()["type"], "views": ui.get("views", []),
                "connections": [_conn_json(c) for c in conns if c.plugin_id == pid]}
        if st == "active":
            item["scripts"] = [f"/plugins/{pid}/{f}?v={p.hash[:10]}" for f in ui.get("scripts", [])]
            item["styles"] = [f"/plugins/{pid}/{f}?v={p.hash[:10]}" for f in ui.get("styles", [])]
        out.append(item)
    return {"plugins": out, "folder": USER_DIR}


@router.post("/api/plugins/rescan")
def plugins_rescan(u=Depends(rolle("admin"))):
    scan()
    return plugins_list(u)


@router.post("/api/plugins/{pid}/approve")
def plugin_approve(pid: str, u=Depends(rolle("admin"))):
    p = plugin(pid)
    if p.error:
        raise HTTPException(400, p.error)
    with sitzung() as s:
        st = s.get(PluginState, pid) or PluginState(id=pid)
        st.approved_hash, st.enabled, st.approved_by, st.approved_at = p.hash, True, u["name"], jetzt()
        s.merge(st)
    _audit(u, "Plugin freigegeben", f"{p.m.get('name', pid)} {p.m.get('version', '')}", details=f"Fingerabdruck {p.hash}")
    return {"ok": True}


@router.post("/api/plugins/{pid}/disable")
def plugin_disable(pid: str, u=Depends(rolle("admin"))):
    p = plugin(pid)
    with sitzung() as s:
        st = s.get(PluginState, pid) or PluginState(id=pid)
        st.enabled = False
        s.merge(st)
    _audit(u, "Plugin ausgeschaltet", p.m.get("name", pid))
    return {"ok": True}


@router.post("/api/plugins/{pid}/discover")
async def plugin_discover(pid: str, req: Request, u=Depends(rolle("admin"))):
    p = _active(pid)
    d = await req.json() if (await req.body()) else {}
    nets = d.get("networks") or []
    if isinstance(nets, str):
        nets = re.split(r"[\s,;]+", nets)
    return await discover(p, nets)


@router.post("/api/plugins/{pid}/probe")
async def plugin_probe(pid: str, req: Request, u=Depends(rolle("admin"))):
    p = _active(pid)
    d = await req.json()
    url = _norm_url(d.get("url"))
    info = await asyncio.to_thread(probe, url, p.connect()["app"], bool(d.get("verify_tls")), p)
    return {"url": url, **info}


@router.post("/api/plugins/{pid}/connect")
async def plugin_connect(pid: str, req: Request, u=Depends(rolle("admin"))):
    p = _active(pid)
    d = await req.json()
    url, verify = _norm_url(d.get("url")), bool(d.get("verify_tls"))
    app = p.connect()["app"]
    info = await asyncio.to_thread(probe, url, app, verify, p)
    if connector(p):
        return await _connect_builtin(p, pid, d, url, verify, info, u)
    if info.get("setup_needed"):
        raise HTTPException(400, f"{info.get('name', app)} ist noch nicht eingerichtet – erst dort den ersten Admin anlegen")
    host = req.headers.get("x-forwarded-host") or req.url.hostname or "MIRA"
    body = {"username": (d.get("username") or "").strip(), "password": d.get("password") or "",
            "client": f"MIRA ({host})", "role": d.get("role") if d.get("role") in LEVEL_RANK else "admin"}

    def pair():
        with _client(verify) as c:
            return c.post(url + (info.get("pair") or "/api/connect/pair"), json=body)
    try:
        r = await asyncio.to_thread(pair)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"Verbinden fehlgeschlagen: {e.__class__.__name__}") from e
    if r.status_code != 200:
        try:
            msg = r.json().get("detail")
        except ValueError:
            msg = None
        _audit(u, "Plugin verbinden fehlgeschlagen", f"{p.m.get('name', pid)} {url}", ok=False, details=str(msg or r.status_code))
        raise HTTPException(400, f"{info.get('name', app)}: {msg or f'HTTP {r.status_code}'}")
    res = r.json()
    with sitzung() as s:
        c = PluginConnection(plugin_id=pid, name=(d.get("name") or info.get("name") or pid).strip()[:128], base_url=url,
                             verify_tls=verify, role=res.get("role", body["role"]), remote_user=body["username"],
                             token_enc=kern.verschluesseln({"token": res["token"], "token_id": res.get("token_id")}),
                             remote_version=str(res.get("version") or info.get("version", "")),
                             status=f"ok: verbunden ({info.get('name', app)} {info.get('version', '')})", checked_at=jetzt(),
                             created_by=u["name"])
        s.add(c)
        s.flush()
        out = _conn_json(c)
    _audit(u, "Plugin verbunden", f"{p.m.get('name', pid)} · {out['name']}", details=f"{url}, Rolle {out['role']}")
    return out


async def _connect_builtin(p, pid, d, url, verify, info, u):
    body = {"username": (d.get("username") or "").strip(), "password": d.get("password") or "",
            "role": d.get("role") if d.get("role") in LEVEL_RANK else "admin"}
    if not body["username"] or not body["password"]:
        raise HTTPException(400, "Benutzer und Passwort fehlen")
    try:
        res = await asyncio.to_thread(connector(p).pair, url, verify, body)
    except HTTPException as e:
        _audit(u, "Plugin verbinden fehlgeschlagen", f"{p.m.get('name', pid)} {url}", ok=False, details=str(e.detail))
        raise
    with sitzung() as s:
        c = PluginConnection(plugin_id=pid, name=(d.get("name") or info.get("name") or pid).strip()[:128], base_url=url,
                             verify_tls=verify, role=res["role"], remote_user=res["remote_user"],
                             token_enc=kern.verschluesseln(res["secret"]), remote_version=str(res.get("version", "")),
                             status=f"ok: verbunden ({info.get('name', pid)} {res.get('version', '')})", checked_at=jetzt(),
                             created_by=u["name"])
        s.add(c)
        s.flush()
        out = _conn_json(c)
    _audit(u, "Plugin verbunden", f"{p.m.get('name', pid)} · {out['name']}",
           details=f"{url}, Rolle {out['role']}, Anmeldung als {res['remote_user']}")
    return out


@router.put("/api/plugins/conns/{cid}")
async def plugin_conn_update(cid: int, req: Request, u=Depends(rolle("admin"))):
    d = await req.json()
    with sitzung() as s:
        c = s.get(PluginConnection, cid)
        if not c:
            raise LookupError("Plugin-Verbindung gibt es nicht")
        if d.get("name"):
            c.name = d["name"].strip()[:128]
        if d.get("base_url"):
            c.base_url = _norm_url(d["base_url"])
        if "verify_tls" in d:
            c.verify_tls = bool(d["verify_tls"])
        out = _conn_json(c)
    _audit(u, "Plugin-Verbindung geändert", out["name"])
    return out


@router.delete("/api/plugins/conns/{cid}")
def plugin_conn_delete(cid: int, u=Depends(rolle("admin"))):
    c = _conn(cid)
    note = ""
    tok = kern.entschluesseln(c.token_enc) or {}
    con = connector(plugin(c.plugin_id)) if _try_plugin(c.plugin_id) else None
    if con:
        note = con.unpair(c)
    elif tok.get("token_id"):        # revoke our own key in the app (works when the key has admin rights)
        try:
            with _client(c.verify_tls, 8) as cl:
                r = cl.delete(f"{c.base_url}/api/connect/tokens/{tok['token_id']}",
                              headers={"Authorization": f"Bearer {tok.get('token', '')}"})
            note = "Schlüssel in der App widerrufen" if r.status_code == 200 else f"Schlüssel bitte in der App widerrufen (HTTP {r.status_code})"
        except httpx.HTTPError:
            note = "App nicht erreichbar – Schlüssel bitte dort unter „Verbundene Apps“ widerrufen"
    with sitzung() as s:
        x = s.get(PluginConnection, cid)
        if x:
            s.delete(x)
    _audit(u, "Plugin-Verbindung entfernt", c.name, details=note)
    return {"ok": True, "note": note}


def _try_plugin(pid: str) -> bool:
    try:
        plugin(pid)
        return True
    except LookupError:
        return False


@router.post("/api/plugins/conns/{cid}/test")
def plugin_conn_test(cid: int, u=Depends(rolle("leser"))):
    c = _conn(cid)
    p = plugin(c.plugin_id)
    ok, msg, info = True, "", None
    if connector(p):
        ok, msg, version = connector(p).check(c)
        with sitzung() as s:
            x = s.get(PluginConnection, cid)
            x.status, x.checked_at = ("ok: " if ok else "fehler: ") + msg[:480], jetzt()
            if version:
                x.remote_version = version
        return {"ok": ok, "meldung": msg}
    try:
        info = probe(c.base_url, p.connect()["app"], c.verify_tls)
        with _client(c.verify_tls, 8) as cl:
            r = cl.get(c.base_url + "/api/me", headers={"Authorization": f"Bearer {_token(c)}"})
        if r.status_code == 401:
            ok, msg = False, "Schlüssel ungültig oder widerrufen – Verbindung neu einrichten"
        elif r.status_code != 200:
            ok, msg = False, f"HTTP {r.status_code}"
        else:
            msg = f"{info.get('name')} {info.get('version', '')} · Rolle {r.json().get('level', c.role)}"
    except HTTPException as e:
        ok, msg = False, e.detail
    except httpx.HTTPError as e:
        ok, msg = False, e.__class__.__name__
    with sitzung() as s:
        x = s.get(PluginConnection, cid)
        x.status, x.checked_at = ("ok: " if ok else "fehler: ") + msg[:480], jetzt()
        if ok and info:
            x.remote_version = str(info.get("version", ""))
    return {"ok": ok, "meldung": msg}


# ---- proxy: the plugin's UI talks to its app through MIRA (token never reaches the browser)
_HOP = {"content-type", "content-disposition", "cache-control", "etag", "last-modified"}


@router.api_route("/api/p/{cid}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def plugin_proxy(cid: int, path: str, req: Request):
    from anmeldung import aktueller
    u = aktueller(req)
    need = "leser" if req.method == "GET" else "operator"
    if {"leser": 1, "operator": 2, "admin": 3}.get(u["rolle"], 0) < {"leser": 1, "operator": 2, "admin": 3}[need]:
        raise HTTPException(403, f"Dafür ist die Rolle „{need}“ nötig")
    c = _conn(cid)
    p = _active(c.plugin_id)
    target = "/" + path
    if not any(target.startswith(x) for x in p.connect()["proxy"]):
        raise HTTPException(403, "Dieser Pfad ist für das Plugin nicht freigegeben")
    level = ROLE_TO_LEVEL.get(u["rolle"], "read")
    con = connector(p)
    if con:
        return await _proxy_builtin(con, c, p, u, req, target, level)
    headers = {"Authorization": f"Bearer {_token(c)}", "X-Acting-User": u["name"], "X-Acting-Role": level,
               "X-Acting-IP": u.get("ip", ""), "Accept": req.headers.get("accept", "application/json")}
    if req.headers.get("content-type"):
        headers["Content-Type"] = req.headers["content-type"]
    body = await req.body()
    try:
        async with httpx.AsyncClient(verify=c.verify_tls, timeout=120) as cl:
            r = await cl.request(req.method, c.base_url + target, params=list(req.query_params.multi_items()),
                                 content=body or None, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{c.name} ist nicht erreichbar ({e.__class__.__name__})") from e
    if r.status_code == 401:     # never let the app's 401 look like an expired MIRA login
        raise HTTPException(502, f"{c.name} lehnt den Zugang ab – Schlüssel widerrufen? Verbindung unter Plugins neu einrichten.")
    out = {k: v for k, v in r.headers.items() if k.lower() in _HOP}
    return Response(content=r.content, status_code=r.status_code, headers=out)


# methods a key role may use with a built-in connector (the app itself knows no MIRA roles)
_METHOD_LEVEL = {"GET": "read", "POST": "write", "PUT": "write", "PATCH": "write", "DELETE": "admin"}


async def _proxy_builtin(con, c, p, u, req: Request, target: str, level: str):
    need = _METHOD_LEVEL.get(req.method, "admin")
    if LEVEL_RANK[need] > min(LEVEL_RANK.get(level, 1), LEVEL_RANK.get(c.role, 1)):
        raise HTTPException(403, {"write": "Ändern", "admin": "Löschen"}.get(need, need)
                            + " ist mit dieser Rolle bzw. Verbindung nicht erlaubt")
    body = await req.body()
    headers = {"accept": req.headers.get("accept", "application/json")}
    if req.headers.get("content-type"):
        headers["content-type"] = req.headers["content-type"]
    try:
        r = await asyncio.to_thread(con.request, c, req.method, target, list(req.query_params.multi_items()),
                                    body or None, headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"{c.name} ist nicht erreichbar ({e.__class__.__name__})") from e
    except HTTPException as e:
        raise HTTPException(502, f"{c.name}: {e.detail}") from e
    if r.status_code == 401:
        raise HTTPException(502, f"{c.name} lehnt die gespeicherte Anmeldung ab – Verbindung unter Plugins neu einrichten.")
    if req.method != "GET":
        ok = r.status_code < 400
        detail = ""
        if not ok:
            try:
                detail = str(r.json().get("message", ""))[:500]
            except ValueError:
                detail = r.text[:300]
        _audit(u, f"{p.m.get('name', p.id)}: {req.method} {target}", c.name, ok=ok, details=detail or f"HTTP {r.status_code}")
    if r.status_code >= 400:          # app errors as {"detail"} like MIRA's own – the UI shows the text
        try:
            msg = r.json().get("message")
        except (ValueError, AttributeError):
            msg = None
        if msg:
            return Response(content=json.dumps({"detail": f"{p.m.get('name', p.id)}: {msg}"}), status_code=r.status_code,
                            media_type="application/json")
    out = {k: v for k, v in r.headers.items() if k.lower() in _HOP}
    return Response(content=r.content, status_code=r.status_code, headers=out)


# ---- plugin files (only for approved plugins; the icon always)
@router.get("/plugins/{pid}/{rel:path}")
def plugin_file(pid: str, rel: str):
    p = plugin(pid)
    if rel != p.m.get("icon"):
        if state_of(p, _states().get(pid)) != "active":
            raise HTTPException(404, "nicht freigegeben")
    f = p.file(rel)
    if not f:
        raise HTTPException(404, "Datei fehlt")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})
