"""Kleinster Docker-Client über den Unix-Socket (nur exec) – für nginx -t / reload im nginx-Container."""
from __future__ import annotations

import http.client
import json
import socket

from module import Fehler


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, pfad, timeout=30):
        super().__init__("localhost", timeout=timeout)
        self.pfad = pfad

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.pfad)
        self.sock = s


def _anfrage(sock, methode, pfad, daten=None):
    try:
        c = _UnixConn(sock)
        body = json.dumps(daten).encode() if daten is not None else None
        c.request(methode, pfad, body=body, headers={"Content-Type": "application/json"} if body else {})
        r = c.getresponse()
        roh = r.read()
        c.close()
    except (OSError, AttributeError) as e:
        raise Fehler(f"Docker-Socket {sock} nicht erreichbar: {e}") from e
    if r.status >= 400:
        try:
            msg = json.loads(roh).get("message")
        except Exception:  # noqa: BLE001
            msg = roh[:200].decode("utf-8", "replace")
        raise Fehler(f"Docker {r.status}: {msg}")
    return roh


def ausfuehren(sock: str, container: str, befehl: list[str]) -> tuple[int, str]:
    """Führt befehl im Container aus. Gibt (Exit-Code, Ausgabe) zurück."""
    e = json.loads(_anfrage(sock, "POST", f"/containers/{container}/exec",
                            {"Cmd": befehl, "AttachStdout": True, "AttachStderr": True, "Tty": True}))
    roh = _anfrage(sock, "POST", f"/exec/{e['Id']}/start", {"Detach": False, "Tty": True})
    info = json.loads(_anfrage(sock, "GET", f"/exec/{e['Id']}/json"))
    return int(info.get("ExitCode") or 0), roh.decode("utf-8", "replace")
