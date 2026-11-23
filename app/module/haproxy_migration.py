"""HAProxy migration assistant: copy configuration between HAProxy servers in MIRA.

Sources and targets: "haproxy-ui" (haproxy-unraid container) and "opnsense" (os-haproxy plugin).
Everything goes through one neutral model:

  backends  [{key, name, mode, balance, servers:[{key, name, address, port, ssl, verify, check, backup, weight, extra}], extra:[lines]}]
  routes    [{key, host, backend, access, tls_mode, note, kind}]     host → backend (dienste.map / Host- or SNI-ACL + use_backend)
  frontends [{key, name, mode, binds:[{address, port, ssl, options}], default_backend,
              acls:[{key, name, criteria:[str], negate}], rules:[{key, name, kind, backend, test, cond, line}], extra:[lines]}]

  rule kind "use_backend": backend + condition tokens; kind "line": any other line (http-request …) with its condition.
  condition tokens: ["acl_name", "!acl_name", "{ anonymous }", "!{ … }", "or"]

The user picks what to import and may rename backends, servers, frontends, ACLs ("Bedingungen") and
rules ("Regeln"); every reference (default_backend, use_backend, routes, conditions, linked ACLs) follows
the new names, so the imported configuration works the same way. Names that already exist in the target
are reported as conflicts: rename, or "skip" = use the existing object of the target.
Import only saves (haproxy-ui: draft, OPNsense: config.xml) – check and apply afterwards as usual.
"""
from __future__ import annotations

import re
import shlex
from datetime import datetime

from module import Fehler
from module import haproxy as m_haproxy
from module import opnsense_haproxy as m_opn

# ------------------------------------------------------------------ haproxy config text
SKIP_SECTIONS = {"global", "defaults", "userlist", "resolvers", "peers", "cache", "program", "mailers", "http-errors", "ring", "crt-store"}
# haproxy-unraid infrastructure (map-based routing, statistics, ACME) – shown, but not selected by default
HUI_INFRA = {"st_quelle", "be_tls_ende", "be_certwarden_http01", "stats", "fe_http", "fe_https", "fe_tls"}
NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
HOST_RE = re.compile(r"^[a-z0-9*][a-z0-9.*-]*$")


SECTIONS = SKIP_SECTIONS | {"frontend", "backend", "listen"}


def _sections(text: str) -> list[dict]:
    out, cur = [], None
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        w = s.split()
        if w[0] in SECTIONS:
            cur = {"type": w[0], "name": w[1] if len(w) > 1 else w[0], "lines": []}
            out.append(cur)
        elif cur is not None:
            cur["lines"].append(s)
    return out


def _cond_tokens(cond: str) -> list[str]:
    """Split a condition into tokens; anonymous ACLs { … } stay together."""
    toks, i, n = [], 0, len(cond)
    while i < n:
        c = cond[i]
        if c.isspace():
            i += 1
            continue
        neg = ""
        if c == "!":
            neg, i = "!", i + 1
            while i < n and cond[i].isspace():
                i += 1
        if i < n and cond[i] == "{":
            j = cond.find("}", i)
            j = n - 1 if j < 0 else j
            toks.append(neg + cond[i:j + 1])
            i = j + 1
            continue
        j = i
        while j < n and not cond[j].isspace():
            j += 1
        w = cond[i:j]
        toks.append("or" if w in ("or", "||") else neg + w)
        i = j
    return toks


def _split_cond(line: str) -> tuple[str, str, list[str]]:
    """'use_backend x if a !b' → ('use_backend x', 'if', [...])"""
    m = re.search(r"\s(if|unless)\s", " " + line + " ")
    if not m:
        return line, "", []
    pos = m.start() - 1 if m.start() > 0 else 0
    head = line[:pos].rstrip()
    return head, m.group(1), _cond_tokens(line[pos + len(m.group(0)) - 1:].strip())


def _join_cond(test: str, toks: list[str]) -> str:
    return f" {test} " + " ".join(toks) if test and toks else ""


def _server(words: list[str], idx: int) -> dict:
    name = words[1] if len(words) > 1 else f"s{idx}"
    addr = (words[2] if len(words) > 2 else "").strip('"')
    m = re.match(r"^(.*):(\d+|\$\{\w+\})$", addr)
    host, port = (m.group(1), m.group(2)) if m and (addr.count(":") == 1 or addr.startswith("[")) else (addr, "")
    opts = words[3:]
    o = {"key": name, "name": name, "address": host, "port": port, "ssl": "ssl" in opts, "check": "check" in opts,
         "backup": "backup" in opts, "verify": "none" if "verify" in opts and opts[opts.index("verify") + 1:opts.index("verify") + 2] == ["none"] else "required",
         "weight": "", "extra": []}
    skip = 0
    for k, x in enumerate(opts):
        if skip:
            skip -= 1
            continue
        if x in ("ssl", "check", "backup"):
            continue
        if x == "verify":
            skip = 1
            continue
        if x == "weight" and k + 1 < len(opts):
            o["weight"] = opts[k + 1]
            skip = 1
            continue
        o["extra"].append(x)
    o["extra"] = " ".join(o["extra"])
    return o


