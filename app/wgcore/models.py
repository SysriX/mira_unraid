"""Database tables (prefix wg_). Work with MariaDB/MySQL and SQLite."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Instance(Base):
    """A WireGuard instance (server/interface wgN) on one OPNsense."""
    __tablename__ = "wg_instance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firewall_id: Mapped[int] = mapped_column(Integer, index=True)
    opn_uuid: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    interface: Mapped[str] = mapped_column(String(16), default="")
    listen_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tunnel_address: Mapped[str] = mapped_column(String(512), default="")
    public_key: Mapped[str] = mapped_column(String(64), default="")
    mtu: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    present: Mapped[bool] = mapped_column(Boolean, default=True)       # still exists in OPNsense
    role: Mapped[str] = mapped_column(String(16), default="clients")    # clients | site
    # defaults for generated client configs (stored only here, not in OPNsense)
    endpoint: Mapped[str] = mapped_column(String(255), default="")      # host[:port]
    client_dns: Mapped[str] = mapped_column(String(255), default="")
    client_allowed_ips: Mapped[str] = mapped_column(String(1024), default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Peer(Base):
    """A peer (access): road-warrior client or remote site."""
    __tablename__ = "wg_peer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    firewall_id: Mapped[int] = mapped_column(Integer, index=True)
    instance_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    opn_uuid: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16), default="client")     # client | site
    owner: Mapped[str] = mapped_column(String(128), default="")
    device: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    public_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    private_key_enc: Mapped[str] = mapped_column(Text, default="")
    preshared_key_enc: Mapped[str] = mapped_column(Text, default="")
    tunnel_address: Mapped[str] = mapped_column(String(1024), default="")   # OPNsense "Allowed IPs" of the peer
    client_allowed_ips: Mapped[str] = mapped_column(String(1024), default="")
    client_dns: Mapped[str] = mapped_column(String(255), default="")
    endpoint_host: Mapped[str] = mapped_column(String(255), default="")
    endpoint_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keepalive: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(String(16), default="import")    # import | created
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    config_changed: Mapped[bool] = mapped_column(Boolean, default=False)  # client must fetch a new config
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_handshake: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    remote_endpoint: Mapped[str] = mapped_column(String(128), default="")


class SiteLink(Base):
    """Site-to-site connection between two OPNsense firewalls."""
    __tablename__ = "wg_site_link"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    firewall_a_id: Mapped[int] = mapped_column(Integer)
    firewall_b_id: Mapped[int] = mapped_column(Integer)
    instance_a_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instance_b_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peer_a_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # peer "B" as seen on firewall A
    peer_b_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tunnel_net: Mapped[str] = mapped_column(String(64), default="")
    port_a: Mapped[int] = mapped_column(Integer, default=51821)
    port_b: Mapped[int] = mapped_column(Integer, default=51821)
    networks_a: Mapped[str] = mapped_column(String(1024), default="")
    networks_b: Mapped[str] = mapped_column(String(1024), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Share(Base):
    """Time-limited link to fetch one peer's config without logging in."""
    __tablename__ = "wg_share"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_id: Mapped[int] = mapped_column(Integer, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)    # 0 = unlimited until expiry
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Store:
    """Database access plus the host's encryption for private keys."""

    def __init__(self, engine, encrypt: Callable[[str], str], decrypt: Callable[[str], str]):
        self.engine = engine
        Base.metadata.create_all(engine)
        self._sm = sessionmaker(engine, expire_on_commit=False)
        self._enc, self._dec = encrypt, decrypt

    @contextmanager
    def session(self) -> Session:
        s = self._sm()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def encrypt(self, value: str) -> str:
        return self._enc(value) if value else ""

    def decrypt(self, token: str) -> str:
        return (self._dec(token) or "") if token else ""
