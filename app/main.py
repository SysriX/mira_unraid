"""MIRA – Mail, Identity, Routing & Access. Startpunkt (FastAPI + uvicorn)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import kern
import module

module.laden()
import anmeldung  # noqa: E402
import einrichtung  # noqa: E402
import verwaltung  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


@asynccontextmanager
async def lebenszyklus(app):
    try:
        kern.fernet()
        if kern.db_starten():
            kern.log("Datenbank:", (kern.konfig_lesen().get("db") or {}).get("typ"))
    except Exception as e:  # noqa: BLE001
        kern.log("Datenbank nicht erreichbar:", e)
    einrichtung.code_melden()
    kern.log(f"MIRA {kern.VERSION} bereit – Module: {', '.join(module.REGISTER)}")
    yield


app = FastAPI(title="MIRA", version=kern.VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lebenszyklus)


@app.middleware("http")
async def schutz(req: Request, call_next):
    # CSRF: schreibende API-Aufrufe nur mit eigenem Header (setzt nur unsere Oberfläche per fetch)
    if req.url.path.startswith("/api/") and req.method in ("POST", "PUT", "PATCH", "DELETE") \
            and req.headers.get("x-mira") != "1":
        return JSONResponse({"detail": "Header X-MIRA fehlt"}, status_code=403)
    resp = await call_next(req)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
    if req.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    elif req.url.path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "no-cache")   # immer kurz nachfragen (ETag) – nach Updates sofort neu
    return resp


@app.exception_handler(module.Fehler)
async def _fehler(req, e):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(PermissionError)
async def _verboten(req, e):
    return JSONResponse({"detail": str(e)}, status_code=403)


@app.exception_handler(LookupError)
async def _fehlt(req, e):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(RuntimeError)
async def _laufzeit(req, e):
    kern.log("Fehler:", repr(e))
    return JSONResponse({"detail": str(e)}, status_code=500)


app.include_router(anmeldung.router)
app.include_router(einrichtung.router)
app.include_router(verwaltung.router)
for m in module.REGISTER.values():
    if m.router:
        app.include_router(m.router)


@app.get("/gesund")
def gesund():
    return {"ok": True, "eingerichtet": einrichtung.eingerichtet(), "version": kern.VERSION}


@app.get("/api/{rest:path}")
def api_unbekannt(rest: str):
    raise HTTPException(404, "unbekannt")


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def start():
    return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    tls = {}
    if os.environ.get("MIRA_TLS_CERT") and os.environ.get("MIRA_TLS_KEY"):
        tls = {"ssl_certfile": os.environ["MIRA_TLS_CERT"], "ssl_keyfile": os.environ["MIRA_TLS_KEY"]}
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("MIRA_PORT", "8080")), proxy_headers=True,
                forwarded_allow_ips=os.environ.get("MIRA_PROXY_IPS", "127.0.0.1"), log_level="warning",
                server_header=False, **tls)
