"""nginx servers: hosts as reverse proxy, web server (static/PHP) or redirect.

MIRA writes one file per host into the configuration folder of the server – mounted into MIRA
(access "local", commands via the Docker socket) or on another host over SSH/SFTP (access "ssh",
commands directly, with sudo or via `docker exec`). See module/remote_fs.py.
Files created by MIRA start with "# MIRA: {...}" – only those are edited as a form, all others as text.
Apply = write file → nginx -t → on error restore the old state, else nginx -s reload.
Routes: /api/srv/<server id>/nginx/...
"""
from __future__ import annotations

import json
import re
import threading

from fastapi import APIRouter, Depends, File, Request, UploadFile

from anmeldung import rolle
from module import Fehler, prot, wahr
from server_store import server_dep

KOPF = "# MIRA: "

FIELDS_COMMON = [
    {"name": "web_pfad_nginx", "titel": "Webseiten-Ordner (wie nginx ihn sieht)", "art": "text", "standard": "/usr/share/nginx/html",
     "hilfe": "derselbe Ordner wie „Webseiten-Ordner“, aus Sicht von nginx"},
    {"name": "zert_pfad_nginx", "titel": "Zertifikat-Ordner (wie nginx ihn sieht)", "art": "text", "standard": "/etc/nginx/certs",
     "hilfe": "Standard für <name>.crt/<name>.key bei HTTPS"},
]
FIELDS_LOCAL = [
    {"name": "conf_pfad", "titel": "Konfigurationsordner (in MIRA)", "art": "text", "standard": "/nginx/conf.d",
     "pflicht": True, "hilfe": "Ordner mit den *.conf-Dateien, in MIRA eingebunden (z. B. appdata/nginx/conf.d → /nginx/conf.d)"},
    {"name": "web_pfad", "titel": "Webseiten-Ordner (in MIRA)", "art": "text", "standard": "/nginx/www",
     "hilfe": "Hier legt MIRA je statischer Seite einen Unterordner an"},
    *FIELDS_COMMON,
    {"name": "container", "titel": "nginx-Container", "art": "text", "standard": "nginx",
     "hilfe": "Name des Containers für Prüfen/Neu laden. Leer = nur Dateien schreiben"},
    {"name": "docker_socket", "titel": "Docker-Socket", "art": "text", "standard": "/var/run/docker.sock"},
]
FIELDS_SSH = [
    {"name": "host", "titel": "Host", "art": "text", "pflicht": True, "hilfe": "z. B. <IP_nginx-Host>"},
    {"name": "port", "titel": "SSH-Port", "art": "number", "standard": 22},
    {"name": "user", "titel": "Benutzer", "art": "text", "standard": "root"},
    {"name": "password", "titel": "Passwort", "art": "password", "hilfe": "oder privater Schlüssel"},
    {"name": "private_key", "titel": "Privater Schlüssel (OpenSSH/PEM)", "art": "password",
     "hilfe": "Ed25519, ECDSA oder RSA – wird verschlüsselt gespeichert"},
    {"name": "key_passphrase", "titel": "Passphrase des Schlüssels", "art": "password"},
    {"name": "conf_pfad", "titel": "Konfigurationsordner (auf dem Host)", "art": "text", "standard": "/etc/nginx/conf.d", "pflicht": True},
    {"name": "web_pfad", "titel": "Webseiten-Ordner (auf dem Host)", "art": "text", "standard": "/var/www",
     "hilfe": "Hier legt MIRA je statischer Seite einen Unterordner an"},
    *FIELDS_COMMON,
    {"name": "container", "titel": "nginx-Container auf dem Host", "art": "text", "standard": "",
     "hilfe": "leer = nginx läuft direkt auf dem Host; sonst „docker exec <Container> nginx …“"},
    {"name": "sudo", "titel": "Befehle mit sudo (ohne Passwort)", "art": "bool", "standard": False},
]

router = APIRouter(prefix="/api/srv/{sid}/nginx", tags=["nginx"])
verb = server_dep("nginx")
_locks: dict[int, threading.Lock] = {}


def _lock(w) -> threading.Lock:
    return _locks.setdefault(int(w.get("_id") or 0), threading.Lock())


def test(w, fs) -> str:
    p = w.get("conf_pfad") or ""
    if not fs.isdir(p):
        if w.get("_access") == "ssh":
            raise Fehler(f"Ordner {p} gibt es auf {w.get('host')} nicht")
        raise Fehler(f"Ordner {p} gibt es in MIRA nicht – Pfad in der Unraid-Vorlage einbinden")
    n = len([f for f in fs.listdir(p) if f.endswith(".conf")])
    if fs.can_run():
        code, aus = fs.run(["nginx", "-v"])
        if code:
            raise Fehler(f"{n} Host-Dateien, aber nginx -v schlägt fehl: {aus.strip()[:300]}")
        return f"{n} Host-Dateien, {aus.strip()}"
    return f"{n} Host-Dateien (ohne Container: Neu laden von Hand)"


