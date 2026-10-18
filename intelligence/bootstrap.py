"""Create ignored local .env exactly once; never print passwords or connection strings."""
from __future__ import annotations
import os
import secrets
from .config import ENV_FILE

def write_local_env() -> bool:
    if ENV_FILE.exists():
        text = ENV_FILE.read_text(encoding="utf-8-sig")
        existing = {line.partition("=")[0].strip() for line in text.splitlines()
                    if "=" in line and not line.lstrip().startswith("#")}
        required = {"POSTGRES_PASSWORD", "CM_DB_DSN", "CM_DB_ENABLED"}
        if required.issubset(existing):
            return False
        if "POSTGRES_PASSWORD" in existing or "CM_DB_DSN" in existing:
            raise RuntimeError("Existing .env is incomplete. Complete it manually without changing your database password.")
        prefix = text.rstrip("\n") + "\n"
    else:
        prefix = "# Local-only ContentMachine PostgreSQL credentials. Never commit this file.\n"
    password = secrets.token_hex(24)
    addition = (f"POSTGRES_PASSWORD={password}\n"
                f"CM_DB_DSN=postgresql://contentmachine:{password}@127.0.0.1:54329/contentmachine\n"
                "CM_DB_ENABLED=1\n")
    with ENV_FILE.open("w", encoding="utf-8") as f:
        f.write(prefix + addition)
    if os.name != "nt":
        ENV_FILE.chmod(0o600)
    return True

def main() -> None:
    created = write_local_env()
    print("Local PostgreSQL configuration " + ("created." if created else "already exists; kept unchanged."))
    print("Credentials remain only in the ignored .env file.")

if __name__ == "__main__":
    main()
