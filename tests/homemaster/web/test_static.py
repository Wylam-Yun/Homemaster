from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from homemaster.web.static import mount_web_static


def test_static_mount_serves_built_index_and_assets_without_shadowing_api(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<h1>HomeMaster Console</h1>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    mount_web_static(app, root=tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert "HomeMaster Console" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
