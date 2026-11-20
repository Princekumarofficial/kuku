from collections import Counter
from pathlib import Path


def write_report(
    path: Path,
    results: list[dict],
    parser_warnings: list[dict],
    construction_rejections: list[dict],
    evaluation_failures: list[dict],
) -> None:
    lines = [
        "# PromoFinder report",
        "",
        "Timestamps are from screenplays, not aligned video cuts. Scores are model judgments, "
        "not measured audience performance. Human review has not been performed.",
        "",
    ]
    for result in results:
        scope = result["scope"].get("episode_id", "Full series")
        lines.extend([f"## {scope}", ""])
        if not result["promos"]:
            lines.extend(["No candidates passed selection. See rejection reasons below.", ""])
        for promo in result["promos"]:
            segments = "; ".join(
                f"{s['episode_id']} {s['start_ms'] / 1000:.3f}–{s['end_ms'] / 1000:.3f}s"
                for s in promo["segments"]
            )
            lines.extend(
                [
                    f"{promo['rank']}. **{promo['scores']['final']:.2f}/10** — {segments}",
                    f"   {promo['why_it_works']}",
                    f"   Hook: {promo['hook']['description']}",
                    f"   Ending: {promo['cliffhanger']['description']}",
                    f"   Warnings: {'; '.join(promo['warnings'])}",
                    "",
                ]
            )
        counts = Counter(
            reason for item in result["rejected_candidates"] for reason in item["reasons"]
        )
        if counts:
            lines.extend(
                ["Selection exclusions: " + ", ".join(f"{k}: {v}" for k, v in counts.items()), ""]
            )
            for item in result["rejected_candidates"][:5]:
                lines.append(f"- `{item['candidate_id']}`: {', '.join(item['reasons'])}")
            lines.append("")
    lines.extend(["## Source and construction failures", ""])
    for warning in parser_warnings:
        lines.append(f"- {warning['episode_id']}: {'; '.join(warning['warnings'])}")
    counts = Counter(reason for item in construction_rejections for reason in item["reasons"])
    lines.extend(
        [
            "",
            "Construction exclusions: "
            + (", ".join(f"{k}: {v}" for k, v in counts.items()) or "none"),
            "",
            f"Evaluation failures: {len(evaluation_failures)} (see evaluation_failures.json).",
            "",
            "## Known limits and editor review",
            "",
            "Selection uses a bounded candidate pool and time-overlap suppression. Similar concepts "
            "in different scenes can still repeat. Long visual descriptions may describe action "
            "outside a clipped portion of a shot; verify the footage. Gap checks are conservative "
            "and may reject intentional silent intervals. These are known risks, not manually "
            "confirmed failures in this run.",
            "",
            "Review opening three seconds, final unresolved beat, missing relationship context, "
            "and actual spoiler damage before posting. Ranking order is a suggested review order; "
            "no posting schedule is generated.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
