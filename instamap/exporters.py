"""Getting places out to Google Maps.

Google has no public API for adding to your personal "Saved" lists, so we give you
two free routes:
  * a per-place Google Maps link: open it, tap Save, pick a list;
  * a KML/CSV file you import into Google My Maps (mymaps.google.com). That map then
    shows up in the Google Maps app under You > Saved > Maps.
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote_plus
from xml.sax.saxutils import escape


def google_maps_url(place: dict) -> str:
    # Searching by name + town lands on the actual business/landmark card (which has the
    # Save button). Coordinates alone would just drop a pin.
    bits = [place["name"]]
    if place.get("city") and place["city"].lower() not in place["name"].lower():
        bits.append(place["city"])
    elif place.get("country"):
        bits.append(place["country"])
    url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(', '.join(bits))}"
    return url


def instagram_url(shortcode: str | None) -> str | None:
    return f"https://www.instagram.com/p/{shortcode}/" if shortcode else None


def _description(place: dict) -> str:
    lines = []
    if place.get("category"):
        lines.append(place["category"].title())
    if place.get("address"):
        lines.append(place["address"])
    for s in place.get("sources", [])[:5]:
        link = instagram_url(s.get("shortcode"))
        who = f"@{s['username']}" if s.get("username") else "post"
        lines.append(f"From {who}: {link}" if link else f"From {who}")
    if place.get("notes"):
        lines.append(place["notes"])
    lines.append(google_maps_url(place))
    return "\n".join(lines)


def to_kml(places: list[dict], title: str = "InstaMap places") -> str:
    by_cat: dict[str, list[dict]] = {}
    for p in places:
        if p.get("lat") is not None:
            by_cat.setdefault((p.get("category") or "other").title(), []).append(p)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           f"<name>{escape(title)}</name>"]
    # My Maps turns each Folder into a layer, so categories become toggleable layers.
    for cat, items in sorted(by_cat.items()):
        out.append(f"<Folder><name>{escape(cat)}</name>")
        for p in items:
            out.append(
                "<Placemark>"
                f"<name>{escape(p['name'])}</name>"
                f"<description>{escape(_description(p))}</description>"
                f"<Point><coordinates>{p['lon']},{p['lat']},0</coordinates></Point>"
                "</Placemark>"
            )
        out.append("</Folder>")
    out.append("</Document></kml>")
    return "\n".join(out)


def to_csv(places: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Latitude", "Longitude", "Category", "City", "Country", "Address",
                "Google Maps", "Instagram", "Notes"])
    for p in places:
        if p.get("lat") is None:
            continue
        posts = " ".join(filter(None, (instagram_url(s.get("shortcode")) for s in p.get("sources", []))))
        w.writerow([p["name"], p["lat"], p["lon"], p.get("category") or "", p.get("city") or "",
                    p.get("country") or "", p.get("address") or "", google_maps_url(p), posts,
                    p.get("notes") or ""])
    return buf.getvalue()


def to_geojson(places: list[dict]) -> str:
    features = []
    for p in places:
        if p.get("lat") is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": {k: p.get(k) for k in ("name", "category", "city", "country", "address", "status")}
            | {"google_maps": google_maps_url(p),
               "instagram": [instagram_url(s.get("shortcode")) for s in p.get("sources", [])]},
        })
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=1)
