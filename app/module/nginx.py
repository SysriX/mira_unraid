"""Modul nginx: Hosts als Reverse-Proxy, Webserver (statisch/PHP) oder Umleitung.

MIRA schreibt eine Datei je Host in den Konfigurationsordner (gemeinsamer Ordner mit dem nginx-Container).
Von MIRA erzeugte Dateien beginnen mit „# MIRA: {...}“ – nur die werden als Formular bearbeitet,
alle anderen als Text. Übernehmen = Datei schreiben → nginx -t → bei Fehler alten Stand zurück,
sonst nginx -s reload (über den Docker-Socket im nginx-Container).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading

from fastapi import APIRouter, Depends, File, Request, UploadFile

from anmeldung import rolle
from module import Fehler, Modul, prot, registrieren, verbindung_dep, wahr
from module import docker_api

KOPF = "# MIRA: "


class Nginx(Modul):
    typ = "nginx"
    titel = "nginx"
    beschreibung = "Webserver und Reverse-Proxy: Hosts anlegen (Proxy, statisch, PHP, Umleitung), prüfen, neu laden."
    felder = [
        {"name": "conf_pfad", "titel": "Konfigurationsordner (in MIRA)", "art": "text", "standard": "/nginx/conf.d",
         "pflicht": True, "hilfe": "Ordner mit den *.conf-Dateien, in MIRA eingebunden (z. B. appdata/nginx/conf.d → /nginx/conf.d)"},
        {"name": "web_pfad", "titel": "Webseiten-Ordner (in MIRA)", "art": "text", "standard": "/nginx/www",
         "hilfe": "Hier legt MIRA je statischer Seite einen Unterordner an"},
        {"name": "web_pfad_nginx", "titel": "Webseiten-Ordner (im nginx-Container)", "art": "text", "standard": "/usr/share/nginx/html",
         "hilfe": "derselbe Ordner, wie nginx ihn sieht"},
        {"name": "zert_pfad_nginx", "titel": "Zertifikat-Ordner (im nginx-Container)", "art": "text", "standard": "/etc/nginx/certs",
         "hilfe": "Standard für <name>.crt/<name>.key bei HTTPS"},
        {"name": "container", "titel": "nginx-Container", "art": "text", "standard": "nginx",
         "hilfe": "Name des Containers für Prüfen/Neu laden. Leer = nur Dateien schreiben"},
        {"name": "docker_socket", "titel": "Docker-Socket", "art": "text", "standard": "/var/run/docker.sock"},
    ]

    def testen(self, w):
        p = w.get("conf_pfad") or ""
        if not os.path.isdir(p):
            raise Fehler(f"Ordner {p} gibt es in MIRA nicht – Pfad in der Unraid-Vorlage einbinden")
        n = len([f for f in os.listdir(p) if f.endswith(".conf")])
        if w.get("container"):
            code, aus = docker_api.ausfuehren(w.get("docker_socket") or "/var/run/docker.sock", w["container"], ["nginx", "-v"])
            return f"{n} Host-Dateien, {aus.strip()}"
        return f"{n} Host-Dateien (ohne Container: Neu laden von Hand)"


MODUL = registrieren(Nginx())
router = APIRouter(prefix="/api/m/nginx/{vid}", tags=["nginx"])
MODUL.router = router
verb = verbindung_dep("nginx")
_lock = threading.Lock()


def _slug(s):
    s = re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-.")
    if not s:
        raise Fehler("Name fehlt")
    return s


def _datei(w, name):
    return os.path.join(w["conf_pfad"], _slug(name) + ".conf")


def _lesen(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def _schreiben(p, text):
    tmp = p + ".mira-neu"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, p)


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


def _docker(w):
    return w.get("docker_socket") or "/var/run/docker.sock", w.get("container")


def pruefen(w) -> tuple[bool, str]:
    sock, cont = _docker(w)
    if not cont:
        return True, "kein Container eingetragen – nicht geprüft"
    code, aus = docker_api.ausfuehren(sock, cont, ["nginx", "-t"])
    return code == 0, aus.strip()


def neu_laden(w) -> str:
    sock, cont = _docker(w)
    if not cont:
        return "Kein Container eingetragen – nginx bitte von Hand neu laden"
    code, aus = docker_api.ausfuehren(sock, cont, ["nginx", "-s", "reload"])
    if code:
        raise Fehler(f"Neu laden fehlgeschlagen: {aus}")
    return "nginx neu geladen"


def anwenden(w, aenderungen: dict[str, str | None]) -> dict:
    """aenderungen: Pfad -> neuer Text (None = löschen). Mit Prüfung und Rücksprung."""
    with _lock:
        alt = {p: (_lesen(p) if os.path.exists(p) else None) for p in aenderungen}
        for p, t in aenderungen.items():
            if t is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                _schreiben(p, t)
        ok, aus = pruefen(w)
        if not ok:
            for p, t in alt.items():
                if t is None:
                    if os.path.exists(p):
                        os.remove(p)
                else:
                    _schreiben(p, t)
            raise Fehler("nginx -t meldet Fehler – nichts geändert:\n" + aus)
        return {"pruefung": aus, "laden": neu_laden(w)}


# ------------------------------------------------------------------ API
@router.get("/hosts")
def hosts(w=Depends(verb), u=Depends(rolle("leser"))):
    p = w["conf_pfad"]
    if not os.path.isdir(p):
        raise Fehler(f"Ordner {p} fehlt")
    out = []
    for f in sorted(os.listdir(p)):
        if not f.endswith(".conf"):
            continue
        t = _lesen(os.path.join(p, f))
        m = _meta(t)
        namen = re.findall(r"server_name\s+([^;]+);", t)
        out.append({"datei": f, "verwaltet": m is not None, "host": m,
                    "namen": sorted({n for z in namen for n in z.split() if n != "_"}),
                    "typ": (m or {}).get("typ") or ("proxy" if "proxy_pass" in t else "statisch" if "root " in t else "?")})
    return {"hosts": out}


@router.get("/datei")
def datei(name: str, w=Depends(verb), u=Depends(rolle("leser"))):
    p = os.path.join(w["conf_pfad"], os.path.basename(name))
    return {"datei": os.path.basename(name), "text": _lesen(p) if os.path.exists(p) else ""}


@router.post("/vorschau")
async def vorschau(req: Request, w=Depends(verb), u=Depends(rolle("leser"))):
    return {"text": erzeugen(w, (await req.json())["host"])}


@router.post("/hosts")
async def host_speichern(req: Request, w=Depends(verb), u=Depends(rolle("operator"))):
    d = await req.json()
    h = d["host"]
    neu = _datei(w, h.get("name", ""))
    aend = {neu: erzeugen(w, h)}
    if d.get("alt_datei") and os.path.basename(d["alt_datei"]) != os.path.basename(neu):
        aend[os.path.join(w["conf_pfad"], os.path.basename(d["alt_datei"]))] = None
    elif not d.get("alt_datei") and os.path.exists(neu):
        raise Fehler(f"{os.path.basename(neu)} gibt es schon")
    if h.get("typ") in ("statisch", "php") and not h.get("root") and w.get("web_pfad"):
        ordner = os.path.join(w["web_pfad"], _slug(h["name"]))
        if not os.path.isdir(ordner):
            os.makedirs(ordner, exist_ok=True)
            with open(os.path.join(ordner, "index.html"), "w", encoding="utf-8") as f:
                f.write(f"<!doctype html><meta charset=utf-8><title>{h['name']}</title><h1>{h['name']}</h1><p>Angelegt von MIRA.</p>\n")
    try:
        r = anwenden(w, aend)
    except Fehler as e:
        prot(u, "nginx", "Host speichern", h.get("name", ""), False, str(e))
        raise
    prot(u, "nginx", "Host gespeichert", h.get("name", ""), details=r["laden"])
    return r


@router.post("/datei")
async def datei_speichern(req: Request, w=Depends(verb), u=Depends(rolle("admin"))):
    d = await req.json()
    name = os.path.basename(d["datei"])
    if not name.endswith(".conf"):
        raise Fehler("Nur *.conf-Dateien")
    r = anwenden(w, {os.path.join(w["conf_pfad"], name): d["text"]})
    prot(u, "nginx", "Datei gespeichert", name, details=r["laden"])
    return r


@router.delete("/hosts/{datei}")
def host_loeschen(datei: str, w=Depends(verb), u=Depends(rolle("operator"))):
    r = anwenden(w, {os.path.join(w["conf_pfad"], os.path.basename(datei)): None})
    prot(u, "nginx", "Host gelöscht", datei)
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
    basis = os.path.realpath(os.path.join(w.get("web_pfad") or "/nonexistent", _slug(site)))
    p = os.path.realpath(os.path.join(basis, rel.lstrip("/\\")))
    if not (p == basis or p.startswith(basis + os.sep)):
        raise Fehler("ungültiger Pfad")
    return basis, p


@router.get("/web/{site}")
def web_liste(site: str, pfad: str = "", w=Depends(verb), u=Depends(rolle("leser"))):
    _, p = _web(w, site, pfad)
    if not os.path.isdir(p):
        return {"eintraege": []}
    out = []
    for n in sorted(os.listdir(p)):
        q = os.path.join(p, n)
        out.append({"name": n, "ordner": os.path.isdir(q), "groesse": os.path.getsize(q) if os.path.isfile(q) else None})
    return {"eintraege": out}


@router.post("/web/{site}/hochladen")
async def web_hochladen(site: str, pfad: str = "", datei: UploadFile = File(...), w=Depends(verb),
                        u=Depends(rolle("operator"))):
    _, ordner = _web(w, site, pfad)
    os.makedirs(ordner, exist_ok=True)
    _, ziel = _web(w, site, os.path.join(pfad, os.path.basename(datei.filename or "datei")))
    with open(ziel, "wb") as f:
        shutil.copyfileobj(datei.file, f)
    prot(u, "nginx", "Datei hochgeladen", f"{site}/{pfad}/{datei.filename}")
    return {"ok": True}


@router.delete("/web/{site}")
def web_loeschen(site: str, pfad: str, w=Depends(verb), u=Depends(rolle("operator"))):
    basis, p = _web(w, site, pfad)
    if p == basis:
        raise Fehler("Den Hauptordner hier nicht löschen")
    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    prot(u, "nginx", "Datei gelöscht", f"{site}/{pfad}")
    return {"ok": True}
