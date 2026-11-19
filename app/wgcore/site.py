"""Site-to-site wizard: connects two OPNsense firewalls with a dedicated WireGuard instance.

Existing road-warrior instances stay untouched: the link gets its own instance (own interface wgN,
own port, own keys). Routes come from the peers' Allowed IPs (disableroutes=0).
Optional: firewall aliases + rules via OPNsense automation (applied with savepoint/rollback);
the listed networks of both sites may reach each other completely.
"""
from __future__ import annotations

import ipaddress
import re

from sqlalchemy import select

from wgcore import WgError
from wgcore.keys import generate_keypair, generate_psk
from wgcore.models import Instance, Peer, SiteLink, Store, now
from wgcore.service import Service, iso, networks, opn_name, split_list

WG_GROUP = "wireguard"   # interface group of all WireGuard instances in OPNsense


def link_dict(l: SiteLink) -> dict:
    return {"id": l.id, "name": l.name, "firewall_a_id": l.firewall_a_id, "firewall_b_id": l.firewall_b_id,
            "instance_a_id": l.instance_a_id, "instance_b_id": l.instance_b_id, "peer_a_id": l.peer_a_id,
            "peer_b_id": l.peer_b_id, "tunnel_net": l.tunnel_net, "port_a": l.port_a, "port_b": l.port_b,
            "networks_a": l.networks_a, "networks_b": l.networks_b, "created_at": iso(l.created_at),
            "firewall_rules": bool((l.details or {}).get("a", {}).get("rules")),
            "hints": (l.details or {}).get("hints", [])}


def list_links(store: Store) -> list[dict]:
    with store.session() as db:
        return [link_dict(l) for l in db.scalars(select(SiteLink).order_by(SiteLink.name))]


def _rw_nets(store: Store, fw_id: int) -> list[str]:
    out = []
    with store.session() as db:
        for i in db.scalars(select(Instance).where(Instance.firewall_id == fw_id, Instance.present.is_(True),
                                                   Instance.role == "clients")):
            for a in split_list(i.tunnel_address):
                try:
                    n = ipaddress.ip_interface(a).network
                    if n.version == 4:
                        out.append(str(n))
                except ValueError:
                    pass
    return out


