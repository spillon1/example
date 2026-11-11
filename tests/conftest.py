import pytest

from instamap.config import Settings
from instamap.db import Database


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTAMAP_DATA_DIR", str(tmp_path / "data"))
    s = Settings()
    s.ollama_url = "http://127.0.0.1:9"  # nothing listens here, so the LLM layer is skipped
    s.ensure_dirs()
    return s


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


class FakeGeocoder:
    """Stands in for OpenStreetMap so tests never touch the network."""

    KNOWN = {
        "lisbon": {"lat": 38.7223, "lon": -9.1393, "city": "Lisbon", "country": "Portugal", "type": "city"},
        "pasteis de belem": {"lat": 38.6975, "lon": -9.2032, "city": "Lisbon", "country": "Portugal", "type": "cafe"},
        "miradouro da senhora do monte": {"lat": 38.7189, "lon": -9.1327, "city": "Lisbon",
                                          "country": "Portugal", "type": "viewpoint"},
    }

    def lookup(self, q):
        from instamap.db import normalise
        return self.KNOWN.get(normalise(q.split(",")[0]))

    def locate(self, name, area):
        r = self.lookup(name)
        return r, name, 1.0 if r else 0.0


@pytest.fixture
def geocoder():
    return FakeGeocoder()
