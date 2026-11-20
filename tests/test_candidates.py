import pytest

from promofinder.candidates import CandidateBuilder, CandidateError
from promofinder.compact import blind_clip
from promofinder.schemas import (
    Beat,
    EpisodeAnalysis,
    EpisodeDiscovery,
    Event,
    Interval,
    Segment,
    SeriesDiscovery,
)


def test_extracts_all_overlaps_without_summing_events(episode):
    builder = CandidateBuilder([episode])
    candidate = builder.build(
        [Segment(episode_id=episode.episode_id, start_ms=0, end_ms=40000)], "test"
    )
    assert candidate.duration_ms == 40000
    assert len(candidate.clip_events) == 3
    assert candidate.clip_events[-1].relative_end_ms == 40000
    payload = blind_clip(candidate)
    assert "SECRET_METADATA" not in payload
    assert "SECRET_FUTURE" not in payload
    assert "test" not in payload
    assert '"How did you find me?"' in payload


def test_partial_dialogue_rejected(episode):
    episode.events.append(
        Event(
            id="e_004",
            scene_id="sc_001",
            type="action",
            start_ms=1000,
            end_ms=21000,
            text="She turns.",
            source_lines=[1, 2],
        )
    )
    with pytest.raises(CandidateError, match="PARTIAL_DIALOGUE"):
        CandidateBuilder([episode]).build(
            [Segment(episode_id=episode.episode_id, start_ms=1000, end_ms=40000)], "test"
        )


def test_gap_rejected_even_with_camera_event(episode):
    episode.coverage = [Interval(start_ms=0, end_ms=10000), Interval(start_ms=11000, end_ms=40000)]
    with pytest.raises(CandidateError, match="MISSING_SOURCE_COVERAGE"):
        CandidateBuilder([episode]).build(
            [Segment(episode_id=episode.episode_id, start_ms=0, end_ms=40000)], "test"
        )


@pytest.mark.parametrize(("start", "end"), [(0, 19000), (0, 61000)])
def test_duration_gate(episode, start, end):
    with pytest.raises(CandidateError, match="DURATION_OUT_OF_RANGE"):
        CandidateBuilder([episode]).build(
            [Segment(episode_id=episode.episode_id, start_ms=start, end_ms=end)], "test"
        )


def test_cross_episode_duration_and_playback_order(episode):
    second = episode.model_copy(deep=True)
    second.episode_id, second.episode_number = "ep_002", 2
    for ep in [episode, second]:
        ep.events.append(
            Event(
                id="e_004",
                scene_id="sc_001",
                type="action",
                start_ms=20000,
                end_ms=38000,
                text="She waits.",
                source_lines=[1, 2],
            )
        )
    builder = CandidateBuilder([episode, second])
    candidate = builder.build(
        [
            Segment(episode_id="ep_002", start_ms=0, end_ms=20000),
            Segment(episode_id="ep_001", start_ms=20000, end_ms=40000),
        ],
        "cross",
        ["EP02:E2"],
    )
    assert candidate.scope == "series"
    assert candidate.duration_ms == 40000
    assert candidate.clip_events[-1].relative_start_ms == 38000
    assert candidate.clip_events[0].ref.startswith("EP02:")


def test_cross_opportunity_builds_ordered_segments(episode):
    episode.events.append(
        Event(
            id="e_004",
            scene_id="sc_001",
            type="action",
            start_ms=0,
            end_ms=20000,
            text="She opens the door.",
            source_lines=[1, 2],
        )
    )
    second = episode.model_copy(deep=True)
    second.episode_id, second.episode_number = "ep_002", 2
    second.events.append(
        Event(
            id="e_005",
            scene_id="sc_001",
            type="action",
            start_ms=20000,
            end_ms=40000,
            text="He pauses before speaking.",
            source_lines=[1, 2],
        )
    )
    discovery = SeriesDiscovery.model_validate(
        {
            "opportunities": [
                {
                    "scope": "series",
                    "local_id": "cross1",
                    "concept": "A visitor holds a secret.",
                    "ordered_segment_groups": [
                        {"episode_id": "ep_001", "anchor_refs": ["EP01:E4"]},
                        {"episode_id": "ep_002", "anchor_refs": ["EP02:E5"]},
                    ],
                }
            ]
        }
    )
    candidates = CandidateBuilder([episode, second]).series(discovery, limit=2)
    assert len(candidates) == 1
    assert candidates[0].duration_ms == 40000
    assert [s.episode_id for s in candidates[0].segments] == ["ep_001", "ep_002"]


@pytest.mark.parametrize("later_beat", [False, True])
def test_recovery_searches_later_evidence_and_beats_before_capping(episode, later_beat):
    # The opening cannot yield a 20s clip without crossing a gap. Later footage can.
    episode.coverage = [Interval(start_ms=0, end_ms=5000), Interval(start_ms=6000, end_ms=40000)]
    episode.events.append(
        Event(
            id="e_004",
            scene_id="sc_001",
            type="action",
            start_ms=10000,
            end_ms=12000,
            text="She sees a weapon.",
            source_lines=[1, 2],
        )
    )
    beat = Beat(
        id="usable",
        kind="threat",
        event_refs=["EP01:E2", "EP01:E4"],
        description="An arrival becomes threatening.",
        intensity_0_10=8,
    )
    beats = [beat]
    if later_beat:
        # Four stronger beats at the episode end must not suppress the viable fifth.
        beats = [
            Beat(
                id=f"late{i}",
                kind="cliffhanger",
                event_refs=["EP01:E3"],
                description="A secret.",
                intensity_0_10=9,
            )
            for i in range(4)
        ] + beats
    analysis = EpisodeAnalysis(
        episode_id=episode.episode_id,
        summary="A confrontation.",
        characters=[],
        beats=beats,
        revelations=[],
        unresolved_questions=[],
    )
    pool = CandidateBuilder([episode]).episode(
        episode, EpisodeDiscovery(opportunities=[]), analysis, limit=2
    )
    assert pool
    assert all(6000 <= c.segments[0].start_ms <= 10000 for c in pool)
    assert all(20000 <= c.duration_ms <= 60000 for c in pool)
