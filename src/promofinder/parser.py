"""Parse the supplied screenplay format without interpreting or inventing footage."""

import hashlib
import re
from decimal import Decimal
from pathlib import Path

from .schemas import Event, Interval, ParsedEpisode, Scene, Source

STAMP = r"\d{1,3}:\d{2}(?::\d{2})?(?:\.\d{1,3})?"
TIMING = re.compile(rf"Start:\s*\(?({STAMP})\)?\s*\|\s*End:\s*\(?({STAMP})\)?")
HEADER = re.compile(r"^\*\*(.+?):\*\*$")
SCENE = re.compile(r"^SCENE\s+(\d+)\s*$")


def timestamp_ms(value: str) -> int:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Invalid timestamp: {value}")
    numbers = [Decimal(part) for part in parts]
    if any(n < 0 for n in numbers) or any(n >= 60 for n in numbers[1:]):
        raise ValueError(f"Invalid timestamp: {value}")
    seconds = numbers[-1] + numbers[-2] * 60
    if len(numbers) == 3:
        seconds += numbers[0] * 3600
    millis = seconds * 1000
    if millis != int(millis):
        raise ValueError("Timestamp precision exceeds milliseconds")
    return int(millis)


def merged_coverage(events: list[Event]) -> list[Interval]:
    result: list[Interval] = []
    for event in sorted(events, key=lambda e: (e.start_ms, e.end_ms)):
        if result and event.start_ms <= result[-1].end_ms:
            result[-1].end_ms = max(result[-1].end_ms, event.end_ms)
        else:
            result.append(Interval(start_ms=event.start_ms, end_ms=event.end_ms))
    return result


class ScreenplayParser:
    def parse(self, path: Path) -> ParsedEpisode:
        raw = path.read_bytes()
        lines = raw.decode("utf-8-sig").splitlines()
        number_match = re.search(r"EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)", "\n".join(lines), re.I)
        if not number_match:
            raise ValueError(f"{path.name}: missing episode number")
        number = int(number_match[1])
        metadata = {}
        for key, label in (
            ("logline", "EPISODE LOGLINE"),
            ("continuity_notes", "CONTINUITY NOTES"),
        ):
            metadata[key] = next(
                (line.split(":", 1)[1].strip() for line in lines if line.startswith(label + ":")),
                "",
            )
        scenes, events, warnings = [], [], []
        scene: Scene | None = None
        unsafe_scenes: set[str] = set()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            match = SCENE.fullmatch(line)
            if match:
                scene_id = f"sc_{int(match[1]):03d}"
                fields = {}
                j = i + 1
                while (
                    j < len(lines)
                    and not HEADER.match(lines[j].strip())
                    and not SCENE.match(lines[j].strip())
                ):
                    if lines[j].lower().startswith("scene ") and ":" in lines[j]:
                        key, value = lines[j].split(":", 1)
                        fields[key.lower()] = value.strip()
                    j += 1
                try:
                    scene = Scene(
                        id=scene_id,
                        heading=fields["scene heading"],
                        start_ms=timestamp_ms(fields["scene start"]),
                        end_ms=timestamp_ms(fields["scene end"]),
                    )
                    if any(s.id == scene.id for s in scenes):
                        raise ValueError(f"Duplicate scene ID {scene.id}")
                    if scenes and scene.start_ms < scenes[-1].end_ms:
                        raise ValueError("Scenes overlap or are out of order")
                    scenes.append(scene)
                except (KeyError, ValueError) as error:
                    raise ValueError(f"{path.name}:{i + 1}: invalid scene: {error}") from error
                i += 1
                continue
            header = HEADER.fullmatch(line)
            if header:
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if (
                        HEADER.fullmatch(next_line)
                        or SCENE.fullmatch(next_line)
                        or next_line.startswith("--- SCRIPT")
                    ):
                        break
                    j += 1
                try:
                    if scene is None:
                        raise ValueError("Event outside a scene")
                    timing_line = i + 1
                    while timing_line < j and not lines[timing_line].strip():
                        timing_line += 1
                    timing = (
                        TIMING.fullmatch(lines[timing_line].strip()) if timing_line < j else None
                    )
                    if not timing:
                        raise ValueError("Missing or malformed event interval")
                    start, end = timestamp_ms(timing[1]), timestamp_ms(timing[2])
                    if not scene.start_ms <= start < end <= scene.end_ms:
                        raise ValueError("Event interval outside scene")
                    text = "\n".join(lines[timing_line + 1 : j]).strip()
                    if not text:
                        raise ValueError("Empty event text")
                    label = header[1]
                    kind = {
                        "CAMERA": "camera",
                        "ACTION": "action",
                        "SOUND FX": "sound",
                        "SOUND": "sound",
                        "MUSIC": "sound",
                    }.get(label, "dialogue")
                    speaker, delivery = None, None
                    if kind == "dialogue":
                        parts = re.fullmatch(r"(.+?)(?:\s*\((.*)\))?", label)
                        speaker, delivery = parts[1], parts[2]
                    last = j
                    while last > timing_line + 1 and not lines[last - 1].strip():
                        last -= 1
                    events.append(
                        Event(
                            id=f"e_{len(events) + 1:03d}",
                            scene_id=scene.id,
                            type=kind,
                            start_ms=start,
                            end_ms=end,
                            text=text,
                            speaker=speaker,
                            delivery=delivery,
                            source_lines=[i + 1, last],
                        )
                    )
                except ValueError as error:
                    warnings.append(f"Line {i + 1}: {error}")
                    if scene:
                        unsafe_scenes.add(scene.id)
                i = j
                continue
            # A malformed header or orphan timestamp means source text was not parsed safely.
            if line.startswith("**") or line.startswith("Start:"):
                warnings.append(f"Line {i + 1}: unrecognized event block")
                if scene:
                    unsafe_scenes.add(scene.id)
            i += 1
        if not scenes or not events:
            raise ValueError(f"{path.name}: no usable scenes/events")
        coverage = merged_coverage([e for e in events if e.scene_id not in unsafe_scenes])
        complete = not warnings
        for item in scenes:
            if not any(c.start_ms <= item.start_ms and c.end_ms >= item.end_ms for c in coverage):
                warnings.append(f"{item.id}: incomplete source coverage; gaps are unavailable")
                complete = False
        if "--- SCRIPT END ---" not in lines:
            warnings.append("Missing SCRIPT END marker")
            complete = False
        return ParsedEpisode(
            episode_id=f"ep_{number:03d}",
            episode_number=number,
            metadata=metadata,
            source=Source(
                file=path.name, sha256=hashlib.sha256(raw).hexdigest(), complete=complete
            ),
            scenes=scenes,
            events=events,
            coverage=coverage,
            warnings=warnings,
        )
