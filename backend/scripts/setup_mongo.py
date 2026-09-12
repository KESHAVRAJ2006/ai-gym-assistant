"""
One-command MongoDB Atlas setup.

    cd backend
    .venv\\Scripts\\python.exe scripts/setup_mongo.py

Paste your Atlas connection string when prompted. The script then:
  1. checks the string is shaped correctly and warns about the classic
     mistakes (the <db_password> placeholder left in, a raw special
     character in the password) BEFORE wasting a 10-second timeout on them;
  2. actually connects and pings the cluster;
  3. writes a real document and reads it back, so you know the user has
     write permission and not just connect permission;
  4. saves MONGO_URL into backend/.env, preserving anything already there;
  5. tells you exactly what to paste into Render.

Your password is never echoed to the terminal and never leaves your machine.
"""
from __future__ import annotations

import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

ENV_PATH = BACKEND / ".env"
SPECIALS = set("@:/?#[]")


def die(msg: str, fix: str = "") -> None:
    print(f"\n  [X] {msg}")
    if fix:
        print(f"      FIX: {fix}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


# --------------------------------------------------------------------------
def validate(uri: str) -> None:
    """Catch the three mistakes that cause 90% of failed first connections."""
    if not uri:
        die("Nothing was entered.")

    if not uri.startswith(("mongodb+srv://", "mongodb://")):
        die("That does not look like a MongoDB connection string.",
            "It must start with mongodb+srv:// - copy it again from "
            "Atlas > Connect > Drivers > Python.")

    if "<" in uri or ">" in uri:
        die("The string still contains a placeholder in angle brackets.",
            "Delete <db_password> INCLUDING the < and > characters, and type "
            "your real password there.")

    if "<db_password>" in uri or "your_password" in uri.lower():
        die("The password placeholder was not replaced.",
            "Replace it with the password you set in Atlas > Database Access.")

    # Split on the LAST '@', not the first. A regex that stops at the first
    # '@' silently treats "p@ssword" as the password "p" and then hands a
    # broken URI to pymongo, which answers with a raw RFC 3986 traceback.
    _, _, rest = uri.partition("://")
    if "@" not in rest:
        die("Could not find a username and password in the string.",
            "The shape must be mongodb+srv://USERNAME:PASSWORD@cluster...")
    creds, _, _hostpart = rest.rpartition("@")
    user, sep, password = creds.partition(":")
    if not sep:
        die("Could not find a password in the string.",
            "The shape must be mongodb+srv://USERNAME:PASSWORD@cluster...")
    if not password:
        die("The password is empty.",
            "Atlas > Database Access > Edit > Edit Password > Autogenerate.")

    bad = SPECIALS & set(password)
    if bad:
        encoded = quote_plus(password)
        die(f"The password contains {' '.join(sorted(bad))}, which breaks the URL.",
            f"Either set a new password in Atlas with no special characters, "
            f"or replace the password in the string with: {encoded}")

    host = urlsplit(uri.replace("mongodb+srv://", "https://", 1)).hostname or "?"
    ok(f"String looks valid (user '{user}', cluster '{host}')")


def connect(uri: str):
    try:
        from pymongo import MongoClient
    except ImportError:
        die("pymongo is not installed.",
            r"Run: .venv\Scripts\python.exe -m pip install -r requirements.txt")

    print("  ... connecting (up to 15 seconds)")
    # Build the client INSIDE the try. With a mongodb+srv:// URI pymongo does
    # the DNS SRV lookup during construction, so a mistyped cluster name
    # raises here rather than at ping() and would otherwise escape as a raw
    # traceback.
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=15000)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001 - message quality matters more here
        name = type(exc).__name__
        text = str(exc)
        low = text.lower()

        if "does not exist" in low or "dns" in low or name == "ConfigurationError":
            die("The cluster address in the string does not exist.",
                "You probably copied it by hand or edited it. Go to Atlas > "
                "Connect > Drivers > Python and use the COPY button, then "
                "only replace <db_password>.")
        if "authentication" in low or "auth failed" in low:
            die("The cluster is reachable but the username/password was rejected.",
                "Atlas > Database Access > Edit your user > Edit Password > "
                "Autogenerate, copy it, and run this script again.")
        if "ServerSelectionTimeout" in name or "timed out" in low:
            die("Could not reach the cluster.",
                "Atlas > Network Access > + ADD IP ADDRESS > ALLOW ACCESS FROM "
                "ANYWHERE (0.0.0.0/0) > Confirm. Wait until it says Active, "
                "then run this again.")
        if "escaped" in low or name == "InvalidURI":
            die("The username or password contains a character that must be escaped.",
                "Set a new password in Atlas with only letters and numbers - "
                "it is far easier than percent-encoding it.")
        die(f"Connection failed: {name}: {text}")
    ok("Connected and pinged the cluster")
    return client


