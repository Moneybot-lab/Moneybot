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
  ```

- **Start Command**
  ```bash
  bash -lc 'flask --app app:app db upgrade && gunicorn app:app --bind 0.0.0.0:$PORT'
  ```

Notes:
- `db upgrade` applies any committed Alembic migrations on each deploy.
- If there are no new migrations, `db upgrade` is a no-op and startup continues.
- Keep `gunicorn` as the long-running process; Render uses it to keep the service alive.

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
