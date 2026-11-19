"""Business logic: import from OPNsense, create/change/delete peers, configs, status, shares."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from wgcore import WgError
from wgcore.keys import generate_keypair, generate_psk, public_from_private, qr_data_uri, valid_key
from wgcore.models import Instance, Peer, Share, Store, now
from wgcore.opnsense import OPNsense

ONLINE_SECONDS = 180


# ------------------------------------------------------------------ helpers
def split_list(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[,\s;]+", str(value or ""))
    return [x.strip() for x in items if x and x.strip()]


def networks(value, strict_host: bool = False) -> list[str]:
    out = []
    for item in split_list(value):
        try:
            if "/" not in item:
                item += "/128" if ":" in item else "/32"
            net = ipaddress.ip_network(item, strict=False)
        except ValueError as e:
            raise WgError(f"„{item}“ ist keine gültige Adresse/kein gültiges Netz") from e
        out.append(item if strict_host else str(net))
    return out


def opn_name(name: str) -> str:
    """OPNsense allows only [0-9a-zA-Z._-] (max 64) in names."""
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", (name or "").strip()).strip("_")
    return (s or "peer")[:64]


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def instance_dict(i: Instance) -> dict:
    return {"id": i.id, "firewall_id": i.firewall_id, "opn_uuid": i.opn_uuid, "name": i.name, "interface": i.interface,
            "listen_port": i.listen_port, "tunnel_address": i.tunnel_address, "public_key": i.public_key, "mtu": i.mtu,
            "enabled": i.enabled, "present": i.present, "role": i.role, "endpoint": i.endpoint,
            "client_dns": i.client_dns, "client_allowed_ips": i.client_allowed_ips, "synced_at": iso(i.synced_at)}


def peer_dict(p: Peer) -> dict:
    hs = aware(p.last_handshake)
    online = bool(hs and (now() - hs).total_seconds() < ONLINE_SECONDS)
    return {"id": p.id, "firewall_id": p.firewall_id, "instance_id": p.instance_id, "opn_uuid": p.opn_uuid,
            "name": p.name, "kind": p.kind, "owner": p.owner, "device": p.device, "description": p.description,
            "public_key": p.public_key, "has_private_key": bool(p.private_key_enc), "has_psk": bool(p.preshared_key_enc),
            "tunnel_address": p.tunnel_address, "client_allowed_ips": p.client_allowed_ips, "client_dns": p.client_dns,
            "endpoint_host": p.endpoint_host, "endpoint_port": p.endpoint_port, "keepalive": p.keepalive,
            "enabled": p.enabled, "origin": p.origin, "present": p.present, "config_changed": p.config_changed,
            "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "synced_at": iso(p.synced_at),
            "last_handshake": iso(hs), "online": online, "rx_bytes": p.rx_bytes, "tx_bytes": p.tx_bytes,
            "remote_endpoint": p.remote_endpoint}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ service per firewall
class Service:
    """fw: url, api_key, api_secret, verify_tls, endpoint_host, client_allowed_ips, client_dns, site_name"""

    def __init__(self, store: Store, firewall_id: int, fw: dict):
        self.store = store
        self.fw_id = firewall_id
        self.fw = fw
        self._opn: OPNsense | None = None

    @property
    def opn(self) -> OPNsense:
        if not self._opn:
            v = self.fw.get("verify_tls", True)
            self._opn = OPNsense(self.fw.get("url", ""), self.fw.get("api_key", ""), self.fw.get("api_secret", ""),
                                 v is True or str(v).lower() in ("1", "true", "on", "ja", "yes"))
        return self._opn

    def close(self):
        if self._opn:
            self._opn.close()

    @property
    def site_name(self) -> str:
        return self.fw.get("site_name") or self.fw.get("name") or f"Firewall {self.fw_id}"

    def test(self) -> str:
        s, c = self.opn.servers(), self.opn.clients()
        return f"Verbunden – {len(s)} Instanz(en), {len(c)} Peer(s)"

    # ---------------------------------------------------------- import / sync
    def sync(self) -> dict:
        servers, clients = self.opn.servers(), self.opn.clients()
        owner_of = {}
        for s in servers:
            for cu in s["peers"]:
                owner_of.setdefault(cu, s["uuid"])
        stats = {"instances": len(servers), "peers": len(clients), "new": 0, "missing": 0, "key_changed": 0}
        with self.store.session() as db:
            inst = {i.opn_uuid: i for i in db.scalars(select(Instance).where(Instance.firewall_id == self.fw_id))}
            for s in servers:
                i = inst.get(s["uuid"])
                if not i:
                    i = Instance(firewall_id=self.fw_id, opn_uuid=s["uuid"])
                    db.add(i)
                    inst[s["uuid"]] = i
                i.name, i.enabled, i.listen_port = s["name"], s["enabled"], s["port"]
                i.interface = f"wg{s['instance']}" if s["instance"] != "" else i.interface
                i.tunnel_address, i.public_key, i.mtu = s["tunnel_address"], s["public_key"], s["mtu"]
                if not i.client_dns and s["dns"]:
                    i.client_dns = s["dns"]
                i.present, i.synced_at = True, now()
            seen = {s["uuid"] for s in servers}
            for i in inst.values():
                if i.opn_uuid not in seen:
                    i.present = False
            db.flush()
            inst_id = {u: i.id for u, i in inst.items()}

            peers = list(db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id)))
            by_uuid = {p.opn_uuid: p for p in peers if p.opn_uuid}
            by_key = {p.public_key: p for p in peers if p.public_key}
            seen_peers = set()
            for c in clients:
                p = by_uuid.get(c["uuid"]) or by_key.get(c["public_key"])
                if not p:
                    p = Peer(firewall_id=self.fw_id, origin="import", name=c["name"],
                             kind="site" if c["server_address"] else "client", keepalive=c["keepalive"])
                    db.add(p)
                    stats["new"] += 1
                if p.public_key and p.public_key != c["public_key"]:
                    p.private_key_enc = ""         # key changed outside → stored private key no longer fits
                    stats["key_changed"] += 1
                p.opn_uuid, p.public_key, p.enabled = c["uuid"], c["public_key"], c["enabled"]
                if opn_name(p.name) != c["name"]:          # renamed in OPNsense (keeps our long name otherwise)
                    p.name = c["name"]
                p.tunnel_address = c["tunnel_address"]
                p.endpoint_host, p.endpoint_port = c["server_address"], c["server_port"]
                if p.kind == "site":
                    p.keepalive = c["keepalive"]
                if c["psk"]:
                    if self.store.decrypt(p.preshared_key_enc) != c["psk"]:
                        p.preshared_key_enc = self.store.encrypt(c["psk"])
                else:
                    p.preshared_key_enc = ""
                if c["uuid"] in owner_of:
                    p.instance_id = inst_id.get(owner_of[c["uuid"]])
                p.present, p.synced_at = True, now()
                db.flush()
                seen_peers.add(p.id)
            for p in peers:
                if p.id not in seen_peers and p.present:
                    p.present = False
                    stats["missing"] += 1
        return stats

    # ---------------------------------------------------------- reading
    def overview(self, with_status: bool = True) -> dict:
        status_error = None
        if with_status:
            try:
                self.refresh_status()
            except WgError as e:
                status_error = str(e)
        with self.store.session() as db:
            inst = list(db.scalars(select(Instance).where(Instance.firewall_id == self.fw_id).order_by(Instance.name)))
            peers = list(db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id).order_by(Peer.name)))
        return {"instances": [instance_dict(i) for i in inst], "peers": [peer_dict(p) for p in peers],
                "status_error": status_error, "site_name": self.site_name,
                "defaults": {"endpoint_host": self.fw.get("endpoint_host", ""),
                             "client_allowed_ips": self.fw.get("client_allowed_ips", ""),
                             "client_dns": self.fw.get("client_dns", "")}}

    def refresh_status(self) -> int:
        rows = self.opn.show()
        info = {}
        for r in rows:
            if str(r.get("type", "peer")) != "peer":
                continue
            key = r.get("public-key") or r.get("public_key") or ""
            if not key:
                continue
            hs = r.get("latest-handshake") or r.get("latest_handshake") or 0
            try:
                hs = int(hs)
            except (TypeError, ValueError):
                hs = 0
            info[key] = (datetime.fromtimestamp(hs, timezone.utc) if hs > 0 else None,
                         int(r.get("transfer-rx") or 0), int(r.get("transfer-tx") or 0), str(r.get("endpoint") or ""))
        if not info:
            return 0
        with self.store.session() as db:
            for p in db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id)):
                if p.public_key in info:
                    hs, rx, tx, ep = info[p.public_key]
                    if hs:
                        p.last_handshake = hs
                    p.rx_bytes, p.tx_bytes = rx, tx
                    if ep and ep != "(none)":
                        p.remote_endpoint = ep[:128]
        return len(info)

    def _instance(self, db, iid) -> Instance:
        i = db.get(Instance, int(iid)) if iid else None
        if not i or i.firewall_id != self.fw_id:
            raise WgError("Instanz nicht gefunden")
        return i

    def _peer(self, db, pid) -> Peer:
        p = db.get(Peer, int(pid))
        if not p or p.firewall_id != self.fw_id:
            raise LookupError("Zugang nicht gefunden")
        return p

    # ---------------------------------------------------------- addresses
    def next_ip(self, iid: int) -> str:
        with self.store.session() as db:
            i = self._instance(db, iid)
            used_peers = [p.tunnel_address for p in db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id))]
            return self._next_ip(i, used_peers)

    @staticmethod
    def _next_ip(i: Instance, used_lists: list[str]) -> str:
        ifaces = []
        for a in split_list(i.tunnel_address):
            try:
                ifaces.append(ipaddress.ip_interface(a))
            except ValueError:
                pass
        v4 = [x for x in ifaces if x.version == 4]
        if not v4:
            raise WgError("Die Instanz hat keine IPv4-Tunneladresse – bitte die Adresse selbst eintragen")
        used = {x.ip for x in ifaces}
        for lst in used_lists:
            for a in split_list(lst):
                try:
                    used.add(ipaddress.ip_interface(a).ip)
                except ValueError:
                    pass
        out = []
        for iface in v4[:1]:
            for host in iface.network.hosts():
                if host not in used:
                    out.append(f"{host}/32")
                    offset = int(host) - int(iface.network.network_address)
                    for v6 in [x for x in ifaces if x.version == 6][:1]:
                        cand = ipaddress.ip_address(int(v6.network.network_address) + offset)
                        if cand in v6.network and cand not in used:
                            out.append(f"{cand}/128")
                    return ",".join(out)
        raise WgError("Im Tunnelnetz der Instanz ist keine Adresse mehr frei")

    def _check_address_free(self, db, tunnel: str, except_id: int | None = None):
        mine = set()
        for a in split_list(tunnel):
            mine.add(ipaddress.ip_interface(a).ip)
        for p in db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id)):
            if p.id == except_id or not p.present:
                continue
            for a in split_list(p.tunnel_address):
                try:
                    if ipaddress.ip_interface(a).ip in mine and ipaddress.ip_interface(a).network.prefixlen in (32, 128):
                        raise WgError(f"Adresse {a} benutzt schon „{p.name}“")
                except ValueError:
                    pass

    def _endpoint(self, i: Instance) -> str:
        ep = (i.endpoint or "").strip()
        host = self.fw.get("endpoint_host", "").strip()
        if ep:
            if ep.startswith("[") and "]:" in ep:
                return ep                                   # [v6]:port
            if ep.endswith("]"):
                return f"{ep}:{i.listen_port}"              # [v6]
            if ep.count(":") == 1:
                return ep                                   # host:port
            if ep.count(":") > 1:
                return f"[{ep}]:{i.listen_port}"            # bare v6
            return f"{ep}:{i.listen_port}"
        if not host:
            raise WgError("Für die Konfiguration fehlt die öffentliche Adresse (Endpoint) – bei der Instanz oder der Firewall eintragen")
        return f"{host}:{i.listen_port}"

    def _default_allowed(self, i: Instance) -> str:
        if i.client_allowed_ips:
            return i.client_allowed_ips
        if self.fw.get("client_allowed_ips"):
            return self.fw["client_allowed_ips"]
        return ",".join(str(ipaddress.ip_interface(a).network) for a in split_list(i.tunnel_address))

    # ---------------------------------------------------------- create / import config
    def create_peer(self, d: dict, user: str = "") -> dict:
        """Creates the peer on OPNsense, links it to the instance, reloads WireGuard → active immediately."""
        name = (d.get("name") or "").strip()
        if not name:
            raise WgError("Name fehlt")
        with self.store.session() as db:
            i = self._instance(db, d.get("instance_id"))
            if not i.present:
                raise WgError("Diese Instanz gibt es auf der OPNsense nicht mehr")
            if d.get("private_key"):
                priv = d["private_key"].strip()
                pub = public_from_private(priv)
            else:
                priv, pub = generate_keypair()
            if db.scalar(select(Peer).where(Peer.firewall_id == self.fw_id, Peer.public_key == pub, Peer.present.is_(True))):
                raise WgError("Ein Zugang mit diesem Schlüssel existiert bereits")
            psk = (d.get("preshared_key") or "").strip()
            if psk and not valid_key(psk):
                raise WgError("Pre-Shared Key ist ungültig")
            if not psk and d.get("use_psk", True) not in (False, "false", "0", 0):
                psk = generate_psk()
            tunnel = ",".join(networks(d.get("tunnel_address"), strict_host=True)) if d.get("tunnel_address") else \
                self._next_ip(i, [p.tunnel_address for p in db.scalars(select(Peer).where(Peer.firewall_id == self.fw_id))])
            self._check_address_free(db, tunnel)
            allowed = ",".join(networks(d.get("client_allowed_ips"))) if d.get("client_allowed_ips") else self._default_allowed(i)
            keepalive = int(d["keepalive"]) if str(d.get("keepalive") or "").strip() else 25
            enabled = d.get("enabled", True) not in (False, "false", "0", 0)
            uuid = self.opn.add_client({"enabled": "1" if enabled else "0", "name": opn_name(name), "pubkey": pub, "psk": psk,
                                        "tunneladdress": tunnel, "serveraddress": "", "serverport": "", "keepalive": ""})
            try:
                self.opn.link_peer(i.opn_uuid, uuid)
                self.opn.reconfigure()
            except Exception:
                try:
                    self.opn.unlink_everywhere(uuid)
                    self.opn.del_client(uuid)
                except Exception:  # noqa: BLE001
                    pass
                raise
            p = Peer(firewall_id=self.fw_id, instance_id=i.id, opn_uuid=uuid, name=name, kind="client",
                     owner=d.get("owner", ""), device=d.get("device", ""), description=d.get("description", ""),
                     public_key=pub, private_key_enc=self.store.encrypt(priv), preshared_key_enc=self.store.encrypt(psk),
                     tunnel_address=tunnel, client_allowed_ips=allowed, client_dns=(d.get("client_dns") or i.client_dns or self.fw.get("client_dns", "")),
                     keepalive=keepalive, enabled=enabled, origin="created", present=True, synced_at=now())
            db.add(p)
            db.flush()
            return peer_dict(p)

    @staticmethod
    def parse_config(text: str) -> dict:
        """Reads a wg-quick client config (.conf) → fields for create_peer."""
        section, out = None, {"peer": {}}
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("["):
                section = line.strip("[]").lower()
                continue
            if "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            k = k.lower()
            if section == "interface":
                out[k] = v
            elif section == "peer" and not out["peer"].get("_done"):
                out["peer"][k] = v
        if "privatekey" not in out:
            raise WgError("In der Konfiguration fehlt [Interface] PrivateKey")
        res = {"private_key": out["privatekey"], "tunnel_address": out.get("address", ""), "client_dns": out.get("dns", ""),
               "client_allowed_ips": out["peer"].get("allowedips", ""), "preshared_key": out["peer"].get("presharedkey", ""),
               "keepalive": out["peer"].get("persistentkeepalive", ""), "server_public_key": out["peer"].get("publickey", ""),
               "endpoint": out["peer"].get("endpoint", "")}
        res["public_key"] = public_from_private(res["private_key"])
        return res

    def import_config(self, d: dict, user: str = "") -> dict:
        cfg = self.parse_config(d.get("config", ""))
        with self.store.session() as db:
            i = self._instance(db, d.get("instance_id"))
            if cfg["server_public_key"] and i.public_key and cfg["server_public_key"] != i.public_key:
                raise WgError(f"Die Konfiguration gehört zu einem anderen Server-Schlüssel als Instanz „{i.name}“")
            existing = db.scalar(select(Peer).where(Peer.firewall_id == self.fw_id, Peer.public_key == cfg["public_key"]))
            if existing and existing.present:
                existing.private_key_enc = self.store.encrypt(cfg["private_key"])
                if cfg["preshared_key"]:
                    existing.preshared_key_enc = self.store.encrypt(cfg["preshared_key"])
                if cfg["client_allowed_ips"]:
                    existing.client_allowed_ips = cfg["client_allowed_ips"]
                if cfg["client_dns"]:
                    existing.client_dns = cfg["client_dns"]
                existing.updated_at = now()
                return {**peer_dict(existing), "merged": True}
        tunnel = ",".join(str(ipaddress.ip_interface(a).ip) + ("/128" if ":" in a else "/32") for a in split_list(cfg["tunnel_address"]))
        return {**self.create_peer({**d, **cfg, "tunnel_address": tunnel or d.get("tunnel_address", ""),
                                    "name": d.get("name") or "import"}, user), "merged": False}

    # ---------------------------------------------------------- change
    def update_peer(self, pid: int, d: dict) -> dict:
        with self.store.session() as db:
            p = self._peer(db, pid)
            opn = {}
            relink = None
            if "name" in d and d["name"].strip() and d["name"].strip() != p.name:
                p.name = d["name"].strip()
                opn["name"] = opn_name(p.name)
            for k in ("owner", "device", "description"):
                if k in d:
                    setattr(p, k, d[k] or "")
            if "client_allowed_ips" in d:
                v = ",".join(networks(d["client_allowed_ips"]))
                if v != p.client_allowed_ips:
                    p.client_allowed_ips, p.config_changed = v, True
            if "client_dns" in d and (d["client_dns"] or "") != p.client_dns:
                p.client_dns, p.config_changed = d["client_dns"] or "", True
            if "keepalive" in d:
                ka = int(d["keepalive"]) if str(d["keepalive"] or "").strip() else None
                if p.kind == "site" and ka != p.keepalive:
                    opn["keepalive"] = str(ka or "")
                p.keepalive = ka
            if "tunnel_address" in d:
                v = ",".join(networks(d["tunnel_address"], strict_host=True))
                if v != p.tunnel_address:
                    self._check_address_free(db, v, p.id)
                    p.tunnel_address, p.config_changed = v, True
                    opn["tunneladdress"] = v
            if "enabled" in d:
                en = d["enabled"] not in (False, "false", "0", 0)
                if en != p.enabled:
                    p.enabled = en
                    opn["enabled"] = "1" if en else "0"
            if "endpoint_host" in d and p.kind == "site":
                p.endpoint_host = d["endpoint_host"] or ""
                opn["serveraddress"] = p.endpoint_host
            if "endpoint_port" in d and p.kind == "site":
                p.endpoint_port = int(d["endpoint_port"]) if str(d["endpoint_port"] or "").strip() else None
                opn["serverport"] = str(p.endpoint_port or "")
            if d.get("private_key"):
                priv = d["private_key"].strip()
                if public_from_private(priv) != p.public_key:
                    raise WgError("Der private Schlüssel passt nicht zum öffentlichen Schlüssel dieses Zugangs")
                p.private_key_enc = self.store.encrypt(priv)
            if d.get("preshared_key"):
                if not valid_key(d["preshared_key"]):
                    raise WgError("Pre-Shared Key ist ungültig")
                p.preshared_key_enc = self.store.encrypt(d["preshared_key"].strip())
                opn["psk"] = d["preshared_key"].strip()
            if "instance_id" in d and d["instance_id"] and int(d["instance_id"]) != (p.instance_id or 0):
                new_i = self._instance(db, d["instance_id"])
                old_i = db.get(Instance, p.instance_id) if p.instance_id else None
                relink = (old_i.opn_uuid if old_i else None, new_i.opn_uuid)
                p.instance_id, p.config_changed = new_i.id, True
            if (opn or relink) and p.present and p.opn_uuid:
                if opn:
                    self.opn.set_client(p.opn_uuid, opn)
                if relink:
                    if relink[0]:
                        self.opn.link_peer(relink[0], p.opn_uuid, linked=False)
                    self.opn.link_peer(relink[1], p.opn_uuid)
                self.opn.reconfigure()
            p.updated_at = now()
            return peer_dict(p)

    def rotate_keys(self, pid: int) -> dict:
        with self.store.session() as db:
            p = self._peer(db, pid)
            if not p.present or not p.opn_uuid:
                raise WgError("Zugang ist auf der OPNsense nicht vorhanden")
            priv, pub = generate_keypair()
            psk = generate_psk()
            self.opn.set_client(p.opn_uuid, {"pubkey": pub, "psk": psk})
            self.opn.reconfigure()
            p.public_key, p.private_key_enc, p.preshared_key_enc = pub, self.store.encrypt(priv), self.store.encrypt(psk)
            p.config_changed, p.updated_at = True, now()
            return peer_dict(p)

    def delete_peer(self, pid: int, keep_record: bool = False) -> None:
        with self.store.session() as db:
            p = self._peer(db, pid)
            if p.opn_uuid and p.present:
                self.opn.unlink_everywhere(p.opn_uuid)
                self.opn.del_client(p.opn_uuid)
                self.opn.reconfigure()
            if keep_record:
                p.present, p.enabled, p.updated_at = False, False, now()
            else:
                for s in db.scalars(select(Share).where(Share.peer_id == p.id)):
                    db.delete(s)
                db.delete(p)

    def update_instance(self, iid: int, d: dict) -> dict:
        with self.store.session() as db:
            i = self._instance(db, iid)
            if "endpoint" in d:
                i.endpoint = (d["endpoint"] or "").strip()
            if "client_dns" in d:
                i.client_dns = (d["client_dns"] or "").strip()
            if "client_allowed_ips" in d:
                i.client_allowed_ips = ",".join(networks(d["client_allowed_ips"])) if d["client_allowed_ips"] else ""
            if d.get("role") in ("clients", "site"):
                i.role = d["role"]
            return instance_dict(i)

    # ---------------------------------------------------------- configs
    def config(self, pid: int, reveal: bool = True, mark_fetched: bool = False) -> dict:
        """reveal=False → keys masked (view-only)."""
        with self.store.session() as db:
            p = self._peer(db, pid)
            i = db.get(Instance, p.instance_id) if p.instance_id else None
            if not i:
                raise WgError("Der Zugang ist keiner Instanz zugeordnet – erst neu importieren oder Instanz wählen")
            if p.kind == "site":
                raise WgError("Standort-Peers haben keine Geräte-Konfiguration")
            priv = self.store.decrypt(p.private_key_enc)
            if reveal and not priv:
                raise WgError("Für diesen (importierten) Zugang ist kein privater Schlüssel gespeichert. "
                              "OPNsense speichert ihn nicht – entweder die vorhandene .conf des Geräts einlesen "
                              "(„Schlüssel nachtragen“) oder „Neue Schlüssel“ erzeugen.")
            psk = self.store.decrypt(p.preshared_key_enc)
            addr = ",".join(str(ipaddress.ip_interface(a).ip) + ("/128" if ":" in a else "/32")
                            for a in split_list(p.tunnel_address) if ipaddress.ip_interface(a).network.prefixlen in (32, 128)) \
                or p.tunnel_address
            mask = "•••••• (verborgen)"
            lines = ["[Interface]", f"# {p.name}" + (f" – {p.owner}" if p.owner else "") + (f" ({p.device})" if p.device else ""),
                     f"PrivateKey = {priv if reveal else mask}", f"Address = {addr}"]
            dns = p.client_dns or i.client_dns or self.fw.get("client_dns", "")
            if dns:
                lines.append(f"DNS = {dns}")
            if i.mtu:
                lines.append(f"MTU = {i.mtu}")
            lines += ["", "[Peer]", f"# {self.site_name} – {i.name}", f"PublicKey = {i.public_key}"]
            if psk:
                lines.append(f"PresharedKey = {psk if reveal else mask}")
            lines += [f"AllowedIPs = {p.client_allowed_ips or self._default_allowed(i)}", f"Endpoint = {self._endpoint(i)}"]
            if p.keepalive:
                lines.append(f"PersistentKeepalive = {p.keepalive}")
            text = "\n".join(lines) + "\n"
            if reveal and mark_fetched:
                p.config_changed = False
            fname = opn_name(f"{self.site_name}-{p.name}")[:40] + ".conf"
            return {"text": text, "filename": fname, "qr": qr_data_uri(text) if reveal else None,
                    "complete": bool(priv), "peer": peer_dict(p)}

    # ---------------------------------------------------------- share links
    def create_share(self, pid: int, hours: int, max_uses: int, user: str) -> dict:
        hours = max(1, min(int(hours or 24), 24 * 14))
        token = secrets.token_urlsafe(24)
        self.config(pid)  # fails early if no complete config
        with self.store.session() as db:
            self._peer(db, pid)
            sh = Share(peer_id=int(pid), token_hash=token_hash(token), expires_at=now() + timedelta(hours=hours),
                       max_uses=max(0, int(max_uses or 0)), created_by=user)
            db.add(sh)
        return {"token": token, "expires_at": iso(now() + timedelta(hours=hours)), "max_uses": max(0, int(max_uses or 0))}

    def list_shares(self, pid: int) -> list[dict]:
        with self.store.session() as db:
            self._peer(db, pid)
            rows = db.scalars(select(Share).where(Share.peer_id == int(pid)).order_by(Share.created_at.desc()))
            return [{"id": s.id, "expires_at": iso(s.expires_at), "max_uses": s.max_uses, "uses": s.uses,
                     "created_by": s.created_by, "created_at": iso(s.created_at),
                     "valid": aware(s.expires_at) > now() and (not s.max_uses or s.uses < s.max_uses)} for s in rows]

    def delete_share(self, pid: int, sid: int) -> None:
        with self.store.session() as db:
            s = db.get(Share, int(sid))
            if s and s.peer_id == int(pid):
                db.delete(s)


def redeem_share(store: Store, token: str, consume: bool, check_uses: bool = True) -> tuple[int, int]:
    """Returns (peer_id, firewall_id) for a valid share token; consume=True counts one use (page view)."""
    with store.session() as db:
        s = db.scalar(select(Share).where(Share.token_hash == token_hash(token or "")))
        if not s or aware(s.expires_at) < now() or (check_uses and s.max_uses and s.uses >= s.max_uses) \
                or (not check_uses and s.uses == 0):
            raise LookupError("Link ist abgelaufen oder ungültig")
        p = db.get(Peer, s.peer_id)
        if not p:
            raise LookupError("Zugang existiert nicht mehr")
        if consume:
            s.uses += 1
        return p.id, p.firewall_id
