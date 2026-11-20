import json
import re

import pytest
from conftest import SCRIPT
from pydantic import ValidationError

from promofinder.candidates import CandidateBuilder
from promofinder.llm import LLMRouter, ProviderError, ProviderResponse
from promofinder.pipeline import PromoPipeline
from promofinder.rank import PromoRanker
from promofinder.schemas import BlindEvaluation, Segment, SpoilerEvaluation
from promofinder.stages import EpisodeAnalyzer


def blind_response(candidate_id, prefix="EP01"):
    return {
        "candidate_id": candidate_id,
        "hook": {"score_0_10": 8, "evidence_refs": [f"{prefix}:E2"], "reason": "Surprise arrival."},
        "cliffhanger": {
            "score_0_10": 9,
            "evidence_refs": [f"{prefix}:E3"],
            "unresolved_at_end": True,
            "reason": "Secret is withheld.",
        },
        "standalone": {"score_0_10": 7, "missing_context": []},
        "emotion": {"score_0_10": 8, "reason": "Fear."},
        "weaknesses": [],
        "one_line_why_it_works": "An arrival opens an unanswered question.",
    }


def spoiler_response(candidate_id):
    return {
        "candidate_id": candidate_id,
        "spoiler_significance_0_10": 8,
        "spoiler_damage_0_10": 3,
        "revealed_information": [],
        "resolves_central_mystery": False,
        "new_questions_created": ["What is the secret?"],
        "reason": "The answer is withheld.",
    }


class ScriptedProvider:
    """Deterministic transport double, not a production 'mock LLM' mode."""

    def __init__(self):
        self.requests = []

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        title, user = kwargs["schema"]["title"], kwargs["user"]
        episode_number = int(re.search(r"EP=ep_(\d+)", user)[1]) if "EP=ep_" in user else 1
        ep, prefix = f"ep_{episode_number:03d}", f"EP{episode_number:02d}"
        if title == "EpisodeAnalysis":
            result = {
                "episode_id": ep,
                "summary": "An unexpected visitor arrives.",
                "characters": [{"name": "Woman", "role": "resident"}],
                "beats": [
                    {
                        "id": "b1",
                        "kind": "confrontation",
                        "event_refs": [f"{prefix}:E2"],
                        "description": "An unexpected arrival.",
                        "intensity_0_10": 8,
                    }
                ],
                "revelations": [],
                "unresolved_questions": [
                    {"question": "What does he know?", "introduced_by": [f"{prefix}:E3"]}
                ],
            }
        elif title == "SeriesAnalysis":
            result = {
                "series_id": "series_001",
                "premise": "A secret visitor.",
                "arcs": [
                    {
                        "id": "a1",
                        "description": "Secret",
                        "beat_refs": [{"episode_id": "ep_001", "beat_id": "b1"}],
                    }
                ],
                "key_revelations": [],
            }
        elif title == "EpisodeDiscovery":
            result = {
                "opportunities": [
                    {
                        "scope": "episode",
                        "local_id": "o1",
                        "episode_id": ep,
                        "concept": "A visitor arrives.",
                        "hook_refs": [f"{prefix}:E2"],
                        "ending_refs": [f"{prefix}:E3"],
                        "context_refs": [],
                        "why_promising": "Mystery.",
                    }
                ]
            }
        elif title in {"BlindEvaluation", "SpoilerEvaluation"}:
            candidate_id = re.search(r"CANDIDATE=(\S+)", user)[1]
            prefix = re.search(r"(EP\d+):E", user)[1]
            result = (
                blind_response(candidate_id, prefix)
                if title == "BlindEvaluation"
                else spoiler_response(candidate_id)
            )
        else:
            raise AssertionError(f"Unexpected schema {title}")
        return ProviderResponse(json.dumps(result))


