"""Render selected PromoFinder clips as source-backed, human-readable ad scripts."""

import argparse
import json
import re
from pathlib import Path


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Cannot read required run artifact: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in run artifact: {path}: {error}") from error


def load_source_events(parsed_dir: Path) -> dict[str, tuple[str, dict]]:
    source_events = {}
    episode_files = sorted(
        path
        for path in parsed_dir.glob("ep_*.json")
        if not path.stem.endswith("_aliases")
    )
    if not episode_files:
        raise ValueError(f"No parsed episode files found in {parsed_dir}")

    for episode_path in episode_files:
        episode = read_json(episode_path)
        aliases_path = episode_path.with_name(f"{episode_path.stem}_aliases.json")
        aliases = read_json(aliases_path)
        events_by_id = {event["id"]: event for event in episode["events"]}
        for alias, canonical_id in aliases.items():
            episode_id, event_id = canonical_id.split(":", 1)
            event = events_by_id.get(event_id)
            if event is None:
                raise ValueError(
                    f"Alias {alias} in {aliases_path} points to missing event {canonical_id}"
                )
            source_events[alias] = (episode_id, event)
    return source_events


def load_candidates(candidates_dir: Path) -> dict[str, dict]:
    candidates = {}
    for path in sorted(candidates_dir.glob("*.json")):
        for candidate in read_json(path):
            candidate_id = candidate.get("candidate_id")
            if not candidate_id:
                raise ValueError(f"Candidate without candidate_id in {path}")
            if candidate_id in candidates:
                raise ValueError(f"Duplicate candidate_id {candidate_id} in {candidates_dir}")
            candidates[candidate_id] = candidate
    if not candidates:
        raise ValueError(f"No saved candidates found in {candidates_dir}")
    return candidates