def parse_config(text: str, warnings: list[str]) -> dict:
    """haproxy config text → model (backends, frontends; routes come from dienste.map separately)."""
    model = {"backends": [], "frontends": [], "routes": []}
    for sec in _sections(text):
        t, name = sec["type"], sec["name"]
        if t in SKIP_SECTIONS:
            continue
        if t == "listen":
            warnings.append(f"„listen {name}“ wird nicht übernommen (bitte als frontend + backend anlegen)")
            continue
        if t == "backend":
            be = {"key": name, "name": name, "mode": "tcp", "balance": "", "servers": [], "extra": [], "infra": name in HUI_INFRA}
            for s in sec["lines"]:
                w = s.split()
                if w[0] == "mode" and len(w) > 1:
                    be["mode"] = w[1]
                elif w[0] == "balance" and len(w) > 1:
                    be["balance"] = w[1]
                elif w[0] == "server":
                    be["servers"].append(_server(shlex_split(s), len(be["servers"]) + 1))
                else:
                    be["extra"].append(s)
            model["backends"].append(be)
            continue
        fe = {"key": name, "name": name, "mode": "tcp", "binds": [], "default_backend": "", "acls": [], "rules": [], "extra": [],
              "infra": name in HUI_INFRA}
        acls: dict[str, dict] = {}
        for s in sec["lines"]:
            w = s.split()
            if w[0] == "mode" and len(w) > 1:
                fe["mode"] = w[1]
            elif w[0] == "bind" and len(w) > 1:
                fe["binds"].append(_bind(s[4:].strip()))
            elif w[0] == "default_backend" and len(w) > 1:
                fe["default_backend"] = w[1]
            elif w[0] == "acl" and len(w) > 2:
                a = acls.get(w[1])
                if not a:
                    a = acls[w[1]] = {"key": w[1], "name": w[1], "criteria": [], "negate": False}
                    fe["acls"].append(a)
                a["criteria"].append(s.split(None, 2)[2])
            elif w[0] in ("use_backend", "use-server") and len(w) > 1:
                head, test, toks = _split_cond(s)
                if w[0] == "use_backend" and "%[" not in w[1]:
                    fe["rules"].append({"key": f"r{len(fe['rules']) + 1}", "name": f"{name}_{len(fe['rules']) + 1}", "kind": "use_backend",
                                        "backend": w[1], "test": test or "if", "cond": toks, "line": ""})
                else:
                    fe["rules"].append({"key": f"r{len(fe['rules']) + 1}", "name": f"{name}_{len(fe['rules']) + 1}", "kind": "line",
                                        "backend": "", "test": test, "cond": toks, "line": head})
            elif w[0] in ("http-request", "http-response", "http-after-response", "tcp-request", "tcp-response", "redirect",
                          "block", "use-service"):
                head, test, toks = _split_cond(s)
                fe["rules"].append({"key": f"r{len(fe['rules']) + 1}", "name": f"{name}_{len(fe['rules']) + 1}", "kind": "line",
                                    "backend": "", "test": test, "cond": toks, "line": head})
            else:
                fe["extra"].append(s)
        model["frontends"].append(fe)
    return model


def shlex_split(s: str) -> list[str]:
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def _bind(spec: str) -> dict:
    w = spec.split()
    addr = w[0] if w else ""
    host, _, port = addr.rpartition(":")
    return {"address": host or "0.0.0.0", "port": port or addr, "ssl": "ssl" in w[1:], "options": " ".join(w[1:])}


def parse_map(text: str) -> list[dict]:
    out = []
    for z in text.splitlines():
        s = z.strip()
        if not s or s.startswith("#"):
            continue
        t = s.split()
        if len(t) < 2:
            continue
        be, access, mode = (t[1].split(":") + ["", ""])[:3]
        note = s.split("#", 1)[1].strip() if "#" in s else ""
        out.append({"key": t[0].lower(), "host": t[0].lower(), "backend": be, "access": access or "alle",
                    "tls_mode": mode or "tls", "note": note, "kind": "sni" if mode == "durch" else "http"})
    return out


# ------------------------------------------------------------------ ACL criteria ↔ OPNsense expressions
# expression → (fetch prefix as in haproxy, value field)
OPN_EXPR = {
    "hdr": ("hdr(host) -i", "hdr"), "hdr_beg": ("hdr_beg(host) -i", "hdr_beg"), "hdr_end": ("hdr_end(host) -i", "hdr_end"),
    "hdr_reg": ("hdr_reg(host) -i", "hdr_reg"), "hdr_sub": ("hdr_sub(host) -i", "hdr_sub"),
    "path_beg": ("path_beg -i", "path_beg"), "path_end": ("path_end -i", "path_end"), "path": ("path -i", "path"),
    "path_reg": ("path_reg -i", "path_reg"), "path_dir": ("path_dir -i", "path_dir"), "path_sub": ("path_sub -i", "path_sub"),
    "src": ("src", "src"), "ssl_fc_sni": ("ssl_fc_sni -i", "ssl_fc_sni"), "ssl_sni": ("req.ssl_sni -i", "ssl_sni"),
    "ssl_sni_beg": ("req.ssl_sni -m beg -i", "ssl_sni_beg"), "ssl_sni_end": ("req.ssl_sni -m end -i", "ssl_sni_end"),
    "ssl_sni_sub": ("req.ssl_sni -m sub -i", "ssl_sni_sub"), "ssl_sni_reg": ("req.ssl_sni -m reg -i", "ssl_sni_reg"),
}
_FETCH_TO_EXPR = [
    (r"^(?:req\.)?hdr\(host\)(?:\s+-i)?\s+(?!-)(.+)$", "hdr"), (r"^(?:req\.)?hdr_beg\(host\)(?:\s+-i)?\s+(?!-)(.+)$", "hdr_beg"),
    (r"^(?:req\.)?hdr_end\(host\)(?:\s+-i)?\s+(?!-)(.+)$", "hdr_end"), (r"^(?:req\.)?hdr_reg\(host\)(?:\s+-i)?\s+(?!-)(.+)$", "hdr_reg"),
    (r"^(?:req\.)?hdr_sub\(host\)(?:\s+-i)?\s+(?!-)(.+)$", "hdr_sub"),
    (r"^path_beg(?:\s+-i)?\s+(?!-)(.+)$", "path_beg"), (r"^path_end(?:\s+-i)?\s+(?!-)(.+)$", "path_end"),
    (r"^path_reg(?:\s+-i)?\s+(?!-)(.+)$", "path_reg"), (r"^path_dir(?:\s+-i)?\s+(?!-)(.+)$", "path_dir"),
    (r"^path_sub(?:\s+-i)?\s+(?!-)(.+)$", "path_sub"), (r"^path(?:\s+-i)?\s+(?!-)(.+)$", "path"),
    (r"^src\s+(?!-)(.+)$", "src"), (r"^ssl_fc_sni(?:\s+-i)?\s+(?!-)(.+)$", "ssl_fc_sni"),
    (r"^req(?:\.ssl_sni|_ssl_sni)(?:\s+-i)?\s+(?!-)(.+)$", "ssl_sni"),
    (r"^req(?:\.ssl_sni|_ssl_sni)\s+-m\s+beg(?:\s+-i)?\s+(.+)$", "ssl_sni_beg"),
    (r"^req(?:\.ssl_sni|_ssl_sni)\s+-m\s+end(?:\s+-i)?\s+(.+)$", "ssl_sni_end"),
    (r"^req(?:\.ssl_sni|_ssl_sni)\s+-m\s+sub(?:\s+-i)?\s+(.+)$", "ssl_sni_sub"),
]


