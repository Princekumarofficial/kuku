from pathlib import Path

import pytest
from conftest import SCRIPT

from promofinder.parser import ScreenplayParser, timestamp_ms


@pytest.mark.parametrize(
    ("stamp", "expected"), [("00:01.23", 1230), ("01:02:03.004", 3723004), ("123:04.5", 7384500)]
)
def test_timestamp(stamp, expected):
    assert timestamp_ms(stamp) == expected


@pytest.mark.parametrize("stamp", ["00:60.00", "-1:02", "01:99:00", "12", "00:00.0001"])
def test_invalid_timestamp(stamp):
    with pytest.raises(ValueError):
        timestamp_ms(stamp)


def test_preserves_text_metadata_overlap_and_line_numbers(episode):
    assert episode.source.complete
    assert episode.events[1].speaker == "WOMAN"
    assert episode.events[1].delivery == "Shocked"
    assert episode.events[1].text == '"How did you find me?"'
    assert episode.events[0].start_ms == episode.events[1].start_ms
    assert episode.events[1].source_lines == [16, 18]
    assert [(c.start_ms, c.end_ms) for c in episode.coverage] == [(0, 40000)]


def test_truncated_source_does_not_inherit_declared_duration(tmp_path):
    path = tmp_path / "truncated.txt"
    path.write_text(
        SCRIPT.format(number=1).split("**WOMAN")[0].replace("End: (00:40.00)", "End: (00:13.20)"),
        encoding="utf-8",
    )
    episode = ScreenplayParser().parse(path)
    assert not episode.source.complete
    assert episode.coverage[0].end_ms == 13200


def test_bad_event_invalidates_its_scene_coverage(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text(
        SCRIPT.format(number=1).replace("End: (00:02.00)", "End: (00:99.00)"), encoding="utf-8"
    )
    episode = ScreenplayParser().parse(path)
    assert not episode.source.complete
    assert episode.coverage == []
    assert any("Invalid timestamp" in w for w in episode.warnings)


def test_multiple_scenes_and_sound(tmp_path):
    path = tmp_path / "multi.txt"
    text = SCRIPT.format(number=1).replace(
        "--- SCRIPT END ---",
        """SCENE 02
Scene heading: EXT. HALL – NIGHT
Scene start: 00:40.00
Scene end: 00:42.00
**SOUND FX:**
Start: (00:40.00) | End: (00:42.00)
A bell rings.
--- SCRIPT END ---""",
    )
    path.write_text(text, encoding="utf-8")
    episode = ScreenplayParser().parse(path)
    assert episode.source.complete
    assert len(episode.scenes) == 2
    assert episode.events[-1].type == "sound"


def test_supplied_dataset_parses_with_unique_ids():
    files = list((Path(__file__).resolve().parents[1] / "data").glob("*.txt"))
    assert files
    episodes = [ScreenplayParser().parse(file) for file in files]
    assert len({e.episode_id for e in episodes}) == len(episodes)
    for episode in episodes:
        assert len({e.id for e in episode.events}) == len(episode.events)
        assert all(e.text and e.start_ms < e.end_ms for e in episode.events)
