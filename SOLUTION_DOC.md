# PromoFinder — Screenplay-to-Promo Discovery

**Applicant:** [Your name] · **Roll no.:** [Your roll number] · **Branch:** [Your branch]  
**Chosen problem:** Problem 1 — discover and rank promotional clips from episode scripts.

**Prototype:** [Add a public GitHub repository or deployed-app URL]  
**Raw outputs:** `ui/jobs/7245de292306/runs/20261120T214934Z_recovered/`  
**Human-readable ad scripts:** `ui/jobs/7245de292306/runs/20261120T214934Z_recovered/outputs/human_readable_ads.md`  
**Walkthrough:** [Optional: add a public video URL]

## Why this problem

I chose this problem because it interested me: I was curious whether AI could find compelling promo moments in a story, and the episode-script data was already available to experiment with. I scoped the prototype to recommendations and an editor-ready, source-backed cut list—not video editing. It targets 20–60 second promos with an early hook, an unresolved ending and enough context to work for a first-time viewer.

I assume scripts contain usable scene/event timestamps. These are screenplay times, not frame-accurate video timecodes. The recorded run used all 50 supplied episodes; cross-episode discovery and spoiler review were disabled.

## How it works

1. **Parse (Python):** Read each script, validate scenes and timed events, preserve overlapping dialogue/action/camera cues, and assign stable IDs and source hashes. The parsed timeline is the source of truth.
2. **Understand and discover (LLM):** Analyze episodes for story beats, then synthesize a series view. Use that context and the source timeline to suggest promo hooks and unresolved endings. Outputs use structured schemas and must cite known event IDs.
3. **Build candidates (Python):** Expand suggested anchors into source windows. Enforce supported boundaries, duration, dialogue integrity and source references; reject invalid windows rather than inventing footage.
4. **Evaluate and rank:** A blind LLM reviewer sees only the candidate and its events—not series notes or the discovery rationale—and rates hook, cliffhanger, standalone clarity and emotion. A separate story-aware spoiler review is available. Python then applies hard gates, calculates the weighted score, suppresses clips with at least 80% time overlap, and exports episode/series selections plus reports and cut lists.

The split is deliberate: models judge narrative and creative quality; code owns provenance, timing, validation and ranking. At production volume, per-episode work can be bounded and cached, with asynchronous provider batches where available; series synthesis remains a shared step. Cost and latency depend on model, candidate cap and provider limits, so they should be measured per run. Editors still verify footage, context, tone and final cuts.

## Evaluation, evidence and limits

The saved run processed **50 episodes** and **200 candidates**. All 200 received blind-review outputs, with **0 recorded evaluation failures**; the series export selected **10 promos**. Recovery reused cached results and made **0 new API requests**. The resulting scripts include source timecodes, dialogue and visual/sound cues.

These are model evaluations, **not** human ratings or a labeled quality benchmark. Deterministic checks independently enforce source/evidence matching, a hook in the opening 3 seconds, ending evidence in the final 3 seconds, an unresolved ending, minimum ratings and overlap suppression. On this run, invalid proposed windows—including partial dialogue and out-of-range durations—were rejected. The repository also has offline parser, construction, provider, ranking and pipeline tests; these check software behavior, not creative quality. No human review or audience-performance test has been completed.

The saved run was blind-only: spoiler checks were off. Rankings can be subjective, clips can lack character context, source timecodes can drift from video, and hard gates may reject usable moments. Camera/action text can describe more than the clipped portion; verify against footage. Full spoiler assessment requires enabling it with the relevant story context.

## AI tools

I used **ChatGPT to help build the app**. The recorded run used **Anthropic Claude Haiku 5.5** for structured story analysis, opportunity discovery and blind evaluations. One observed model error was proposing clip windows that cut through dialogue or fell outside the target duration; Python rejected those windows before evaluation.

## Run and inspect

From the repository root on Windows, run `.\ui\start.ps1` to launch the dashboard. Select episodes and configure a provider in the app; parse-only mode runs without API calls. To regenerate the readable ads for the saved run:

```powershell
.\.venv\Scripts\python.exe scripts\render_ads.py ui\jobs\7245de292306\runs\20261120T214934Z_recovered
```

See [README.md](README.md), [PLAN.md](PLAN.md) and [ui/README.md](ui/README.md) for implementation and operating details.