def criterion_to_opn(crit: str) -> tuple[str, dict]:
    c = crit.strip()
    for rx, expr in _FETCH_TO_EXPR:
        m = re.match(rx, c)
        if m:
            return expr, {OPN_EXPR[expr][1]: m.group(1).strip()}
    return "custom_acl", {"custom_acl": c}


def opn_to_criterion(acl: dict) -> str | None:
    expr = acl.get("expression") or ""
    if expr == "custom_acl":
        return acl.get("custom_acl") or None
    if expr in OPN_EXPR:
        v = acl.get(OPN_EXPR[expr][1]) or ""
        return f"{OPN_EXPR[expr][0]} {v}".strip() if v else None
    if expr == "src_is_local":
        return "src_is_local"
    if expr == "traffic_is_http":
        return "req.proto_http"
    if expr == "traffic_is_ssl":
        return "req.ssl_ver gt 0"
    if expr == "ssl_fc":
        return "ssl_fc"
    return None


# ------------------------------------------------------------------ read sources
def _sel(v):
    """selected key of an OPNsense get-field ({key:{value,selected}}) or the raw string"""
    if isinstance(v, dict):
        ks = [k for k, x in v.items() if isinstance(x, dict) and str(x.get("selected")) == "1"]
        return ",".join(k for k in ks if k)
    return "" if v is None else str(v)


def read_haproxy_ui(w: dict) -> tuple[dict, list[str]]:
    c = m_haproxy.Client.fuer(w)
    warnings: list[str] = []
    files = c.get("/api/dateien").get("dateien") or []
    text = ""
    for rel in ("haproxy.cfg", "dienste.cfg"):
        if rel in files:
            text += "\n" + (c.anfrage("GET", "/api/datei", params={"pfad": rel}).get("text") or "")
    model = parse_config(text, warnings)
    if "listen/dienste.map" in files:
        model["routes"] = parse_map(c.anfrage("GET", "/api/datei", params={"pfad": "listen/dienste.map"}).get("text") or "")
    if any(f.endswith(".lst") for f in files):
        warnings.append("Listen (listen/*.lst, z. B. interne Netze, Sperrliste) werden nicht mitkopiert – Regeln, die darauf verweisen, brauchen sie im Ziel.")
    return model, warnings


