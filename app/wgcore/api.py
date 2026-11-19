"""FastAPI routes for WireGuard management. Both hosts (VERA, MIRA) plug in the same routes.

Host interface (duck-typed object):
    store() -> Store
    service(firewall_id) -> Service          (LookupError if unknown/disabled)
    role(level) -> FastAPI dependency         level: "read" | "write" | "admin"; returns {"name", "ip"}
    audit(user, action, target="", ok=True, details="")
"""
from __future__ import annotations

import html
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from wgcore import WgError
from wgcore.keys import qr_data_uri
from wgcore.service import Service, redeem_share
from wgcore import site


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except WgError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


def build_router(host, prefix: str) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=["wireguard"])
    read, write, admin = host.role("read"), host.role("write"), host.role("admin")

    def svc(fw: int):
        s = _run(host.service, fw)
        try:
            yield s
        finally:
            s.close()

    # ---------------------------------------------------------------- firewall level
    @r.post("/{fw}/test")
    def test(s: Service = Depends(svc), u=Depends(read)):
        return {"status": _run(s.test)}

    @r.get("/{fw}/overview")
    def overview(status: int = 1, s: Service = Depends(svc), u=Depends(read)):
        return _run(s.overview, bool(status))

    @r.post("/{fw}/sync")
    def sync(s: Service = Depends(svc), u=Depends(write)):
        res = _run(s.sync)
        host.audit(u, "WireGuard importiert", s.site_name, details=str(res))
        return res

    @r.get("/{fw}/instances/{iid}/next-ip")
    def next_ip(iid: int, s: Service = Depends(svc), u=Depends(read)):
        return {"tunnel_address": _run(s.next_ip, iid)}

    @r.put("/{fw}/instances/{iid}")
    async def instance_update(iid: int, req: Request, s: Service = Depends(svc), u=Depends(write)):
        res = _run(s.update_instance, iid, await req.json())
        host.audit(u, "WireGuard-Instanz geändert", f"{s.site_name}/{res['name']}")
        return res

    # ---------------------------------------------------------------- peers
    @r.post("/{fw}/peers")
    async def peer_create(req: Request, s: Service = Depends(svc), u=Depends(write)):
        d = await req.json()
        res = _run(s.create_peer, d, u.get("name", ""))
        host.audit(u, "WireGuard-Zugang angelegt", f"{s.site_name}/{res['name']}", details=res["tunnel_address"])
        return res

    @r.post("/{fw}/parse-config")
    async def parse_config(req: Request, s: Service = Depends(svc), u=Depends(write)):
        d = _run(Service.parse_config, (await req.json()).get("config", ""))
        d.pop("private_key", None)
        d.pop("preshared_key", None)
        return d

    @r.post("/{fw}/peers/import-config")
    async def peer_import_config(req: Request, s: Service = Depends(svc), u=Depends(write)):
        res = _run(s.import_config, await req.json(), u.get("name", ""))
        host.audit(u, "WireGuard-Konfiguration übernommen" if not res.get("merged") else "WireGuard-Schlüssel nachgetragen",
                   f"{s.site_name}/{res['name']}")
        return res

    @r.put("/{fw}/peers/{pid}")
    async def peer_update(pid: int, req: Request, s: Service = Depends(svc), u=Depends(write)):
        d = await req.json()
        res = _run(s.update_peer, pid, d)
        host.audit(u, "WireGuard-Zugang geändert", f"{s.site_name}/{res['name']}",
                   details=", ".join(k for k in d if k not in ("private_key", "preshared_key")) +
                   (" + Schlüssel nachgetragen" if d.get("private_key") else ""))
        return res

    @r.delete("/{fw}/peers/{pid}")
    def peer_delete(pid: int, keep: int = 0, s: Service = Depends(svc), u=Depends(write)):
        _run(s.delete_peer, pid, bool(keep))
        host.audit(u, "WireGuard-Zugang gelöscht", f"{s.site_name}/#{pid}")
        return {"ok": True}

    @r.post("/{fw}/peers/{pid}/rotate")
    def peer_rotate(pid: int, s: Service = Depends(svc), u=Depends(write)):
        res = _run(s.rotate_keys, pid)
        host.audit(u, "WireGuard-Schlüssel erneuert", f"{s.site_name}/{res['name']}")
        return res

    @r.get("/{fw}/peers/{pid}/config")
    def peer_config(pid: int, reveal: int = 0, s: Service = Depends(svc), u=Depends(read)):
        if reveal:
            _need(host, u, "write")
        res = _run(s.config, pid, bool(reveal))
        if reveal:
            host.audit(u, "WireGuard-Konfiguration angezeigt", f"{s.site_name}/{res['peer']['name']}")
        return res

    @r.get("/{fw}/peers/{pid}/download")
    def peer_download(pid: int, format: str = "conf", s: Service = Depends(svc), u=Depends(write)):
        res = _run(s.config, pid, True, True)
        host.audit(u, "WireGuard-Konfiguration heruntergeladen", f"{s.site_name}/{res['peer']['name']}", details=format)
        return _file(res, format)

    @r.get("/{fw}/peers/{pid}/shares")
    def shares(pid: int, s: Service = Depends(svc), u=Depends(write)):
        return {"shares": _run(s.list_shares, pid)}

    @r.post("/{fw}/peers/{pid}/shares")
    async def share_create(pid: int, req: Request, s: Service = Depends(svc), u=Depends(write)):
        d = await req.json()
        res = _run(s.create_share, pid, d.get("hours", 24), d.get("max_uses", 1), u.get("name", ""))
        host.audit(u, "WireGuard-Freigabelink erstellt", f"{s.site_name}/#{pid}",
                   details=f"{d.get('hours', 24)} h, {d.get('max_uses', 1)}× ")
        return res

    @r.delete("/{fw}/peers/{pid}/shares/{sid}")
    def share_delete(pid: int, sid: int, s: Service = Depends(svc), u=Depends(write)):
        _run(s.delete_share, pid, sid)
        return {"ok": True}

    # ---------------------------------------------------------------- site links
    @r.get("/site-links")
    def links(u=Depends(read)):
        return {"links": site.list_links(host.store())}

    @r.post("/site-links")
    async def link_create(req: Request, u=Depends(admin)):
        d = await req.json()
        a = _run(host.service, int(d.get("firewall_a_id") or 0))
        b = _run(host.service, int(d.get("firewall_b_id") or 0))
        try:
            res = _run(site.create_link, host.store(), a, b, d, u.get("name", ""))
        except HTTPException as e:
            host.audit(u, "Standortverbindung fehlgeschlagen", d.get("name", ""), ok=False, details=str(e.detail))
            raise
        finally:
            a.close()
            b.close()
        host.audit(u, "Standortverbindung angelegt", res["name"], details=f"{res['networks_a']} ↔ {res['networks_b']}")
        return res

    @r.delete("/site-links/{lid}")
    def link_delete(lid: int, u=Depends(admin)):
        links_ = {x["id"]: x for x in site.list_links(host.store())}
        if lid not in links_:
            raise HTTPException(404, "Standortverbindung nicht gefunden")
        l = links_[lid]
        sides = {}
        for side in ("a", "b"):
            try:
                sides[side] = host.service(l[f"firewall_{side}_id"])
            except LookupError:
                sides[side] = None
        try:
            warnings = _run(site.delete_link, host.store(), lid, sides)
        finally:
            for s_ in sides.values():
                if s_:
                    s_.close()
        host.audit(u, "Standortverbindung gelöscht", l["name"], details="; ".join(warnings))
        return {"ok": True, "warnings": warnings}

    return r


