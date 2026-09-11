"""
Emergency table creator: `python -m app.bootstrap_db`.

Alembic is the real migration tool for this project, but if a migration fails
on a fresh Render database we still want a working demo, so this creates every
table straight from the models. It is idempotent.
"""
from app.db import Base, engine
from app import models  # noqa: F401  (import registers the tables on Base)


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("[bootstrap_db] tables ensured:", ", ".join(sorted(Base.metadata.tables)))


if __name__ == "__main__":
    main()