def read_opnsense(w: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    full = ((m_opn.api(w, "GET", "/settings/get") or {}).get("haproxy") or {})

    def objs(container, item):
        return ((full.get(container) or {}).get(item) or {})
    servers, backends = objs("servers", "server"), objs("backends", "backend")
    acls, actions, frontends = objs("acls", "acl"), objs("actions", "action"), objs("frontends", "frontend")
    model = {"backends": [], "frontends": [], "routes": []}
    be_name = {u: b.get("name", u) for u, b in backends.items()}
    for u, b in backends.items():
        srv = []
        for su in [x for x in _sel(b.get("linkedServers")).split(",") if x]:
            s = servers.get(su)
            if not s:
                continue
            mode = _sel(s.get("mode"))
            srv.append({"key": s.get("name", su), "name": s.get("name", su), "address": s.get("address", ""), "port": s.get("port", ""),
                        "ssl": _sel(s.get("ssl")) == "1", "verify": "required" if _sel(s.get("sslVerify")) == "1" else "none",
                        "check": _sel(b.get("healthCheckEnabled")) == "1", "backup": mode == "backup", "weight": s.get("weight", "") or "",
                        "extra": s.get("advanced", "") or ""})
            if mode == "disabled":
                srv[-1]["extra"] = (srv[-1]["extra"] + " disabled").strip()
        extra = [x for x in str(b.get("customOptions") or "").splitlines() if x.strip()]
        algo = _sel(b.get("algorithm"))
        model["backends"].append({"key": b.get("name", u), "name": b.get("name", u), "mode": _sel(b.get("mode")) or "http",
                                  "balance": algo, "servers": srv, "extra": extra, "infra": False, "enabled": _sel(b.get("enabled")) == "1"})
    acl_by_uuid = {u: a for u, a in acls.items()}
    for u, f in frontends.items():
        fe = {"key": f.get("name", u), "name": f.get("name", u), "mode": _sel(f.get("mode")) or "http", "binds": [],
              "default_backend": be_name.get(_sel(f.get("defaultBackend")), ""), "acls": [], "rules": [],
              "extra": [x for x in str(f.get("customOptions") or "").splitlines() if x.strip()], "infra": False,
              "enabled": _sel(f.get("enabled")) == "1"}
        ssl = _sel(f.get("ssl_enabled")) == "1"
        for b in [x for x in str(f.get("bind") or "").split(",") if x.strip()]:
            bb = _bind(b.strip())
            bb["ssl"] = ssl
            bb["options"] = ("ssl crt ../certs/" if ssl else "") + (" " + f.get("bindOptions") if f.get("bindOptions") else "")
            fe["binds"].append(bb)
        if ssl:
            warnings.append(f"Frontend {fe['name']}: TLS-Zertifikate der OPNsense werden nicht kopiert – im Ziel Zertifikat zuweisen bzw. ../certs/ befüllen (Cert Warden).")
        used = {}
        for au in [x for x in _sel(f.get("linkedActions")).split(",") if x]:
            a = actions.get(au)
            if not a:
                continue
            toks = []
            for i, lu in enumerate([x for x in _sel(a.get("linkedAcls")).split(",") if x]):
                acl = acl_by_uuid.get(lu)
                if not acl:
                    continue
                nm = acl.get("name", lu)
                if nm not in used:
                    crit = opn_to_criterion({k: _sel(v) for k, v in acl.items()})
                    if crit is None:
                        warnings.append(f"ACL {nm}: Ausdruck „{_sel(acl.get('expression'))}“ wird nicht übersetzt – als eigene Bedingung prüfen.")
                        crit = f"always_false  # {_sel(acl.get('expression'))}"
                    used[nm] = {"key": nm, "name": nm, "criteria": [crit], "negate": _sel(acl.get("negate")) == "1"}
                    fe["acls"].append(used[nm])
                if i and _sel(a.get("operator")) == "or":
                    toks.append("or")
                toks.append(("!" if used[nm]["negate"] else "") + nm)
            typ = _sel(a.get("type"))
            test = _sel(a.get("testType")) or "if"
            name = a.get("name", au)
            if typ == "use_backend":
                fe["rules"].append({"key": name, "name": name, "kind": "use_backend", "backend": be_name.get(_sel(a.get("use_backend")), ""),
                                    "test": test, "cond": toks, "line": ""})
            else:
                line = _opn_action_line(a, typ)
                if not line:
                    warnings.append(f"Aktion {name} (Typ {typ}) wird nicht übersetzt.")
                    continue
                fe["rules"].append({"key": name, "name": name, "kind": "line", "backend": "", "test": test if toks else "", "cond": toks, "line": line})
        for a in fe["acls"]:      # negation lives in the token now
            a["negate"] = False
        model["frontends"].append(fe)
    # routes: use_backend rules with one plain host/SNI ACL
    for fe in model["frontends"]:
        accl = {a["name"]: a for a in fe["acls"]}
        for r in fe["rules"]:
            toks = [t for t in r["cond"] if t != "or"]
            if r["kind"] != "use_backend" or r["test"] != "if" or "or" in r["cond"] or not 1 <= len(toks) <= 2 or toks[0].startswith("!"):
                continue
            a = accl.get(toks[0])
            if not a or len(a["criteria"]) != 1:
                continue
            access = "alle"
            if len(toks) == 2:              # second condition "source address" = access intern / extern
                b2 = accl.get(toks[1].lstrip("!"))
                if not b2 or len(b2["criteria"]) != 1 or not b2["criteria"][0].startswith("src "):
                    continue
                access = "extern" if toks[1].startswith("!") else "intern"
            m = re.match(r"^(hdr\(host\) -i|ssl_fc_sni -i|req\.ssl_sni -i)\s+(\S+)$", a["criteria"][0])
            if m and HOST_RE.match(m.group(2).lower()):
                sni = "ssl_sni" in m.group(1)
                model["routes"].append({"key": m.group(2).lower(), "host": m.group(2).lower(), "backend": r["backend"], "access": access,
                                        "tls_mode": "durch" if sni else "tls", "note": f"aus Frontend {fe['name']}", "kind": "sni" if sni else "http",
                                        "from_frontend": fe["key"]})
    return model, warnings


def _opn_action_line(a: dict, typ: str) -> str:
    g = {k: _sel(v) for k, v in a.items()}
    if typ == "custom":
        return g.get("custom", "")
    if typ in ("http-request", "http-response", "tcp-request", "tcp-response"):
        pre = typ.replace("-", "_")
        act = g.get(f"{pre}_action", "")
        if not act:
            return ""
        if act == "redirect":
            return f"{typ} redirect {g.get(f'{pre}_redirect', '')}".strip()
        if act == "deny":
            st = g.get(f"{pre}_deny_status", "")
            return f"{typ} deny" + (f" deny_status {st}" if st else "")
        for kind in ("add-header", "set-header"):
            if act == kind:
                k2 = kind.replace("-", "_")
                return f"{typ} {kind} {g.get(f'{pre}_{k2}_name', '')} {g.get(f'{pre}_{k2}_content', '')}".strip()
        if act == "del-header":
            return f"{typ} del-header {g.get(f'{pre}_del_header_name', '')}".strip()
        opt = g.get(f"{pre}_option", "")
        return f"{typ} {act} {opt}".strip()
    return ""


def read_source(srv_access: str, w: dict) -> tuple[dict, list[str]]:
    if srv_access == "haproxy-ui":
        return read_haproxy_ui(w)
    if srv_access == "opnsense":
        return read_opnsense(w)
    raise Fehler("Quelle muss ein HAProxy-Server sein")


# ------------------------------------------------------------------ target names (conflicts)
def target_names(srv_access: str, w: dict) -> dict:
    if srv_access == "haproxy-ui":
        m, _ = read_haproxy_ui(w)
        return {"backends": sorted({b["name"] for b in m["backends"]}), "frontends": sorted({f["name"] for f in m["frontends"]}),
                "routes": sorted({r["host"] for r in m["routes"]}), "servers": [], "acls": [], "rules": []}
    out = {}
    for kind, plural in (("backends", "Backends"), ("frontends", "Frontends"), ("servers", "Servers"), ("acls", "Acls"), ("rules", "Actions")):
        out[kind] = sorted({r.get("name", "") for r in (m_opn.api(w, "GET", f"/settings/search{plural}?rowCount=-1") or {}).get("rows") or []})
    out["routes"] = []
    return out


def target_frontends(srv_access: str, w: dict) -> list[dict]:
    """OPNsense: frontends for the routes (uuid, name, mode) and certificates to choose for TLS frontends."""
    if srv_access != "opnsense":
        return []
    rows = (m_opn.api(w, "GET", "/settings/searchFrontends?rowCount=-1") or {}).get("rows") or []
    return [{"uuid": r["uuid"], "name": r.get("name", ""), "mode": r.get("mode", ""), "bind": r.get("bind", "")} for r in rows]


def target_certificates(srv_access: str, w: dict) -> list[list[str]]:
    if srv_access != "opnsense":
        return []
    tpl = (m_opn.api(w, "GET", "/settings/getFrontend") or {}).get("frontend") or {}
    certs = tpl.get("ssl_certificates") or {}
    return [[k, str(v.get("value", k))] for k, v in certs.items() if k] if isinstance(certs, dict) else []


# ------------------------------------------------------------------ selection + renames
def apply_choices(model: dict, choices: dict) -> tuple[dict, list[str]]:
    """choices: {"backends": {key: {"sel": bool, "name": str, "servers": {key: name}}}, "routes": {key: {"sel","host"?}},
                 "frontends": {key: {"sel", "name", "acls": {key: name}, "rules": {key: name}}}}
    → the chosen part with new names and all references rewritten."""
    warnings = []
    ch_b, ch_f, ch_r = choices.get("backends") or {}, choices.get("frontends") or {}, choices.get("routes") or {}
    be_ren = {}
    for b in model["backends"]:
        c = ch_b.get(b["key"]) or {}
        # not chosen or "use existing" → references keep the original name (= the object in the target)
        be_ren[b["key"]] = (c.get("name") or b["name"]).strip() if c.get("sel") and c.get("action") != "skip" else b["name"]
    out = {"backends": [], "frontends": [], "routes": []}
    for b in model["backends"]:
        c = ch_b.get(b["key"]) or {}
        if not c.get("sel"):
            continue
        nb = dict(b, name=be_ren[b["key"]], skip=c.get("action") == "skip")
        sren = c.get("servers") or {}
        nb["servers"] = [dict(s, name=(sren.get(s["key"]) or s["name"]).strip()) for s in b["servers"]]
        out["backends"].append(nb)
    for r in model["routes"]:
        c = ch_r.get(r["key"]) or {}
        if not c.get("sel"):
            continue
        nr = dict(r, backend=be_ren.get(r["backend"], r["backend"]), skip=c.get("action") == "skip", replace=c.get("action") == "replace")
        if c.get("host"):
            nr["host"] = c["host"].strip().lower()
        out["routes"].append(nr)
    for f in model["frontends"]:
        c = ch_f.get(f["key"]) or {}
        if not c.get("sel"):
            continue
        aren = {a["key"]: (((c.get("acls") or {}).get(a["key"])) or a["name"]).strip() for a in f["acls"]}
        rren = c.get("rules") or {}
        nf = dict(f, name=f["name"] if c.get("action") == "skip" else (c.get("name") or f["name"]).strip(), skip=c.get("action") == "skip",
                  default_backend=be_ren.get(f["default_backend"], f["default_backend"]))
        nf["acls"] = [dict(a, name=aren[a["key"]]) for a in f["acls"]]
        nf["rules"] = []
        for r in f["rules"]:
            nr = dict(r, name=(rren.get(r["key"]) or r["name"]).strip(), cond=[_ren_tok(t, aren) for t in r["cond"]])
            if nr["kind"] == "use_backend":
                nr["backend"] = be_ren.get(r["backend"], r["backend"])
            nf["rules"].append(nr)
        out["frontends"].append(nf)
    names = [b["name"] for b in out["backends"]] + [f["name"] for f in out["frontends"]]
    for n in names:
        if not NAME_RE.match(n):
            raise Fehler(f"Name „{n}“ ungültig – Buchstaben, Zahlen, _ . : - (höchstens 64)")
    for kind, items in (("Backend", [b["name"] for b in out["backends"]]), ("Frontend", [f["name"] for f in out["frontends"]]),
                        ("Adresse", [r["host"] for r in out["routes"]])):
        d = {x for x in items if items.count(x) > 1}
        if d:
            raise Fehler(f"{kind} doppelt: {', '.join(sorted(d))}")
    chosen = {b["name"] for b in out["backends"]}
    for f in out["frontends"]:
        for ref in [f["default_backend"]] + [r["backend"] for r in f["rules"] if r["kind"] == "use_backend"]:
            if ref and ref not in chosen:
                warnings.append(f"Frontend {f['name']} verweist auf Backend {ref}, das nicht mit importiert wird – muss im Ziel schon so heißen.")
    for r in out["routes"]:
        if r["backend"] not in chosen:
            warnings.append(f"Adresse {r['host']} verweist auf Backend {r['backend']}, das nicht mit importiert wird – muss im Ziel schon so heißen.")
    return out, warnings


def _ren_tok(t: str, ren: dict) -> str:
    neg = "!" if t.startswith("!") else ""
    core = t[1:] if neg else t
    return neg + ren.get(core, core) if not core.startswith("{") else t


# ------------------------------------------------------------------ write: haproxy-ui (config text + dienste.map)
def backend_text(b: dict) -> str:
    z = [f"backend {b['name']}", f"    mode {b['mode'] or 'http'}"]
    if b.get("balance"):
        z.append(f"    balance {b['balance']}")
    z += [f"    {x}" for x in b.get("extra") or []]
    for s in b["servers"]:
        addr = f"{s['address']}:{s['port']}" if s.get("port") else s["address"]
        opts = []
        if s.get("check"):
            opts.append("check")
        if s.get("ssl"):
            opts += ["ssl", "verify", s.get("verify") or "none"]
        if s.get("backup"):
            opts.append("backup")
        if s.get("weight"):
            opts += ["weight", str(s["weight"])]
        if s.get("extra"):
            opts.append(s["extra"])
        z.append(f"    server {s['name']} {addr} {' '.join(opts)}".rstrip())
    return "\n".join(z)


def frontend_text(f: dict) -> str:
    z = [f"frontend {f['name']}"]
    for b in f["binds"]:
        z.append(f"    bind {b['address'] if b['address'] not in ('0.0.0.0', '*') else ''}:{b['port']} {b.get('options') or ''}".rstrip())
    z.append(f"    mode {f['mode'] or 'http'}")
    z += [f"    {x}" for x in f.get("extra") or []]
    for a in f["acls"]:
        for c in a["criteria"]:
            z.append(f"    acl {a['name']} {c}")
    for r in f["rules"]:
        if r["kind"] == "use_backend":
            z.append(f"    use_backend {r['backend']}{_join_cond(r['test'], r['cond'])}")
        else:
            z.append(f"    {r['line']}{_join_cond(r['test'], r['cond'])}")
    if f.get("default_backend"):
        z.append(f"    default_backend {f['default_backend']}")
    return "\n".join(z)


def plan_haproxy_ui(chosen: dict, tnames: dict) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    blocks = [b for b in chosen["backends"] if not b.get("skip")]
    fes = [f for f in chosen["frontends"] if not f.get("skip")]
    for b in blocks:
        if b["name"] in tnames["backends"]:
            raise Fehler(f"Backend {b['name']} gibt es im Ziel schon – umbenennen oder „vorhandenes benutzen“")
    for f in fes:
        if f["name"] in tnames["frontends"]:
            raise Fehler(f"Frontend {f['name']} gibt es im Ziel schon – umbenennen oder „vorhandenes benutzen“")
    text = ""
    if blocks or fes:
        text = f"\n# >>> MIRA-Import {stamp}\n" + "\n\n".join([backend_text(b) for b in blocks] + [frontend_text(f) for f in fes]) + \
               f"\n# <<< MIRA-Import {stamp}\n"
    routes = []
    for r in chosen["routes"]:
        if r.get("skip"):
            continue
        if not HOST_RE.match(r["host"]):
            raise Fehler(f"Adresse „{r['host']}“ ungültig (klein, nur a–z 0–9 . - *)")
        if r["host"] in tnames["routes"] and not r.get("replace"):
            raise Fehler(f"Adresse {r['host']} gibt es im Ziel schon – „ersetzen“ oder „überspringen“")
        routes.append({"name": r["host"], "backend": r["backend"], "zugang": r.get("access") or "alle",
                       "modus": r.get("tls_mode") or "tls", "notiz": (r.get("note") or "MIRA-Import")[:120]})
    for r in routes:
        be = next((b for b in chosen["backends"] if b["name"] == r["backend"]), None)
        if be and (be["mode"] == "tcp") != (r["modus"] == "durch"):
            r["warn"] = f"{r['name']}: Modus {r['modus']} passt nicht zu Backend {be['name']} (mode {be['mode']})"
    return {"text": text, "routes": routes}


def apply_haproxy_ui(w: dict, plan: dict) -> list[dict]:
    c = m_haproxy.Client.fuer(w)
    log = []
    if plan["text"]:
        cur = c.anfrage("GET", "/api/datei", params={"pfad": "dienste.cfg"}).get("text") or ""
        c.post("/api/datei", {"pfad": "dienste.cfg", "text": cur.rstrip("\n") + "\n" + plan["text"]})
        log.append({"ok": True, "text": "dienste.cfg: Backends/Frontends als Block „MIRA-Import“ angehängt (Entwurf)"})
    if plan["routes"]:
        d = c.get("/api/dienste")
        rows = [x for x in d.get("dienste") or [] if x["name"] not in {r["name"] for r in plan["routes"]}]
        rows += [{k: r[k] for k in ("name", "backend", "zugang", "modus", "notiz")} for r in plan["routes"]]
        res = c.post("/api/dienste", {"dienste": rows})
        log.append({"ok": True, "text": f"dienste.map: {len(plan['routes'])} Adresse(n) eingetragen (Entwurf)",
                    "details": "\n".join(res.get("hinweise") or [])})
    return log


# ------------------------------------------------------------------ write: OPNsense (API objects)
def _opn_check(d, what):
    return m_opn._check_result(d, what)


def plan_opnsense(chosen: dict, tnames: dict, opts: dict) -> dict:
    """List of operations (also the preview); executed by apply_opnsense in this order."""
    ops = []
    for b in chosen["backends"]:
        if b.get("skip"):
            continue
        if b["name"] in tnames["backends"]:
            raise Fehler(f"Backend {b['name']} gibt es in der OPNsense schon – umbenennen oder „vorhandenes benutzen“")
        for s in b["servers"]:
            if s["name"] in tnames["servers"]:
                raise Fehler(f"Server {s['name']} (Backend {b['name']}) gibt es in der OPNsense schon – umbenennen")
            ops.append({"op": "server", "name": s["name"], "fields": {
                "enabled": "1", "name": s["name"], "description": f"MIRA-Import ({b['name']})", "address": s["address"], "port": str(s.get("port") or ""),
                "mode": "backup" if s.get("backup") else ("disabled" if "disabled" in (s.get("extra") or "") else "active"),
                "ssl": "1" if s.get("ssl") else "0", "sslVerify": "0" if s.get("verify") == "none" else "1",
                "weight": str(s.get("weight") or ""), "advanced": " ".join(x for x in str(s.get("extra") or "").split() if x != "disabled")}})
        algo = b.get("balance") if b.get("balance") in ("source", "roundrobin", "static-rr", "leastconn", "uri", "random") else "roundrobin"
        ops.append({"op": "backend", "name": b["name"], "servers": [s["name"] for s in b["servers"]], "fields": {
            "enabled": "1", "name": b["name"], "description": "MIRA-Import", "mode": "tcp" if b["mode"] == "tcp" else "http",
            "algorithm": algo, "healthCheckEnabled": "1" if any(s.get("check") for s in b["servers"]) else "0",
            "customOptions": "\n".join(b.get("extra") or [])}})
    used_acl = set(tnames["acls"])
    for f in chosen["frontends"]:
        if f.get("skip"):
            continue
        if f["name"] in tnames["frontends"]:
            raise Fehler(f"Frontend {f['name']} gibt es in der OPNsense schon – umbenennen oder „vorhandenes benutzen“")
        aclmap = {a["name"]: a for a in f["acls"]}
        acl_ops: dict[str, str] = {}

        def need_acl(tok, f=f, aclmap=aclmap, acl_ops=acl_ops):
            neg = tok.startswith("!")
            core = tok[1:] if neg else tok
            if core.startswith("{"):
                crit = core.strip("{} ").strip()
                nm = f"{f['name']}_anon{len(acl_ops) + 1}"
                return _add_acl(ops, nm, [crit], neg, used_acl, acl_ops, f)
            a = aclmap.get(core)
            if not a:
                raise Fehler(f"Frontend {f['name']}: Bedingung „{core}“ ist nicht definiert")
            nm = a["name"] + ("_nicht" if neg else "")
            return _add_acl(ops, nm, a["criteria"], neg, used_acl, acl_ops, f)
        actions = []
        for r in f["rules"]:
            toks = r["cond"]
            only_and = "or" not in toks
            only_or = bool(toks) and all(t == "or" for t in toks[1::2]) and "or" not in toks[0::2]
            simple = r["kind"] == "use_backend" and (only_and or only_or)
            if r["name"] in tnames["rules"]:
                raise Fehler(f"Regel {r['name']} gibt es in der OPNsense schon – umbenennen")
            if r["kind"] == "use_backend" and simple:
                linked = [need_acl(t) for t in toks if t != "or"]
                ops.append({"op": "action", "name": r["name"], "acls": linked, "backend": r["backend"], "fields": {
                    "enabled": "1", "name": r["name"], "description": f"MIRA-Import ({f['name']})", "testType": r["test"] or "if",
                    "operator": "or" if "or" in toks else "and", "type": "use_backend"}})
            else:
                line = (f"use_backend {r['backend']}" if r["kind"] == "use_backend" else r["line"]) + _join_cond(r["test"], _inline(toks, aclmap))
                ops.append({"op": "action", "name": r["name"], "acls": [], "backend": "", "fields": {
                    "enabled": "1", "name": r["name"], "description": f"MIRA-Import ({f['name']})", "testType": "if",
                    "operator": "and", "type": "custom", "custom": line}})
            actions.append(r["name"])
        bind = ",".join(f"{b['address'] or '0.0.0.0'}:{b['port']}" for b in f["binds"])
        ssl = any(b.get("ssl") for b in f["binds"])
        bopts = " ".join(sorted({x for b in f["binds"] for x in _bind_opts(b.get("options") or "")}))
        fields = {"enabled": "1", "name": f["name"], "description": "MIRA-Import", "bind": bind, "bindOptions": bopts,
                  "mode": "tcp" if f["mode"] == "tcp" else "http", "ssl_enabled": "1" if ssl else "0",
                  "customOptions": "\n".join(f.get("extra") or [])}
        if ssl and opts.get("certificate"):
            fields["ssl_certificates"] = opts["certificate"]
            fields["ssl_default_certificate"] = opts["certificate"]
        ops.append({"op": "frontend", "name": f["name"], "actions": actions, "default_backend": f.get("default_backend") or "", "fields": fields})
    # routes into one frontend of the target (existing or new)
    routes = [r for r in chosen["routes"] if not r.get("skip")]
    if routes:
        tf = opts.get("route_frontend") or ""
        if not tf:
            raise Fehler("Für die Adressen ein Frontend der OPNsense wählen (oder „neues Frontend“)")
        internal = (opts.get("internal_networks") or "").strip()
        if tf == "new":
            nm = (opts.get("new_frontend_name") or "fe_import").strip()
            if nm in tnames["frontends"]:
                raise Fehler(f"Frontend {nm} gibt es schon")
            ops.append({"op": "frontend", "name": nm, "actions": [], "default_backend": "", "fields": {
                "enabled": "1", "name": nm, "description": "MIRA-Import (Adressen)", "bind": opts.get("new_frontend_bind") or "0.0.0.0:443",
                "mode": "http" if all(r["kind"] == "http" for r in routes) else "tcp",
                "ssl_enabled": "1" if all(r["kind"] == "http" for r in routes) and opts.get("certificate") else "0",
                **({"ssl_certificates": opts["certificate"], "ssl_default_certificate": opts["certificate"]} if opts.get("certificate") else {})}})
            tf = "name:" + nm
        acl_ops: dict[str, str] = {}
        if internal and any(r.get("access") in ("intern", "extern") for r in routes):
            for nm2, neg in (("mira_intern", False), ("mira_nicht_intern", True)):
                if nm2 not in used_acl:
                    ops.append({"op": "acl", "name": nm2, "fields": {"name": nm2, "description": "MIRA-Import: interne Netze", "expression": "src",
                                                                    "src": internal, "negate": "1" if neg else "0"}})
                    used_acl.add(nm2)
        elif any(r.get("access") in ("intern", "extern") for r in routes):
            ops.append({"op": "note", "text": "Zugang intern/extern wird ohne „interne Netze“ nicht übernommen – Adressen sind dann von überall erreichbar."})
        rule_names = []
        for r in routes:
            expr = "ssl_sni" if r["kind"] == "sni" else "hdr"
            an = _uniq("host_" + re.sub(r"[^a-z0-9]+", "_", r["host"]).strip("_"), used_acl)
            ops.append({"op": "acl", "name": an, "fields": {"name": an, "description": f"MIRA-Import: {r['host']}", "expression": expr,
                                                           OPN_EXPR[expr][1]: r["host"], "negate": "0"}})
            acls = [an]
            if internal and r.get("access") == "intern":
                acls.append("mira_intern")
            elif internal and r.get("access") == "extern":
                acls.append("mira_nicht_intern")
            rn = _uniq("route_" + re.sub(r"[^a-z0-9]+", "_", r["host"]).strip("_"), set(tnames["rules"]) | set(rule_names))
            ops.append({"op": "action", "name": rn, "acls": acls, "backend": r["backend"], "fields": {
                "enabled": "1", "name": rn, "description": f"MIRA-Import: {r['host']}", "testType": "if", "operator": "and", "type": "use_backend"}})
            rule_names.append(rn)
        ops.append({"op": "link", "frontend": tf, "label": opts.get("route_frontend_label") or tf.replace("name:", ""), "actions": rule_names})
    return {"ops": ops}


def _uniq(base: str, used: set) -> str:
    n, i = base[:60], 2
    while n in used:
        n, i = f"{base[:56]}_{i}", i + 1
    used.add(n)
    return n


def _bind_opts(options: str) -> list[str]:
    """bind options OPNsense handles itself (ssl, certificates) are dropped"""
    w, out, skip = options.split(), [], 0
    for x in w:
        if skip:
            skip -= 1
            continue
        if x in ("ssl",):
            continue
        if x in ("crt", "crt-list", "ca-file"):
            skip = 1
            continue
        out.append(x)
    return [" ".join(out)] if out else []


def _split_crit(c: str) -> tuple[str, list[str]]:
    """'path_beg -i /a /b' → ('path_beg -i', ['/a', '/b'])  (fetch + flags, values)"""
    w = c.split()
    head, i = [w[0]] if w else [], 1
    while i < len(w) and w[i].startswith("-"):
        head.append(w[i])
        if w[i] in ("-m", "-f", "-M") and i + 1 < len(w):
            head.append(w[i + 1])
            i += 1
        i += 1
    return " ".join(head), w[i:]


def merge_criteria(criteria: list[str]) -> str | None:
    """Several 'acl x …' lines (OR) → one criterion with several values, if they use the same fetch."""
    if len(criteria) == 1:
        return criteria[0]
    parts = [_split_crit(c) for c in criteria]
    if len({h for h, _ in parts}) != 1 or any(not v for _, v in parts) or "-f" in parts[0][0].split():
        return None
    return parts[0][0] + " " + " ".join(x for _, v in parts for x in v)


def _add_acl(ops, nm, criteria, neg, used_acl, acl_ops, f):
    key = (nm, tuple(criteria), neg)
    if key in acl_ops:
        return acl_ops[key]
    if nm in used_acl and nm not in acl_ops.values():
        raise Fehler(f"Bedingung (ACL) {nm} gibt es in der OPNsense schon – im Frontend {f['name']} umbenennen")
    crit = merge_criteria(criteria)
    if crit is None:
        raise Fehler(f"Frontend {f['name']}: Bedingung {nm} besteht aus mehreren Zeilen mit verschiedenen Prüfungen "
                     f"({' | '.join(criteria)}) – das kann die OPNsense nicht als eine ACL abbilden. Frontend abwählen und von Hand übertragen.")
    expr, extra = criterion_to_opn(crit)
    ops.append({"op": "acl", "name": nm, "fields": {"name": nm, "description": f"MIRA-Import ({f['name']})", "expression": expr,
                                                   "negate": "1" if neg else "0", **extra}})
    used_acl.add(nm)
    acl_ops[key] = nm
    return nm


def _inline(toks, aclmap):
    out = []
    for t in toks:
        if t == "or":
            out.append("or")
            continue
        neg = "!" if t.startswith("!") else ""
        core = t[1:] if neg else t
        if core.startswith("{"):
            out.append(t)
            continue
        a = aclmap.get(core)
        if not a:
            out.append(t)
        else:
            crit = merge_criteria(a["criteria"])
            if crit is None:
                raise Fehler(f"Bedingung {core} besteht aus mehreren Zeilen mit verschiedenen Prüfungen – "
                             "das Frontend bitte von Hand übertragen (abwählen).")
            out.append(f"{neg}{{ {crit} }}")
    return out


def apply_opnsense(w: dict, plan: dict) -> list[dict]:
    log = []
    uuids: dict[tuple[str, str], str] = {}

    def lookup(kind, name):
        if (kind, name) in uuids:
            return uuids[(kind, name)]
        plural = {"server": "Servers", "backend": "Backends", "acl": "Acls", "action": "Actions", "frontend": "Frontends"}[kind]
        for r in (m_opn.api(w, "GET", f"/settings/search{plural}?rowCount=-1") or {}).get("rows") or []:
            uuids.setdefault((kind, r.get("name", "")), r["uuid"])
        if (kind, name) not in uuids:
            raise Fehler(f"{kind} {name} gibt es in der OPNsense nicht")
        return uuids[(kind, name)]

    def add(kind, fields):
        r = _opn_check(m_opn.api(w, "POST", f"/settings/add{kind.capitalize()}", {kind: fields}), f"{kind} {fields.get('name')}")
        uuids[(kind, fields["name"])] = r.get("uuid", "")
        return r.get("uuid", "")

    for op in plan["ops"]:
        try:
            if op["op"] == "note":
                log.append({"ok": True, "text": op["text"], "warn": True})
                continue
            if op["op"] in ("server", "acl"):
                add(op["op"], op["fields"])
            elif op["op"] == "backend":
                add("backend", {**op["fields"], "linkedServers": ",".join(lookup("server", s) for s in op["servers"])})
            elif op["op"] == "action":
                f = dict(op["fields"], linkedAcls=",".join(lookup("acl", a) for a in op["acls"]))
                if op["backend"]:
                    f["use_backend"] = lookup("backend", op["backend"])
                add("action", f)
            elif op["op"] == "frontend":
                f = dict(op["fields"], linkedActions=",".join(lookup("action", a) for a in op["actions"]))
                if op["default_backend"]:
                    f["defaultBackend"] = lookup("backend", op["default_backend"])
                add("frontend", f)
            elif op["op"] == "link":
                fu = lookup("frontend", op["frontend"][5:]) if op["frontend"].startswith("name:") else op["frontend"]
                cur = (m_opn.api(w, "GET", f"/settings/getFrontend/{fu}") or {}).get("frontend") or {}
                have = [x for x in _sel(cur.get("linkedActions")).split(",") if x]
                new = have + [lookup("action", a) for a in op["actions"]]
                _opn_check(m_opn.api(w, "POST", f"/settings/setFrontend/{fu}", {"frontend": {"linkedActions": ",".join(new)}}), "Frontend verknüpfen")
                op_name = cur.get("name") or fu
                log.append({"ok": True, "text": f"Frontend {op_name}: {len(op['actions'])} Regel(n) angehängt"})
                continue
            log.append({"ok": True, "text": f"{op['op']} {op['name']} angelegt"})
        except Fehler as e:
            log.append({"ok": False, "text": f"{op['op']} {op.get('name', '')}: {e}"})
            log.append({"ok": False, "text": "Abgebrochen – bereits angelegte Objekte bleiben in der OPNsense gespeichert (nicht übernommen) und lassen sich dort oder in MIRA löschen."})
            break
    return log


def preview_text(target_access: str, plan: dict) -> str:
    if target_access == "haproxy-ui":
        rows = "\n".join(f"{r['name']:<32} {r['backend']}:{r['zugang']}:{r['modus']}   # {r['notiz']}" for r in plan["routes"])
        return ((plan["text"].strip() or "(keine Backends/Frontends)") +
                ("\n\n# listen/dienste.map – neue/ersetzte Zeilen:\n" + rows if rows else ""))
    lines = []
    for op in plan["ops"]:
        if op["op"] == "note":
            lines.append(f"! {op['text']}")
        elif op["op"] == "link":
            lines.append(f"→ Frontend {op.get('label') or op['frontend'].replace('name:', '')}: Regeln anhängen {', '.join(op['actions'])}")
        else:
            f = op["fields"]
            detail = {"server": lambda: f"{f['address']}:{f['port']} {'ssl' if f['ssl'] == '1' else ''}",
                      "backend": lambda: f"mode {f['mode']}, {f['algorithm']}, Server: {', '.join(op['servers']) or '–'}",
                      "acl": lambda: f"{f['expression']} {next((v for k, v in f.items() if k not in ('name', 'description', 'expression', 'negate')), '')}"
                                     + (" (negiert)" if f.get("negate") == "1" else ""),
                      "action": lambda: (f"use_backend {op['backend']} {f['testType']} {(' ' + f['operator'] + ' ').join(op['acls'])}"
                                         if f["type"] == "use_backend" else f"eigene Regel: {f.get('custom', '')}"),
                      "frontend": lambda: f"bind {f['bind']}, mode {f['mode']}" + (", TLS" if f["ssl_enabled"] == "1" else "")
                                          + (f", Standard {op['default_backend']}" if op["default_backend"] else "")
                                          + (f", Regeln: {', '.join(op['actions'])}" if op["actions"] else "")}[op["op"]]()
            lines.append(f"+ {op['op']:<8} {op['name']:<28} {detail}")
    return "\n".join(lines) or "(nichts zu tun)"
