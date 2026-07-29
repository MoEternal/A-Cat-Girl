from __future__ import annotations

import shutil
from pathlib import Path

import uvicorn

from catgirl.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "artifacts" / ".playwright-runtime"


def main() -> None:
    if RUNTIME_ROOT.exists():
        shutil.rmtree(RUNTIME_ROOT)
    app = create_app(
        RUNTIME_ROOT / "data",
        allow_unconfigured_management=True,
    )
    uvicorn.run(app, host="127.0.0.1", port=8732, log_level="warning")


if __name__ == "__main__":
    main()
