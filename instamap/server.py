"""Local web app: http://127.0.0.1:8765"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import exporters
from .config import Settings, settings as default_settings
from .db import Database
from .instagram import InstagramError, NotLoggedIn, browser_login, load_session
from .worker import Worker

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class CollectionIn(BaseModel):
    id: str
    name: str


class PlaceUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    notes: str | None = None
    category: str | None = None


class RegeocodeIn(BaseModel):
    query: str


def create_app(settings: Settings | None = None, start_worker: bool = True) -> FastAPI:
    settings = settings or default_settings
    settings.ensure_dirs()
    db = Database(settings.db_path)
    worker = Worker(db, settings)
    login_state = {"state": "idle", "error": None}

    @asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="InstaMap", lifespan=lifespan)
    app.state.db, app.state.worker = db, worker
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    # ---- login -----------------------------------------------------------
    @app.get("/api/status")
    def status():
        session = load_session(settings.session_file)
        return worker.status() | {
            "logged_in": bool(session and session.get("sessionid")),
            "login": login_state,
        }

    @app.post("/api/login")
    def login():
        if login_state["state"] == "waiting":
            return login_state

        def run():
            try:
                browser_login(settings.browser_profile, settings.session_file)
                login_state.update(state="done", error=None)
                worker.nudge()
            except Exception as exc:
                log.exception("Login failed")
                login_state.update(state="failed", error=str(exc))

        login_state.update(state="waiting", error=None)
        threading.Thread(target=run, daemon=True).start()
        return login_state

    @app.post("/api/logout")
    def logout():
        settings.session_file.unlink(missing_ok=True)
        return {"ok": True}

    # ---- collections & syncing -------------------------------------------
    @app.get("/api/collections")
    def collections():
        try:
            return worker.client().collections()
        except NotLoggedIn as exc:
            raise HTTPException(401, str(exc))
        except InstagramError as exc:
            raise HTTPException(502, str(exc))

    @app.post("/api/collection")
    def choose_collection(c: CollectionIn):
        db.set("collection", c.model_dump())
        db.set("last_sync", 0)
        worker.request_sync()
        return worker.status()

    @app.post("/api/sync")
    def sync():
        worker.request_sync()
        return worker.status()

    @app.post("/api/retry")
    def retry():
        n = db.retry_failed()
        worker.nudge()
        return {"requeued": n}

    @app.get("/api/posts")
    def posts(status: str | None = None):
        sql = "SELECT id, shortcode, username, status, error, caption, transcript, ocr_text FROM posts"
        return db.query(sql + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT 500",
                        (status,) if status else ())

    # ---- places ----------------------------------------------------------
    def _places():
        places = db.list_places()
        for p in places:
            p["google_maps_url"] = exporters.google_maps_url(p)
        return places

    @app.get("/api/places")
    def places():
        return _places()

    @app.patch("/api/places/{place_id}")
    def update_place(place_id: int, body: PlaceUpdate):
        db.update_place(place_id, body.model_dump(exclude_none=True))
        return {"ok": True}

    @app.delete("/api/places/{place_id}")
    def delete_place(place_id: int):
        db.delete_place(place_id)
        return {"ok": True}

    @app.post("/api/places/{place_id}/regeocode")
    def regeocode(place_id: int, body: RegeocodeIn):
        geo = worker.geocoder.lookup(body.query)
        if not geo:
            raise HTTPException(404, "OpenStreetMap couldn't find that. Try adding the town or country.")
        db.update_place(place_id, {"lat": geo["lat"], "lon": geo["lon"], "address": geo.get("address"),
                                   "city": geo.get("city"), "country": geo.get("country"), "query": body.query})
        return geo

    # ---- export ----------------------------------------------------------
    @app.get("/api/export.{fmt}")
    def export(fmt: str, status: str = "approved"):
        places = [p for p in _places() if status == "all" or p["status"] in status.split(",")]
        if fmt == "kml":
            body, mime = exporters.to_kml(places), "application/vnd.google-earth.kml+xml"
        elif fmt == "csv":
            body, mime = exporters.to_csv(places), "text/csv"
        elif fmt == "geojson":
            body, mime = exporters.to_geojson(places), "application/geo+json"
        else:
            raise HTTPException(404, "Unknown format")
        return Response(body, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="instamap-places.{fmt}"'})

    return app
