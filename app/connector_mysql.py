"""Built-in connector "mysql": MySQL / MariaDB servers administered directly from MIRA (plugin DANA).

Every plugin connection is one database server (mysql://host:port). MIRA keeps user + password Fernet-encrypted
and talks to the server itself with PyMySQL – there is no app in between. The plugin UI calls virtual paths below
/api/db/ which this connector answers locally (request() builds an httpx.Response, so the generic plugin proxy,
the role checks and the audit log of MIRA stay the same):

  GET  read   – browse servers, databases, tables, rows, structure, users, processes, exports,
                read-only queries (/api/db/select: one SELECT/SHOW/… statement in a READ ONLY transaction)
  POST/PUT    – write: SQL console, import, create/alter databases, tables, columns, rows, users, grants
  DELETE      – admin: drop database/table/column/index/user, truncate, kill process

A fresh connection is opened per request (stateless, no pool to leak); the session therefore never carries
USE/SET from one call to the next – every call names its database.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import decimal
import gzip
import io
import json
import re
import ssl
import time
from urllib.parse import urlsplit

import httpx
import pymysql
from pymysql.constants import CLIENT
from fastapi import HTTPException

import kern

DEFAULT_PORT = 3306
MAX_ROWS = 1000            # rows per result in the SQL console
CELL_LIMIT = 4096          # characters per cell shown in lists (full value in the row editor)
PAGE_MAX = 500

PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "REFERENCES", "INDEX", "ALTER",
              "CREATE TEMPORARY TABLES", "LOCK TABLES", "EXECUTE", "CREATE VIEW", "SHOW VIEW", "CREATE ROUTINE",
              "ALTER ROUTINE", "EVENT", "TRIGGER", "RELOAD", "SHUTDOWN", "PROCESS", "FILE", "SHOW DATABASES",
              "SUPER", "REPLICATION SLAVE", "REPLICATION CLIENT", "CREATE USER", "CREATE TABLESPACE"]
GLOBAL_ONLY = {"RELOAD", "SHUTDOWN", "PROCESS", "FILE", "SHOW DATABASES", "SUPER", "REPLICATION SLAVE",
               "REPLICATION CLIENT", "CREATE USER", "CREATE TABLESPACE"}
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}


class DbError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ helpers
def q(name: str) -> str:
    """Quote an identifier with backticks."""
    name = str(name or "")
    if not name or len(name) > 64 or "\x00" in name:
        raise DbError(400, f"Ungültiger Name „{name[:70]}“")
    return "`" + name.replace("`", "``") + "`"


_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def lit(s: str) -> str:
    """String literal (quoted, escaped)."""
    return pymysql.converters.escape_str(str(s))


def plain(x: str, what: str) -> str:
    if not x or not _NAME_RE.match(x) or len(x) > 64:
        raise DbError(400, f"{what} „{x}“ ist ungültig")
    return x


def cell(v, limit: int | None = CELL_LIMIT):
    """One value as JSON. Binary → {"$hex"}, long text → {"$trunc"} (the editor loads the full value)."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, dt.timedelta):
        s = int(v.total_seconds())
        sign = "-" if s < 0 else ""
        s = abs(s)
        return f"{sign}{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    if isinstance(v, set):
        return ",".join(sorted(v))
    if isinstance(v, (bytes, bytearray)):
        try:
            v = bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            b = bytes(v)
            if limit is not None and len(b) > limit // 2:
                return {"$hex": b[: limit // 2].hex(), "len": len(b), "$trunc": True}
            return {"$hex": b.hex(), "len": len(b)}
    s = str(v)
    if limit is not None and len(s) > limit:
        return {"$trunc": s[:limit], "len": len(s)}
    return s


def value_in(v):
    """Value from the UI → Python value for a parameter. {"$hex": "…"} = binary, None = NULL."""
    if isinstance(v, dict):
        if "$hex" in v:
            try:
                return bytes.fromhex(re.sub(r"\s+", "", v["$hex"] or ""))
            except ValueError as e:
                raise DbError(400, "Ungültige Hex-Daten") from e
        raise DbError(400, "Ungültiger Wert")
    if isinstance(v, bool):
        return int(v)
    return v


def split_sql(text: str) -> list[str]:
    """Split a script into statements: quotes, backticks, comments (--, #, /* */) and DELIMITER lines."""
    out, buf, delim = [], [], ";"
    i, n = 0, len(text)
    at_line_start = True
    while i < n:
        if at_line_start:
            m = re.match(r"[ \t]*DELIMITER[ \t]+(\S+)[ \t]*(\r?\n|$)", text[i:], re.I)
            if m:
                stmt = "".join(buf).strip()
                if _meaningful(stmt):
                    out.append(stmt)
                buf = []
                delim = m.group(1)
                i += m.end()
                continue
        c = text[i]
        at_line_start = False
        if text.startswith(delim, i):
            stmt = "".join(buf).strip()
            if _meaningful(stmt):
                out.append(stmt)
            buf = []
            i += len(delim)
            continue
        if c in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if text[j] == "\\" and c != "`":
                    j += 2
                    continue
                if text[j] == c:
                    if j + 1 < n and text[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "#" or (c == "-" and text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n")):
            j = text.find("\n", i)
            j = n if j < 0 else j
            buf.append(text[i:j])
            i = j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            buf.append(text[i:j])
            i = j
            continue
        if c == "\n":
            at_line_start = True
        buf.append(c)
        i += 1
    stmt = "".join(buf).strip()
    if _meaningful(stmt):
        out.append(stmt)
    return out


def _strip_comments(s: str) -> str:
    s = re.sub(r"/\*(?!!).*?\*/", " ", s, flags=re.S)
    s = re.sub(r"(--[ \t][^\n]*|--$|#[^\n]*)", " ", s, flags=re.M)
    return s.strip()


def _meaningful(stmt: str) -> bool:
    return bool(stmt) and bool(_strip_comments(stmt))


def _strip_literals(s: str) -> str:
    return re.sub(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.|\"\")*\"", "''", s, flags=re.S)


READ_FIRST = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH", "TABLE", "VALUES", "HELP", "("}


def is_read_only(stmt: str) -> bool:
    s = _strip_comments(stmt)
    if not s:
        return False
    first = re.match(r"\(|[A-Za-z]+", s)
    if not first or first.group(0).upper() not in READ_FIRST:
        return False
    flat = _strip_literals(s).lower()
    if re.search(r"\binto\s+(outfile|dumpfile)\b", flat):
        return False
    return True


# ------------------------------------------------------------------ the connector
class MySQL:
    name = "mysql"
    scheme = "mysql"
    https_ports: set[int] = set()

    # --- addresses: mysql://host:port
    @staticmethod
    def norm_url(url: str) -> str:
        url = (url or "").strip().rstrip("/")
        if not url:
            raise HTTPException(400, "Adresse fehlt")
        if "://" not in url:
            url = "mysql://" + url
        sp = urlsplit(url)
        if sp.scheme not in ("mysql", "mariadb", "tcp") or not sp.hostname:
            raise HTTPException(400, "Adresse bitte als host:port angeben, z. B. 192.168.10.20:3306")
        try:
            port = sp.port or DEFAULT_PORT
        except ValueError as e:
            raise HTTPException(400, "Ungültiger Port") from e
        host = sp.hostname
        if ":" in host:
            host = f"[{host}]"
        return f"mysql://{host}:{port}"

    @staticmethod
    def _hostport(url: str) -> tuple[str, int]:
        sp = urlsplit(url)
        return sp.hostname or "", sp.port or DEFAULT_PORT

    # --- recognise (server greeting)
    @staticmethod
    def _parse_greeting(data: bytes) -> dict | None:
        if len(data) < 6:
            return None
        payload = data[4:4 + int.from_bytes(data[:3], "little")]
        if not payload:
            return None
        if payload[0] == 0x0A:
            end = payload.find(b"\x00", 1)
            if end < 0:
                return None
            ver = payload[1:end].decode("latin-1", "replace")
            if ver.startswith("5.5.5-"):
                ver = ver[6:]
            maria = "mariadb" in ver.lower()
            short = ver.split("-")[0]
            return {"name": "MariaDB" if maria else "MySQL", "version": short, "full_version": ver}
        if payload[0] == 0xFF:             # e.g. "Host '…' is not allowed to connect to this MariaDB server"
            msg = payload[3:].decode("utf-8", "replace")
            maria = "mariadb" in msg.lower()
            return {"name": "MariaDB" if maria else "MySQL/MariaDB", "version": "", "full_version": "",
                    "note": msg[:200], "blocked": True}
        return None

    async def _greet(self, host: str, port: int, timeout: float = 1.5) -> dict | None:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=min(timeout, 0.8))
        except Exception:  # noqa: BLE001
            return None
        try:
            data = await asyncio.wait_for(r.read(512), timeout=timeout)
        except Exception:  # noqa: BLE001
            data = b""
        finally:
            w.close()
        return self._parse_greeting(data)

    def probe(self, url: str, verify: bool) -> dict:
        url = self.norm_url(url)
        host, port = self._hostport(url)
        try:
            info = asyncio.run(self._greet(host, port, 6))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                info = loop.run_until_complete(self._greet(host, port, 6))
            finally:
                loop.close()
        if not info:
            raise HTTPException(400, f"Unter {host}:{port} antwortet kein MySQL/MariaDB-Server")
        if info.get("blocked"):
            raise HTTPException(400, f"{info['name']} unter {host}:{port} lässt MIRA nicht zu: {info.get('note', '')} – "
                                     "Benutzer mit passendem Host (z. B. 'mira'@'%' oder @'<IP_MIRA>') anlegen")
        return {"app": "mysql", "name": info["name"], "version": info["version"], "instance": url, "setup_needed": False}

    async def detect(self, client, url: str) -> dict | None:
        try:
            host, port = self._hostport(url)
            info = await self._greet(host, port)
        except Exception:  # noqa: BLE001
            return None
        if not info:
            return None
        return {"app": "mysql", "name": info["name"] + (" (MIRA nicht zugelassen)" if info.get("blocked") else ""),
                "version": info["version"], "instance": url, "setup_needed": False}

    # --- connections
    @staticmethod
    def _ssl_ctx(verify: bool):
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _open(self, url: str, verify: bool, sec: dict, database: str | None = None, read_timeout: int = 300):
        host, port = self._hostport(url)
        kw = dict(host=host, port=port, user=sec.get("username", ""), password=sec.get("password", ""),
                  database=database or None, charset="utf8mb4", connect_timeout=8, read_timeout=read_timeout,
                  write_timeout=300, autocommit=True, client_flag=CLIENT.MULTI_RESULTS)
        try:
            return pymysql.connect(ssl=self._ssl_ctx(verify), **kw)
        except pymysql.err.OperationalError as e:
            # server without TLS: retry in plain text unless the certificate has to be verified
            if not verify and ((e.args and e.args[0] == 2026) or "SSL" in str(e)):
                try:
                    return pymysql.connect(**kw)
                except pymysql.MySQLError as e2:
                    raise self._err(e2) from e2
            raise self._err(e) from e
        except pymysql.MySQLError as e:
            raise self._err(e) from e
        except OSError as e:
            raise DbError(502, f"{host}:{port} ist nicht erreichbar ({e.__class__.__name__})") from e

    @staticmethod
    def _err(e: Exception) -> DbError:
        code = e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], int) else 0
        msg = e.args[1] if getattr(e, "args", None) and len(e.args) > 1 else str(e)
        if code in (1045, 1698):
            return DbError(401, f"Anmeldung abgelehnt: {msg}")
        if code == 2059:      # server asked for an auth plugin PyMySQL cannot do (e.g. gssapi after a wrong password)
            return DbError(401, f"Anmeldung abgelehnt – Benutzer/Passwort prüfen (Server verlangt Anmeldeart, die MIRA nicht kann: {msg})")
        if code in (2003, 2005, 2006, 2013):
            return DbError(502, f"Server nicht erreichbar ({code}): {msg}")
        return DbError(400, f"Fehler {code}: {msg}" if code else str(msg))

    # --- plugin protocol
    def pair(self, url: str, verify: bool, body: dict) -> dict:
        url = self.norm_url(url)
        sec = {"username": body["username"], "password": body["password"]}
        try:
            con = self._open(url, verify, sec)
        except DbError as e:
            raise HTTPException(400, e.message) from e
        try:
            with con.cursor() as c:
                c.execute("SELECT VERSION(), CURRENT_USER()")
                ver, who = c.fetchone()
        finally:
            con.close()
        return {"secret": sec, "role": body.get("role") or "admin", "remote_user": who or body["username"],
                "version": str(ver).split("-")[0]}

    def check(self, conn) -> tuple[bool, str, str]:
        sec = kern.entschluesseln(conn.token_enc) or {}
        try:
            con = self._open(conn.base_url, conn.verify_tls, sec)
        except DbError as e:
            return False, e.message, ""
        try:
            with con.cursor() as c:
                c.execute("SELECT VERSION(), CURRENT_USER(), @@version_comment")
                ver, who, comment = c.fetchone()
                tls = ""
                try:
                    c.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                    row = c.fetchone()
                    tls = " · TLS" if row and row[1] else " · ohne TLS"
                except pymysql.MySQLError:
                    pass
        finally:
            con.close()
        kind = "MariaDB" if "mariadb" in f"{ver} {comment}".lower() else "MySQL"
        short = str(ver).split("-")[0]
        return True, f"{kind} {short} · angemeldet als {who}{tls}", short

    def unpair(self, conn) -> str:
        return "Zugangsdaten in MIRA gelöscht (der Datenbank-Benutzer selbst bleibt bestehen)"

    def request(self, conn, method: str, path: str, params, body: bytes | None, headers: dict) -> httpx.Response:
        sec = kern.entschluesseln(conn.token_enc) or {}
        qp: dict[str, str] = {}
        for k, v in params or []:
            qp[k] = v
        try:
            data = json.loads(body) if body and "json" in (headers.get("content-type") or "") else None
        except ValueError:
            return _json(400, {"message": "Ungültiges JSON"})
        api = Api(self, conn, sec, qp, data, body)
        try:
            res = api.dispatch(method, path)
        except DbError as e:
            return _json(e.status, {"message": e.message})
        except pymysql.MySQLError as e:
            err = self._err(e)
            return _json(err.status, {"message": err.message})
        if isinstance(res, httpx.Response):
            return res
        return _json(200, res)


def _json(status: int, data) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                          headers={"content-type": "application/json"})


def _download(content: bytes, filename: str, ctype: str) -> httpx.Response:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120]
    return httpx.Response(200, content=content, headers={"content-type": ctype,
                                                         "content-disposition": f'attachment; filename="{safe}"'})