def _slug(s):
    s = re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.")
    if not s:
        raise Fehler("Name fehlt")
    return s


def _fs(w):
    return w["_fs"]


def _conf(w, name):
    return _fs(w).join(w["conf_pfad"], _base(name))


def _base(name):
    n = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    if not n or n in (".", ".."):
        raise Fehler("ungültiger Dateiname")
    return n


def _datei(w, name):
    return _fs(w).join(w["conf_pfad"], _slug(name) + ".conf")


def _meta(text):
    erste = text.split("\n", 1)[0]
    if erste.startswith(KOPF):
        try:
            return json.loads(erste[len(KOPF):])
        except ValueError:
            return None
    return None


def _namen(h):
    return " ".join(x.strip() for x in re.split(r"[\s,]+", h.get("namen", "")) if x.strip()) or "_"


def erzeugen(w, h: dict) -> str:
    """Konfiguration für einen Host aus dem Formular."""
    typ = h.get("typ", "proxy")
    https = wahr(h.get("https"))
    z = [KOPF + json.dumps(h, ensure_ascii=False), f"# {h.get('name')} – von MIRA verwaltet, Änderungen im Formular machen", ""]
    if https and wahr(h.get("http_umleiten", True)):
        z += ["server {", f"    listen {h.get('http_port') or 80};", f"    listen [::]:{h.get('http_port') or 80};",
              f"    server_name {_namen(h)};", "    return 301 https://$host$request_uri;", "}", ""]
    pp = " proxy_protocol" if wahr(h.get("proxy_protocol")) else ""
    z += ["server {"]
    if https:
        port = h.get("https_port") or 443
        z += [f"    listen {port} ssl{pp};", f"    listen [::]:{port} ssl{pp};", "    http2 on;"]
        name = _slug(h.get("name", ""))
        zp = (w.get("zert_pfad_nginx") or "/etc/nginx/certs").rstrip("/")
        z += [f"    ssl_certificate {h.get('zert') or f'{zp}/{name}.crt'};",
              f"    ssl_certificate_key {h.get('schluessel') or f'{zp}/{name}.key'};",
              "    ssl_protocols TLSv1.2 TLSv1.3;"]
    if not https or not wahr(h.get("http_umleiten", True)):
        port = h.get("http_port") or 80
        z += [f"    listen {port}{pp};", f"    listen [::]:{port}{pp};"]
    z += [f"    server_name {_namen(h)};"]
    if h.get("echte_ip_von"):
        for n in re.split(r"[\s,]+", h["echte_ip_von"].strip()):
            if n:
                z.append(f"    set_real_ip_from {n};")
        z.append("    real_ip_header " + ("proxy_protocol;" if wahr(h.get("proxy_protocol")) else "X-Forwarded-For;"))
    if h.get("max_upload"):
        z.append(f"    client_max_body_size {h['max_upload']};")
    if wahr(h.get("sicherheits_header")):
        z += ['    add_header X-Content-Type-Options "nosniff" always;',
              '    add_header X-Frame-Options "SAMEORIGIN" always;',
              '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;']
        if https:
            z.append('    add_header Strict-Transport-Security "max-age=31536000" always;')
    if wahr(h.get("gzip")):
        z += ["    gzip on;", "    gzip_types text/plain text/css application/json application/javascript text/xml image/svg+xml;"]
    z.append("")

    def proxy_block(pfad, ziel, ws):
        b = [f"    location {pfad} {{", f"        proxy_pass {ziel};", "        proxy_http_version 1.1;",
             "        proxy_set_header Host $host;", "        proxy_set_header X-Real-IP $remote_addr;",
             "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
             "        proxy_set_header X-Forwarded-Proto $scheme;", "        proxy_set_header X-Forwarded-Host $host;"]
        if ws:
            b += ["        proxy_set_header Upgrade $http_upgrade;", '        proxy_set_header Connection $http_connection;',
                  "        proxy_read_timeout 3600s;"]
        if wahr(h.get("puffer_aus")):
            b.append("        proxy_buffering off;")
        if str(ziel).startswith("https") and not wahr(h.get("ziel_tls_pruefen")):
            b.append("        proxy_ssl_verify off;")
        if str(ziel).startswith("https"):
            b.append("        proxy_ssl_server_name on;")
        return b + ["    }", ""]

    if typ == "proxy":
        ziel = str(h.get("ziel", "")).strip()
        if not re.match(r"^https?://", ziel):
            raise Fehler("Ziel muss mit http:// oder https:// beginnen")
        z += proxy_block("/", ziel, wahr(h.get("websocket", True)))
    elif typ in ("statisch", "php"):
        root = h.get("root") or ((w.get("web_pfad_nginx") or "/usr/share/nginx/html").rstrip("/") + "/" + _slug(h.get("name", "")))
        z += [f"    root {root};", "    index index.html index.htm" + (" index.php;" if typ == "php" else ";"), ""]
        fallback = "/index.html" if wahr(h.get("spa")) else ("/index.php?$query_string" if typ == "php" else "=404")
        z += ["    location / {", f"        try_files $uri $uri/ {fallback};"]
        if wahr(h.get("verzeichnisliste")):
            z.append("        autoindex on;")
        z += ["    }", ""]
        if typ == "php":
            z += ["    location ~ \\.php$ {", "        try_files $uri =404;", "        include fastcgi_params;",
                  f"        fastcgi_pass {h.get('php_ziel') or 'php:9000'};", "        fastcgi_index index.php;",
                  "        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;", "    }", ""]
        z += ["    location ~ /\\.(?!well-known) { deny all; }", ""]
    elif typ == "umleitung":
        ziel = str(h.get("ziel", "")).rstrip("/")
        if not ziel:
            raise Fehler("Ziel der Umleitung fehlt")
        code = int(h.get("code") or 301)
        z += [f"    return {code} {ziel}" + ("$request_uri;" if wahr(h.get("pfad_behalten", True)) else ";"), ""]
    else:
        raise Fehler("unbekannter Typ")
    for o in h.get("orte") or []:
        if o.get("pfad") and o.get("ziel"):
            z += proxy_block(o["pfad"], o["ziel"], wahr(o.get("websocket")))
    for zeile in str(h.get("extra", "")).splitlines():
        if zeile.strip():
            z.append("    " + zeile.strip())
    z.append("}")
    return "\n".join(z) + "\n"


