from fastapi.testclient import TestClient

from instamap.server import create_app


def test_api_flow(settings):
    app = create_app(settings, start_worker=False)
    db = app.state.db
    db.upsert_post({"media_id": "1", "shortcode": "S1", "media": []})
    post = db.next_pending_post()
    pid = db.add_place(post_id=post["id"], name="Time Out Market", method="caption", evidence="x",
                       confidence=0.8, geo={"lat": 38.70, "lon": -9.14, "city": "Lisbon"})
    with TestClient(app) as client:
        st = client.get("/api/status").json()
        assert st["logged_in"] is False and st["counts"]["total"] == 1
        assert client.get("/").status_code == 200

        places = client.get("/api/places").json()
        assert places[0]["google_maps_url"].startswith("https://www.google.com/maps/search/")

        assert client.get("/api/export.kml").text.count("<Placemark>") == 0  # nothing approved yet
        client.patch(f"/api/places/{pid}", json={"status": "approved"})
        resp = client.get("/api/export.kml")
        assert resp.text.count("<Placemark>") == 1
        assert "attachment" in resp.headers["content-disposition"]

        assert client.get("/api/collections").status_code == 401  # not logged in yet
        assert client.post("/api/collection", json={"id": "77", "name": "Travel"}).json()["collection"]["id"] == "77"


def test_worker_without_login_does_not_strand_posts(settings):
    import pytest
    from instamap.instagram import NotLoggedIn
    app = create_app(settings, start_worker=False)
    app.state.db.upsert_post({"media_id": "1", "media": []})
    with pytest.raises(NotLoggedIn):
        app.state.worker.process_queue()
    assert app.state.db.post_counts()["pending"] == 1
