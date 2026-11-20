"""Narrative stages. Evidence checks run inside the router's repair/fallback loop."""

import json
import re

from .compact import alias, blind_clip, timeline
from .llm import LLMRouter
from .schemas import (
    BlindEvaluation,
    EpisodeAnalysis,
    EpisodeDiscovery,
    ParsedEpisode,
    PromoCandidate,
    SeriesAnalysis,
    SeriesDiscovery,
    SpoilerEvaluation,
)


def check_refs(refs, allowed: set[str]) -> None:
    # Accept an existing evidence ID followed by whitespace-delimited annotations.
    # Never guess an ID or accept references belonging to another candidate.
    for index, ref in enumerate(refs):
        match = re.fullmatch(r"(EP\d+:E\d+)\s+.+", ref, flags=re.DOTALL)
        if ref not in allowed and match and match.group(1) in allowed:
            refs[index] = match.group(1)
    unknown = set(refs) - allowed
    if unknown:
        raise ValueError(f"Unknown evidence references: {sorted(unknown)}")


def unique_ids(items) -> None:
    ids = [item.id if hasattr(item, "id") else item.local_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate local IDs")


class EpisodeAnalyzer:
    def __init__(self, llm: LLMRouter):
        self.llm = llm

    def analyze(self, episode: ParsedEpisode) -> EpisodeAnalysis:
        allowed = {alias(episode, e.id) for e in episode.events}

        def validate(result):
            if result.episode_id != episode.episode_id:
                raise ValueError("Wrong episode ID")
            unique_ids(result.beats)
            unique_ids(result.revelations)
            for item in [*result.beats, *result.revelations]:
                check_refs(item.event_refs, allowed)
            for item in result.unresolved_questions:
                check_refs(item.introduced_by, allowed)

        return self.llm.call(
            "episode_analysis",
            "Analyze narrative beats, characters, revelations and unresolved questions. "
            "Only event text is on-screen evidence; metadata may describe unseen events. "
            "Use supplied EPxx:Ex aliases. Report uncertainty in the summary. "
            "A revelation's significance is not its spoiler damage.",
            timeline(episode),
            EpisodeAnalysis,
            validate,
        )


class SeriesAnalyzer:
    def __init__(self, llm: LLMRouter):
        self.llm = llm

    def analyze(self, series_id: str, analyses: list[EpisodeAnalysis]) -> SeriesAnalysis:
        beats = {(a.episode_id, b.id) for a in analyses for b in a.beats}
        reveals = {(a.episode_id, r.id) for a in analyses for r in a.revelations}

        def validate(result):
            if result.series_id != series_id:
                raise ValueError("Wrong series ID")
            unique_ids(result.arcs)
            for arc in result.arcs:
                if any((ref.episode_id, ref.beat_id) not in beats for ref in arc.beat_refs):
                    raise ValueError("Unknown beat reference")
            if any((r.episode_id, r.revelation_id) not in reveals for r in result.key_revelations):
                raise ValueError("Unknown revelation reference")

        return self.llm.call(
            "series_analysis",
            "Synthesize premise, narrative arcs and important revelations from episode analyses "
            "in source order. Reference only supplied beat and revelation IDs.",
            json.dumps({"series_id": series_id, "episodes": [a.model_dump() for a in analyses]}),
            SeriesAnalysis,
            validate,
        )


class CandidateDiscoverer:
    def __init__(self, llm: LLMRouter):
        self.llm = llm

    def episode(
        self, episode: ParsedEpisode, analysis: EpisodeAnalysis, series: SeriesAnalysis
    ) -> EpisodeDiscovery:
        allowed = {alias(episode, e.id) for e in episode.events}

        def validate(result):
            unique_ids(result.opportunities)
            for item in result.opportunities:
                if item.episode_id != episode.episode_id:
                    raise ValueError("Wrong episode ID")
                check_refs(item.hook_refs + item.ending_refs + item.context_refs, allowed)

        return self.llm.call(
            "candidate_discovery",
            "Find diverse promo opportunities, preferably 8-12 if supported. Return hook and "
            "unresolved ending anchors, not timestamps. A continuous clip must last 20-60 seconds "
            "with its hook in the first 3 seconds, understandable without prior context. "
            "Check the anchor timestamps: ending.end_ms minus hook.start_ms should be "
            "20,000-60,000ms. Do not propose isolated 2-10 second moments. Both anchors "
            "and required context must fit in one supplied coverage interval. "
            "Ending anchors should precede resolution. Context refs must occur within the clip. "
            "Empty opportunities are valid when evidence is insufficient.",
            timeline(episode)
            + "\nAVAILABLE COVERAGE (milliseconds)="
            + json.dumps([interval.model_dump() for interval in episode.coverage])
            + "\nEPISODE ANALYSIS="
            + analysis.model_dump_json()
            + "\nSERIES CONTEXT="
            + series.model_dump_json(),
            EpisodeDiscovery,
            validate,
        )

    def series(
        self, episodes: list[ParsedEpisode], analyses: list[EpisodeAnalysis], series: SeriesAnalysis
    ) -> SeriesDiscovery:
        # Retrieve full scenes containing referenced arc beats, never highlight text alone.
        by_id = {a.episode_id: a for a in analyses}
        scenes: dict[str, set[str]] = {}
        for arc in series.arcs[:4]:
            for ref in arc.beat_refs[:3]:
                beat = next(b for b in by_id[ref.episode_id].beats if b.id == ref.beat_id)
                episode = next(e for e in episodes if e.episode_id == ref.episode_id)
                for event in episode.events:
                    if alias(episode, event.id) in beat.event_refs:
                        scenes.setdefault(episode.episode_id, set()).add(event.scene_id)
        selected = [
            ep.model_copy(
                update={
                    "scenes": [s for s in ep.scenes if s.id in scenes.get(ep.episode_id, set())],
                    "events": [
                        e for e in ep.events if e.scene_id in scenes.get(ep.episode_id, set())
                    ],
                }
            )
            for ep in episodes
            if ep.episode_id in scenes
        ]
        if len(selected) < 2:
            return SeriesDiscovery(opportunities=[])
        allowed = {ep.episode_id: {alias(ep, e.id) for e in ep.events} for ep in selected}

        def validate(result):
            unique_ids(result.opportunities)
            for item in result.opportunities:
                for group in item.ordered_segment_groups:
                    check_refs(group.anchor_refs, allowed.get(group.episode_id, set()))

        return self.llm.call(
            "candidate_discovery",
            "Propose at most 4 cross-episode promos with 2-3 ordered segment groups. Each group "
            "lists anchors in playback order, first anchor starts the segment, last ends it. "
            "Total duration 20-60 seconds. First group opens on a hook, last ends unresolved. "
            "Only use supplied source scenes. Return no opportunities if none works.",
            series.model_dump_json() + "\n" + "\n".join(timeline(ep) for ep in selected),
            SeriesDiscovery,
            validate,
        )


class PromoEvaluator:
    def __init__(self, llm: LLMRouter):
        self.llm = llm

    def blind(self, candidate: PromoCandidate) -> BlindEvaluation:
        allowed = {e.ref for e in candidate.clip_events}

        def validate(result):
            if result.candidate_id != candidate.candidate_id:
                raise ValueError("Wrong candidate ID")
            check_refs(result.hook.evidence_refs, allowed)
            check_refs(result.cliffhanger.evidence_refs, allowed)

        return self.llm.call(
            "blind_evaluation",
            "Act as a first-time viewer who knows only this clip. Rate hook, unresolved "
            "cliffhanger, standalone clarity and emotion from 0 to 10. Cite hook evidence "
            "starting within playback 0-3000ms and ending evidence within the final 3000ms. "
            "Do not assume omitted plot or use external knowledge. Report missing context "
            "and weaknesses. An intense but resolved ending is not a cliffhanger.",
            blind_clip(candidate),
            BlindEvaluation,
            validate,
        )

    def spoiler(
        self, candidate: PromoCandidate, series: SeriesAnalysis, analyses: list[EpisodeAnalysis]
    ) -> SpoilerEvaluation:
        allowed = {e.ref for e in candidate.clip_events}

        def validate(result):
            if result.candidate_id != candidate.candidate_id:
                raise ValueError("Wrong candidate ID")
            for item in result.revealed_information:
                check_refs(item.event_refs, allowed)

        revelations = [
            {"episode_id": a.episode_id, "revelations": [r.model_dump() for r in a.revelations]}
            for a in analyses
        ]
        return self.llm.call(
            "spoiler_analysis",
            "Assess actual spoiler damage against the supplied story. Significance of a reveal "
            "is distinct from damage: exposing a conflict can create curiosity. Penalize "
            "disclosed resolutions that remove a reason to watch. Cite only visible clip "
            "events for revealed_information; do not assert unseen spoilers.",
            blind_clip(candidate)
            + "\nSERIES="
            + series.model_dump_json()
            + "\nREVELATIONS IN EPISODE ORDER="
            + json.dumps(revelations),
            SpoilerEvaluation,
            validate,
        )