def timecode(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().replace("|", r"\|")


def promo_heading(promo: dict, fallback: str) -> str:
    rank = promo.get("rank", "?")
    score = promo.get("scores", {}).get("final")
    score_text = f"{score:.2f}/10" if isinstance(score, (int, float)) else "unscored"
    promo_id = promo.get("promo_id", promo.get("candidate_id", "promo"))
    return f"### {rank}. {fallback} — {promo_id} — {score_text}"


def source_label(episode_id: str) -> str:
    match = re.fullmatch(r"ep_(\d+)", episode_id)
    return f"Episode {int(match.group(1))}" if match else episode_id


def render_promo(promo: dict, candidate: dict, source_events: dict[str, tuple[str, dict]]) -> list[str]:
    promo_id = promo.get("candidate_id")
    if candidate.get("candidate_id") != promo_id:
        raise ValueError(f"Candidate mismatch for promo {promo_id}")
    if candidate.get("segments") != promo.get("segments"):
        raise ValueError(f"Saved source segments do not match promo {promo_id}")

    segments = promo.get("segments", [])
    duration_ms = sum(segment["end_ms"] - segment["start_ms"] for segment in segments)
    if not segments or duration_ms != promo.get("duration_ms"):
        raise ValueError(f"Invalid or inconsistent duration for promo {promo_id}")

    lines = [
        promo_heading(
            promo,
            " + ".join(source_label(segment["episode_id"]) for segment in segments),
        ),
        "",
        f"- **Duration:** {timecode(duration_ms)}",
        f"- **Why it works:** {clean_text(promo.get('why_it_works', 'Not provided.'))}",
    ]
    for index, segment in enumerate(segments, start=1):
        lines.append(
            f"- **Source {index}:** {source_label(segment['episode_id'])}, "
            f"{timecode(segment['start_ms'])}–{timecode(segment['end_ms'])}"
        )

    hook = promo.get("hook", {}).get("description")
    ending = promo.get("cliffhanger", {}).get("description")
    if hook:
        lines.extend(["", f"**Hook:** {clean_text(hook)}"])
    if ending:
        lines.append(f"**Ending:** {clean_text(ending)}")

    lines.extend(["", "**Ad script / source cut list**"])
    clip_events = candidate.get("clip_events", [])
    if not clip_events:
        raise ValueError(f"Candidate {promo_id} has no saved source events")

    ordered_events = sorted(
        clip_events,
        key=lambda event: (
            event["relative_start_ms"],
            event["relative_end_ms"],
            event["ref"],
        ),
    )
    for clip_event in ordered_events:
        ref = clip_event["ref"]
        source = source_events.get(ref)
        if source is None:
            raise ValueError(f"Candidate {promo_id} references missing source event {ref}")
        source_episode, event = source
        segment_index = clip_event["segment_index"]
        if not 0 <= segment_index < len(segments):
            raise ValueError(f"Candidate {promo_id} has invalid segment index for {ref}")
        if source_episode != segments[segment_index]["episode_id"]:
            raise ValueError(f"Source event {ref} belongs to the wrong segment in {promo_id}")
        if (
            clip_event["text"] != event["text"]
            or clip_event["type"] != event["type"]
            or clip_event["speaker"] != event.get("speaker")
        ):
            raise ValueError(f"Candidate {promo_id} does not match source event {ref}")
        start = clip_event["relative_start_ms"]
        end = clip_event["relative_end_ms"]
        if not 0 <= start < end <= duration_ms:
            raise ValueError(f"Candidate {promo_id} has invalid playback timing for {ref}")

        kind = event["type"]
        if kind == "dialogue":
            label = f"DIALOGUE — {event.get('speaker') or 'SPEAKER'}"
        elif kind in {"camera", "action"}:
            label = "VISUAL"
        else:
            label = "SOUND"
        description = clean_text(event["text"])
        lines.append(
            f"- **{timecode(start)}–{timecode(end)} · {label}:** {description} "
            f"`[{ref}]`"
        )

    warnings = promo.get("warnings", [])
    if warnings:
        lines.extend(["", "**Editor review:** " + "; ".join(clean_text(w) for w in warnings)])
    lines.extend(["", "---", ""])
    return lines


def render_run(run_dir: Path, scope: str = "both", output_path: Path | None = None) -> Path:
    run_dir = run_dir.resolve()
    outputs_dir = run_dir / "outputs"
    candidates = load_candidates(run_dir / "candidates")
    source_events = load_source_events(run_dir / "parsed")
    output_path = output_path or outputs_dir / "human_readable_ads.md"

    lines = [
        "# PromoFinder — Human-readable ad scripts",
        "",
        "Each cut list uses the selected promo's original screenplay events and source "
        "timecodes. Visual and dialogue cues can overlap. These are source-based promo "
        "scripts, not newly written voiceovers or finished video edits. Verify timecodes "
        "against footage before production.",
        "",
    ]
    total_promos = 0

    if scope in {"series", "both"}:
        series_result = read_json(outputs_dir / "series_promos.json")
        promos = series_result.get("promos", [])
        lines.extend(["## Series-selected ads", ""])
        if not promos:
            lines.extend(["No series promos passed selection.", ""])
        for promo in promos:
            candidate = candidates.get(promo.get("candidate_id"))
            if candidate is None:
                raise ValueError(f"No saved candidate for promo {promo.get('candidate_id')}")
            lines.extend(render_promo(promo, candidate, source_events))
            total_promos += 1

    if scope in {"episode", "both"}:
        episode_paths = sorted(
            outputs_dir.glob("episode_*_promos.json"),
            key=lambda path: int(re.search(r"episode_(\d+)_promos", path.stem).group(1)),
        )
        if not episode_paths:
            raise ValueError(f"No episode promo exports found in {outputs_dir}")
        lines.extend(["## Episode-selected ads", ""])
        for path in episode_paths:
            result = read_json(path)
            episode_id = result.get("scope", {}).get("episode_id", path.stem)
            promos = result.get("promos", [])
            lines.extend([f"## {source_label(episode_id)}", ""])
            if not promos:
                lines.extend(["No episode promos passed selection.", ""])
            for promo in promos:
                candidate = candidates.get(promo.get("candidate_id"))
                if candidate is None:
                    raise ValueError(f"No saved candidate for promo {promo.get('candidate_id')}")
                lines.extend(render_promo(promo, candidate, source_events))
                total_promos += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {total_promos} human-readable ads to {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn saved PromoFinder run outputs into source-backed ad scripts."
    )
    parser.add_argument("run_dir", type=Path, help="Run directory containing parsed/, candidates/, outputs/")
    parser.add_argument(
        "--scope",
        choices=("series", "episode", "both"),
        default="both",
        help="Export series selections, episode selections, or both (default: both).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Markdown destination (default: <run_dir>/outputs/human_readable_ads.md).",
    )
    args = parser.parse_args()
    try:
        render_run(args.run_dir, scope=args.scope, output_path=args.output)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
