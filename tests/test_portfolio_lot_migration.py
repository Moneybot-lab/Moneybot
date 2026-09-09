import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_removes_legacy_symbol_unique_after_startup_backfill(tmp_path):
    """Regression: create_app adds original_shares before Alembic gets control."""
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL,
                username VARCHAR(80) NOT NULL UNIQUE, email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE watchlist_items (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, symbol VARCHAR(16) NOT NULL,
                company VARCHAR(255), buy_price NUMERIC(16,4), shares NUMERIC(16,6),
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_watchlist_user_symbol UNIQUE(user_id, symbol),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE sold_trades (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, symbol VARCHAR(16) NOT NULL,
                shares_sold NUMERIC(16,6) NOT NULL, sold_price NUMERIC(16,4) NOT NULL,
                entry_price NUMERIC(16,4) NOT NULL, realized_amount NUMERIC(16,4) NOT NULL,
                sold_at DATETIME NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
            -- Simulate the affected deployment: the first repair revision was
            -- stamped as complete but the uniqueness constraint remained.
            INSERT INTO alembic_version VALUES ('20260909_01');
            INSERT INTO users VALUES (1,'Test','test','test@example.com','x',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
            INSERT INTO watchlist_items VALUES (1,1,'AAPL',NULL,100,10,CURRENT_TIMESTAMP);
            """
        )

    env = os.environ | {
        "DATABASE_URL": f"sqlite:///{database}",
        "MONEYBOT_SECRET_KEY": "migration-test",
    }
    subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app:app", "db", "upgrade"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database) as connection:
        # This was the exact production failure: a second lot for an owned
        # ticker raised the legacy uniqueness violation.
        connection.execute(
            "INSERT INTO watchlist_items "
            "(id,user_id,symbol,buy_price,shares,original_shares,created_at) "
            "VALUES (2,1,'AAPL',120,5,5,CURRENT_TIMESTAMP)"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM watchlist_items WHERE user_id=1 AND symbol='AAPL'"
        ).fetchone()[0] == 2
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260910_01"
