"""Small client for the OPNsense REST API (WireGuard + firewall automation).

API user needs the privileges "VPN: WireGuard" and – for the site-to-site wizard with firewall rules –
"Firewall: Rules: API" (Automation). Authentication: API key + secret (HTTP basic).
"""
from __future__ import annotations

import httpx

from wgcore import WgError


def _selected(value) -> list[str]:
    """OPNsense returns option fields as {key: {value, selected}}; plain fields as strings."""
    if isinstance(value, dict):
        return [k for k, v in value.items() if isinstance(v, dict) and str(v.get("selected", 0)) in ("1", "True", "true")]
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def _text(value) -> str:
    return ",".join(_selected(value)) if isinstance(value, (dict, list)) else str(value or "")


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class OPNsense:
    def __init__(self, url: str, api_key: str, api_secret: str, verify_tls: bool = True, timeout: float = 20):
        if not url:
            raise WgError("Adresse der OPNsense fehlt")
        self.base = url.rstrip("/")
        self.client = httpx.Client(auth=(api_key or "", api_secret or ""), verify=verify_tls, timeout=timeout)

    def close(self):
        self.client.close()

    # ------------------------------------------------------------ transport
    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            if payload is None:
                r = self.client.request(method, self.base + path)
            else:
                r = self.client.request(method, self.base + path, json=payload)
        except httpx.HTTPError as e:
            raise WgError(f"OPNsense nicht erreichbar: {e}") from e
        if r.status_code in (401, 403):
            raise WgError(f"OPNsense lehnt den API-Schlüssel ab ({r.status_code}) – Schlüssel/Rechte prüfen")
        if r.status_code >= 400:
            raise WgError(f"OPNsense: Fehler {r.status_code} bei {path}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise WgError(f"OPNsense: unerwartete Antwort bei {path}") from e

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, payload: dict | None = None) -> dict:
        return self.request("POST", path, payload)

    @staticmethod
    def _check(res: dict, what: str) -> dict:
        if str(res.get("result", "")).lower() in ("saved", "deleted", "ok"):
            return res
        val = res.get("validations") or {}
        msg = "; ".join(f"{k.split('.')[-1]}: {v}" for k, v in val.items()) if val else (res.get("message") or str(res)[:200])
        raise WgError(f"OPNsense hat „{what}“ abgelehnt – {msg}")

    # ------------------------------------------------------------ WireGuard: instances
    def servers(self) -> list[dict]:
        data = self.get("/api/wireguard/server/get")
        rows = (((data.get("server") or {}).get("servers") or {}).get("server")) or {}
        out = []
        for uuid, s in rows.items():
            out.append({
                "uuid": uuid,
                "name": _text(s.get("name")),
                "enabled": _text(s.get("enabled")) == "1",
                "instance": _text(s.get("instance")),
                "port": _int(_text(s.get("port"))),
                "tunnel_address": _text(s.get("tunneladdress")),
                "public_key": _text(s.get("pubkey")),
                "mtu": _int(_text(s.get("mtu"))),
                "dns": _text(s.get("dns")),
                "peers": _selected(s.get("peers")),
            })
        return out

    def server(self, uuid: str) -> dict:
        for s in self.servers():
            if s["uuid"] == uuid:
                return s
        raise WgError("Instanz gibt es auf der OPNsense nicht mehr – bitte neu importieren")

    def add_server(self, data: dict) -> str:
        return self._check(self.post("/api/wireguard/server/addServer", {"server": data}), "Instanz anlegen")["uuid"]

    def set_server(self, uuid: str, data: dict) -> None:
        self._check(self.post(f"/api/wireguard/server/setServer/{uuid}", {"server": data}), "Instanz ändern")

    def del_server(self, uuid: str) -> None:
        self._check(self.post(f"/api/wireguard/server/delServer/{uuid}"), "Instanz löschen")

    # ------------------------------------------------------------ WireGuard: peers
    def clients(self) -> list[dict]:
        data = self.get("/api/wireguard/client/get")
        rows = (((data.get("client") or {}).get("clients") or {}).get("client")) or {}
        out = []
        for uuid, c in rows.items():
            out.append({
                "uuid": uuid,
                "name": _text(c.get("name")),
                "enabled": _text(c.get("enabled")) == "1",
                "public_key": _text(c.get("pubkey")),
                "psk": _text(c.get("psk")),
                "tunnel_address": _text(c.get("tunneladdress")),
                "server_address": _text(c.get("serveraddress")),
                "server_port": _int(_text(c.get("serverport"))),
                "keepalive": _int(_text(c.get("keepalive"))),
            })
        return out

    def add_client(self, data: dict) -> str:
        return self._check(self.post("/api/wireguard/client/addClient", {"client": data}), "Peer anlegen")["uuid"]

    def set_client(self, uuid: str, data: dict) -> None:
        self._check(self.post(f"/api/wireguard/client/setClient/{uuid}", {"client": data}), "Peer ändern")

    def del_client(self, uuid: str) -> None:
        self._check(self.post(f"/api/wireguard/client/delClient/{uuid}"), "Peer löschen")

    def link_peer(self, server_uuid: str, client_uuid: str, linked: bool = True) -> None:
        s = self.server(server_uuid)
        peers = [p for p in s["peers"] if p != client_uuid] + ([client_uuid] if linked else [])
        if peers != s["peers"]:
            self.set_server(server_uuid, {"peers": ",".join(peers)})

    def unlink_everywhere(self, client_uuid: str) -> None:
        for s in self.servers():
            if client_uuid in s["peers"]:
                self.set_server(s["uuid"], {"peers": ",".join(p for p in s["peers"] if p != client_uuid)})

    def reconfigure(self) -> None:
        res = self.post("/api/wireguard/service/reconfigure")
        if str(res.get("status", "ok")).lower() not in ("ok", "running", ""):
            raise WgError(f"WireGuard-Dienst ließ sich nicht neu laden: {res}")

    def show(self) -> list[dict]:
        """Live status (`wg show`) as rows; empty list if this OPNsense version has no row output."""
        res = self.get("/api/wireguard/service/show")
        rows = res.get("rows")
        return rows if isinstance(rows, list) else []

    # ------------------------------------------------------------ firewall automation (filter)
    def add_filter_rule(self, rule: dict) -> str:
        return self._check(self.post("/api/firewall/filter/addRule", {"rule": rule}), "Firewall-Regel anlegen")["uuid"]

    def del_filter_rule(self, uuid: str) -> None:
        self._check(self.post(f"/api/firewall/filter/delRule/{uuid}"), "Firewall-Regel löschen")

    def add_alias(self, name: str, networks: list[str], description: str = "") -> str:
        return self._check(self.post("/api/firewall/alias/addItem", {"alias": {
            "enabled": "1", "name": name, "type": "network", "content": "\n".join(networks),
            "description": description}}), "Alias anlegen")["uuid"]

    def del_alias(self, uuid: str) -> None:
        self._check(self.post(f"/api/firewall/alias/delItem/{uuid}"), "Alias löschen")

    def reconfigure_aliases(self) -> None:
        self.post("/api/firewall/alias/reconfigure")

    def apply_filter_safely(self) -> None:
        """Apply with savepoint: OPNsense rolls back by itself after 60 s unless we confirm."""
        rev = (self.post("/api/firewall/filter/savepoint") or {}).get("revision")
        if not rev:
            raise WgError("OPNsense hat keinen Sicherungspunkt für die Firewall-Regeln geliefert")
        self.post(f"/api/firewall/filter/apply/{rev}")
        self.post(f"/api/firewall/filter/cancelRollback/{rev}")   # still reachable → keep
