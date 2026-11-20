# ruff: noqa: E402
"""Offline recovery of a completed blind batch. Never makes network requests."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from promofinder.candidates import CandidateBuilder
from promofinder.config import Config
from promofinder.llm import LLMRouter, ProviderResponse
from promofinder.pipeline import PromoPipeline
from promofinder.schemas import ParsedEpisode, PromoCandidate, SeriesAnalysis
from promofinder.stages import PromoEvaluator
from promofinder.storage import write_json


def recover(job, destination):
    source = sorted((job / "runs").iterdir())[-1]
    config = Config.model_validate_json((source / "config.json").read_text(encoding="utf-8"))
    if config.pipeline.check_spoilers:
        raise ValueError("This offline recovery supports blind-only saved runs.")
    candidates = {
        c.candidate_id: c
        for p in (source / "candidates").glob("*.json")
        for c in [
            PromoCandidate.model_validate(v) for v in json.loads(p.read_text(encoding="utf-8"))
        ]
    }
    responses = {}
    for path in (job / "batches/blind_evaluation").glob("*.json"):
        batch = json.loads(path.read_text(encoding="utf-8"))
        for key, result in batch.get("results", {}).items():
            if result["type"] == "succeeded":
                params = batch["requests"][key]
                responses[(params["system"], params["messages"][0]["content"])] = result["message"]

    class OfflineProvider:
        def generate(self, *, system, user, schema, settings):
            message = responses.get((system, user))
            if message is None:
                raise RuntimeError(
                    "Unmatched: "
                    + user[-500:]
                    + " SYSTEM MATCH="
                    + str(any(k[0] == system for k in responses))
                    + " USER MATCH="
                    + str(any(k[1] == user for k in responses))
                )
            if message.get("stop_reason") != "end_turn":
                raise RuntimeError("Saved response is incomplete")
            return ProviderResponse(
                "".join(b["text"] for b in message["content"] if b["type"] == "text"),
                message.get("usage", {}),
            )

    router = LLMRouter(
        config, destination / "cache", {name: OfflineProvider() for name in config.providers}
    )
    evaluator = PromoEvaluator(router)
    evaluated = [(c, evaluator.blind(c), None) for c in candidates.values()]
    episodes = [
        ParsedEpisode.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted((source / "parsed").glob("ep_*.json"))
        if not p.stem.endswith("_aliases")
    ]
    series = SeriesAnalysis.model_validate_json(
        (source / "analysis/series.json").read_text(encoding="utf-8")
    )
    pipeline = PromoPipeline(config, destination, router)
    pipeline.run_dir = destination
    builder = CandidateBuilder(episodes)
    builder.rejections = json.loads(
        (source / "construction_rejections.json").read_text(encoding="utf-8")
    )
    diagnostics = json.loads((source / "construction_diagnostics.json").read_text(encoding="utf-8"))
    for candidate, blind, _ in evaluated:
        write_json(destination / "evaluations" / f"{candidate.candidate_id}_blind.json", blind)
    write_json(destination / "evaluation_failures.json", [])
    pipeline.finish(episodes, candidates, builder, evaluated, [], series, diagnostics, False)
    print(
        json.dumps({"validated": len(evaluated), "source": str(source), "output": str(destination)})
    )


if __name__ == "__main__":
    recover(Path(sys.argv[1]), Path(sys.argv[2]))
