"""Server list (multi-server support): several HAProxy and nginx instances side by side.

Table mira_server – one row per proxy instance:
  kind    haproxy | nginx
  access  haproxy-ui   HAProxy managed through haproxy-ui (SysriX)          → module/haproxy.py
          opnsense     HAProxy plugin (os-haproxy) on an OPNsense, API key   → module/opnsense_haproxy.py
          local        nginx folders mounted into MIRA + Docker socket        → module/nginx.py
          ssh          nginx on another host (SFTP + SSH, optional docker)    → module/nginx.py
Settings are stored as JSON, secrets (passwords, keys) Fernet-encrypted like the connections.
Until 0.3.x HAProxy/nginx were ordinary connections (mira_verbindung); servers.migrate() moves them here.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

import kern
from kern import Basis, jetzt, sitzung

KINDS = {"haproxy": "HAProxy", "nginx": "nginx"}


class Server(Basis):
    __tablename__ = "mira_server"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), index=True)          # haproxy | nginx
    access: Mapped[str] = mapped_column(String(16))                    # haproxy-ui | opnsense | local | ssh
    site: Mapped[str] = mapped_column(String(128), default="")         # location / note, e.g. "Unraid", "Büro"
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(512), default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    migrated_from: Mapped[int | None] = mapped_column(Integer, nullable=True)   # id in mira_verbindung (0.3.x)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=jetzt)


def load(sid: int) -> Server:
    with sitzung() as s:
        x = s.get(Server, sid)
        if not x:
            raise LookupError("Server gibt es nicht")
        s.expunge(x)
        return x


def all_servers(kind: str | None = None) -> list[Server]:
    with sitzung() as s:
        q = select(Server).order_by(Server.kind, Server.sort, Server.name)
        if kind:
            q = q.where(Server.kind == kind)
        out = list(s.scalars(q))
        for x in out:
            s.expunge(x)
        return out


def values(x: Server) -> dict:
    """Settings + decrypted secrets – the same dict shape the modules used for connections."""
    w = dict(x.settings or {})
    w.update(kern.entschluesseln(x.secret_enc))
    w["_id"] = x.id
    w["_name"] = x.name
    w["_access"] = x.access
    return w


def remember_host_key(sid: int, fp: str) -> None:
    """SSH: pin the host key seen on first contact."""
    if not sid:
        return
    with sitzung() as s:
        x = s.get(Server, sid)
        if x and not (x.settings or {}).get("host_key"):
            x.settings = {**(x.settings or {}), "host_key": fp}
    kern.protokoll("MIRA", "server", "SSH-Host-Schlüssel gemerkt", str(sid), True, fp[:80])


def open_fs(w: dict):
    """File/command access for an nginx server (caller closes it)."""
    from module.remote_fs import LocalFS, SshFS
    if w.get("_access") == "ssh":
        return SshFS(w, on_new_host_key=lambda fp: remember_host_key(int(w.get("_id") or 0), fp))
    return LocalFS(w)


def server_dep(kind: str, access: tuple[str, ...] | None = None):
    """FastAPI dependency: loads the server from path parameter sid (kind/access must match).
    nginx servers get an open file system in w["_fs"], closed after the request."""

    def dep(sid: int):
        try:
            x = load(sid)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        if x.kind != kind or (access and x.access not in access):
            raise HTTPException(400, "Server hat eine andere Art")
        if not x.enabled:
            raise HTTPException(409, f"Server „{x.name}“ ist ausgeschaltet")
        w = values(x)
        fs = None
        if kind == "nginx":
            fs = open_fs(w)
            w["_fs"] = fs
        try:
            yield w
        finally:
            if fs:
                fs.close()
    return dep
