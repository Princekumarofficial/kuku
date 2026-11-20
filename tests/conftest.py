from pathlib import Path

import pytest

from promofinder.config import Config
from promofinder.parser import ScreenplayParser

SCRIPT = """EPISODE NUMBER: Episode {number}
EPISODE LOGLINE: SECRET_METADATA: The visitor is her brother.
CONTINUITY NOTES: SECRET_FUTURE: They eventually reconcile.

--- SCRIPT START ---
SCENE 01
Scene heading: INT. HALL – DAY
Scene start: 00:00.00
Scene end: 00:40.00
Actions and dialogue:

**CAMERA:**
Start: (00:00.00) | End: (00:40.00)
A visitor confronts a woman in the doorway.

**WOMAN (Shocked):**
Start: (00:00.00) | End: (00:02.00)
"How did you find me?"

**VISITOR (Urgent):**
Start: (00:38.00) | End: (00:40.00)
"There is something you need to know."

--- SCRIPT END ---
"""


@pytest.fixture
def episode(tmp_path):
    path = tmp_path / "episode-001.txt"
    path.write_text(SCRIPT.format(number=1), encoding="utf-8")
    return ScreenplayParser().parse(path)


@pytest.fixture
def config():
    return Config.load(Path(__file__).resolve().parents[1] / "config.toml")
