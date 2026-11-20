"""Deterministic hard gates, scoring, overlap suppression and output formatting."""

from .candidates import CandidateBuilder, CandidateError
from .schemas import BlindEvaluation, PromoCandidate, SpoilerEvaluation


class PromoRanker:
    def __init__(self, builder: CandidateBuilder):
        self.builder = builder

    def assess(
        self, candidate: PromoCandidate, blind: BlindEvaluation, spoiler: SpoilerEvaluation
    ) -> tuple[dict | None, list[str]]:
        reasons = []
        try:
            rebuilt = self.builder.build(candidate.segments, candidate.origin)
            if rebuilt != candidate:
                reasons.append("CANDIDATE_SOURCE_MISMATCH")
        except CandidateError as error:
            reasons.append(str(error))
        if (
            blind.candidate_id != candidate.candidate_id
            or spoiler.candidate_id != candidate.candidate_id
        ):
            reasons.append("EVALUATION_ID_MISMATCH")
        evidence = {e.ref for e in candidate.clip_events}
        if not set(blind.hook.evidence_refs + blind.cliffhanger.evidence_refs) <= evidence:
            reasons.append("UNKNOWN_EVIDENCE")
        if any(not set(r.event_refs) <= evidence for r in spoiler.revealed_information):
            reasons.append("UNKNOWN_SPOILER_EVIDENCE")
        hook_events = [e for e in candidate.clip_events if e.ref in blind.hook.evidence_refs]
        if not any(e.relative_start_ms <= 3000 for e in hook_events):
            reasons.append("HOOK_OUTSIDE_OPENING")
        ending_events = [
            e for e in candidate.clip_events if e.ref in blind.cliffhanger.evidence_refs
        ]
        if not any(e.relative_end_ms >= candidate.duration_ms - 3000 for e in ending_events):
            reasons.append("ENDING_EVIDENCE_NOT_AT_END")
        if not blind.cliffhanger.unresolved_at_end:
            reasons.append("ENDING_RESOLVED")
        if blind.hook.score_0_10 < 5:
            reasons.append("WEAK_HOOK")
        if blind.cliffhanger.score_0_10 < 5:
            reasons.append("WEAK_CLIFFHANGER")
        if blind.standalone.score_0_10 < 5:
            reasons.append("LOW_STANDALONE_CLARITY")
        if spoiler.resolves_central_mystery:
            reasons.append("CENTRAL_MYSTERY_RESOLVED")
        if reasons:
            return None, reasons
        base = (
            0.30 * blind.hook.score_0_10
            + 0.30 * blind.cliffhanger.score_0_10
            + 0.25 * blind.standalone.score_0_10
            + 0.15 * blind.emotion.score_0_10
        )
        score = round(max(0, min(10, base - 0.15 * spoiler.spoiler_damage_0_10)), 4)
        return {
            "candidate_id": candidate.candidate_id,
            "segments": [s.model_dump() for s in candidate.segments],
            "duration_ms": candidate.duration_ms,
            "hook": {"description": blind.hook.reason, "evidence_refs": blind.hook.evidence_refs},
            "cliffhanger": {
                "description": blind.cliffhanger.reason,
                "evidence_refs": blind.cliffhanger.evidence_refs,
            },
            "why_it_works": blind.one_line_why_it_works,
            "scores": {
                "hook": blind.hook.score_0_10,
                "cliffhanger": blind.cliffhanger.score_0_10,
                "standalone": blind.standalone.score_0_10,
                "emotion": blind.emotion.score_0_10,
                "spoiler_significance": spoiler.spoiler_significance_0_10,
                "spoiler_damage": spoiler.spoiler_damage_0_10,
                "final": score,
            },
            "warnings": [
                *blind.weaknesses,
                *blind.standalone.missing_context,
                "Script timestamps; verify against video before editing.",
            ],
        }, []

    @staticmethod
    def overlap(left: dict, right: dict) -> float:
        intersection = sum(
            max(0, min(a["end_ms"], b["end_ms"]) - max(a["start_ms"], b["start_ms"]))
            for a in left["segments"]
            for b in right["segments"]
            if a["episode_id"] == b["episode_id"]
        )
        return intersection / min(left["duration_ms"], right["duration_ms"])

    def select(self, promos: list[dict], top_k: int) -> tuple[list[dict], list[dict]]:
        selected, excluded = [], []
        for promo in sorted(promos, key=lambda p: (-p["scores"]["final"], p["candidate_id"])):
            if any(self.overlap(promo, prior) >= 0.8 for prior in selected):
                excluded.append(
                    {"candidate_id": promo["candidate_id"], "reasons": ["HEAVY_TIME_OVERLAP"]}
                )
            elif len(selected) >= top_k:
                excluded.append(
                    {"candidate_id": promo["candidate_id"], "reasons": ["OUTSIDE_TOP_K"]}
                )
            else:
                rank = len(selected) + 1
                selected.append({**promo, "rank": rank, "promo_id": f"p_{rank:03d}"})
        return selected, excluded
