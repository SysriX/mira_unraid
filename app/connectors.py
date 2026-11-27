"""Built-in connectors for plugins whose app does not speak the SysriX connect standard.

A plugin selects one with "connect": {"type": "<name>"} in its plugin.json. A connector knows how to
  probe(url, verify)            recognise the app at an address → {"app","name","version","instance","setup_needed"}
  async detect(client, url)     the same during the network search (fast, no exceptions) → dict | None
  pair(url, verify, body)       log in once → {"secret": {...}, "role", "remote_user", "version"}
  request(conn, …)              forward one API call with fresh credentials (MIRA keeps them server-side)
  check(conn)                   test → (ok, message, version)
  unpair(conn)                  log out / revoke on removal → note
Secrets are stored Fernet-encrypted in mira_plugin_conn.token_enc.

certwarden – Cert Warden (github.com/gregtwallace/certwarden, checked against v0.31):
  API under /certwarden/api, no API keys for the admin API → MIRA stores user + password encrypted,
  logs in (POST /v1/app/auth/login → access_token, valid 4 min, raw "Authorization: <token>" header),
  renews it with the session cookie (POST /v1/app/auth/refresh, session 15 min) and logs in again if needed.
"""
from __future__ import annotations

import threading
import time

import httpx
from fastapi import HTTPException

import kern

CW = "/certwarden/api"


class CertWarden:
    name = "certwarden"
    https_ports = {4055}

    def __init__(self):
        self._sessions: dict[int, dict] = {}
        self._lock = threading.Lock()

    # --- recognise
    def probe(self, url: str, verify: bool) -> dict:
        try:
            with httpx.Client(verify=verify, timeout=6, follow_redirects=True) as c:
                r = c.get(url + CW + "/v1/app/auth/status")
        except httpx.HTTPError as e:
            raise HTTPException(400, f"{url} ist nicht erreichbar: {e.__class__.__name__}") from e
        info = self._parse(url, r)
        if not info:
            raise HTTPException(400, f"Unter {url} antwortet kein Cert Warden (HTTP {r.status_code}) – "
                                     "Adresse mit Port angeben, z. B. https://<IP_CertWarden>:4055")
        return info

    async def detect(self, client: httpx.AsyncClient, url: str) -> dict | None:
        try:
            r = await client.get(url + CW + "/v1/app/auth/status")
        except Exception:  # noqa: BLE001
            return None
        return self._parse(url, r)

    @staticmethod
    def _parse(url, r) -> dict | None:
        if r.status_code != 200:
            return None
        try:
            d = r.json()
        except ValueError:
            return None
        if not isinstance(d, dict) or "auth_status" not in d:
            return None
        local = ((d.get("auth_status") or {}).get("local") or {}).get("enabled", True)
        return {"app": "certwarden", "name": "Cert Warden", "version": "", "instance": url,
                "setup_needed": False, "local_login": bool(local)}

    # --- session handling
    def _login(self, url, verify, username, password) -> dict:
        cl = httpx.Client(verify=verify, timeout=30, follow_redirects=False)
        try:
            r = cl.post(url + CW + "/v1/app/auth/login", json={"username": username, "password": password})
        except httpx.HTTPError as e:
            cl.close()
            raise HTTPException(400, f"Cert Warden nicht erreichbar: {e.__class__.__name__}") from e
        if r.status_code != 200:
            cl.close()
            raise HTTPException(400, "Cert Warden: Anmeldung abgelehnt – Benutzer/Passwort prüfen (nur lokale Konten, kein OIDC)")
        a = (r.json() or {}).get("authorization") or {}
        return {"client": cl, "token": a.get("access_token", ""), "exp": float(a.get("access_token_exp") or 0),
                "session_exp": float(a.get("session_exp") or 0)}

    def _session(self, conn, force: bool = False) -> dict:
        sec = kern.entschluesseln(conn.token_enc) or {}
        with self._lock:
            s = self._sessions.get(conn.id)
            now = time.time()
            if s and not force and s["token"] and s["exp"] - 20 > now:
                return s
            if s and now < s.get("session_exp", 0) - 10:          # refresh with the session cookie
                try:
                    r = s["client"].post(conn.base_url + CW + "/v1/app/auth/refresh")
                    if r.status_code == 200:
                        a = (r.json() or {}).get("authorization") or {}
                        s.update(token=a.get("access_token", ""), exp=float(a.get("access_token_exp") or 0),
                                 session_exp=float(a.get("session_exp") or 0))
                        return s
                except httpx.HTTPError:
                    pass
            if s:
                s["client"].close()
            s = self._login(conn.base_url, conn.verify_tls, sec.get("username", ""), sec.get("password", ""))
            self._sessions[conn.id] = s
            return s

    def forget(self, cid: int):
        with self._lock:
            s = self._sessions.pop(cid, None)
        if s:
            s["client"].close()

    # --- pair / call / check / unpair
    def pair(self, url: str, verify: bool, body: dict) -> dict:
        s = self._login(url, verify, body["username"], body["password"])
        try:
            r = s["client"].get(url + CW + "/status", headers={"Authorization": s["token"]})
            version = ((r.json() or {}).get("server") or {}).get("version", "") if r.status_code == 200 else ""
            s["client"].post(url + CW + "/v1/app/auth/logout", headers={"Authorization": s["token"]})
        finally:
            s["client"].close()
        return {"secret": {"username": body["username"], "password": body["password"]},
                "role": body.get("role") or "admin", "remote_user": body["username"], "version": version}

    def request(self, conn, method: str, path: str, params, body: bytes | None, headers: dict) -> httpx.Response:
        for attempt in (0, 1):
            s = self._session(conn, force=attempt == 1)
            h = {k: v for k, v in headers.items() if k.lower() in ("accept", "content-type")}
            h["Authorization"] = s["token"]
            r = s["client"].request(method, conn.base_url + path, params=params, content=body, headers=h, timeout=120)
            if r.status_code != 401:
                return r
        return r

    def check(self, conn) -> tuple[bool, str, str]:
        try:
            r = self.request(conn, "GET", CW + "/status", None, None, {"accept": "application/json"})
        except HTTPException as e:
            return False, str(e.detail), ""
        except httpx.HTTPError as e:
            return False, e.__class__.__name__, ""
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}", ""
        v = ((r.json() or {}).get("server") or {}).get("version", "")
        return True, f"Cert Warden {v}", v

    def unpair(self, conn) -> str:
        s = self._sessions.get(conn.id)
        if s:
            try:
                s["client"].post(conn.base_url + CW + "/v1/app/auth/logout", headers={"Authorization": s["token"]})
            except httpx.HTTPError:
                pass
        self.forget(conn.id)
        return "Zugangsdaten in MIRA gelöscht (Cert Warden hat keine eigenen API-Schlüssel – ggf. Passwort dort ändern)"


from connector_mysql import MySQL  # noqa: E402

CONNECTORS = {"certwarden": CertWarden(), "mysql": MySQL()}
