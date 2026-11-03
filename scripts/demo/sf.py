"""Tiny Snowflake helper of build_rich_demo.sh: `sf.py query SQL` prints rows as JSON; every identifier it is given has
already been validated by the shell script ([A-Za-z0-9_] only). Reads the same SNOWFLAKE_* variables as dbt."""
import json
import os
import sys

import snowflake.connector
from cryptography.hazmat.primitives import serialization


def connect():
    with open(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"], "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"], private_key=der,
        role=os.environ["SNOWFLAKE_ROLE"], warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"])


if __name__ == "__main__":
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sys.argv[1])
        rows = cur.fetchall() if cur.description else []
        print(json.dumps([[str(v) if v is not None else None for v in r] for r in rows]))
    finally:
        conn.close()