# ------------------------------------------------------------------ API
R = re.compile
ROUTES = [
    ("GET", R(r"/api/db/server$"), "server"),
    ("GET", R(r"/api/db/charsets$"), "charsets"),
    ("GET", R(r"/api/db/privileges$"), "privileges"),
    ("GET", R(r"/api/db/databases$"), "databases"),
    ("POST", R(r"/api/db/databases$"), "db_create"),
    ("PUT", R(r"/api/db/databases/(?P<db>[^/]+)$"), "db_alter"),
    ("DELETE", R(r"/api/db/databases/(?P<db>[^/]+)$"), "db_drop"),
    ("GET", R(r"/api/db/databases/(?P<db>[^/]+)/tables$"), "tables"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables$"), "table_create"),
    ("GET", R(r"/api/db/databases/(?P<db>[^/]+)/export$"), "export_db"),
    ("GET", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/structure$"), "structure"),
    ("PUT", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)$"), "table_alter"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/maintenance$"), "table_maintenance"),
    ("DELETE", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)$"), "table_drop"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/columns$"), "column_add"),
    ("PUT", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/columns/(?P<c>[^/]+)$"), "column_change"),
    ("DELETE", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/columns/(?P<c>[^/]+)$"), "column_drop"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/indexes$"), "index_add"),
    ("DELETE", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/indexes/(?P<i>[^/]+)$"), "index_drop"),
    ("GET", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/rows$"), "rows"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/rows$"), "row_insert"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/rows/get$"), "row_get"),
    ("PUT", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/rows$"), "row_update"),
    ("POST", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/rows/delete$"), "row_delete"),
    ("GET", R(r"/api/db/databases/(?P<db>[^/]+)/tables/(?P<t>[^/]+)/export$"), "export_table"),
    ("POST", R(r"/api/db/query$"), "query"),
    ("GET", R(r"/api/db/select$"), "select"),
    ("POST", R(r"/api/db/import$"), "import_sql"),
    ("GET", R(r"/api/db/users$"), "users"),
    ("GET", R(r"/api/db/users/grants$"), "user_grants"),
    ("POST", R(r"/api/db/users$"), "user_create"),
    ("PUT", R(r"/api/db/users$"), "user_update"),
    ("POST", R(r"/api/db/users/grant$"), "user_grant"),
    ("POST", R(r"/api/db/users/revoke$"), "user_revoke"),
    ("DELETE", R(r"/api/db/users$"), "user_drop"),
    ("GET", R(r"/api/db/processes$"), "processes"),
    ("DELETE", R(r"/api/db/processes/(?P<pid>\d+)$"), "process_kill"),
    ("GET", R(r"/api/db/variables$"), "variables"),
]


class Api:
    def __init__(self, con: MySQL, conn, sec: dict, qp: dict, data, raw: bytes | None):
        self.c, self.conn, self.sec, self.qp, self.d, self.raw = con, conn, sec, qp, data or {}, raw

    def dispatch(self, method: str, path: str):
        for m, rx, fn in ROUTES:
            if m == method:
                mt = rx.match(path)
                if mt:
                    return getattr(self, fn)(**mt.groupdict())
        raise DbError(404, f"Unbekannter Aufruf {method} {path}")

    # --- db access
    def open(self, database: str | None = None, read_timeout: int = 300):
        return self.c._open(self.conn.base_url, self.conn.verify_tls, self.sec, database, read_timeout)

    def run(self, sql: str, args=None, database: str | None = None, dict_rows: bool = False):
        con = self.open(database)
        try:
            with con.cursor(pymysql.cursors.DictCursor if dict_rows else pymysql.cursors.Cursor) as cur:
                cur.execute(sql, args)
                return cur.fetchall() if cur.description else cur.rowcount
        finally:
            con.close()

    def exec_many(self, stmts: list[tuple[str, object]], database: str | None = None):
        con = self.open(database)
        try:
            with con.cursor() as cur:
                for sql, args in stmts:
                    cur.execute(sql, args)
        finally:
            con.close()

    # --- server
    def server(self):
        con = self.open()
        try:
            with con.cursor() as c:
                c.execute("SELECT VERSION(), @@version_comment, CURRENT_USER(), @@hostname, @@port, "
                          "@@character_set_server, @@collation_server, @@max_connections")
                ver, comment, who, hostname, port, cs, coll, maxc = c.fetchone()
                c.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Threads_connected','Threads_running',"
                          "'Questions','Bytes_received','Bytes_sent','Max_used_connections','Slow_queries',"
                          "'Aborted_connects','Connections','Ssl_cipher')")
                st = {k: v for k, v in c.fetchall()}
                c.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                row = c.fetchone()
                c.execute("SELECT table_schema, COUNT(*), SUM(COALESCE(data_length,0)+COALESCE(index_length,0)) "
                          "FROM information_schema.tables GROUP BY table_schema")
                sizes = c.fetchall()
                c.execute("SHOW DATABASES")
                dbs = [r[0] for r in c.fetchall()]
        finally:
            con.close()
        kind = "MariaDB" if "mariadb" in f"{ver} {comment}".lower() else "MySQL"
        return {"kind": kind, "version": str(ver).split("-")[0], "full_version": ver, "comment": comment,
                "user": who, "hostname": hostname, "port": port, "charset": cs, "collation": coll,
                "max_connections": maxc, "status": st, "tls": (row[1] if row else "") or "",
                "databases": len(dbs), "user_databases": len([d for d in dbs if d.lower() not in SYSTEM_DBS]),
                "size": sum(int(s[2] or 0) for s in sizes), "tables": sum(int(s[1] or 0) for s in sizes),
                "address": self.conn.base_url}

    def charsets(self):
        rows = self.run("SHOW COLLATION", dict_rows=True)
        cs = {}
        for r in rows:
            name = r.get("Charset")
            if not name:
                continue
            cs.setdefault(name, {"name": name, "default": "", "collations": []})
            cs[name]["collations"].append(r["Collation"])
            if str(r.get("Default", "")).lower() == "yes":
                cs[name]["default"] = r["Collation"]
        srv = self.run("SELECT @@character_set_server, @@collation_server")[0]
        for v in cs.values():
            v["collations"].sort()
        return {"charsets": sorted(cs.values(), key=lambda x: x["name"]), "server": {"charset": srv[0], "collation": srv[1]}}

    def privileges(self):
        return {"privileges": PRIVILEGES, "global_only": sorted(GLOBAL_ONLY)}

    # --- databases
    def databases(self):
        con = self.open()
        try:
            with con.cursor() as c:
                c.execute("SELECT schema_name, default_character_set_name, default_collation_name FROM information_schema.schemata")
                schemata = c.fetchall()
                c.execute("SELECT table_schema, COUNT(*), SUM(COALESCE(data_length,0)), SUM(COALESCE(index_length,0)), "
                          "SUM(COALESCE(table_rows,0)) FROM information_schema.tables GROUP BY table_schema")
                stats = {r[0]: r for r in c.fetchall()}
        finally:
            con.close()
        out = []
        for name, cs, coll in schemata:
            s = stats.get(name) or (name, 0, 0, 0, 0)
            out.append({"name": name, "charset": cs, "collation": coll, "tables": int(s[1] or 0),
                        "data": int(s[2] or 0), "index": int(s[3] or 0), "rows": int(s[4] or 0),
                        "system": name.lower() in SYSTEM_DBS})
        out.sort(key=lambda x: (x["system"], x["name"].lower()))
        return {"databases": out}

    def _charset_sql(self) -> str:
        sql = ""
        if self.d.get("charset"):
            sql += f" CHARACTER SET {plain(self.d['charset'], 'Zeichensatz')}"
        if self.d.get("collation"):
            sql += f" COLLATE {plain(self.d['collation'], 'Sortierung')}"
        return sql

    def db_create(self):
        name = (self.d.get("name") or "").strip()
        self.run(f"CREATE DATABASE {q(name)}{self._charset_sql()}")
        return {"ok": True, "name": name}

    def db_alter(self, db):
        sql = self._charset_sql()
        if not sql:
            raise DbError(400, "Nichts zu ändern")
        self.run(f"ALTER DATABASE {q(db)}{sql}")
        return {"ok": True}

    def db_drop(self, db):
        if db.lower() in SYSTEM_DBS:
            raise DbError(400, f"Die Systemdatenbank „{db}“ wird nicht gelöscht")
        self.run(f"DROP DATABASE {q(db)}")
        return {"ok": True}

    # --- tables
    def tables(self, db):
        rows = self.run("SELECT table_name, table_type, engine, table_rows, data_length, index_length, data_free, "
                        "auto_increment, table_collation, table_comment, create_time, update_time "
                        "FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name", (db,))
        out = [{"name": r[0], "type": "view" if "VIEW" in (r[1] or "") else "table", "engine": r[2], "rows": r[3],
                "data": r[4], "index": r[5], "free": r[6], "auto_increment": r[7], "collation": r[8],
                "comment": r[9] if (r[1] or "") != "VIEW" else "", "created": cell(r[10]), "updated": cell(r[11])}
               for r in rows]
        info = self.run("SELECT default_character_set_name, default_collation_name FROM information_schema.schemata "
                        "WHERE schema_name=%s", (db,))
        if not info:
            raise DbError(404, f"Datenbank „{db}“ gibt es nicht")
        return {"tables": out, "database": db, "charset": info[0][0], "collation": info[0][1]}

    def structure(self, db, t):
        con = self.open()
        try:
            with con.cursor(pymysql.cursors.DictCursor) as c:
                c.execute(f"SHOW FULL COLUMNS FROM {q(db)}.{q(t)}")
                cols = c.fetchall()
                c.execute("SELECT table_type, engine, table_collation, table_comment, auto_increment, table_rows "
                          "FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", (db, t))
                meta = c.fetchone() or {}
                meta = {k.lower(): v for k, v in meta.items()}
                view = "VIEW" in (meta.get("table_type") or "")
                idx = []
                if not view:
                    c.execute(f"SHOW INDEX FROM {q(db)}.{q(t)}")
                    idx = c.fetchall()
                c.execute(f"SHOW CREATE {'VIEW' if view else 'TABLE'} {q(db)}.{q(t)}")
                cr = c.fetchone()
                c.execute("SELECT constraint_name, column_name, referenced_table_schema, referenced_table_name, "
                          "referenced_column_name FROM information_schema.key_column_usage WHERE table_schema=%s "
                          "AND table_name=%s AND referenced_table_name IS NOT NULL ORDER BY constraint_name, "
                          "ordinal_position", (db, t))
                fks = c.fetchall()
                c.execute("SELECT trigger_name, event_manipulation, action_timing, action_statement "
                          "FROM information_schema.triggers WHERE event_object_schema=%s AND event_object_table=%s",
                          (db, t))
                trg = c.fetchall()
        finally:
            con.close()
        indexes: dict[str, dict] = {}
        for r in idx:
            i = indexes.setdefault(r["Key_name"], {"name": r["Key_name"], "unique": not int(r["Non_unique"]),
                                                   "type": r.get("Index_type", ""), "columns": [],
                                                   "comment": r.get("Index_comment", "")})
            i["columns"].append(r["Column_name"] + (f"({r['Sub_part']})" if r.get("Sub_part") else ""))
        create = list(cr.values())[1] if cr else ""
        return {"view": view, "engine": meta.get("engine"), "collation": meta.get("table_collation"),
                "comment": meta.get("table_comment") if not view else "", "auto_increment": meta.get("auto_increment"),
                "rows": meta.get("table_rows"),
                "columns": [{"name": c["Field"], "type": c["Type"], "collation": c.get("Collation"),
                             "null": c["Null"] == "YES", "key": c["Key"], "default": cell(c["Default"]),
                             "extra": c["Extra"], "comment": c.get("Comment", "")} for c in cols],
                "indexes": list(indexes.values()),
                "foreign_keys": [{k.lower(): v for k, v in f.items()} for f in fks],
                "triggers": [{k.lower(): v for k, v in x.items()} for x in trg], "create": create}

    @staticmethod
    def column_def(c: dict) -> str:
        name = q((c.get("name") or "").strip())
        typ = (c.get("type") or "").strip()
        if not typ or re.search(r";|--|/\*|#", _strip_literals(typ)):
            raise DbError(400, f"Ungültiger Typ für {name}")
        sql = f"{name} {typ}"
        if c.get("collation"):
            sql += f" COLLATE {plain(c['collation'], 'Sortierung')}"
        sql += " NULL" if c.get("null") else " NOT NULL"
        d = c.get("default_kind") or "none"
        if d == "null":
            sql += " DEFAULT NULL"
        elif d == "value":
            sql += " DEFAULT " + lit(str(c.get("default") or ""))
        elif d == "expr":
            expr = (c.get("default") or "").strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\(\s*\d*\s*\))?$", expr):
                raise DbError(400, "Standard-Ausdruck bitte wie CURRENT_TIMESTAMP oder CURRENT_TIMESTAMP(3)")
            sql += " DEFAULT " + expr
        if c.get("auto_increment"):
            sql += " AUTO_INCREMENT"
        if c.get("on_update_ts"):
            sql += " ON UPDATE CURRENT_TIMESTAMP"
        if c.get("comment"):
            sql += " COMMENT " + lit(str(c["comment"]))
        return sql

    def table_create(self, db):
        name = (self.d.get("name") or "").strip()
        cols = self.d.get("columns") or []
        if not cols:
            raise DbError(400, "Mindestens eine Spalte angeben")
        parts = [self.column_def(c) for c in cols]
        pk = [c["name"] for c in cols if c.get("primary")]
        if pk:
            parts.append("PRIMARY KEY (" + ", ".join(q(x) for x in pk) + ")")
        sql = f"CREATE TABLE {q(db)}.{q(name)} (\n  " + ",\n  ".join(parts) + "\n)"
        if self.d.get("engine"):
            sql += f" ENGINE={plain(self.d['engine'], 'Engine')}"
        if self.d.get("charset"):
            sql += f" DEFAULT CHARSET={plain(self.d['charset'], 'Zeichensatz')}"
        if self.d.get("collation"):
            sql += f" COLLATE={plain(self.d['collation'], 'Sortierung')}"
        if self.d.get("comment"):
            sql += " COMMENT=" + lit(str(self.d["comment"]))
        if self.d.get("preview"):
            return {"sql": sql}
        self.run(sql)
        return {"ok": True, "sql": sql}

    def table_alter(self, db, t):
        stmts = []
        if self.d.get("name") and self.d["name"] != t:
            stmts.append(f"RENAME TABLE {q(db)}.{q(t)} TO {q(db)}.{q(self.d['name'])}")
            t = self.d["name"]
        alter = []
        if self.d.get("engine"):
            alter.append(f"ENGINE={plain(self.d['engine'], 'Engine')}")
        if self.d.get("collation"):
            cs = self.d.get("charset") or self.d["collation"].split("_")[0]
            alter.append(f"CONVERT TO CHARACTER SET {plain(cs, 'Zeichensatz')} COLLATE {plain(self.d['collation'], 'Sortierung')}")
        if "comment" in self.d:
            alter.append("COMMENT=" + lit(str(self.d.get("comment") or "")))
        if self.d.get("auto_increment"):
            alter.append(f"AUTO_INCREMENT={int(self.d['auto_increment'])}")
        if alter:
            stmts.append(f"ALTER TABLE {q(db)}.{q(t)} " + ", ".join(alter))
        if not stmts:
            raise DbError(400, "Nichts zu ändern")
        self.exec_many([(s, None) for s in stmts])
        return {"ok": True, "sql": ";\n".join(stmts)}

    def table_maintenance(self, db, t):
        op = (self.d.get("op") or "").upper()
        if op not in ("OPTIMIZE", "ANALYZE", "CHECK", "REPAIR", "FLUSH"):
            raise DbError(400, "Unbekannte Aktion")
        if op == "FLUSH":
            self.run(f"FLUSH TABLE {q(db)}.{q(t)}")
            return {"result": [{"Msg_text": "OK"}]}
        rows = self.run(f"{op} TABLE {q(db)}.{q(t)}", dict_rows=True)
        return {"result": [{k: cell(v) for k, v in r.items()} for r in rows]}

    def table_drop(self, db, t):
        mode = self.qp.get("mode", "drop")
        if mode == "truncate":
            self.run(f"TRUNCATE TABLE {q(db)}.{q(t)}")
        elif mode == "empty":
            n = self.run(f"DELETE FROM {q(db)}.{q(t)}")
            return {"ok": True, "affected": n}
        else:
            view = self.qp.get("view") == "1"
            self.run(f"DROP {'VIEW' if view else 'TABLE'} {q(db)}.{q(t)}")
        return {"ok": True}

    def column_add(self, db, t):
        sql = f"ALTER TABLE {q(db)}.{q(t)} ADD COLUMN {self.column_def(self.d)}"
        pos = self.d.get("after")
        if pos == "^first":
            sql += " FIRST"
        elif pos:
            sql += f" AFTER {q(pos)}"
        self.run(sql)
        return {"ok": True, "sql": sql}

    def column_change(self, db, t, c):
        sql = f"ALTER TABLE {q(db)}.{q(t)} CHANGE COLUMN {q(c)} {self.column_def(self.d)}"
        self.run(sql)
        return {"ok": True, "sql": sql}

    def column_drop(self, db, t, c):
        self.run(f"ALTER TABLE {q(db)}.{q(t)} DROP COLUMN {q(c)}")
        return {"ok": True}

    def index_add(self, db, t):
        kind = (self.d.get("kind") or "INDEX").upper()
        if kind not in ("INDEX", "UNIQUE", "FULLTEXT", "PRIMARY", "SPATIAL"):
            raise DbError(400, "Unbekannte Index-Art")
        cols = []
        for x in self.d.get("columns") or []:
            m = re.match(r"^(.+?)(?:\((\d+)\))?$", str(x).strip())
            cols.append(q(m.group(1)) + (f"({m.group(2)})" if m.group(2) else ""))
        if not cols:
            raise DbError(400, "Mindestens eine Spalte wählen")
        if kind == "PRIMARY":
            sql = f"ALTER TABLE {q(db)}.{q(t)} ADD PRIMARY KEY ({', '.join(cols)})"
        else:
            name = (self.d.get("name") or "").strip()
            word = {"INDEX": "INDEX", "UNIQUE": "UNIQUE INDEX", "FULLTEXT": "FULLTEXT INDEX", "SPATIAL": "SPATIAL INDEX"}[kind]
            sql = f"ALTER TABLE {q(db)}.{q(t)} ADD {word} {q(name) + ' ' if name else ''}({', '.join(cols)})"
        self.run(sql)
        return {"ok": True, "sql": sql}

    def index_drop(self, db, t, i):
        sql = f"ALTER TABLE {q(db)}.{q(t)} DROP PRIMARY KEY" if i == "PRIMARY" else f"ALTER TABLE {q(db)}.{q(t)} DROP INDEX {q(i)}"
        self.run(sql)
        return {"ok": True}

    # --- rows
    def _columns(self, cur, db, t) -> list[dict]:
        cur.execute(f"SHOW FULL COLUMNS FROM {q(db)}.{q(t)}")
        return [{"name": r[0], "type": r[1], "null": r[3] == "YES", "key": r[4], "default": cell(r[5]), "extra": r[6],
                 "comment": r[8] if len(r) > 8 else ""} for r in cur.fetchall()]

    @staticmethod
    def _row_key(cur, db, t, cols) -> list[str]:
        """Primary key, else the first unique key without NULL columns, else [] (all columns are used)."""
        cur.execute(f"SHOW INDEX FROM {q(db)}.{q(t)}")
        idx: dict[str, list] = {}
        uniq: dict[str, bool] = {}
        for r in cur.fetchall():
            idx.setdefault(r[2], []).append(r[4])
            uniq[r[2]] = not int(r[1])
        if "PRIMARY" in idx:
            return idx["PRIMARY"]
        notnull = {c["name"] for c in cols if not c["null"]}
        for k, v in idx.items():
            if uniq[k] and all(x in notnull for x in v):
                return v
        return []

    _OPS = {"=": "=", "!=": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">=", "like": "LIKE", "not like": "NOT LIKE",
            "contains": "LIKE", "null": "IS NULL", "not null": "IS NOT NULL"}

    def _where(self, cols) -> tuple[str, list]:
        """Filters: f=<col>|<op>|<value> (several, AND) and s=<text> (search in all columns)."""
        names = {c["name"] for c in cols}
        parts, args = [], []
        for raw in [v for k, v in self.qp.items() if k == "f"] + [self.qp[k] for k in sorted(self.qp) if k.startswith("f.")]:
            col, op, val = (raw.split("|", 2) + ["", ""])[:3]
            if col not in names or op not in self._OPS:
                raise DbError(400, f"Ungültiger Filter {raw[:80]}")
            if op in ("null", "not null"):
                parts.append(f"{q(col)} {self._OPS[op]}")
            elif op == "contains":
                parts.append(f"{q(col)} LIKE %s")
                args.append("%" + val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            else:
                parts.append(f"{q(col)} {self._OPS[op]} %s")
                args.append(val)
        s = (self.qp.get("s") or "").strip()
        if s:
            like = "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            ors = [f"CAST({q(c['name'])} AS CHAR) LIKE %s" for c in cols if not re.match(r"(tiny|medium|long)?blob|binary|varbinary|geometry|point", c["type"], re.I)]
            if ors:
                parts.append("(" + " OR ".join(ors) + ")")
                args.extend([like] * len(ors))
        raw_where = (self.qp.get("where") or "").strip()
        if raw_where:
            flat = _strip_literals(raw_where).lower()
            if ";" in flat or re.search(r"\binto\s+(outfile|dumpfile)\b", flat):
                raise DbError(400, "WHERE darf kein ; und kein INTO OUTFILE enthalten")
            parts.append(f"({raw_where})")
        return (" WHERE " + " AND ".join(parts)) if parts else "", args

    def rows(self, db, t):
        limit = max(1, min(PAGE_MAX, int(self.qp.get("limit") or 50)))
        offset = max(0, int(self.qp.get("offset") or 0))
        con = self.open(db)          # free WHERE text may name other tables of the database
        try:
            with con.cursor() as cur:
                cols = self._columns(cur, db, t)
                key = self._row_key(cur, db, t, cols)
                where, args = self._where(cols)
                if self.qp.get("where"):           # free WHERE text: runs in a read-only transaction
                    cur.execute("START TRANSACTION READ ONLY")
                order = ""
                if self.qp.get("order") in {c["name"] for c in cols}:
                    order = f" ORDER BY {q(self.qp['order'])} {'DESC' if self.qp.get('dir') == 'desc' else 'ASC'}"
                cur.execute(f"SELECT * FROM {q(db)}.{q(t)}{where}{order} LIMIT %s OFFSET %s", (*args, limit, offset))
                desc = [d[0] for d in cur.description]
                data = [[cell(v) for v in r] for r in cur.fetchall()]
                approx = False
                cur.execute("SELECT table_rows, table_type FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", (db, t))
                est = cur.fetchone()
                if not where and est and est[0] and int(est[0]) > 2_000_000:
                    total, approx = int(est[0]), True
                else:
                    cur.execute(f"SELECT COUNT(*) FROM {q(db)}.{q(t)}{where}", args)
                    total = cur.fetchone()[0]
                if self.qp.get("where"):
                    cur.execute("ROLLBACK")
        finally:
            con.close()
        return {"columns": cols, "names": desc, "rows": data, "total": total, "approx": approx, "offset": offset,
                "limit": limit, "key": key, "view": bool(est and "VIEW" in (est[1] or ""))}

    def _key_where(self, cur, db, t, keyvals: dict) -> tuple[str, list]:
        cols = self._columns(cur, db, t)
        names = {c["name"] for c in cols}
        if not keyvals or any(k not in names for k in keyvals):
            raise DbError(400, "Zeile nicht eindeutig bestimmbar")
        parts, args = [], []
        for k, v in keyvals.items():
            if isinstance(v, dict) and v.get("$trunc") is not None and "$hex" not in v:
                raise DbError(400, "Zeile ohne Schlüssel mit langen Werten – bitte per SQL ändern")
            parts.append(f"{q(k)} <=> %s")
            args.append(value_in({"$hex": v["$hex"]} if isinstance(v, dict) and "$hex" in v else v))
        return " WHERE " + " AND ".join(parts), args

    def row_get(self, db, t):
        con = self.open()
        try:
            with con.cursor() as cur:
                where, args = self._key_where(cur, db, t, self.d.get("key") or {})
                cur.execute(f"SELECT * FROM {q(db)}.{q(t)}{where} LIMIT 2", args)
                desc = [d[0] for d in cur.description]
                rows = cur.fetchall()
        finally:
            con.close()
        if not rows:
            raise DbError(404, "Zeile nicht (mehr) vorhanden")
        return {"names": desc, "row": [cell(v, None if not isinstance(v, (bytes, bytearray)) else 2_000_000) for v in rows[0]],
                "unique": len(rows) == 1}

    def row_insert(self, db, t):
        vals = self.d.get("values") or {}
        if not vals:
            sql, args = f"INSERT INTO {q(db)}.{q(t)} () VALUES ()", []
        else:
            sql = (f"INSERT INTO {q(db)}.{q(t)} (" + ", ".join(q(k) for k in vals) + ") VALUES ("
                   + ", ".join(["%s"] * len(vals)) + ")")
            args = [value_in(v) for v in vals.values()]
        con = self.open()
        try:
            with con.cursor() as cur:
                n = cur.execute(sql, args)
                return {"ok": True, "affected": n, "insert_id": cur.lastrowid}
        finally:
            con.close()

    def row_update(self, db, t):
        vals = self.d.get("values") or {}
        if not vals:
            raise DbError(400, "Nichts geändert")
        con = self.open()
        try:
            with con.cursor() as cur:
                where, wargs = self._key_where(cur, db, t, self.d.get("key") or {})
                sets = ", ".join(f"{q(k)} = %s" for k in vals)
                n = cur.execute(f"UPDATE {q(db)}.{q(t)} SET {sets}{where} LIMIT 1", [*(value_in(v) for v in vals.values()), *wargs])
        finally:
            con.close()
        return {"ok": True, "affected": n}

    def row_delete(self, db, t):
        keys = self.d.get("keys") or ([self.d["key"]] if self.d.get("key") else [])
        if not keys:
            raise DbError(400, "Keine Zeile gewählt")
        con = self.open()
        n = 0
        try:
            con.begin()
            with con.cursor() as cur:
                for k in keys:
                    where, args = self._key_where(cur, db, t, k)
                    n += cur.execute(f"DELETE FROM {q(db)}.{q(t)}{where} LIMIT 1", args)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        return {"ok": True, "affected": n}

    # --- SQL
    def _run_script(self, sql: str, database: str | None, max_rows: int, read_only: bool = False,
                    stop_on_error: bool = True):
        stmts = split_sql(sql or "")
        if not stmts:
            raise DbError(400, "Keine SQL-Anweisung")
        if read_only:
            if len(stmts) != 1:
                raise DbError(403, "Mit Leserechten ist nur eine einzelne Abfrage erlaubt")
            if not is_read_only(stmts[0]):
                raise DbError(403, "Mit Leserechten sind nur SELECT, SHOW, DESCRIBE und EXPLAIN erlaubt")
        con = self.open(database, read_timeout=600)
        results = []
        try:
            if read_only:
                with con.cursor() as c:
                    c.execute("START TRANSACTION READ ONLY")
            for i, st in enumerate(stmts):
                t0 = time.perf_counter()
                cur = con.cursor(pymysql.cursors.SSCursor)
                try:
                    cur.execute(st)
                    sets = []
                    while True:
                        if cur.description:
                            names = [d[0] for d in cur.description]
                            rows, trunc = [], False
                            for r in cur:
                                if len(rows) >= max_rows:
                                    trunc = True
                                    break
                                rows.append([cell(v) for v in r])
                            sets.append({"columns": names, "rows": rows, "truncated": trunc})
                            if trunc:
                                break
                        if not cur.nextset():
                            break
                    ms = round((time.perf_counter() - t0) * 1000, 1)
                    res = {"statement": st[:2000], "ms": ms, "sets": sets}
                    if not sets:
                        res["affected"] = cur.rowcount
                        res["insert_id"] = cur.lastrowid or None
                    try:
                        warns = con.show_warnings()
                        if warns:
                            res["warnings"] = [f"{w[0]} {w[1]}: {w[2]}" for w in warns[:20]]
                    except pymysql.MySQLError:
                        pass
                    results.append(res)
                except pymysql.MySQLError as e:
                    err = MySQL._err(e)
                    results.append({"statement": st[:2000], "error": err.message,
                                    "ms": round((time.perf_counter() - t0) * 1000, 1)})
                    if stop_on_error:
                        break
                finally:
                    try:
                        cur.close()
                    except pymysql.MySQLError:
                        pass
            if read_only:
                with con.cursor() as c:
                    c.execute("ROLLBACK")
        finally:
            con.close()
        return {"results": results, "count": len(stmts), "done": len(results),
                "errors": sum(1 for r in results if r.get("error"))}

    def query(self):
        mr = max(1, min(10000, int(self.d.get("max_rows") or MAX_ROWS)))
        return self._run_script(self.d.get("sql", ""), self.d.get("database") or None, mr,
                                stop_on_error=self.d.get("continue") is not True)

    def select(self):
        mr = max(1, min(10000, int(self.qp.get("max_rows") or MAX_ROWS)))
        return self._run_script(self.qp.get("sql", ""), self.qp.get("database") or None, mr, read_only=True)

    def import_sql(self):
        raw = self.raw or b""
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError as e:
                raise DbError(400, "gzip-Datei beschädigt") from e
        text = raw.decode("utf-8-sig", "replace")
        db = self.qp.get("database") or None
        stmts = split_sql(text)
        if not stmts:
            raise DbError(400, "Die Datei enthält keine SQL-Anweisungen")
        cont = self.qp.get("continue") == "1"
        con = self.open(db, read_timeout=3600)
        done, errors, affected = 0, [], 0
        t0 = time.perf_counter()
        try:
            with con.cursor() as cur:
                for i, st in enumerate(stmts):
                    try:
                        n = cur.execute(st)
                        if cur.description:
                            cur.fetchall()
                        else:
                            affected += max(0, n or 0)
                        done += 1
                    except pymysql.MySQLError as e:
                        errors.append({"index": i + 1, "statement": st[:500], "error": MySQL._err(e).message})
                        if not cont or len(errors) >= 50:
                            break
        finally:
            con.close()
        return {"statements": len(stmts), "executed": done, "affected": affected, "errors": errors,
                "seconds": round(time.perf_counter() - t0, 1)}

    # --- export
    def _dump(self, db: str, tables: list[str] | None, structure: bool, data: bool, drop: bool,
              routines: bool) -> bytes:
        out = io.StringIO()
        con = self.open(db, read_timeout=3600)
        try:
            with con.cursor() as c:
                c.execute("SELECT VERSION()")
                ver = c.fetchone()[0]
                c.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema=%s "
                          "ORDER BY table_name", (db,))
                all_t = c.fetchall()
            want = set(tables) if tables else None
            base = [n for n, ty in all_t if "VIEW" not in ty and (want is None or n in want)]
            views = [n for n, ty in all_t if "VIEW" in ty and (want is None or n in want)]
            out.write(f"-- MIRA · DANA SQL-Export\n-- Server: {ver} ({self.conn.base_url})\n-- Datenbank: {db}\n"
                      f"-- Zeit: {dt.datetime.now().isoformat(timespec='seconds')}\n\n"
                      "SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\nSET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';\n"
                      "SET time_zone='+00:00';\n\n")
            with con.cursor() as c:
                c.execute("SET time_zone='+00:00'")
            for t in base:
                if structure:
                    with con.cursor() as c:
                        c.execute(f"SHOW CREATE TABLE {q(t)}")
                        create = c.fetchone()[1]
                    out.write(f"-- Tabelle {t}\n")
                    if drop:
                        out.write(f"DROP TABLE IF EXISTS {q(t)};\n")
                    out.write(create + ";\n\n")
                if data:
                    cur = con.cursor(pymysql.cursors.SSCursor)
                    try:
                        cur.execute(f"SELECT * FROM {q(t)}")
                        names = ", ".join(q(d[0]) for d in cur.description)
                        batch = []
                        for r in cur:
                            batch.append("(" + ",".join(con.escape(v) for v in r) + ")")
                            if len(batch) >= 200:
                                out.write(f"INSERT INTO {q(t)} ({names}) VALUES\n" + ",\n".join(batch) + ";\n")
                                batch = []
                        if batch:
                            out.write(f"INSERT INTO {q(t)} ({names}) VALUES\n" + ",\n".join(batch) + ";\n")
                        out.write("\n")
                    finally:
                        cur.close()
            if structure:
                for v in views:
                    with con.cursor() as c:
                        c.execute(f"SHOW CREATE VIEW {q(v)}")
                        create = c.fetchone()[1]
                    out.write(f"-- Ansicht {v}\n")
                    if drop:
                        out.write(f"DROP VIEW IF EXISTS {q(v)};\n")
                    out.write(re.sub(r"DEFINER=`[^`]*`@`[^`]*` ", "", create) + ";\n\n")
                if routines:
                    with con.cursor() as c:
                        c.execute("SELECT trigger_name, event_object_table FROM information_schema.triggers "
                                  "WHERE trigger_schema=%s", (db,))
                        trigs = [r[0] for r in c.fetchall() if want is None or r[1] in want]
                        c.execute("SELECT routine_name, routine_type FROM information_schema.routines WHERE routine_schema=%s", (db,))
                        routs = c.fetchall()
                    if trigs or routs:
                        out.write("DELIMITER ;;\n")
                    for tr in trigs:
                        with con.cursor() as c:
                            c.execute(f"SHOW CREATE TRIGGER {q(tr)}")
                            row = c.fetchone()
                        if drop:
                            out.write(f"DROP TRIGGER IF EXISTS {q(tr)};;\n")
                        out.write(re.sub(r"DEFINER=`[^`]*`@`[^`]*` ", "", row[2]) + ";;\n")
                    for name, typ in routs:
                        with con.cursor() as c:
                            c.execute(f"SHOW CREATE {typ} {q(name)}")
                            row = c.fetchone()
                        if drop:
                            out.write(f"DROP {typ} IF EXISTS {q(name)};;\n")
                        out.write(re.sub(r"DEFINER=`[^`]*`@`[^`]*` ", "", row[2] or "") + ";;\n")
                    if trigs or routs:
                        out.write("DELIMITER ;\n\n")
            out.write("SET FOREIGN_KEY_CHECKS=1;\n")
        finally:
            con.close()
        return out.getvalue().encode("utf-8")

    def _export(self, db: str, tables: list[str] | None, fname: str):
        fmt = self.qp.get("format", "sql")
        flag = lambda k, d="1": self.qp.get(k, d) == "1"  # noqa: E731
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        if fmt == "csv":
            if not tables or len(tables) != 1:
                raise DbError(400, "CSV gibt es nur für eine Tabelle")
            con = self.open(db, read_timeout=3600)
            buf = io.StringIO()
            try:
                cur = con.cursor(pymysql.cursors.SSCursor)
                cur.execute(f"SELECT * FROM {q(tables[0])}")
                w = csv.writer(buf, delimiter=";" if self.qp.get("sep", ";") == ";" else ",", lineterminator="\n")
                w.writerow([d[0] for d in cur.description])
                for r in cur:
                    w.writerow(["" if v is None else (v.hex() if isinstance(v, (bytes, bytearray)) and not _utf8(v)
                                                    else cell(v, None)) for v in r])
                cur.close()
            finally:
                con.close()
            return _download(("﻿" + buf.getvalue()).encode("utf-8"), f"{fname}-{stamp}.csv", "text/csv; charset=utf-8")
        content = self._dump(db, tables, flag("structure"), flag("data"), flag("drop"), flag("routines", "1"))
        if self.qp.get("gzip") == "1":
            return _download(gzip.compress(content), f"{fname}-{stamp}.sql.gz", "application/gzip")
        return _download(content, f"{fname}-{stamp}.sql", "application/sql; charset=utf-8")

    def export_db(self, db):
        tables = [x for x in (self.qp.get("tables") or "").split(",") if x] or None
        return self._export(db, tables, db)

    def export_table(self, db, t):
        return self._export(db, [t], f"{db}.{t}")

    # --- users
    def users(self):
        try:
            rows = self.run("SELECT * FROM mysql.user", dict_rows=True)
        except pymysql.MySQLError as e:
            raise DbError(403, "Benutzerliste nicht lesbar – dem MIRA-Benutzer fehlt das Recht auf mysql.user "
                               f"({MySQL._err(e).message})") from e
        locked: dict[tuple, bool] = {}
        if rows and "account_locked" not in {k.lower() for k in rows[0]}:      # MariaDB: lock flag in global_priv
            try:
                for u, h, lk in self.run("SELECT User, Host, JSON_VALUE(Priv, '$.account_locked') FROM mysql.global_priv"):
                    locked[(cell(u), cell(h))] = str(lk) in ("true", "1")
            except pymysql.MySQLError:
                pass
        out = []
        for r in rows:
            r = {k.lower(): v for k, v in r.items()}
            dec = lambda x: x.decode() if isinstance(x, (bytes, bytearray)) else x  # noqa: E731
            out.append({"user": dec(r.get("user")), "host": dec(r.get("host")), "plugin": dec(r.get("plugin")) or "",
                        "locked": locked.get((dec(r.get("user")), dec(r.get("host"))), str(dec(r.get("account_locked")) or "N").upper() == "Y"),
                        "system": dec(r.get("user")) in ("mariadb.sys", "mysql.sys", "mysql.session", "mysql.infoschema"),
                        "expired": str(dec(r.get("password_expired")) or "N").upper() == "Y",
                        "role": str(dec(r.get("is_role")) or "N").upper() == "Y",
                        "has_password": bool(dec(r.get("authentication_string")) or dec(r.get("password"))),
                        "super": str(dec(r.get("super_priv")) or "N").upper() == "Y",
                        "max_connections": r.get("max_user_connections")})
        out.sort(key=lambda x: (x["user"].lower(), x["host"]))
        return {"users": out}

    def _acct(self, src: dict | None = None) -> tuple[str, str]:
        src = src if src is not None else (self.d or self.qp)
        user, host = str(src.get("user", "")), str(src.get("host", "%") or "%")
        if len(user) > 80 or len(host) > 255:
            raise DbError(400, "Benutzer oder Host zu lang")
        return user, host

    def user_grants(self):
        user, host = self._acct(self.qp)
        rows = self.run("SHOW GRANTS FOR %s@%s", (user, host))
        # never show password hashes ("IDENTIFIED BY PASSWORD '*…'", "VIA … USING '…'")
        return {"grants": [re.sub(r"\b(PASSWORD|USING) '[^']*'", r"\1 '•••'", r[0]) for r in rows]}

    @staticmethod
    def _target(on: str) -> str:
        on = (on or "*.*").strip()
        if on == "*.*":
            return "*.*"
        m = re.match(r"^(?:`((?:[^`]|``)+)`|([^.`]+))\.(?:\*|`((?:[^`]|``)+)`|([^.`]+))$", on)
        if not m:
            raise DbError(400, "Ziel bitte als *.*, datenbank.* oder datenbank.tabelle")
        db = (m.group(1) or "").replace("``", "`") or m.group(2)
        t = (m.group(3) or "").replace("``", "`") or m.group(4)
        return f"{q(db)}.{q(t) if t else '*'}"

    def _privs(self) -> str:
        pr = self.d.get("privileges") or []
        if pr == "ALL" or pr == ["ALL"] or "ALL PRIVILEGES" in pr:
            return "ALL PRIVILEGES"
        bad = [p for p in pr if p not in PRIVILEGES and p != "USAGE"]
        if bad or not pr:
            raise DbError(400, "Unbekannte Rechte: " + ", ".join(bad) if bad else "Keine Rechte gewählt")
        return ", ".join(pr)

    def user_create(self):
        user, host = self._acct()
        if not user:
            raise DbError(400, "Benutzername fehlt")
        stmts = [("CREATE USER %s@%s IDENTIFIED BY %s", (user, host, self.d.get("password") or ""))]
        if self.d.get("create_db"):
            stmts.insert(0, (f"CREATE DATABASE IF NOT EXISTS {q(user)}", None))
            stmts.append((f"GRANT ALL PRIVILEGES ON {q(user)}.* TO %s@%s", (user, host)))
        if self.d.get("privileges") and self.d.get("on"):
            stmts.append((f"GRANT {self._privs()} ON {self._target(self.d['on'])} TO %s@%s"
                          + (" WITH GRANT OPTION" if self.d.get("grant_option") else ""), (user, host)))
        self.exec_many(stmts)
        return {"ok": True}

    def user_update(self):
        user, host = self._acct()
        stmts = []
        if self.d.get("password"):
            stmts.append(("ALTER USER %s@%s IDENTIFIED BY %s", (user, host, self.d["password"])))
        if "locked" in self.d:
            stmts.append((f"ALTER USER %s@%s ACCOUNT {'LOCK' if self.d['locked'] else 'UNLOCK'}", (user, host)))
        if self.d.get("max_connections") is not None and str(self.d["max_connections"]) != "":
            stmts.append((f"ALTER USER %s@%s WITH MAX_USER_CONNECTIONS {int(self.d['max_connections'])}", (user, host)))
        new_user, new_host = self.d.get("new_user"), self.d.get("new_host")
        if (new_user and new_user != user) or (new_host and new_host != host):
            stmts.append(("RENAME USER %s@%s TO %s@%s", (user, host, new_user or user, new_host or host)))
        if not stmts:
            raise DbError(400, "Nichts zu ändern")
        self.exec_many(stmts)
        return {"ok": True}

    def user_grant(self):
        user, host = self._acct()
        sql = f"GRANT {self._privs()} ON {self._target(self.d.get('on'))} TO %s@%s"
        if self.d.get("grant_option"):
            sql += " WITH GRANT OPTION"
        self.run(sql, (user, host))
        return {"ok": True}

    def user_revoke(self):
        user, host = self._acct()
        if self.d.get("grant"):       # revoke a line exactly as SHOW GRANTS printed it
            g = str(self.d["grant"])
            m = re.match(r"^GRANT (.+?) ON (.+?) TO ", g)
            if not m:
                raise DbError(400, "Diese Zeile lässt sich nicht automatisch entziehen (z. B. Rollen) – bitte per SQL")
            privs, on = m.group(1), m.group(2)
            if privs.strip().upper() == "USAGE":
                raise DbError(400, "USAGE ist das Anmelderecht – Benutzer stattdessen löschen")
            if not re.match(r"^[A-Z ,()`_a-z0-9]+$", privs):
                raise DbError(400, "Rechte nicht erkannt")
            on_sql = self._target(on)
            stmts = [(f"REVOKE {privs} ON {on_sql} FROM %s@%s", (user, host))]
            if " WITH GRANT OPTION" in g:
                stmts.append((f"REVOKE GRANT OPTION ON {on_sql} FROM %s@%s", (user, host)))
            self.exec_many(stmts)
            return {"ok": True}
        self.run(f"REVOKE {self._privs()} ON {self._target(self.d.get('on'))} FROM %s@%s", (user, host))
        return {"ok": True}

    def user_drop(self):
        user, host = self._acct(self.qp)
        me = self.run("SELECT CURRENT_USER()")[0][0]
        if me == f"{user}@{host}":
            raise DbError(400, "MIRA löscht nicht den Benutzer, mit dem es selbst angemeldet ist")
        self.run("DROP USER %s@%s", (user, host))
        return {"ok": True}

    # --- processes / variables
    def processes(self):
        rows = self.run("SHOW FULL PROCESSLIST", dict_rows=True)
        cid = self.run("SELECT CONNECTION_ID()")[0][0]
        return {"processes": [{k.lower(): cell(v, 2000) for k, v in r.items()} for r in rows], "own": cid}

    def process_kill(self, pid):
        self.run("KILL %s", (int(pid),))
        return {"ok": True}

    def variables(self):
        kind = "STATUS" if self.qp.get("kind") == "status" else "VARIABLES"
        like = self.qp.get("like") or "%"
        rows = self.run(f"SHOW GLOBAL {kind} LIKE %s", ("%" + like.strip("%") + "%" if like != "%" else "%",))
        return {"items": [[r[0], cell(r[1], 2000)] for r in rows]}


def _utf8(b) -> bool:
    try:
        bytes(b).decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
