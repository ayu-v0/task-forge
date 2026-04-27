"""Create TaskForge database tables from SQLAlchemy metadata."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.packages.core.db import Base, create_engine_from_env  # noqa: E402
from src.packages.core.db import models  # noqa: F401,E402


def main() -> None:
    engine = create_engine_from_env()
    Base.metadata.create_all(engine)
    print("TaskForge database tables created.")


if __name__ == "__main__":
    main()
