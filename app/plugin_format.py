"""MIRA plugin format 1 – fingerprint, signature, safe ZIP handling.

Same rules as tools/mira_plugin.py (template repo SysriX/mira_plugin_template) and the PIA plugin directory:
  * files: every file below the plugin folder except hidden ones (".x"), __pycache__ and signature.json
  * fingerprint: SHA-256 over (path UTF-8 + SHA-256(content)) for all files sorted bytewise by path
  * signature.json: Ed25519 over "mira-plugin\\n<id>\\n<version>\\n<fingerprint>"
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FORMAT = 1
SIGNATURE_FILE = "signature.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$")
MAX_FILES = 2000
MAX_UNPACKED = 100 * 1024 * 1024
MAX_PACKAGE = 50 * 1024 * 1024

# Built-in trusted publisher: SysriX (private key is kept offline, never in a repository).
BUILTIN_KEYS = [{"key_id": "sysrix", "name": "SysriX", "public_key": "uX87KIS2qhvI+QExNITB9/ZcxiUuFC7+rKbenCdpf1s=",
                 "builtin": True}]


def ignored(rel: str) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.split("/"))


def plugin_files(root: str) -> list[tuple[str, str]]:
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x != "__pycache__"]
        for name in files:
            p = os.path.join(d, name)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if ignored(rel) or rel == SIGNATURE_FILE or os.path.islink(p):
                continue
            out.append((rel, p))
    out.sort(key=lambda x: x[0].encode("utf-8"))
    return out


def fingerprint_dir(root: str) -> str:
    h = hashlib.sha256()
    for rel, p in plugin_files(root):
        h.update(rel.encode("utf-8"))
        try:
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            pass
    return h.hexdigest()


def signed_message(pid: str, version: str, fingerprint: str) -> bytes:
    return f"mira-plugin\n{pid}\n{version}\n{fingerprint}".encode("utf-8")


def read_signature(root: str) -> dict | None:
    p = os.path.join(root, SIGNATURE_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def check_signature(sig: dict | None, pid: str, version: str, fingerprint: str, trusted: list[dict]) -> dict:
    """{"state": none|valid|invalid, "key_id", "publisher", "trusted": bool, "reason"}"""
    if sig is None:
        return {"state": "none", "trusted": False}
    out = {"state": "invalid", "key_id": str(sig.get("key_id", ""))[:64], "trusted": False, "publisher": ""}
    if sig.get("fingerprint") != fingerprint:
        return {**out, "reason": "Dateien passen nicht zur Signatur (nach dem Signieren geändert)"}
    if sig.get("id") not in (None, pid) or sig.get("version") not in (None, version):
        return {**out, "reason": "Signatur gehört zu einem anderen Plugin bzw. einer anderen Version"}
    try:
        pub = base64.b64decode(sig["public_key"])
        Ed25519PublicKey.from_public_bytes(pub).verify(base64.b64decode(sig["signature"]),
                                                       signed_message(pid, version, fingerprint))
    except Exception:  # noqa: BLE001
        return {**out, "reason": "Signatur ungültig"}
    out["state"] = "valid"
    for k in trusted:
        if k.get("public_key") == sig.get("public_key"):
            out["trusted"], out["publisher"] = True, k.get("name") or k.get("key_id")
            break
    if not out["trusted"]:
        out["reason"] = "Herausgeber-Schlüssel ist in MIRA nicht als vertrauenswürdig eingetragen"
    return out


def version_tuple(v: str) -> tuple:
    parts = re.split(r"[.-]", str(v or "0"))
    out = []
    for p in parts[:3]:
        out.append(int(p) if p.isdigit() else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out) + ((1,) if len(parts) <= 3 else (0,))     # 1.0.0 > 1.0.0-beta


def newer(a: str, b: str) -> bool:
    return version_tuple(a) > version_tuple(b)


# ------------------------------------------------------------------ ZIP
def safe_entries(data: bytes) -> dict[str, bytes]:
    """Reads a plugin ZIP; refuses unsafe paths, links, too many/too large files. A single top folder is stripped."""
    if len(data) > MAX_PACKAGE:
        raise ValueError("Paket zu groß")
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ValueError("zu viele Dateien im Paket")
        total = 0
        for i in infos:
            name = i.filename
            if name.startswith("/") or "\\" in name or ".." in name.split("/") or re.match(r"^[A-Za-z]:", name) \
                    or "\x00" in name:
                raise ValueError(f"unsicherer Pfad im Paket: {name}")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symbolischer Link im Paket: {name}")
            total += i.file_size
            if total > MAX_UNPACKED:
                raise ValueError("Paket entpackt zu groß")
            out[name] = z.read(i)
    if "plugin.json" not in out:
        tops = {k.split("/", 1)[0] for k in out}
        if len(tops) == 1 and all("/" in k for k in out):
            top = tops.pop() + "/"
            out = {k[len(top):]: v for k, v in out.items()}
    if "plugin.json" not in out:
        raise ValueError("plugin.json fehlt im Paket")
    return {k: v for k, v in out.items() if k and not ignored(k)}


def write_tree(entries: dict[str, bytes], target: str) -> None:
    os.makedirs(target, exist_ok=False)
    root = os.path.realpath(target)
    for rel, data in entries.items():
        p = os.path.realpath(os.path.join(root, *rel.split("/")))
        if not p.startswith(root + os.sep):
            raise ValueError(f"unsicherer Pfad im Paket: {rel}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)


def pack_dir(root: str) -> bytes:
    """ZIP of a plugin folder (plugin files + signature.json, stable timestamps)."""
    files = plugin_files(root)
    sig = os.path.join(root, SIGNATURE_FILE)
    if os.path.isfile(sig):
        files.append((SIGNATURE_FILE, sig))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(p, "rb") as f:
                z.writestr(info, f.read())
    return buf.getvalue()


def replace_dir(new_dir: str, target: str, backup_root: str) -> str | None:
    """Moves target into backup_root (if present) and new_dir to target. Returns the backup path."""
    backup = None
    if os.path.exists(target):
        os.makedirs(backup_root, exist_ok=True)
        backup = os.path.join(backup_root, os.path.basename(target) + "-" + hashlib.sha1(os.urandom(8)).hexdigest()[:8])
        shutil.move(target, backup)
    shutil.move(new_dir, target)
    return backup