def _overlaps(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    res = []
    for x in a:
        for y in b:
            nx, ny = ipaddress.ip_network(x, strict=False), ipaddress.ip_network(y, strict=False)
            if nx.version == ny.version and nx.overlaps(ny):
                res.append((x, y))
    return res


def _alias_base(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", name or "")[:10]
    return "S2S" + (s or "Link")


def create_link(store: Store, a: Service, b: Service, d: dict, user: str = "") -> dict:
    if a.fw_id == b.fw_id:
        raise WgError("Bitte zwei verschiedene Firewalls wählen")
    name = (d.get("name") or f"{a.site_name} – {b.site_name}").strip()
    try:
        tnet = ipaddress.ip_network((d.get("tunnel_net") or "").strip(), strict=False)
    except ValueError as e:
        raise WgError("Tunnelnetz ist ungültig (z. B. 10.255.255.0/30)") from e
    hosts = list(tnet.hosts())[:2] if tnet.num_addresses <= 65536 else []
    if tnet.version != 4 or len(hosts) < 2:
        raise WgError("Tunnelnetz braucht mindestens zwei IPv4-Adressen (z. B. /30)")
    ip_a, ip_b, prefix = str(hosts[0]), str(hosts[1]), tnet.prefixlen
    port_a, port_b = int(d.get("port_a") or 51821), int(d.get("port_b") or 51821)
    ep_a, ep_b = (d.get("endpoint_a") or a.fw.get("endpoint_host") or "").strip(), (d.get("endpoint_b") or b.fw.get("endpoint_host") or "").strip()
    if not ep_a and not ep_b:
        raise WgError("Mindestens eine Seite braucht eine öffentlich erreichbare Adresse (Endpoint)")
    nets_a, nets_b = networks(d.get("networks_a")), networks(d.get("networks_b"))
    if not nets_a or not nets_b:
        raise WgError("Bitte die lokalen Netze beider Standorte angeben")
    if d.get("include_rw", True):
        nets_a += [n for n in _rw_nets(store, a.fw_id) if n not in nets_a]
        nets_b += [n for n in _rw_nets(store, b.fw_id) if n not in nets_b]
    clash = _overlaps(nets_a + [str(tnet)], nets_b) + _overlaps([str(tnet)], nets_a)
    if clash:
        raise WgError("Netze überschneiden sich – so kann nicht geroutet werden: "
                      + ", ".join(f"{x} ↔ {y}" for x, y in clash))
    for side, svc in (("a", a), ("b", b)):
        with store.session() as db:
            used = [i.listen_port for i in db.scalars(select(Instance).where(Instance.firewall_id == svc.fw_id, Instance.present.is_(True)))]
        if (port_a if side == "a" else port_b) in used:
            raise WgError(f"Port {port_a if side == 'a' else port_b} ist auf „{svc.site_name}“ schon von einer Instanz belegt")

    keepalive = str(int(d.get("keepalive") or 25))
    mtu = str(int(d["mtu"])) if str(d.get("mtu") or "").strip() else ""
    psk = generate_psk() if d.get("use_psk", True) else ""
    ka, kb = generate_keypair(), generate_keypair()
    created = {"a": {"rules": [], "aliases": []}, "b": {"rules": [], "aliases": []}}
    sides = {"a": a, "b": b}
    hints = []

    try:
        for side, svc, me_ip, other_ip, port, keys, other_keys, other_nets, other_ep, other_port, other_name in (
                ("a", a, ip_a, ip_b, port_a, ka, kb, nets_b, ep_b, port_b, b.site_name),
                ("b", b, ip_b, ip_a, port_b, kb, ka, nets_a, ep_a, port_a, a.site_name)):
            c_uuid = svc.opn.add_client({
                "enabled": "1", "name": opn_name(f"site_{other_name}"), "pubkey": other_keys[1], "psk": psk,
                "tunneladdress": ",".join([f"{other_ip}/32"] + other_nets),
                "serveraddress": other_ep, "serverport": str(other_port) if other_ep else "", "keepalive": keepalive})
            created[side]["client"] = c_uuid
            s_uuid = svc.opn.add_server({
                "enabled": "1", "name": opn_name(f"s2s_{other_name}"), "privkey": keys[0], "pubkey": keys[1],
                "port": str(port), "tunneladdress": f"{me_ip}/{prefix}", "mtu": mtu, "dns": "",
                "disableroutes": "0", "gateway": "", "peers": c_uuid})
            created[side]["server"] = s_uuid

        if d.get("firewall_rules", True):
            base = _alias_base(name)
            for side, svc, port, own_nets, other_nets in (("a", a, port_a, nets_a, nets_b),
                                                         ("b", b, port_b, nets_b, nets_a)):
                wan = (d.get(f"wan_interface_{side}") or "wan").strip()
                created[side]["rules"].append(svc.opn.add_filter_rule({
                    "enabled": "1", "action": "pass", "quick": "1", "interface": wan, "direction": "in",
                    "ipprotocol": "inet", "protocol": "UDP", "source_net": "any", "destination_net": "any",
                    "destination_port": str(port), "description": f"{name}: WireGuard Standortverbindung"}))
                al_src = f"{base}_{side}_rem"
                al_dst = f"{base}_{side}_loc"
                created[side]["aliases"].append(svc.opn.add_alias(al_src, other_nets + [str(tnet)], f"{name}: Netze Gegenseite"))
                created[side]["aliases"].append(svc.opn.add_alias(al_dst, own_nets, f"{name}: eigene Netze"))
                created[side]["rules"].append(svc.opn.add_filter_rule({
                    "enabled": "1", "action": "pass", "quick": "1", "interface": WG_GROUP, "direction": "in",
                    "ipprotocol": "inet", "protocol": "any", "source_net": al_src, "destination_net": al_dst,
                    "description": f"{name}: Gegenseite → eigene Netze"}))
            for svc in (a, b):
                svc.opn.reconfigure_aliases()
                svc.opn.apply_filter_safely()
        else:
            hints.append("Firewall-Regeln wurden nicht angelegt: auf beiden Seiten UDP-Port auf WAN freigeben und "
                         "unter Regeln › WireGuard (Gruppe) den Verkehr der Gegenseite erlauben.")

        a.opn.reconfigure()
        b.opn.reconfigure()
    except Exception:
        _undo(created, sides)
        raise

    a.sync()
    b.sync()
    with store.session() as db:
        ids = {}
        for side, svc in sides.items():
            i = db.scalar(select(Instance).where(Instance.firewall_id == svc.fw_id, Instance.opn_uuid == created[side]["server"]))
            p = db.scalar(select(Peer).where(Peer.firewall_id == svc.fw_id, Peer.opn_uuid == created[side]["client"]))
            if i:
                i.role = "site"
            if p:
                p.kind, p.origin, p.instance_id = "site", "created", i.id if i else p.instance_id
                p.preshared_key_enc = store.encrypt(psk)
                p.description = f"Standortverbindung {name}"
            ids[side] = (i.id if i else None, p.id if p else None)
        if d.get("extend_rw", True):
            for side, svc, remote in (("a", a, nets_b), ("b", b, nets_a)):
                n = _extend_rw(db, svc, remote)
                if n:
                    hints.append(f"{svc.site_name}: {n} Zugang/Zugänge brauchen eine neue Konfiguration (Netze der Gegenseite "
                                 "ergänzt) – in der Liste markiert.")
        hints += [
            "Ist eine OPNsense hinter einem Router, dort UDP " + (str(port_a) if port_a == port_b else f"{port_a} (A) bzw. {port_b} (B)")
            + " zur OPNsense weiterleiten.",
            "Die neuen Instanzen müssen nicht zugewiesen werden; zuweisen (Schnittstellen › Zuweisungen) lohnt sich "
            "nur für Gateway-Überwachung oder eigene Regeln je Tunnel.",
            "Bestehende WireGuard-Zugänge (Road-Warrior) laufen unverändert weiter – eigene Instanz, eigener Port.",
        ]
        link = SiteLink(name=name, firewall_a_id=a.fw_id, firewall_b_id=b.fw_id,
                        instance_a_id=ids["a"][0], instance_b_id=ids["b"][0], peer_a_id=ids["a"][1], peer_b_id=ids["b"][1],
                        tunnel_net=str(tnet), port_a=port_a, port_b=port_b,
                        networks_a=",".join(nets_a), networks_b=",".join(nets_b),
                        details={"a": created["a"], "b": created["b"], "hints": hints, "created_by": user})
        db.add(link)
        db.flush()
        return link_dict(link)


def _extend_rw(db, svc: Service, remote: list[str]) -> int:
    changed = 0
    for i in db.scalars(select(Instance).where(Instance.firewall_id == svc.fw_id, Instance.role == "clients",
                                               Instance.present.is_(True))):
        current = split_list(svc._default_allowed(i))
        if "0.0.0.0/0" in current:
            continue
        i.client_allowed_ips = ",".join(current + [n for n in remote if n not in current])
        for p in db.scalars(select(Peer).where(Peer.instance_id == i.id, Peer.kind == "client")):
            cur = split_list(p.client_allowed_ips) or current
            if "0.0.0.0/0" in cur:
                continue
            new = cur + [n for n in remote if n not in cur]
            if new != cur:
                p.client_allowed_ips, p.config_changed, p.updated_at = ",".join(new), True, now()
                changed += 1
    return changed


def _undo(created: dict, sides: dict) -> None:
    for side, c in created.items():
        opn = sides[side].opn
        for fn, key in ((opn.del_filter_rule, "rules"), (opn.del_alias, "aliases")):
            for uuid in reversed(c.get(key) or []):
                try:
                    fn(uuid)
                except Exception:  # noqa: BLE001
                    pass
        for fn, key in ((opn.del_server, "server"), (opn.del_client, "client")):
            if c.get(key):
                try:
                    fn(c[key])
                except Exception:  # noqa: BLE001
                    pass
        try:
            if c.get("rules"):
                opn.reconfigure_aliases()
                opn.apply_filter_safely()
            opn.reconfigure()
        except Exception:  # noqa: BLE001
            pass


def delete_link(store: Store, lid: int, sides: dict) -> list[str]:
    """sides: {"a": Service|None, "b": Service|None}. Missing services → only DB cleanup, returns warnings."""
    warnings = []
    with store.session() as db:
        l = db.get(SiteLink, int(lid))
        if not l:
            raise LookupError("Standortverbindung nicht gefunden")
        det = l.details or {}
        for side in ("a", "b"):
            svc = sides.get(side)
            c = det.get(side) or {}
            if not svc:
                warnings.append(f"Seite {side.upper()}: Firewall-Verbindung fehlt – dort bitte von Hand aufräumen.")
                continue
            try:
                opn = svc.opn
                for uuid in reversed(c.get("rules") or []):
                    opn.del_filter_rule(uuid)
                for uuid in reversed(c.get("aliases") or []):
                    opn.del_alias(uuid)
                if c.get("server"):
                    opn.del_server(c["server"])
                if c.get("client"):
                    opn.del_client(c["client"])
                if c.get("rules"):
                    opn.reconfigure_aliases()
                    opn.apply_filter_safely()
                opn.reconfigure()
            except WgError as e:
                warnings.append(f"{svc.site_name}: {e}")
        for iid in (l.instance_a_id, l.instance_b_id):
            if iid and (i := db.get(Instance, iid)):
                db.delete(i)
        for pid in (l.peer_a_id, l.peer_b_id):
            if pid and (p := db.get(Peer, pid)):
                db.delete(p)
        db.delete(l)
    return warnings
