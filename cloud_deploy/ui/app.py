# ruff: noqa: E402
import json
import subprocess
import sys
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from promofinder.config import Config
from promofinder.parser import ScreenplayParser
from ui.model_settings import render_settings
from ui.providers import build_config

HOME = ROOT / "ui"
JOBS = HOME / "jobs"
JOBS.mkdir(exist_ok=True)
READ_ONLY = globals().get("READ_ONLY", False)
st.set_page_config(page_title="PromoFinder", page_icon="ðŸŽ¬", layout="wide")


def read(p, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def launch_job(job, keys):
    log = (job / "worker.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(HOME / "worker.py"), str(job)],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log.close()
    assert process.stdin is not None
    process.stdin.write(json.dumps(keys))
    process.stdin.close()
    st.session_state.process = process
    st.session_state.job = str(job)


@st.dialog("Choose episodes", width="large")
def choose(files):
    rows = []
    for p in files:
        try:
            ep = ScreenplayParser().parse(p)
            rows.append(
                {
                    "Run": str(p) in st.session_state.get("selected", []),
                    "File": str(p),
                    "Episode": ep.episode_number,
                    "Seconds": max(s.end_ms for s in ep.scenes) / 1000,
                    "Events": len(ep.events),
                    "Warnings": "; ".join(ep.warnings),
                }
            )
        except Exception as exc:
            st.error(f"{p.name}: {exc}")
    all_selected = st.checkbox("Select all")
    if all_selected:
        for r in rows:
            r["Run"] = True
    edited = st.data_editor(
        rows,
        disabled=["File", "Episode", "Seconds", "Events", "Warnings"],
        hide_index=True,
        width="stretch",
    )
    if st.button("Apply selection", type="primary"):
        st.session_state.selected = [r["File"] for r in edited if r["Run"]]
        st.rerun()


st.title("ðŸŽ¬ PromoFinder")
st.caption("Script-based promo discovery Â· source evidence, complete ad scripts, and decisions")
files = sorted((ROOT / "data").glob("*.txt"))
if READ_ONLY:
    st.info("Saved results demo · 50 episodes and 200 evaluated candidates. No API calls are made.")
else:
    agents, api_keys = render_settings()
    with st.sidebar:
        uploads = st.file_uploader("Add episode scripts", type="txt", accept_multiple_files=True)
        upload_dir = HOME / "uploads"
        upload_dir.mkdir(exist_ok=True)
        for upload in uploads:
            (upload_dir / Path(upload.name).name).write_bytes(upload.getvalue())
        files = sorted((ROOT / "data").glob("*.txt")) + sorted(upload_dir.glob("*.txt"))
        if st.button("Choose episodesâ€¦"):
            choose(files)
        selected = st.session_state.get("selected", [])
        st.write(f"{len(selected)} episodes selected")
        cap = st.number_input("Candidates per episode", 1, 30, 4)
        delay = st.number_input("Seconds between model calls", 0, 300, 15)
        parse_only = st.checkbox("Parse only â€” no API calls")
        active = st.session_state.get("process")
        running = active is not None and active.poll() is None
        if st.button("Run selected", disabled=not selected or running, type="primary"):
            if not parse_only:
                try:
                    build_config(Config.load(ROOT / "config.toml"), agents, api_keys)
                except ValueError as exc:
                    st.error(str(exc))
                    st.stop()
            job = JOBS / uuid.uuid4().hex[:12]
            (job / "inputs").mkdir(parents=True)
            for i, name in enumerate(selected):
                (job / "inputs" / f"{i:03d}_{Path(name).name}").write_bytes(Path(name).read_bytes())
            (job / "settings.json").write_text(
                json.dumps(
                    {
                        "cap": cap,
                        "delay": delay,
                        "parse_only": parse_only,
                        "selected": selected,
                        "agents": agents,
                    }
                )
            )
            launch_job(job, api_keys if not parse_only else {})
            st.rerun()
        saved_jobs = sorted([p.parent for p in JOBS.glob("*/settings.json")], reverse=True)
        if saved_jobs:
            reconnect = st.selectbox(
                "Reconnect to saved job", saved_jobs, format_func=lambda p: p.name
            )
            saved = read(reconnect / "settings.json", {})
            with st.expander("Saved job model settings"):
                st.json(saved.get("agents", {}))
            if st.button("Reconnect / retry saved job", disabled=running):
                try:
                    if not saved.get("parse_only"):
                        for stage, row in saved.get("agents", {}).items():
                            if agents[stage]["provider"] != row["provider"]:
                                raise ValueError(
                                    "Select the saved providers above before entering keys and reconnecting."
                                )
                        build_config(Config.load(ROOT / "config.toml"), saved["agents"], api_keys)
                    launch_job(reconnect, api_keys if not saved.get("parse_only") else {})
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            st.caption(
                "Reconnect uses saved inputs and model settings, current dashboard keys, and existing batch IDs. Pause does not cancel work already submitted to Anthropic."
            )
        if st.session_state.get("job"):
            job = Path(st.session_state.job)
            if st.button("Pause before next model call"):
                (job / "pause").touch()
            if st.button("Resume"):
                (job / "pause").unlink(missing_ok=True)
        st.caption(
            "New runs reuse validated cached responses. Starting a run may send selected scripts to configured providers."
        )


@st.fragment(run_every=None if READ_ONLY else 3)
def workspace():
    if st.session_state.get("job"):
        st.info(read(Path(st.session_state.job) / "live.json", {"state": "starting"}))
    runs = sorted(
        [p.parent for p in (ROOT / "runs").glob("*/status.json")]
        + [p.parent for p in JOBS.glob("*/runs/*/status.json")],
        reverse=True,
    )
    if not runs:
        st.info("Choose episodes and start a run.")
        return
    run = st.selectbox(
        "Inspect saved run",
        runs,
        format_func=lambda p: f"{p.name} Â· {read(p / 'status.json', {}).get('status', 'unknown')}",
    )
    if not read(run / "config.json", {}).get("pipeline", {}).get("check_spoilers", True):
        st.info("Spoilers not checked. Scores use blind quality only, without a spoiler penalty.")
    st.json(read(run / "status.json", {}), expanded=False)
    episodes = sorted(
        p.stem for p in (run / "parsed").glob("ep_*.json") if not p.stem.endswith("_aliases")
    )
    if not episodes:
        return
    ep = st.selectbox("Inspect episode", episodes)
    st.caption(
        f"Analysis scope: {len(episodes)} episode(s) in this run. Not necessarily the full series."
    )
    pipeline, candidates = st.tabs(["Pipeline & inputs/outputs", "Candidates & full ads"])
    with pipeline:
        names = [
            "Parse",
            "Episode analysis",
            "Series analysis",
            "Discovery",
            "Build candidates",
            "Blind review",
            "Spoiler review",
            "Rank & export",
        ]
        cols = st.columns(4)
        for i, name in enumerate(names):
            if cols[i % 4].button(f"{i + 1} â†’ {name}", key=f"stage{i}"):
                st.session_state.stage = i
        stage = st.session_state.get("stage", 0)
        st.subheader(names[stage])
        paths = [
            run / "parsed" / f"{ep}.json",
            run / "analysis" / f"{ep}.json",
            run / "analysis/series.json",
            run / "discovery" / f"{ep}.json",
            run / "candidates" / f"{ep}.json",
            None,
            None,
            run / "outputs" / f"episode_{int(ep.split('_')[1]):03d}_promos.json",
        ]
        output = read(paths[stage]) if paths[stage] else None
        for i, path in enumerate(paths):
            if path:
                cols[i % 4].caption(
                    f"{names[i]}: " + ("âœ“ Saved" if path.exists() else "â—‹ Pending")
                )
        pool = read(run / "candidates" / f"{ep}.json", [])
        for i, suffix in [(5, "blind"), (6, "spoiler")]:
            count = sum(
                (run / "evaluations" / f"{c['candidate_id']}_{suffix}.json").exists() for c in pool
            )
            cols[i % 4].caption(f"{names[i]}: {count}/{len(pool)} saved")
        if stage in (5, 6):
            suffix = "blind" if stage == 5 else "spoiler"
            cs = read(run / "candidates" / f"{ep}.json", [])
            if cs:
                cid = st.selectbox("Candidate", [c["candidate_id"] for c in cs])
                output = read(run / "evaluations" / f"{cid}_{suffix}.json")
        a, b = st.columns(2)
        with b:
            st.write("Saved output")
            st.json(output if output is not None else {"status": "No output saved"})
        with a:
            st.write("Input / request")
            matches = []
            for p in (ROOT / "runs/cache").glob("*/*.json"):
                record = read(p, {})
                if output is not None and record.get("output") == output:
                    matches.append(record)
            if matches:
                record = matches[0]
                st.caption(
                    "Exact successful cached messages; raw response bytes were not retained."
                )
                st.code(record["request"]["system"], language=None)
                st.code(record["request"]["user"] + record.get("repair_prompt", ""), language=None)
            elif stage == 4:
                st.caption("Python arguments reconstructed from saved artifacts")
                st.json(
                    {
                        k: read(run / folder / f"{ep}.json")
                        for k, folder in [
                            ("episode", "parsed"),
                            ("analysis", "analysis"),
                            ("opportunities", "discovery"),
                        ]
                    }
                )
            elif stage == 0 and output:
                source_files = files + list((run.parent.parent / "inputs").glob("*.txt"))
                source = next((p for p in source_files if p.name == output["source"]["file"]), None)
                if source:
                    st.code(source.read_text(encoding="utf-8"), language=None)
                else:
                    st.info("Original file unavailable in current dataset.")
            elif stage == 7:
                st.caption("Ranking inputs reconstructed from saved artifacts")
                st.json(
                    {
                        "candidates": pool,
                        "evaluations": [read(p) for p in (run / "evaluations").glob("*.json")],
                        "config": read(run / "config.json"),
                    }
                )
            else:
                st.info("No exact request record available. Inspect preceding stage outputs.")
        requests = sorted((run.parent.parent / "requests").glob("*.json"))
        if requests:
            with st.expander("Live request history â€” stage inputs and outputs"):
                selected_request = st.selectbox(
                    "Recorded call",
                    requests,
                    format_func=lambda p: (
                        f"{read(p, {}).get('stage')} Â· {read(p, {}).get('state')} Â· {p.stem}"
                    ),
                )
                st.caption(
                    "Stage-level arguments; the router adds its schema wrapper. Exact successful messages are shown above when available."
                )
                st.json(read(selected_request))
    with candidates:
        cs = read(run / "candidates" / f"{ep}.json", [])
        result = read(run / "outputs" / f"episode_{int(ep.split('_')[1]):03d}_promos.json", {})
        if (
            st.radio("Ranking scope", ["This episode", "Selected series"], horizontal=True)
            == "Selected series"
        ):
            cs = list(
                {
                    c["candidate_id"]: c
                    for path in (run / "candidates").glob("*.json")
                    for c in read(path, [])
                }.values()
            )
            result = read(run / "outputs/series_promos.json", {})
        promos = {p["candidate_id"]: p for p in result.get("promos", [])}
        rejected = {p["candidate_id"]: p for p in result.get("rejected_candidates", [])}
        rows = []
        for c in cs:
            cid = c["candidate_id"]
            p = promos.get(cid, {})
            rows.append(
                {
                    "Candidate": cid,
                    "Rank": p.get("rank"),
                    "Seconds": c["duration_ms"] / 1000,
                    "Score": p.get("scores", {}).get("final"),
                    "Decision": "Selected"
                    if p
                    else ", ".join(rejected.get(cid, {}).get("reasons", ["Pending"])),
                }
            )
        decision = st.selectbox("Filter decision", ["All"] + sorted({r["Decision"] for r in rows}))
        if decision != "All":
            rows = [r for r in rows if r["Decision"] == decision]
        rows.sort(key=lambda r: r["Rank"] or 999)
        st.dataframe(rows, width="stretch", hide_index=True)
        cs = [c for c in cs if c["candidate_id"] in {r["Candidate"] for r in rows}]
        if cs:
            cid = st.selectbox("Open full ad", [c["candidate_id"] for c in cs])
            c = next(c for c in cs if c["candidate_id"] == cid)
            st.subheader(f"Full ad Â· {c['duration_ms'] / 1000}s")
            st.json(c["segments"], expanded=False)
            st.caption(
                "Overlapping events play together. Partial visual descriptions retain full source text; verify against footage."
            )
            for e in c["clip_events"]:
                st.markdown(
                    f"**{e['relative_start_ms'] / 1000:.3f}â€“{e['relative_end_ms'] / 1000:.3f}s Â· {e.get('speaker') or e['type']} Â· {e['ref']}**"
                )
                st.text(e["text"])
            with st.expander("Scores, evidence, weaknesses and rejection reasons"):
                st.json(
                    {
                        "decision": promos.get(cid) or rejected.get(cid),
                        "blind": read(run / "evaluations" / f"{cid}_blind.json"),
                        "spoiler": read(run / "evaluations" / f"{cid}_spoiler.json"),
                    }
                )
            st.download_button(
                "Download complete candidate JSON", json.dumps(c, indent=2), f"{cid}.json"
            )
        with st.expander("Construction-rejected attempts"):
            st.dataframe(read(run / "construction_rejections.json", []), width="stretch")
        report = run / "outputs/report.md"
        if report.exists():
            st.download_button("Download report", report.read_bytes(), "report.md")


workspace()
