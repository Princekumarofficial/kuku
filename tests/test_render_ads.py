import json

import pytest

from scripts.render_ads import render_run


def create_run(tmp_path):
    run = tmp_path / "run"
    for directory in ("parsed", "candidates", "outputs"):
        (run / directory).mkdir(parents=True)

    event = {
        "id": "e_001",
        "scene_id": "sc_001",
        "type": "dialogue",
        "start_ms": 1000,
        "end_ms": 2200,
        "text": '"Who are you?"',
        "speaker": "JULIA",
    }
    (run / "parsed" / "ep_001.json").write_text(
        json.dumps({"episode_id": "ep_001", "events": [event]}), encoding="utf-8"
    )
    (run / "parsed" / "ep_001_aliases.json").write_text(
        json.dumps({"EP01:E1": "ep_001:e_001"}), encoding="utf-8"
    )
    segment = {"episode_id": "ep_001", "start_ms": 0, "end_ms": 20_000}
    candidate = {
        "candidate_id": "c_1",
        "segments": [segment],
        "duration_ms": 20_000,
        "clip_events": [
            {
                "ref": "EP01:E1",
                "segment_index": 0,
                "relative_start_ms": 1000,
                "relative_end_ms": 2200,
                "type": "dialogue",
                "speaker": "JULIA",
                "text": '"Who are you?"',
            }
        ],
    }
    (run / "candidates" / "ep_001.json").write_text(
        json.dumps([candidate]), encoding="utf-8"
    )
    promo = {
        "candidate_id": "c_1",
        "promo_id": "p_001",
        "rank": 1,
        "segments": [segment],
        "duration_ms": 20_000,
        "scores": {"final": 7.5},
        "why_it_works": "An unanswered question opens the story.",
        "hook": {"description": "A direct question."},
        "cliffhanger": {"description": "The answer is withheld."},
        "warnings": ["Verify timing against video."],
    }
    (run / "outputs" / "series_promos.json").write_text(
        json.dumps({"promos": [promo]}), encoding="utf-8"
    )
    (run / "outputs" / "episode_001_promos.json").write_text(
        json.dumps({"scope": {"episode_id": "ep_001"}, "promos": [promo]}),
        encoding="utf-8",
    )
    return run


def test_render_run_exports_source_dialogue_for_both_scopes(tmp_path):
    run = create_run(tmp_path)

    output = render_run(run)

    content = output.read_text(encoding="utf-8")
    assert output == run / "outputs" / "human_readable_ads.md"
    assert "## Series-selected ads" in content
    assert "## Episode-selected ads" in content
    assert "DIALOGUE" in content
    assert "JULIA" in content
    assert '"Who are you?"' in content
    assert "EP01:E1" in content
    assert "not newly written voiceovers" in content


def test_render_run_rejects_candidate_text_that_does_not_match_source(tmp_path):
    run = create_run(tmp_path)
    candidate_path = run / "candidates" / "ep_001.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate[0]["clip_events"][0]["text"] = "Invented dialogue"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match source event"):
        render_run(run, scope="series")
