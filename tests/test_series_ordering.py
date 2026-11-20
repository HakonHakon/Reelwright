import os

from fastapi.testclient import TestClient

from studio.app import app
from studio import settings, store


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    return TestClient(app)


def test_projects_default_to_most_recently_edited(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    first = api.post("/api/series", json={"name": "First"}).json()
    second = api.post("/api/series", json={"name": "Second"}).json()
    api.post(f"/api/s/{first['id']}/episodes", json={"title": "Pilot"})
    api.post(f"/api/s/{second['id']}/episodes", json={"title": "Pilot"})

    for sid, timestamp in ((first["id"], 100), (second["id"], 200)):
        for current, _, files in os.walk(store.series_dir(sid)):
            for name in files:
                if name.endswith(".json"):
                    os.utime(os.path.join(current, name), (timestamp, timestamp))

    projects = api.get("/api/series").json()

    assert [project["id"] for project in projects] == [second["id"], first["id"]]
    assert projects[0]["updated"] > projects[1]["updated"]


def test_manual_project_order_persists_and_requires_every_project_once(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    ids = [api.post("/api/series", json={"name": name}).json()["id"] for name in ("One", "Two", "Three")]

    response = api.put("/api/series/order", json={"ids": [ids[2], ids[0], ids[1]]})

    assert response.status_code == 200
    assert response.json() == [ids[2], ids[0], ids[1]]
    assert [project["id"] for project in api.get("/api/series").json()] == [ids[2], ids[0], ids[1]]
    assert api.put("/api/series/order", json={"ids": [ids[0], ids[1]]}).status_code == 400
    assert api.put("/api/series/order", json={"ids": [ids[0], ids[0], ids[2]]}).status_code == 400
    assert api.put("/api/series/order", json={"ids": [ids[0], ids[1], "missing"]}).status_code == 400
