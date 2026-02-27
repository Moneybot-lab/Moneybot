import subprocess
import sys


def _install_runtime_deps() -> None:
    """Best-effort runtime recovery when deploy build command missed DB deps."""
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "Flask-SQLAlchemy",
            "SQLAlchemy",
            "Flask-Migrate",
        ]
    )


try:
    from flask_sqlalchemy import SQLAlchemy
except Exception:  # pragma: no cover - runtime recovery path
    _install_runtime_deps()
    from flask_sqlalchemy import SQLAlchemy

try:
    from flask_migrate import Migrate
except Exception:  # pragma: no cover - runtime fallback when package unavailable
    class Migrate:  # minimal no-op fallback
        def init_app(self, *args, **kwargs):
            return None


db = SQLAlchemy()
migrate = Migrate()