def pruefen(w) -> tuple[bool, str]:
    fs = _fs(w)
    if not fs.can_run():
        return True, "kein Container eingetragen – nicht geprüft"
    code, aus = fs.run(["nginx", "-t"])
    return code == 0, aus.strip()


def neu_laden(w) -> str:
    fs = _fs(w)
    if not fs.can_run():
        return "Kein Container eingetragen – nginx bitte von Hand neu laden"
    code, aus = fs.run(["nginx", "-s", "reload"])
    if code:
        raise Fehler(f"Neu laden fehlgeschlagen: {aus}")
    return "nginx neu geladen"


def anwenden(w, aenderungen: dict[str, str | None]) -> dict:
    """aenderungen: Pfad -> neuer Text (None = löschen). Mit Prüfung und Rücksprung."""
    fs = _fs(w)
    with _lock(w):
        alt = {p: (fs.read(p) if fs.exists(p) else None) for p in aenderungen}
        for p, t in aenderungen.items():
            if t is None:
                if fs.exists(p):
                    fs.remove(p)
            else:
                fs.write(p, t)
        ok, aus = pruefen(w)
        if not ok:
            for p, t in alt.items():
                if t is None:
                    if fs.exists(p):
                        fs.remove(p)
                else:
                    fs.write(p, t)
            raise Fehler("nginx -t meldet Fehler – nichts geändert:\n" + aus)
        return {"pruefung": aus, "laden": neu_laden(w)}


# ------------------------------------------------------------------ API
@router.get("/hosts")
def hosts(w=Depends(verb), u=Depends(rolle("leser"))):
    fs, p = _fs(w), w["conf_pfad"]
    if not fs.isdir(p):
        raise Fehler(f"Ordner {p} fehlt")
    out = []
    for f in fs.listdir(p):
        if not f.endswith(".conf"):
            continue
        t = fs.read(fs.join(p, f))
        m = _meta(t)
        namen = re.findall(r"server_name\s+([^;]+);", t)
        out.append({"datei": f, "verwaltet": m is not None, "host": m,
                    "namen": sorted({n for z in namen for n in z.split() if n != "_"}),
                    "typ": (m or {}).get("typ") or ("proxy" if "proxy_pass" in t else "statisch" if "root " in t else "?")})
    return {"hosts": out}


@router.get("/datei")
def datei(name: str, w=Depends(verb), u=Depends(rolle("leser"))):
    p = _conf(w, name)
    return {"datei": _base(name), "text": _fs(w).read(p) if _fs(w).exists(p) else ""}


