# PromoFinder — source-backed promo discovery

**PRINCE KUMAR · Roll no. 230051013 · Engineering Physics**  
**Problem 1:** Discover and rank promotional clips from episode scripts.  
**Prototype:** https://diningfee.iiti.ac.in/kuku/  
**Walkthrough:** https://canva.link/znz61mwqzt93y03  
**Raw outputs:** [Add the publicly accessible folder URL before submission.]

## Problem choice and scope

I chose promo discovery because it combines creative judgment with constraints that can be checked independently: a compelling story moment still needs usable boundaries and traceable source dialogue. The supplied scripts let me test the entire workflow on real inputs. My prototype recommends cuts and exports full scripts with timestamps; it does not generate dialogue or edit video.

The target is a 20–60-second promo with a hook in the first three seconds, an unresolved ending, and enough immediate context for a new viewer. These are editorial design choices, not proven predictors of conversion. Script timestamps are assumed usable but require verification against footage. The full run used all 50 episodes, four candidates per episode, Haiku 5.5, batch processing, and no cross-episode generation or spoiler checks. Series-wide ranking still compares candidates from every episode.

## Pipeline: inputs, decisions, and outputs

```text
Selected episode scripts
  → 1. Parse → 2. Episode analysis → 3. Series analysis
  → 4. Opportunity discovery → 5. Clip construction
  → 6. Blind quality review → 7. Optional spoiler review
  → 8. Rank and export → Editor verifies cuts against video
```

| Step / owner | What enters, what happens, what comes out |
|---|---|
| **1. Parse · Python** | Timestamped scripts → parse scenes and overlapping dialogue/action/camera events → source-backed events with IDs, millisecond timestamps, hashes, and parsing warnings. |
| **2. Episode analysis · LLM** | Each parsed timeline → identify characters, beats, revelations, and unresolved questions → one structured analysis per episode. Independent requests use a batch. |
| **3. Series analysis · LLM** | All selected episode analyses → connect arcs and important revelations → shared series context. Runs once after episode analysis, using the standard API. |
| **4. Opportunity discovery · LLM** | Episode timeline + episode analysis + series context → propose hooks, endings, and required context by event ID → opportunities, not finished clips. Batched across episodes; optional cross-episode discovery is off in this run. |
| **5. Clip construction · Python** | Opportunities + high-intensity fallback beats → enumerate source windows; check duration, boundaries, whole dialogue events, and context inclusion → up to four candidates per episode plus construction-rejection records. No model call. |
| **6. Blind review · LLM** | Each candidate's full extracted clip, without story summaries or discovery rationale → judge hook, cliffhanger, clarity, and emotion → scores, cited evidence, and weaknesses. Batched across all episodes. |
| **7. Spoiler review · LLM, optional** | Candidate + series context + episode revelations → assess revealed resolutions and spoiler damage → separate spoiler assessment. Supports batching; disabled in the measured run and labeled not checked. |
| **8. Rank/export · Python** | Validated evaluations → apply quality gates, weighted scoring, and overlap suppression → episode shortlists, top 10 series promos, full cut scripts, and rejection reasons. No model call. |

The blind-only score weights hook/cliffhanger/clarity/emotion at **30/30/25/15%**. Hook, cliffhanger, and clarity must each reach 5; the ending must be unresolved, with evidence in the final three seconds. Candidates overlapping at least 80% of the shorter clip are deduplicated. Series ranking uses all accepted candidates, not only episode winners. These thresholds and weights are editorial heuristics.

The dashboard exposes episode selection, interchangeable agent models, session-only API keys, batch options, stage inputs/outputs, and full candidate scripts. An editor verifies context, tone, visual continuity, and actual footage before using a recommendation.

## Evidence: full-input run and honest failures

| Measured outcome | Result |
|---|---:|
| Episodes processed / candidates evaluated | 50 / 200 |
| Evaluation failures after offline recovery | 0 |
| Candidates rejected by quality gates | 157 |
| Additional overlap exclusions | 3 |
| Episode-level selections / series shortlist | 40 / 10 |
| Episodes with no selected promo | 22 |

Successful validation is not proof of creative quality. There is no labeled human benchmark or audience-performance experiment. The principal measured weakness is **standalone clarity**: 147 candidates failed its threshold, and 112 failed only that rule. One Episode 13 candidate scored 7 for hook, 8 for cliffhanger, 9 for emotion, but 4 for clarity; it was rejected. The judge sometimes demands relationship history that could instead be useful curiosity. Next evaluation should distinguish inability to understand the immediate conflict from intentionally unanswered questions, using editor-rated examples.

Four candidates limit search recall. Cliffhanger gates exclude some romantic or resolved-payoff promos. Whole dialogue events do not guarantee complete conversations; clipped camera/action events can yield awkward shot fragments. Spoiler safety was not evaluated. A parser assumption equating timestamp gaps with missing source proved too restrictive for complete scripts: its construction rejection was disabled, but parser warnings and discovery-prompt coverage restrictions still need reconciliation. The arbitrary preference for clips near 35 seconds was also removed; the recovered run preserves its original candidates rather than claiming a fresh run under every later change.

## Scale, cost, and repeatability

Independent requests are batched by agent; successful validated responses are cached by request content and settings. Model/provider settings are interchangeable through the dashboard. Series analysis remains a shared dependency. Approximately **$0.33** is attributable to recorded completed requests in this run, including recorded repairs, using published Haiku rates; this is a token-based estimate, not a billing receipt, and excludes an outstanding repair batch. The series prompt exceeded 100K tokens and incurred higher input pricing.

Across a catalogue, isolate jobs by show, retain immutable source versions, queue bounded batches, and move results/cache to durable shared storage. Long shows need hierarchical series summaries and targeted context retrieval; sending every episode summary in one request will not scale indefinitely. Add spend limits, reliable job recovery, authentication, and human quality calibration before production. These are extensions, not capabilities claimed by this local prototype.

## AI assistance and reproducibility

I used ChatGPT/Codex to help design, implement, debug, and review the system; Haiku 5.5 performed the recorded narrative tasks. AI got evidence formatting wrong: 40 of 200 blind responses appended timestamps or descriptions to event IDs. Strict validation caused slow individual repair batches. I added narrow normalization that accepts only IDs present in that candidate, retained rejection of unknown references, and recovered all 200 saved reviews without new API requests. Six episode analyses also needed ID-format repairs. Later batch handling groups repairs instead of waiting on each separately.

The offline suite passed **51 tests**, with **3 coverage-dependent tests explicitly skipped**. It checks software contracts, not audience appeal. Run instructions are in `README.md` and `ui/README.md`; the dashboard can process new scripts and swap models for an interview demonstration.

**Raw evidence to share:** `ui/jobs/7245de292306/batches/` contains original provider responses; preserve these unedited. Include the original run `runs/20261120T211212022336Z/` and the separately labeled recovered run `runs/20261120T214934Z_recovered/` under that job. The latter contains normalized evaluations, rankings, and `recovery.json`; it must not be presented as untouched model output.
