# Render Deployment Steps (MoneyBot)

## 1) Create services
1. Create a **PostgreSQL** service in Render.
2. Create a **Web Service** for this repo.

## 2) Environment variables
Set these env vars on the web service:
- `DATABASE_URL` = internal connection string from Render Postgres
- `MONEYBOT_SECRET_KEY` = long random stable secret (do not rotate frequently)
- `DATA_PROVIDER` = `yfinance` (optional; defaults to `yfinance`)

## 3) Install + migrate + run
Use these commands:

- Build command:
  ```bash
  pip install -r requirements.txt
  ```

- Start command:
  ```bash
  flask --app app:app db upgrade && gunicorn app:app
  ```

## 4) First-time migration bootstrap (local/dev)
If migrations are not initialized yet in your repo, run once locally:

```bash
export MONEYBOT_SECRET_KEY='dev-secret'
export DATABASE_URL='postgresql://...'
flask --app app:app db init
flask --app app:app db migrate -m "initial schema"
flask --app app:app db upgrade
```

Commit generated `migrations/` folder.
