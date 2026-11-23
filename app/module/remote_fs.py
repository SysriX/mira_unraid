"""File access + command execution for nginx servers: local (mounted folder + Docker socket) or over SSH.

Both classes offer the same small interface, so module/nginx.py does not care where nginx runs:
  isdir, isfile, exists, listdir, read, write (atomic), remove, makedirs, size, rmtree, join, under(base, rel)
  run(argv) -> (exit code, output)      runs nginx commands (in the container or on the host)
"""
from __future__ import annotations

import io
import os
import posixpath
import shlex
import shutil
import stat
import threading

from module import Fehler, docker_api, wahr


class LocalFS:
    """nginx folders mounted into MIRA; commands via the Docker socket in the nginx container."""

    sep = os.sep

    def __init__(self, w: dict):
        self.sock = w.get("docker_socket") or "/var/run/docker.sock"
        self.container = (w.get("container") or "").strip()

    def join(self, *p):
        return os.path.join(*p)

    def under(self, base: str, rel: str) -> tuple[str, str]:
        b = os.path.realpath(base)
        p = os.path.realpath(os.path.join(b, rel.lstrip("/\\")))
        if not (p == b or p.startswith(b + os.sep)):
            raise Fehler("ungültiger Pfad")
        return b, p

    def isdir(self, p):
        return os.path.isdir(p)

    def isfile(self, p):
        return os.path.isfile(p)

    def exists(self, p):
        return os.path.exists(p)

    def listdir(self, p):
        return sorted(os.listdir(p))

    def size(self, p):
        return os.path.getsize(p)

    def read(self, p) -> str:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()

    def write(self, p, text: str):
        tmp = p + ".mira-neu"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, p)

    def write_bytes(self, p, fobj):
        with open(p, "wb") as f:
            shutil.copyfileobj(fobj, f)

    def remove(self, p):
        os.remove(p)

    def makedirs(self, p):
        os.makedirs(p, exist_ok=True)

    def rmtree(self, p):
        shutil.rmtree(p)

    def can_run(self) -> bool:
        return bool(self.container)

    def run(self, argv: list[str]) -> tuple[int, str]:
        if not self.container:
            raise Fehler("kein Container eingetragen")
        return docker_api.ausfuehren(self.sock, self.container, argv)

    def close(self):
        pass


class SshFS:
    """nginx on another host: files over SFTP, commands over SSH (optionally `docker exec` / sudo).

    The host key is pinned on first contact (TOFU) and stored with the server – a changed key is refused.
    """

    sep = "/"
    _lock = threading.Lock()

    def __init__(self, w: dict, on_new_host_key=None):
        try:
            import paramiko
        except ImportError as e:  # pragma: no cover
            raise Fehler("paramiko fehlt im Image") from e
        self.w = w
        self.container = (w.get("container") or "").strip()
        self.sudo = wahr(w.get("sudo"))
        host = (w.get("host") or "").strip()
        if not host:
            raise Fehler("SSH: Host fehlt")
        port = int(w.get("port") or 22)
        self.cl = paramiko.SSHClient()
        known = (w.get("host_key") or "").strip()
        seen: dict = {}

        class _Pin(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = f"{key.get_name()} {key.get_base64()}"
                if known and fp != known:
                    raise Fehler(f"SSH: Host-Schlüssel von {host} hat sich geändert – Verbindung abgelehnt. "
                                 "Wenn das gewollt ist: unter Server den gemerkten Schlüssel zurücksetzen.")
                seen["fp"] = fp

        self.cl.set_missing_host_key_policy(_Pin())
        kw = {"hostname": host, "port": port, "username": w.get("user") or "root", "timeout": 10,
              "allow_agent": False, "look_for_keys": False, "banner_timeout": 10, "auth_timeout": 10}
        if (w.get("private_key") or "").strip():
            kw["pkey"] = _load_key(w["private_key"], w.get("key_passphrase") or None)
        elif w.get("password"):
            kw["password"] = w["password"]
        else:
            raise Fehler("SSH: Passwort oder privater Schlüssel fehlt")
        try:
            self.cl.connect(**kw)
        except Fehler:
            raise
        except Exception as e:  # noqa: BLE001
            raise Fehler(f"SSH zu {host}:{port} fehlgeschlagen: {type(e).__name__}: {e}") from e
        if seen.get("fp") and not known and on_new_host_key:
            on_new_host_key(seen["fp"])
        self.sftp = self.cl.open_sftp()

    # --- paths
    def join(self, *p):
        return posixpath.join(*p)

    def under(self, base: str, rel: str) -> tuple[str, str]:
        b = posixpath.normpath(base)
        p = posixpath.normpath(posixpath.join(b, rel.lstrip("/\\")))
        if not (p == b or p.startswith(b.rstrip("/") + "/")):
            raise Fehler("ungültiger Pfad")
        return b, p

    def _stat(self, p):
        try:
            return self.sftp.stat(p)
        except OSError:
            return None

    def isdir(self, p):
        s = self._stat(p)
        return bool(s and stat.S_ISDIR(s.st_mode))

    def isfile(self, p):
        s = self._stat(p)
        return bool(s and stat.S_ISREG(s.st_mode))

    def exists(self, p):
        return self._stat(p) is not None

    def listdir(self, p):
        return sorted(self.sftp.listdir(p))

    def size(self, p):
        s = self._stat(p)
        return s.st_size if s else 0

    def read(self, p) -> str:
        with self.sftp.open(p, "rb") as f:
            return f.read().decode("utf-8", "replace")

    def write(self, p, text: str):
        tmp = p + ".mira-neu"
        with self.sftp.open(tmp, "wb") as f:
            f.write(text.encode("utf-8"))
        try:
            self.sftp.posix_rename(tmp, p)
        except OSError:
            if self.exists(p):
                self.sftp.remove(p)
            self.sftp.rename(tmp, p)

    def write_bytes(self, p, fobj):
        self.sftp.putfo(fobj, p)

    def remove(self, p):
        self.sftp.remove(p)

    def makedirs(self, p):
        parts, cur = p.strip("/").split("/"), ""
        for x in parts:
            cur += "/" + x
            if not self.isdir(cur):
                self.sftp.mkdir(cur)

    def rmtree(self, p):
        for n in self.sftp.listdir(p):
            q = posixpath.join(p, n)
            if self.isdir(q):
                self.rmtree(q)
            else:
                self.sftp.remove(q)
        self.sftp.rmdir(p)

    # --- commands
    def can_run(self) -> bool:
        return True

    def run(self, argv: list[str]) -> tuple[int, str]:
        cmd = argv
        if self.container:
            cmd = ["docker", "exec", self.container, *argv]
        line = " ".join(shlex.quote(x) for x in cmd)
        if self.sudo:
            line = "sudo -n " + line
        _, out, err = self.cl.exec_command(line, timeout=60)
        text = out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")
        return out.channel.recv_exit_status(), text

    def close(self):
        try:
            self.sftp.close()
        finally:
            self.cl.close()


def _load_key(text: str, passphrase: str | None):
    import paramiko
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text.strip() + "\n"), password=passphrase)
        except paramiko.PasswordRequiredException as e:
            raise Fehler("SSH: privater Schlüssel ist verschlüsselt – Passphrase eintragen") from e
        except (paramiko.SSHException, ValueError):
            continue
    raise Fehler("SSH: privater Schlüssel nicht lesbar (Ed25519, ECDSA oder RSA im OpenSSH/PEM-Format)")