@router.post("/vorschau")
async def vorschau(req: Request, w=Depends(verb), u=Depends(rolle("leser"))):
    return {"text": erzeugen(w, (await req.json())["host"])}


@router.post("/hosts")
async def host_speichern(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    h = d["host"]
    fs = _fs(w)
    neu = _datei(w, h.get("name", ""))
    aend = {neu: erzeugen(w, h)}
    if d.get("alt_datei") and _base(d["alt_datei"]) != _base(neu):
        aend[_conf(w, d["alt_datei"])] = None
    elif not d.get("alt_datei") and fs.exists(neu):
        raise Fehler(f"{_base(neu)} gibt es schon")
    if h.get("typ") in ("statisch", "php") and not h.get("root") and w.get("web_pfad"):
        ordner = fs.join(w["web_pfad"], _slug(h["name"]))
        if not fs.isdir(ordner):
            fs.makedirs(ordner)
            fs.write(fs.join(ordner, "index.html"),
                     f"<!doctype html><meta charset=utf-8><title>{h['name']}</title><h1>{h['name']}</h1><p>Angelegt von MIRA.</p>\n")
    try:
        r = anwenden(w, aend)
    except Fehler as e:
        prot(u, "nginx", "Host speichern", f"{w['_name']}: {h.get('name', '')}", False, str(e))
        raise
    prot(u, "nginx", "Host gespeichert", f"{w['_name']}: {h.get('name', '')}", details=r["laden"])
    return r


@router.post("/datei")
async def datei_speichern(req: Request, w=Depends(verb), u=Depends(rolle("admin"))):
    d = await req.json()
    name = _base(d["datei"])
    if not name.endswith(".conf"):
        raise Fehler("Nur *.conf-Dateien")
    r = anwenden(w, {_conf(w, name): d["text"]})
    prot(u, "nginx", "Datei gespeichert", f"{w['_name']}: {name}", details=r["laden"])
    return r


@router.delete("/hosts/{datei}")
def host_loeschen(datei: str, w=Depends(verb), u=Depends(rolle("operator"))):
    r = anwenden(w, {_conf(w, datei): None})
    prot(u, "nginx", "Host gelöscht", f"{w['_name']}: {datei}")
    return r


@router.post("/pruefen")
def pruefen_api(w=Depends(verb), u=Depends(rolle("leser"))):
    ok, aus = pruefen(w)
    return {"ok": ok, "ausgabe": aus}


@router.post("/neu-laden")
def laden_api(w=Depends(verb), u=Depends(rolle("operator"))):
    ok, aus = pruefen(w)
    if not ok:
        raise Fehler(aus)
    r = neu_laden(w)
    prot(u, "nginx", "neu geladen", w["_name"])
    return {"ok": True, "ausgabe": r}


# --- Dateien einer statischen Seite
def _web(w, site, rel=""):
    fs = _fs(w)
    if not w.get("web_pfad"):
        raise Fehler("Kein Webseiten-Ordner eingetragen")
    return fs.under(fs.join(w["web_pfad"], _slug(site)), rel)


@router.get("/web/{site}")
def web_liste(site: str, pfad: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    fs = _fs(w)
    _, p = _web(w, site, pfad)
    if not fs.isdir(p):
        return {"eintraege": []}
    out = []
    for n in fs.listdir(p):
        q = fs.join(p, n)
        out.append({"name": n, "ordner": fs.isdir(q), "groesse": fs.size(q) if fs.isfile(q) else None})
    return {"eintraege": out}


@router.post("/web/{site}/hochladen")
async def web_hochladen(site: str, pfad: str = "", datei: UploadFile = File(...), w=Depends(verb),
                        u=Depends(rolle("operator"))):
    fs = _fs(w)
    _, ordner = _web(w, site, pfad)
    fs.makedirs(ordner)
    _, ziel = _web(w, site, (pfad.rstrip("/") + "/" if pfad else "") + _base(datei.filename or "datei"))
    fs.write_bytes(ziel, datei.file)
    prot(u, "nginx", "Datei hochgeladen", f"{w['_name']}: {site}/{pfad}/{datei.filename}")
    return {"ok": True}


@router.delete("/web/{site}")
def web_loeschen(site: str, pfad: str, w=Depends(verb), u=Depends(rolle("operator"))):
    fs = _fs(w)
    basis, p = _web(w, site, pfad)
    if p == basis:
        raise Fehler("Den Hauptordner hier nicht löschen")
    fs.rmtree(p) if fs.isdir(p) else fs.remove(p)
    prot(u, "nginx", "Datei gelöscht", f"{w['_name']}: {site}/{pfad}")
    return {"ok": True}
