from flask_sqlalchemy import SQLAlchemy

try:
    from flask_migrate import Migrate
except Exception:  # pragma: no cover - runtime fallback when package unavailable
    class Migrate:  # minimal no-op fallback
        def init_app(self, *args, **kwargs):
            return None


db = SQLAlchemy()
migrate = Migrate()