def check_write(client, db_name: str) -> None:
    """A user can often connect but not write. Prove write access now."""
    db = client[db_name]
    try:
        res = db["_setup_check"].insert_one(
            {"ok": True, "at": datetime.now(timezone.utc)}
        )
        db["_setup_check"].delete_one({"_id": res.inserted_id})
    except Exception as exc:  # noqa: BLE001
        die(f"Connected, but cannot write to the database: {type(exc).__name__}",
            "Atlas > Database Access > Edit your user > Database User Privileges "
            "> choose 'Read and write to any database'.")
    ok(f"Write permission confirmed on database '{db_name}'")


def save_env(uri: str, db_name: str) -> None:
    """Update MONGO_URL / MONGO_DB in backend/.env, keeping other keys."""
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    def upsert(key: str, value: str) -> None:
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                return
        lines.append(f"{key}={value}")

    upsert("MONGO_URL", uri)
    upsert("MONGO_DB", db_name)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"Saved to {ENV_PATH}")
    print("       (.env is in .gitignore, so this password will NOT be pushed "
          "to GitHub)")


# --------------------------------------------------------------------------
def read_uri() -> str:
    """
    Prompt for the string, hidden.

    `--uri <string>` is accepted so this can be run non-interactively, and
    because getpass() reads from the terminal rather than stdin - piping into
    it simply hangs forever, which is a miserable way to discover a typo.
    """
    argv = sys.argv[1:]
    if argv and argv[0] == "--uri" and len(argv) > 1:
        return argv[1].strip().strip('"').strip("'")

    print("\n  Paste your connection string from:")
    print("    Atlas > Connect > Drivers > Python")
    print("\n  It is hidden as you paste (right-click pastes in PowerShell).\n")
    try:
        raw = getpass.getpass("  Connection string: ")
    except (EOFError, OSError):
        raw = input("  Connection string: ")
    return raw.strip().strip('"').strip("'")


def main() -> int:
    print("=" * 68)
    print("  MongoDB Atlas setup")
    print("=" * 68)

    uri = read_uri()

    print()
    validate(uri)
    client = connect(uri)

    db_name = "aigym"
    check_write(client, db_name)

    existing = [d for d in client.list_database_names()
                if d not in ("admin", "local", "config")]
    if existing:
        ok(f"Databases already on this cluster: {', '.join(existing)}")

    save_env(uri, db_name)
    client.close()

    print("\n" + "=" * 68)
    print("  DONE - MongoDB is connected.")
    print("=" * 68)
    print("\n  1. Restart your backend, then open http://127.0.0.1:8000/health")
    print('     You should now see:  "mongo":"connected (aigym)"')
    print("\n  2. For Render, add ONE environment variable to aigym-api:")
    print("       Key:   MONGO_URL")
    print("       Value: (the same string you just pasted)")
    print("     Render dashboard > aigym-api > Environment > Add Environment")
    print("     Variable > Save.")
    print("\n  Your string is stored in backend/.env if you need to copy it.\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        raise SystemExit(130) from None
