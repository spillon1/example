from instamap.instagram import ALL_SAVED, InstagramClient, parse_media

REEL = {
    "pk": "3301", "code": "C0abc", "media_type": 2,
    "user": {"username": "wanderer"},
    "caption": {"text": "Lisbon in 48h 📍 Pastéis de Belém"},
    "location": {"pk": 1, "name": "Lisbon, Portugal", "lat": 38.72, "lng": -9.14, "city": "Lisbon"},
    "video_versions": [{"width": 1080, "url": "https://cdn/big.mp4"}, {"width": 720, "url": "https://cdn/mid.mp4"},
                       {"width": 240, "url": "https://cdn/tiny.mp4"}],
    "image_versions2": {"candidates": [{"width": 640, "url": "https://cdn/t640.jpg"},
                                       {"width": 1080, "url": "https://cdn/t1080.jpg"}]},
}
CAROUSEL = {
    "pk": "3302", "code": "C0def", "media_type": 8, "user": {"username": "eats"}, "caption": None,
    "carousel_media": [
        {"image_versions2": {"candidates": [{"width": 1080, "url": "https://cdn/a.jpg"}]}},
        {"video_versions": [{"width": 720, "url": "https://cdn/b.mp4"}],
         "image_versions2": {"candidates": [{"width": 720, "url": "https://cdn/b.jpg"}]}},
    ],
}


def test_parse_reel_picks_small_but_usable_video_and_location():
    p = parse_media(REEL, "c1")
    assert p["media_id"] == "3301" and p["shortcode"] == "C0abc" and p["username"] == "wanderer"
    assert p["media"] == [{"kind": "video", "url": "https://cdn/mid.mp4"}]
    assert p["thumbnail"] == "https://cdn/t1080.jpg"
    assert p["ig_location"]["lat"] == 38.72 and p["ig_location"]["lon"] == -9.14
    assert "Pastéis" in p["caption"]


def test_parse_carousel_and_missing_caption():
    p = parse_media(CAROUSEL)
    assert [m["kind"] for m in p["media"]] == ["image", "video"]
    assert p["caption"] == "" and p["ig_location"] is None


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []
        self.headers, self.cookies = {}, type("C", (), {"set": lambda *a, **k: None})()

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self.pages.pop(0))


def test_saved_media_paginates_collection():
    http = FakeHTTP([
        {"items": [{"media": REEL}], "more_available": True, "next_max_id": "abc"},
        {"items": [{"media": CAROUSEL}], "more_available": False},
    ])
    client = InstagramClient({"sessionid": "x", "csrftoken": "y"}, delay=0, session=http)
    got = list(client.saved_media("123"))
    assert [g["shortcode"] for g in got] == ["C0abc", "C0def"]
    assert http.calls[0][0].endswith("/api/v1/feed/collection/123/posts/")
    assert http.calls[1][1] == {"max_id": "abc"}


def test_collections_lists_named_ones_after_all():
    http = FakeHTTP([{"items": [
        {"collection_id": 1, "collection_name": "All posts", "collection_type": "ALL_MEDIA_AUTO_COLLECTION"},
        {"collection_id": 77, "collection_name": "Travel", "collection_type": "MEDIA", "collection_media_count": 12},
    ], "more_available": False}])
    cols = InstagramClient({"sessionid": "x"}, delay=0, session=http).collections()
    assert cols[0]["id"] == ALL_SAVED
    assert cols[1:] == [{"id": "77", "name": "Travel", "count": 12}]


def test_login_required_raises():
    import pytest
    from instamap.instagram import NotLoggedIn
    http = FakeHTTP([{"message": "login_required", "status": "fail"}])
    with pytest.raises(NotLoggedIn):
        InstagramClient({"sessionid": "x"}, delay=0, session=http).collections()
