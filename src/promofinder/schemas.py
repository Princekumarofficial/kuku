"""Small strict contracts shared by the parser, stages and provider adapters."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Score = Annotated[float, Field(ge=0, le=10)]
Refs = Annotated[list[str], Field(min_length=1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Interval(Contract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Interval must have positive duration")
        return self


class Scene(Interval):
    id: str
    heading: str


class Event(Interval):
    id: str
    scene_id: str
    type: Literal["camera", "action", "dialogue", "sound"]
    text: str
    speaker: str | None = None
    delivery: str | None = None
    source_lines: list[int]


class Source(Contract):
    file: str
    sha256: str
    complete: bool


class ParsedEpisode(Contract):
    schema_version: str = "1.0"
    episode_id: str
    episode_number: int
    metadata: dict[str, str]
    source: Source
    scenes: list[Scene]
    events: list[Event]
    coverage: list[Interval]
    warnings: list[str]


class Beat(Contract):
    id: str
    kind: str
    event_refs: Refs
    description: str
    intensity_0_10: Score


class Revelation(Contract):
    id: str
    event_refs: Refs
    claim: str
    spoiler_significance_0_10: Score
    creates_questions: list[str]


class Question(Contract):
    question: str
    introduced_by: Refs


class Character(Contract):
    name: str
    role: str


class EpisodeAnalysis(Contract):
    episode_id: str
    summary: str
    characters: list[Character]
    beats: list[Beat]
    revelations: list[Revelation]
    unresolved_questions: list[Question]


class BeatRef(Contract):
    episode_id: str
    beat_id: str


class Arc(Contract):
    id: str
    description: str
    beat_refs: list[BeatRef]


class SeriesRevelation(Contract):
    episode_id: str
    revelation_id: str
    prerequisite_context: list[str]


class SeriesAnalysis(Contract):
    series_id: str
    premise: str
    arcs: list[Arc]
    key_revelations: list[SeriesRevelation]


class EpisodeOpportunity(Contract):
    scope: Literal["episode"]
    local_id: str
    episode_id: str
    concept: str
    hook_refs: Refs
    ending_refs: Refs
    context_refs: list[str]
    why_promising: str


class SegmentGroup(Contract):
    episode_id: str
    anchor_refs: Refs


class SeriesOpportunity(Contract):
    scope: Literal["series"]
    local_id: str
    concept: str
    ordered_segment_groups: Annotated[list[SegmentGroup], Field(min_length=2, max_length=3)]


class EpisodeDiscovery(Contract):
    opportunities: list[EpisodeOpportunity]


class SeriesDiscovery(Contract):
    opportunities: list[SeriesOpportunity]


class Segment(Interval):
    episode_id: str


class ClipEvent(Contract):
    ref: str
    segment_index: int
    relative_start_ms: int
    relative_end_ms: int
    type: Literal["camera", "action", "dialogue", "sound"]
    speaker: str | None
    text: str


class PromoCandidate(Contract):
    candidate_id: str
    scope: Literal["episode", "series"]
    segments: list[Segment]
    duration_ms: int
    origin: str
    clip_events: list[ClipEvent]


class EvidenceRating(Contract):
    score_0_10: Score
    evidence_refs: Refs
    reason: str


class CliffhangerRating(EvidenceRating):
    unresolved_at_end: bool


class StandaloneRating(Contract):
    score_0_10: Score
    missing_context: list[str]


class EmotionRating(Contract):
    score_0_10: Score
    reason: str


class BlindEvaluation(Contract):
    candidate_id: str
    hook: EvidenceRating
    cliffhanger: CliffhangerRating
    standalone: StandaloneRating
    emotion: EmotionRating
    weaknesses: list[str]
    one_line_why_it_works: str


class RevealedInformation(Contract):
    description: str
    event_refs: Refs


class SpoilerEvaluation(Contract):
    candidate_id: str
    spoiler_significance_0_10: Score
    spoiler_damage_0_10: Score
    revealed_information: list[RevealedInformation]
    resolves_central_mystery: bool
    new_questions_created: list[str]
    reason: str
