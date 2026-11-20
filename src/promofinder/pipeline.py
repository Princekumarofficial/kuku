"""The entire workflow is intentionally a plain, sequential run method."""

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .candidates import CandidateBuilder
from .compact import alias_map
from .config import Config
from .llm import LLMError, LLMRouter
from .parser import ScreenplayParser
from .rank import PromoRanker
from .report import write_report
from .stages import CandidateDiscoverer, EpisodeAnalyzer, PromoEvaluator, SeriesAnalyzer
from .storage import write_json

log = logging.getLogger(__name__)


class PromoPipeline:
    def __init__(self, config: Config, output_dir: Path, llm: LLMRouter | None = None):
        self.config = config
        self.output_dir = output_dir
        self.llm = llm or LLMRouter(config, output_dir / "cache")
        self.parser = ScreenplayParser()
        self.episode_analyzer = EpisodeAnalyzer(self.llm)
        self.series_analyzer = SeriesAnalyzer(self.llm)
        self.discoverer = CandidateDiscoverer(self.llm)
        self.evaluator = PromoEvaluator(self.llm)
        self.run_dir: Path | None = None
        self.stage = "starting"

    def _stage(self, name: str) -> None:
        self.stage = name
        log.info("Stage: %s", name)
        write_json(self.run_dir / "status.json", {"status": "running", "stage": name})

    def run(self, data_dir: Path, *, parse_only: bool = False, limit: int | None = None) -> Path:
        self.run_dir = self.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.run_dir.mkdir(parents=True)
        write_json(self.run_dir / "config.json", self.config)
        try:
            return self._run(data_dir, parse_only, limit)
        except Exception as error:
            write_json(
                self.run_dir / "status.json",
                {"status": "failed", "stage": self.stage, "error": str(error)},
            )
            raise

    def _run(self, data_dir: Path, parse_only: bool, limit: int | None) -> Path:
        self._stage("parse")
        files = sorted(data_dir.glob("*.txt"))
        if not files:
            raise ValueError(f"No .txt screenplay files in {data_dir}")
        episodes = [self.parser.parse(path) for path in files]
        episodes.sort(key=lambda e: e.episode_number)
        if len({e.episode_id for e in episodes}) != len(episodes):
            raise ValueError("Duplicate episode numbers in input files")
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            episodes = episodes[:limit]
        for episode in episodes:
            write_json(self.run_dir / "parsed" / f"{episode.episode_id}.json", episode)
            write_json(
                self.run_dir / "parsed" / f"{episode.episode_id}_aliases.json", alias_map(episode)
            )
        write_json(
            self.run_dir / "parsed" / "series_manifest.json",
            {
                "series_id": self.config.pipeline.series_id,
                "partial_series": len(episodes) < len(files),
                "episodes": [
                    {
                        "episode_id": e.episode_id,
                        "number": e.episode_number,
                        "file": f"{e.episode_id}.json",
                        "source_sha256": e.source.sha256,
                        "duration_ms": max(s.end_ms for s in e.scenes),
                        "source_complete": e.source.complete,
                    }
                    for e in episodes
                ],
            },
        )
        if parse_only:
            write_json(
                self.run_dir / "status.json", {"status": "parsed", "episodes": len(episodes)}
            )
            return self.run_dir

        self._stage("episode_analysis")
        self.llm.prepare(
            "episode_analysis",
            [
                lambda episode=episode: self.episode_analyzer.analyze(episode)
                for episode in episodes
            ],
        )
        analyses = []
        for episode in episodes:
            analysis = self.episode_analyzer.analyze(episode)
            analyses.append(analysis)
            write_json(self.run_dir / "analysis" / f"{episode.episode_id}.json", analysis)

        self._stage("series_analysis")
        series = self.series_analyzer.analyze(self.config.pipeline.series_id, analyses)
        write_json(self.run_dir / "analysis" / "series.json", series)

        self._stage("candidate_discovery_and_construction")
        builder = CandidateBuilder(episodes)
        candidates = {}
        construction_diagnostics = {}
        self.llm.prepare(
            "candidate_discovery",
            [
                lambda episode=episode, analysis=analysis: self.discoverer.episode(
                    episode, analysis, series
                )
                for episode, analysis in zip(episodes, analyses)
            ],
        )
        for episode, analysis in zip(episodes, analyses):
            discovery = self.discoverer.episode(episode, analysis, series)
            write_json(self.run_dir / "discovery" / f"{episode.episode_id}.json", discovery)
            rejection_start = len(builder.rejections)
            pool = builder.episode(
                episode, discovery, analysis, self.config.pipeline.max_candidates_per_episode
            )
            reasons = dict(
                Counter(
                    reason
                    for rejection in builder.rejections[rejection_start:]
                    for reason in rejection["reasons"]
                )
            )
            construction_diagnostics[episode.episode_id] = {
                "opportunities": len(discovery.opportunities),
                "candidates": len(pool),
                "rejected_windows_by_reason": reasons,
            }
            log.info(
                "%s: %d opportunities, %d candidates; rejected windows: %s",
                episode.episode_id,
                len(discovery.opportunities),
                len(pool),
                reasons,
            )
            if not pool:
                log.warning(
                    "%s: no valid candidates; no clips from this episode will be evaluated",
                    episode.episode_id,
                )
            write_json(self.run_dir / "candidates" / f"{episode.episode_id}.json", pool)
            candidates.update({c.candidate_id: c for c in pool})
        cross_candidates = []
        if self.config.pipeline.cross_episode and len(episodes) > 1:
            discovery = self.discoverer.series(episodes, analyses, series)
            write_json(self.run_dir / "discovery" / "series.json", discovery)
            cross_candidates = builder.series(discovery, self.config.pipeline.max_cross_candidates)
            candidates.update({c.candidate_id: c for c in cross_candidates})
        write_json(self.run_dir / "candidates" / "series.json", cross_candidates)
        write_json(self.run_dir / "construction_rejections.json", builder.rejections)
        write_json(self.run_dir / "construction_diagnostics.json", construction_diagnostics)

        self._stage("blind_evaluation_and_spoiler_analysis")
        self.llm.prepare(
            "blind_evaluation",
            [
                lambda candidate=candidate: self.evaluator.blind(candidate)
                for candidate in candidates.values()
            ],
        )
        if self.config.pipeline.check_spoilers:
            self.llm.prepare(
                "spoiler_analysis",
                [
                    lambda candidate=candidate: self.evaluator.spoiler(candidate, series, analyses)
                    for candidate in candidates.values()
                ],
            )
        evaluations, failures = [], []
        for index, candidate in enumerate(candidates.values(), start=1):
            progress = f"Candidate {index}/{len(candidates)} {candidate.candidate_id}"
            started = time.monotonic()
            try:
                log.info("%s: blind evaluation", progress)
                blind = self.evaluator.blind(candidate)
                write_json(
                    self.run_dir / "evaluations" / f"{candidate.candidate_id}_blind.json", blind
                )
                spoiler = None
                if self.config.pipeline.check_spoilers:
                    log.info("%s: spoiler analysis", progress)
                    spoiler = self.evaluator.spoiler(candidate, series, analyses)
                    write_json(
                        self.run_dir / "evaluations" / f"{candidate.candidate_id}_spoiler.json",
                        spoiler,
                    )
                evaluations.append((candidate, blind, spoiler))
                log.info("%s: evaluated in %.1fs", progress, time.monotonic() - started)
            except LLMError as error:
                failures.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "error": str(error),
                        "reasons": ["EVALUATION_FAILED"],
                    }
                )
                log.warning(
                    "%s: skipped after %.1fs; all evaluation providers failed",
                    progress,
                    time.monotonic() - started,
                )
        log.info(
            "Evaluation finished: %d/%d evaluated, %d skipped",
            len(evaluations),
            len(candidates),
            len(failures),
        )
        write_json(self.run_dir / "evaluation_failures.json", failures)

        return self.finish(
            episodes,
            candidates,
            builder,
            evaluations,
            failures,
            series,
            construction_diagnostics,
            len(episodes) < len(files),
        )

    def finish(
        self,
        episodes,
        candidates,
        builder,
        evaluations,
        failures,
        series,
        construction_diagnostics,
        partial_series,
    ):
        self._stage("rank_and_export")
        ranker = PromoRanker(builder)
        accepted, rejected = [], list(failures)
        for candidate, blind, spoiler in evaluations:
            promo, reasons = ranker.assess(candidate, blind, spoiler)
            if promo:
                accepted.append(promo)
            else:
                rejected.append({"candidate_id": candidate.candidate_id, "reasons": reasons})
        outputs = self.run_dir / "outputs"
        results = []
        for episode in episodes:
            episode_ids = {
                c.candidate_id
                for c in candidates.values()
                if all(s.episode_id == episode.episode_id for s in c.segments)
            }
            selected, excluded = ranker.select(
                [p for p in accepted if p["candidate_id"] in episode_ids],
                self.config.pipeline.top_k_episode,
            )
            result = {
                "schema_version": "1.0",
                "series_id": series.series_id,
                "scope": {"type": "episode", "episode_id": episode.episode_id},
                "promos": selected,
                "construction": construction_diagnostics[episode.episode_id],
                "rejected_candidates": [r for r in rejected if r["candidate_id"] in episode_ids]
                + excluded,
            }
            results.append(result)
            write_json(outputs / f"episode_{episode.episode_number:03d}_promos.json", result)
        selected, excluded = ranker.select(accepted, self.config.pipeline.top_k_series)
        result = {
            "schema_version": "1.0",
            "series_id": series.series_id,
            "scope": {"type": "series"},
            "partial_series": partial_series,
            "promos": selected,
            "rejected_candidates": rejected + excluded,
        }
        results.insert(0, result)
        outcome = (
            "no_candidates"
            if not candidates
            else "no_accepted_promos"
            if not selected
            else "promos_created"
        )
        log.info(
            "Result: %s (%d evaluated, %d series promos)", outcome, len(evaluations), len(selected)
        )
        write_json(outputs / "series_promos.json", result)
        write_report(
            outputs / "report.md",
            results,
            [{"episode_id": e.episode_id, "warnings": e.warnings} for e in episodes if e.warnings],
            builder.rejections,
            failures,
        )
        write_json(
            self.run_dir / "status.json",
            {
                "status": "complete_with_errors" if failures else "complete",
                "episodes": len(episodes),
                "candidates": len(candidates),
                "evaluated": len(evaluations),
                "evaluation_failures": len(failures),
                "outcome": outcome,
                "series_promos": len(selected),
            },
        )
        return self.run_dir
