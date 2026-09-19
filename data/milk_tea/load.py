"""Load the committed synthetic fixtures into a NEW, dedicated MySQL database.

python -m data.milk_tea.load --database milk_tea
Uses DB_HOST/DB_PORT/DB_ADMIN_USER/DB_ADMIN_PASSWORD from .env/environment.
Never drops or truncates tables. Refuses any existing nonempty database.
"""
import argparse
import os
import re
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from pymysql.constants import CLIENT

from .generate import COUNTS, HERE, artifacts


def load(database):
    if not re.fullmatch(r"milk_tea(?:_[A-Za-z0-9]+)*", database):
        raise ValueError("Use a dedicated database named milk_tea or milk_tea_<suffix>.")
    # Guard against importing a stale seed or manually modified gold bundle.
    for filename, expected in artifacts().items():
        if (HERE / filename).read_bytes() != expected.encode("utf-8"):
            raise ValueError(f"Artifact drift: {filename}; regenerate and review before importing.")
    conn = pymysql.connect(host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")), user=os.getenv("DB_ADMIN_USER", "root"),
        password=os.getenv("DB_ADMIN_PASSWORD", ""), charset="utf8mb4",
        autocommit=True, client_flag=CLIENT.MULTI_STATEMENTS)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (database,))
            if cur.fetchone()[0]:
                raise ValueError(f"Refusing to overwrite nonempty database {database}. Choose a fresh suffix.")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4")
            cur.execute(f"USE `{database}`")
            for filename in ("schema.sql", "seed.sql"):
                cur.execute((HERE / filename).read_text(encoding="utf-8"))
                while cur.nextset():
                    pass
            for table, count in COUNTS.items():
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                assert cur.fetchone()[0] == count, table
            user, password = os.getenv("DB_USER", "harness_ro"), os.getenv("DB_PASSWORD", "readonly")
            cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (user, password))
            cur.execute(f"GRANT SELECT ON `{database}`.* TO %s@'%%'", (user,))
    finally:
        conn.close()
    print(f"Loaded exactly 10000 rows into {database}; granted SELECT to agent account.")


def main():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="milk_tea")
    args = parser.parse_args()
    load(args.database)


if __name__ == "__main__":
    main()
