"""Read-only entry point for the public Community Cloud demo."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "ui/app.py"), init_globals={"READ_ONLY": True})
