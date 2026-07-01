from __future__ import annotations

from pathlib import Path


def test_render_web_service_uses_gunicorn_not_flask_dev_server():
    config = Path("render.yaml").read_text(encoding="utf-8")

    assert "gunicorn app:app" in config
    assert "--workers ${WEB_CONCURRENCY:-2}" in config
    assert "--threads ${WEB_THREADS:-4}" in config
    assert "python app.py" not in config