def test_sequential_pipeline_exports_and_reuses_cache(config, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for number in (1, 2):
        (data / f"episode-{number}.txt").write_text(SCRIPT.format(number=number), encoding="utf-8")
    provider = ScriptedProvider()
    router = LLMRouter(config, tmp_path / "cache", {name: provider for name in config.providers})
    pipeline = PromoPipeline(config, tmp_path / "runs", router)
    run = pipeline.run(data)
    result = json.loads((run / "outputs/series_promos.json").read_text())
    assert len(result["promos"]) == 2
    assert result["promos"][0]["scores"]["final"] == 7.6
    assert (run / "outputs/episode_001_promos.json").exists()
    assert (run / "outputs/episode_002_promos.json").exists()
    assert (run / "outputs/report.md").exists()
    assert json.loads((run / "status.json").read_text())["status"] == "complete"
    stages = [r["schema"]["title"] for r in provider.requests]
    assert stages[:3] == ["EpisodeAnalysis", "EpisodeAnalysis", "SeriesAnalysis"]
    for request in provider.requests:
        if request["schema"]["title"] == "BlindEvaluation":
            assert "SECRET_METADATA" not in request["user"]
            assert "SECRET_FUTURE" not in request["user"]
            assert "why_promising" not in request["user"]
    calls = len(provider.requests)
    pipeline.run(data)
    assert len(provider.requests) == calls


def test_ranking_rejects_resolved_endings_and_late_hooks(episode):
    builder = CandidateBuilder([episode])
    candidate = builder.build(
        [Segment(episode_id=episode.episode_id, start_ms=0, end_ms=40000)], "o1"
    )
    blind = BlindEvaluation.model_validate(blind_response(candidate.candidate_id))
    spoiler = SpoilerEvaluation.model_validate(spoiler_response(candidate.candidate_id))
    ranker = PromoRanker(builder)
    assert ranker.assess(candidate, blind, spoiler)[0]["scores"]["final"] == 7.6
    blind.cliffhanger.unresolved_at_end = False
    blind.hook.evidence_refs = ["EP01:E3"]
    blind.cliffhanger.evidence_refs = ["EP01:E2"]
    promo, reasons = ranker.assess(candidate, blind, spoiler)
    assert promo is None
    assert set(reasons) >= {"ENDING_RESOLVED", "HOOK_OUTSIDE_OPENING", "ENDING_EVIDENCE_NOT_AT_END"}


def test_overlap_deduplication(episode):
    builder = CandidateBuilder([episode])
    candidate = builder.build(
        [Segment(episode_id=episode.episode_id, start_ms=0, end_ms=40000)], "o1"
    )
    ranker = PromoRanker(builder)
    promo, _ = ranker.assess(
        candidate,
        BlindEvaluation.model_validate(blind_response(candidate.candidate_id)),
        SpoilerEvaluation.model_validate(spoiler_response(candidate.candidate_id)),
    )
    selected, excluded = ranker.select([promo, promo], 5)
    assert len(selected) == 1
    assert excluded[0]["reasons"] == ["HEAVY_TIME_OVERLAP"]


def test_scores_are_bounded():
    response = blind_response("c_1")
    response["hook"]["score_0_10"] = 11
    with pytest.raises(ValidationError):
        BlindEvaluation.model_validate(response)


def test_invented_analysis_alias_is_rejected(episode):
    class InvalidRouter:
        def call(self, stage, system, user, output, validate):
            response = ScriptedProvider().generate(schema={"title": "EpisodeAnalysis"}, user=user)
            result = output.model_validate_json(response.content)
            result.beats[0].event_refs = ["EP01:E99999"]
            validate(result)

    with pytest.raises(ValueError, match="Unknown evidence"):
        EpisodeAnalyzer(InvalidRouter()).analyze(episode)


def test_evaluation_failure_is_reported_without_exporting_unjudged_clips(config, tmp_path):
    class EvaluationFailureProvider(ScriptedProvider):
        def generate(self, **kwargs):
            if kwargs["schema"]["title"] == "BlindEvaluation":
                raise ProviderError("Unavailable")
            return super().generate(**kwargs)

    data = tmp_path / "data"
    data.mkdir()
    (data / "episode-1.txt").write_text(SCRIPT.format(number=1), encoding="utf-8")
    provider = EvaluationFailureProvider()
    router = LLMRouter(config, tmp_path / "cache", {name: provider for name in config.providers})
    run = PromoPipeline(config, tmp_path / "runs", router).run(data)
    status = json.loads((run / "status.json").read_text())
    assert status["status"] == "complete_with_errors"
    assert status["evaluation_failures"] == 1
    result = json.loads((run / "outputs/series_promos.json").read_text())
    assert result["promos"] == []
    assert result["rejected_candidates"][0]["reasons"] == ["EVALUATION_FAILED"]


def test_empty_candidate_pool_explains_why_evaluation_was_skipped(config, tmp_path, caplog):
    data = tmp_path / "data"
    data.mkdir()
    short_script = (
        SCRIPT.format(number=1).replace("00:40.00", "00:13.00").replace("00:38.00", "00:11.00")
    )
    (data / "episode-1.txt").write_text(short_script, encoding="utf-8")
    provider = ScriptedProvider()
    router = LLMRouter(config, tmp_path / "cache", {name: provider for name in config.providers})
    run = PromoPipeline(config, tmp_path / "runs", router).run(data)
    status = json.loads((run / "status.json").read_text())
    assert status["outcome"] == "no_candidates"
    assert status["evaluated"] == 0
    result = json.loads((run / "outputs/episode_001_promos.json").read_text())
    assert result["construction"]["rejected_windows_by_reason"]["DURATION_OUT_OF_RANGE"] > 0
    assert "no valid candidates" in caplog.text
    assert not any(r["schema"]["title"] == "BlindEvaluation" for r in provider.requests)
