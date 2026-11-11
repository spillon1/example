import json

from instamap import exporters, pipeline


def test_process_post_records_places(db, settings, geocoder, monkeypatch):
    db.upsert_post({
        "media_id": "1", "shortcode": "C0abc", "username": "wanderer",
        "caption": "📍 Pastéis de Belém\n📍 Somewhere Made Up",
        "ig_location": {"name": "Miradouro da Senhora do Monte", "lat": 38.7189, "lon": -9.1327, "city": "Lisbon"},
        "media": [{"kind": "video", "url": "https://cdn/x.mp4"}],
    })
    post = db.next_pending_post()
    # Pretend the video said one more place out loud.
    monkeypatch.setattr(pipeline, "analyse_media", lambda *a, **k: ("", "📍 Lisbon\nRandom Title Words"))
    n = pipeline.process_post(post, db=db, geocoder=geocoder, downloader=None, settings=settings)
    assert n == 4

    places = {p["name"]: p for p in db.list_places()}
    assert places["Miradouro da Senhora do Monte"]["sources"][0]["method"] == "ig_location"
    assert places["Pastéis de Belém"]["lat"] == 38.6975
    assert places["Somewhere Made Up"]["lat"] is None          # kept so you can fix it by hand
    assert places["Lisbon"]["category"] == "area"
    assert "Random Title Words" not in places                  # weak and not on a map: dropped
    assert db.post_counts()["done"] == 1


def test_same_place_from_two_posts_is_merged(db, settings, geocoder, monkeypatch):
    monkeypatch.setattr(pipeline, "analyse_media", lambda *a, **k: ("", ""))
    for i in ("1", "2"):
        db.upsert_post({"media_id": i, "shortcode": f"S{i}", "caption": "📍 Pasteis de Belem", "media": []})
        pipeline.process_post(db.next_pending_post(), db=db, geocoder=geocoder, downloader=None, settings=settings)
    places = db.list_places()
    assert len(places) == 1 and len(places[0]["sources"]) == 2


def test_exports(db, settings, geocoder, monkeypatch):
    monkeypatch.setattr(pipeline, "analyse_media", lambda *a, **k: ("", ""))
    db.upsert_post({"media_id": "1", "shortcode": "S1", "username": "eats",
                    "caption": "📍 Pastéis de Belém", "media": []})
    pipeline.process_post(db.next_pending_post(), db=db, geocoder=geocoder, downloader=None, settings=settings)
    db.update_place(db.list_places()[0]["id"], {"notes": "tarts & coffee"})
    places = db.list_places()
    kml = exporters.to_kml(places)
    assert "<coordinates>-9.2032,38.6975,0</coordinates>" in kml and "tarts &amp; coffee" in kml
    assert "instagram.com/p/S1/" in kml
    assert exporters.to_csv(places).splitlines()[0].startswith("Name,Latitude,Longitude")
    assert json.loads(exporters.to_geojson(places))["features"][0]["geometry"]["coordinates"] == [-9.2032, 38.6975]
    assert "query=" in exporters.google_maps_url(places[0])


def test_upsert_refreshes_urls_without_requeueing(db):
    assert db.upsert_post({"media_id": "9", "media": [{"kind": "video", "url": "old"}]})
    db.finish_post(db.next_pending_post()["id"])
    assert not db.upsert_post({"media_id": "9", "media": [{"kind": "video", "url": "new"}]})
    row = db.one("SELECT status, media FROM posts WHERE media_id='9'")
    assert row["status"] == "done" and "new" in row["media"]
