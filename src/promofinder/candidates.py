"""Python owns source lookup, boundaries, coverage, extraction and clip identity."""

import hashlib
import itertools
import json
from collections import deque

from .compact import alias
from .schemas import (
    ClipEvent,
    EpisodeAnalysis,
    EpisodeDiscovery,
    EpisodeOpportunity,
    ParsedEpisode,
    PromoCandidate,
    Segment,
    SeriesDiscovery,
)


class CandidateError(ValueError):
    pass


class CandidateBuilder:
    MIN_DURATION = 20_000
    MAX_DURATION = 60_000
    HOOK_WINDOW = 3_000

    def __init__(self, episodes: list[ParsedEpisode]):
        self.episodes = {e.episode_id: e for e in episodes}
        self.events = {alias(ep, e.id): (ep, e) for ep in episodes for e in ep.events}
        self.rejections: list[dict] = []

    def build(
        self, segments: list[Segment], origin: str, hook_refs: list[str] | None = None
    ) -> PromoCandidate:
        if not 1 <= len(segments) <= 3:
            raise CandidateError("SEGMENT_COUNT")
        duration = sum(s.end_ms - s.start_ms for s in segments)
        if not self.MIN_DURATION <= duration <= self.MAX_DURATION:
            raise CandidateError("DURATION_OUT_OF_RANGE")
        clips, offset = [], 0
        previous: dict[str, int] = {}
        for index, segment in enumerate(segments):
            episode = self.episodes.get(segment.episode_id)
            if episode is None:
                raise CandidateError("UNKNOWN_EPISODE")
            # Episode order may change across montage cuts; within an episode never replay time.
            if segment.start_ms < previous.get(segment.episode_id, 0):
                raise CandidateError("REPEATED_OR_REVERSED_FOOTAGE")
            previous[segment.episode_id] = segment.end_ms
            if not any(
                c.start_ms <= segment.start_ms and segment.end_ms <= c.end_ms
                for c in episode.coverage
            ):
                raise CandidateError("MISSING_SOURCE_COVERAGE")
            boundaries = self.boundaries(episode)
            if segment.start_ms not in boundaries or segment.end_ms not in boundaries:
                raise CandidateError("UNSUPPORTED_BOUNDARY")
            for event in sorted(episode.events, key=lambda e: (e.start_ms, e.end_ms, e.id)):
                if event.start_ms >= segment.end_ms or event.end_ms <= segment.start_ms:
                    continue
                partial = event.start_ms < segment.start_ms or event.end_ms > segment.end_ms
                if event.type == "dialogue" and partial:
                    raise CandidateError("PARTIAL_DIALOGUE")
                clips.append(
                    ClipEvent(
                        ref=alias(episode, event.id),
                        segment_index=index,
                        relative_start_ms=offset
                        + max(event.start_ms, segment.start_ms)
                        - segment.start_ms,
                        relative_end_ms=offset
                        + min(event.end_ms, segment.end_ms)
                        - segment.start_ms,
                        type=event.type,
                        speaker=event.speaker,
                        text=event.text,
                    )
                )
            offset += segment.end_ms - segment.start_ms
        if hook_refs:
            # Anchor must actually begin in the opening, not just overlap it from an earlier time.
            first = segments[0]
            if not any(
                ref in self.events
                and self.events[ref][0].episode_id == first.episode_id
                and first.start_ms
                <= self.events[ref][1].start_ms
                <= first.start_ms + self.HOOK_WINDOW
                for ref in hook_refs
            ):
                raise CandidateError("HOOK_OUTSIDE_OPENING")
        signature = json.dumps([s.model_dump() for s in segments], sort_keys=True)
        identity = hashlib.sha256(signature.encode()).hexdigest()[:16]
        return PromoCandidate(
            candidate_id=f"c_{identity}",
            scope="episode" if len({s.episode_id for s in segments}) == 1 else "series",
            segments=segments,
            duration_ms=duration,
            origin=origin,
            clip_events=clips,
        )

    @staticmethod
    def boundaries(episode: ParsedEpisode) -> set[int]:
        return {
            t for item in [*episode.events, *episode.scenes] for t in (item.start_ms, item.end_ms)
        }

    def episode(
        self,
        episode: ParsedEpisode,
        discovery: EpisodeDiscovery,
        analysis: EpisodeAnalysis,
        limit: int,
    ) -> list[PromoCandidate]:
        boundaries = sorted(self.boundaries(episode))
        opportunities = list(discovery.opportunities)
        # Consider every evidence anchor before capping the valid pool. A beat can span
        # source gaps, and its earliest event may be unusable even when later ones work.
        for beat in sorted(analysis.beats, key=lambda b: -b.intensity_0_10):
            if beat.intensity_0_10 < 7:
                continue
            hooks = [self.events[ref][1] for ref in beat.event_refs]
            ending_refs = [
                alias(episode, e.id)
                for e in episode.events
                if any(
                    hook.start_ms + self.MIN_DURATION - self.HOOK_WINDOW
                    <= e.end_ms
                    <= hook.start_ms + self.MAX_DURATION
                    for hook in hooks
                )
            ]
            if ending_refs:
                opportunities.append(
                    EpisodeOpportunity(
                        scope="episode",
                        local_id="recovery_" + beat.id,
                        episode_id=episode.episode_id,
                        concept=beat.description,
                        hook_refs=beat.event_refs,
                        ending_refs=ending_refs,
                        context_refs=[],
                        why_promising="High-intensity beat recovery",
                    )
                )
        pools = []
        for opportunity in opportunities:
            pool: dict[str, PromoCandidate] = {}
            for hook_ref in opportunity.hook_refs:
                hook = self.events[hook_ref][1]
                starts = [
                    b for b in boundaries if hook.start_ms - self.HOOK_WINDOW <= b <= hook.start_ms
                ]
                for ending_ref in opportunity.ending_refs:
                    ending = self.events[ending_ref][1]
                    # Stop at the anchor's end. No arbitrary padding into its resolution.
                    for start in starts:
                        if ending.end_ms <= start:
                            continue
                        segment = Segment(
                            episode_id=episode.episode_id, start_ms=start, end_ms=ending.end_ms
                        )
                        try:
                            candidate = self.build([segment], opportunity.local_id, [hook_ref])
                            if not all(
                                start <= self.events[r][1].start_ms
                                and self.events[r][1].end_ms <= ending.end_ms
                                for r in opportunity.context_refs
                            ):
                                raise CandidateError("REQUIRED_CONTEXT_OUTSIDE_CLIP")
                            pool[candidate.candidate_id] = candidate
                        except CandidateError as error:
                            self.rejections.append(
                                {
                                    "episode_id": episode.episode_id,
                                    "origin": opportunity.local_id,
                                    "segments": [segment.model_dump()],
                                    "reasons": [str(error)],
                                }
                            )
            # Prefer a hook close to the start, then a medium duration; evaluator decides quality.
            pools.append(
                deque(
                    sorted(
                        pool.values(), key=lambda c: (abs(c.duration_ms - 35000), c.candidate_id)
                    )
                )
            )
        # Round-robin preserves opportunity diversity before applying the cost cap.
        selected = {}
        while any(pools) and len(selected) < limit:
            for pool in pools:
                if pool:
                    candidate = pool.popleft()
                    selected.setdefault(candidate.candidate_id, candidate)
                    if len(selected) == limit:
                        break
        return list(selected.values())

    def series(self, discovery: SeriesDiscovery, limit: int) -> list[PromoCandidate]:
        selected = {}
        for opportunity in discovery.opportunities:
            choices = []
            for group in opportunity.ordered_segment_groups:
                episode = self.episodes[group.episode_id]
                anchors = [self.events[r][1] for r in group.anchor_refs]
                first, last = min(e.start_ms for e in anchors), max(e.end_ms for e in anchors)
                starts = sorted(b for b in self.boundaries(episode) if first - 3000 <= b <= first)
                choices.append(
                    [
                        Segment(episode_id=episode.episode_id, start_ms=s, end_ms=last)
                        for s in starts[-3:]
                    ]
                )
            for segments in itertools.product(*choices):
                try:
                    candidate = self.build(
                        list(segments),
                        opportunity.local_id,
                        opportunity.ordered_segment_groups[0].anchor_refs,
                    )
                    selected.setdefault(candidate.candidate_id, candidate)
                except CandidateError as error:
                    self.rejections.append(
                        {
                            "origin": opportunity.local_id,
                            "segments": [s.model_dump() for s in segments],
                            "reasons": [str(error)],
                        }
                    )
                if len(selected) >= limit:
                    return list(selected.values())
        return list(selected.values())
