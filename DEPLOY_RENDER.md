# Render Deployment Steps (MoneyBot)

## 1) Create services
1. Create a **PostgreSQL** service in Render.
2. Create a **Web Service** for this repo.

## 2) Environment variables
Set these env vars on the web service:
- `PYTHON_VERSION` = `3.11.11` (prevents Render from selecting Python 3.14, which breaks `numba`-based builds)
- `DATABASE_URL` = internal connection string from Render Postgres
- `MONEYBOT_SECRET_KEY` = long random stable secret (do not rotate frequently)
- `DATA_PROVIDER` = `yfinance` (optional; defaults to `yfinance`)

## 3) Install + migrate + run (Render Web Service)
Configure the web service with the following commands:

- **Build Command**
  ```bash
  pip install -r requirements.txt
  pip install Flask-SQLAlchemy Flask-Migrate SQLAlchemy psycopg2-binary "psycopg[binary]"
  ```

- **Start Command**
  ```bash
  bash -lc 'if [ -d migrations ]; then flask --app app:app db upgrade || echo "Migration step failed; starting web server anyway."; else echo "No migrations directory found; skipping database migration step."; fi; python app.py'
  ```

Notes:
- `db upgrade` applies committed Alembic migrations when a `migrations/` folder exists.
- If `migrations/` is missing, startup now skips migrations instead of failing the deploy.
- The start command runs `python app.py` as the long-running process.

## 4) First-time migration bootstrap (local/dev)
If your repo does **not** already contain a `migrations/` folder, initialize it once locally:

```bash
export MONEYBOT_SECRET_KEY='dev-secret'
export DATABASE_URL='postgresql://<user>:<password>@<host>:5432/<db_name>'

flask --app app:app db init
flask --app app:app db migrate -m "initial schema"
flask --app app:app db upgrade
```

Then commit the generated migrations:

```bash
git add migrations
git commit -m "Add initial database migrations"
```

After that, deploy to Render and keep using step 3 (`db upgrade && gunicorn`) for ongoing deploys.
