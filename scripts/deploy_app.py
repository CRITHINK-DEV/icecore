"""Deploy the icecore app to Streamlit in Snowflake: stage, one PUT per file, CREATE STREAMLIT (see docs/app.md).

Reads the artifacts list of streamlit/snowflake.yml (including the components/console/build/** glob), so the
files that ship are the ones the project definition lists. Settings, all from the environment:

  ICECORE_APP_DATABASE   database of the app (required)
  ICECORE_APP_SCHEMA     schema of the app (default: ICECORE_OBS, the views schema: the zero-config path)
  ICECORE_APP_WAREHOUSE  query warehouse of the app (required)
  SNOWFLAKE_CONNECTION_NAME, or SNOWFLAKE_ACCOUNT + SNOWFLAKE_USER (+ SNOWFLAKE_PRIVATE_KEY_PATH, else browser login),
  optional SNOWFLAKE_ROLE

It never runs a GRANT: it prints the statements a viewer role needs. `--dry-run` prints the statements only.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STREAMLIT = ROOT / "streamlit"
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
APP, STAGE = "ICECORE_APP", "ICECORE_APP_STAGE"


def ident(label: str, value: str) -> str:
    if not IDENT.match(value or ""):
        raise ValueError(f"{label} must be a plain identifier ([A-Za-z_][A-Za-z0-9_]*), got {value!r}")
    return value.upper()


def files(streamlit_dir: Path = STREAMLIT) -> list[tuple[Path, str]]:
    """(local file, folder on the stage) for every artifact of snowflake.yml; '' is the stage root."""
    definition = yaml.safe_load((streamlit_dir / "snowflake.yml").read_text())
    entity = next(e for e in definition["entities"].values() if e.get("type") == "streamlit")
    out: list[tuple[Path, str]] = []
    for art in entity["artifacts"]:
        if isinstance(art, str):
            parent = Path(art).parent.as_posix()
            out.append((streamlit_dir / art, "" if parent == "." else parent))
            continue
        base = art["src"].split("*", 1)[0].rstrip("/")   # components/console/build
        dest = art.get("dest", base).strip("/")
        for f in sorted(p for p in (streamlit_dir / art["src"].replace("**", "*")).parent.rglob("*") if p.is_file()):
            rel = f.relative_to(streamlit_dir / base).parent
            out.append((f, (Path(dest) / rel).as_posix()))
    toml = streamlit_dir / "icecore_app.toml"
    if toml.exists():
        out.append((toml, ""))
    for f, _ in out:
        if "'" in str(f) or not f.exists():
            raise ValueError(f"artifact not found or unusable: {f.name}")
    return out


def plan(database: str, schema: str, warehouse: str, streamlit_dir: Path = STREAMLIT) -> list[str]:
    db, sch, wh = ident("ICECORE_APP_DATABASE", database), ident("ICECORE_APP_SCHEMA", schema), ident("ICECORE_APP_WAREHOUSE", warehouse)
    stage = f"{db}.{sch}.{STAGE}"
    stmts = [f"create or replace stage {stage} directory=(enable=true) encryption=(type='SNOWFLAKE_SSE')"]
    for f, folder in files(streamlit_dir):
        target = f"@{stage}/{folder}" if folder else f"@{stage}"
        stmts.append(f"put 'file://{f}' {target} auto_compress=false overwrite=true")
    stmts.append(f"create or replace streamlit {db}.{sch}.{APP} from @{stage} main_file='streamlit_app.py' "
                 f"query_warehouse={wh} runtime_name='SYSTEM$WAREHOUSE_RUNTIME'")
    return stmts


def grants(database: str, schema: str) -> str:
    return (f"GRANT USAGE ON STREAMLIT {database.upper()}.{schema.upper()}.{APP} TO ROLE <viewer_role>;\n"
            f"-- the app runs with its owner's rights: that role needs USAGE on the database and the views schema,\n"
            f"-- SELECT on the views and USAGE on ICECORE_ACK (see docs/configuration.md#what-setup-creates)")


def _connect():
    import snowflake.connector
    if os.environ.get("SNOWFLAKE_CONNECTION_NAME"):
        return snowflake.connector.connect(connection_name=os.environ["SNOWFLAKE_CONNECTION_NAME"])
    kw = {"account": os.environ["SNOWFLAKE_ACCOUNT"], "user": os.environ["SNOWFLAKE_USER"]}
    if os.environ.get("SNOWFLAKE_ROLE"):
        kw["role"] = os.environ["SNOWFLAKE_ROLE"]
    key = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key:
        from cryptography.hazmat.primitives import serialization
        pk = serialization.load_pem_private_key(Path(key).read_bytes(), password=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "").encode() or None)
        kw["private_key"] = pk.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption())
    else:
        kw["authenticator"] = "externalbrowser"
    return snowflake.connector.connect(**kw)


def main(argv: list[str]) -> int:
    env = os.environ
    try:
        stmts = plan(env.get("ICECORE_APP_DATABASE", ""), env.get("ICECORE_APP_SCHEMA", "ICECORE_OBS"),
                     env.get("ICECORE_APP_WAREHOUSE", ""))
    except ValueError as exc:
        print(f"deploy_app: {exc}", file=sys.stderr)
        return 2
    if "--dry-run" in argv:
        print("\n".join(s + ";" for s in stmts))
    else:
        conn = _connect()
        try:
            cur = conn.cursor()
            for s in stmts:
                print("deploy_app:", s.split(" file://")[0] if s.startswith("put") else s[:110], flush=True)
                cur.execute(s)
        finally:
            conn.close()
    db, sch = env["ICECORE_APP_DATABASE"], env.get("ICECORE_APP_SCHEMA", "ICECORE_OBS")
    print(f"\nDone. Open it in Snowsight: Projects > Streamlit > {APP} ({db.upper()}.{sch.upper()}).")
    print("Grant it to the roles that may view it (this script runs no grant):\n" + grants(db, sch))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