def _need(host, u, level):
    order = {"read": 1, "write": 2, "admin": 3}
    if order.get(u.get("level", "read"), 1) < order[level]:
        raise HTTPException(403, "Dafür fehlen die Rechte (Schlüssel anzeigen)")


def _file(res: dict, fmt: str) -> Response:
    name = res["filename"]
    if fmt == "qr":
        try:
            import segno
        except ImportError as e:
            raise HTTPException(500, "QR-Erzeugung nicht installiert (segno)") from e
        buf = io.BytesIO()
        segno.make(res["text"], error="m").save(buf, kind="png", scale=8, border=3)
        return Response(buf.getvalue(), media_type="image/png",
                        headers={"Content-Disposition": f'attachment; filename="{name[:-5]}.png"'})
    return Response(res["text"], media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def build_share_router(host, prefix: str = "/wg-share", product: str = "WireGuard") -> APIRouter:
    """Public routes (no login) for time-limited config links."""
    r = APIRouter(prefix=prefix, tags=["wireguard-share"])

    def load(token, consume, check_uses=True):
        try:
            pid, fw = redeem_share(host.store(), token, consume, check_uses)
            s = host.service(fw)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        try:
            return _run(s.config, pid, True, True)
        finally:
            s.close()

    @r.get("/{token}", response_class=HTMLResponse)
    def page(token: str):
        try:
            res = load(token, consume=True)
        except HTTPException as e:
            return HTMLResponse(_page(product, "Link ungültig", f"<p>{html.escape(str(e.detail))}</p>"), status_code=e.status_code)
        qr = res.get("qr") or qr_data_uri(res["text"])
        t = html.escape(token)
        body = (f"<p>Konfiguration für <b>{html.escape(res['peer']['name'])}</b>. Mit der WireGuard-App den QR-Code scannen "
                f"oder die Datei herunterladen und importieren.</p>"
                + (f'<img src="{qr}" alt="QR-Code" class="qr">' if qr else "")
                + f'<p><a class="btn" href="{prefix}/{t}/download">Datei {html.escape(res["filename"])} herunterladen</a></p>'
                + f"<details><summary>Konfiguration als Text</summary><pre>{html.escape(res['text'])}</pre></details>"
                + "<p class='hint'>Diesen Link nicht weitergeben – er enthält den privaten Schlüssel.</p>")
        return HTMLResponse(_page(product, "WireGuard-Zugang", body),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @r.get("/{token}/download")
    def download(token: str):
        res = load(token, consume=False, check_uses=False)
        return _file(res, "conf")

    return r


def _page(product: str, title: str, body: str) -> str:
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{html.escape(title)} – {html.escape(product)}</title><style>
body{{margin:0;font:15px/1.5 system-ui,Segoe UI,sans-serif;background:#111418;color:#e8eaed}}
main{{max-width:560px;margin:6vh auto;padding:0 16px}}h1{{font-size:20px}}
.qr{{display:block;width:min(320px,100%);margin:16px auto;background:#fff;border-radius:8px}}
.btn{{display:inline-block;background:#f28c28;color:#111;padding:9px 14px;border-radius:8px;text-decoration:none;font-weight:600}}
pre{{background:#1c2026;padding:12px;border-radius:8px;overflow:auto;font-size:12.5px}}.hint{{color:#9aa0a6;font-size:13px}}
@media (prefers-color-scheme:light){{body{{background:#f6f7f9;color:#1d2228}}pre{{background:#e9ecf0}}.hint{{color:#5f6670}}}}
</style></head><body><main><h1>{html.escape(title)}</h1>{body}<p class="hint">{html.escape(product)}</p></main></body></html>"""
