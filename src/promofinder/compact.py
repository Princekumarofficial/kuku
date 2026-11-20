from .schemas import ParsedEpisode, PromoCandidate


def alias(episode: ParsedEpisode, event_id: str) -> str:
    return f"EP{episode.episode_number:02d}:E{int(event_id.split('_')[1])}"


def alias_map(episode: ParsedEpisode) -> dict[str, str]:
    return {alias(episode, e.id): f"{episode.episode_id}:{e.id}" for e in episode.events}


def timeline(episode: ParsedEpisode) -> str:
    lines = [
        f"EP={episode.episode_id}; timestamps are integer milliseconds",
        f"SOURCE_SHA256={episode.source.sha256}",
        f"METADATA ONLY (not on-screen evidence): {episode.metadata}",
        f"SOURCE WARNINGS={episode.warnings}",
    ]
    for scene in episode.scenes:
        lines.append(f"SCENE {scene.id} {scene.start_ms}-{scene.end_ms} {scene.heading}")
        for e in episode.events:
            if e.scene_id == scene.id:
                label = e.speaker or e.type.upper()
                delivery = f" [{e.delivery}]" if e.delivery else ""
                lines.append(
                    f"{alias(episode, e.id)} {e.start_ms}-{e.end_ms} {label}{delivery}: {e.text}"
                )
    return "\n".join(lines)


def blind_clip(candidate: PromoCandidate) -> str:
    # Deliberately excludes origins, concepts, metadata, analyses and absolute source times.
    lines = [f"CANDIDATE={candidate.candidate_id} DURATION_MS={candidate.duration_ms}"]
    for index in range(len(candidate.segments)):
        lines.append(f"SEGMENT {index + 1} (playback order)")
        for e in candidate.clip_events:
            if e.segment_index == index:
                lines.append(
                    f"{e.ref} {e.relative_start_ms}-{e.relative_end_ms} "
                    f"{e.speaker or e.type.upper()}: {e.text}"
                )
    return "\n".join(lines)
