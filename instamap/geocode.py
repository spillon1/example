"""Free geocoding with OpenStreetMap (Nominatim, with Photon as a backup). Results are cached."""

from __future__ import annotations

import logging
import math
import threading
import time

import requests

from .db import Database

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api/"


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


class Geocoder:
    def __init__(self, db: Database, user_agent: str, session: requests.Session | None = None):
        self.db = db
        self.http = session or requests.Session()
        self.http.headers["User-Agent"] = user_agent
        self._lock = threading.Lock()
        self._last = 0.0

    def _throttle(self) -> None:
        # Nominatim's usage policy: at most one request per second.
        with self._lock:
            wait = 1.1 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def _nominatim(self, query: str) -> dict | None:
        self._throttle()
        resp = self.http.get(NOMINATIM, timeout=20, params={
            "q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en",
        })
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        r = rows[0]
        addr = r.get("address", {})
        return {
            "lat": float(r["lat"]), "lon": float(r["lon"]), "address": r.get("display_name"),
            "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county"),
            "country": addr.get("country"), "type": r.get("type"), "osm": f"{r.get('osm_type')}/{r.get('osm_id')}",
        }

    def _photon(self, query: str) -> dict | None:
        self._throttle()
        resp = self.http.get(PHOTON, timeout=20, params={"q": query, "limit": 1, "lang": "en"})
        resp.raise_for_status()
        feats = resp.json().get("features") or []
        if not feats:
            return None
        f = feats[0]
        lon, lat = f["geometry"]["coordinates"]
        p = f.get("properties", {})
        parts = [p.get(k) for k in ("name", "street", "city", "state", "country") if p.get(k)]
        return {"lat": lat, "lon": lon, "address": ", ".join(parts), "city": p.get("city"),
                "country": p.get("country"), "type": p.get("osm_value")}

    def lookup(self, query: str) -> dict | None:
        query = " ".join(query.split())
        hit, cached = self.db.cached_geocode(query)
        if hit:
            return cached
        result = None
        for backend in (self._nominatim, self._photon):
            try:
                result = backend(query)
            except Exception as exc:
                log.warning("Geocoder %s failed for %r: %s", backend.__name__, query, exc)
                continue
            if result:
                break
        self.db.cache_geocode(query, result)
        return result

    def locate(self, name: str, area: str | None) -> tuple[dict | None, str | None, float]:
        """Find coordinates for a place name. Returns (result, query used, confidence multiplier).

        With an area hint we search "name, area" first. A bare-name match far from that
        area is probably a different place with the same name, so we reject it.
        """
        area_geo = self.lookup(area) if area else None
        queries = [f"{name}, {area}"] if area else []
        queries.append(name)
        for q in queries:
            result = self.lookup(q)
            if not result:
                continue
            if q == name and area_geo:
                if distance_km((result["lat"], result["lon"]), (area_geo["lat"], area_geo["lon"])) > 250:
                    continue
                return result, q, 0.9
            return result, q, 1.0 if area else 0.7
        return None, queries[0], 0.0
