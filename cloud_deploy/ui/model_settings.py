"""Session-only agent settings. No credentials are serialized to disk."""

import streamlit as st

from promofinder.config import STAGES
from promofinder.llm import ProviderError
from ui.providers import CATALOG, LABELS, DashboardProvider, preset


def apply_preset():
    for stage, row in preset(st.session_state["agent_preset"]).items():
        if st.session_state.get(f"provider_{stage}") != row["provider"]:
            st.session_state[f"key_{stage}"] = ""
        st.session_state[f"provider_{stage}"] = row["provider"]
        st.session_state[f"model_{stage}"] = row["model"]


def provider_changed(stage):
    provider = st.session_state[f"provider_{stage}"]
    st.session_state[f"model_{stage}"] = CATALOG[provider]["model"]
    st.session_state[f"key_{stage}"] = ""


def clear_keys():
    for name in list(st.session_state):
        if name.startswith(("key_", "shared_key_")):
            st.session_state[name] = ""


def render_settings():
    st.session_state.setdefault("agent_preset", "Haiku recommended")
    defaults = preset("Haiku recommended")
    for stage, row in defaults.items():
        st.session_state.setdefault(f"provider_{stage}", row["provider"])
        st.session_state.setdefault(f"model_{stage}", row["model"])
    routes, keys = {}, {}
    with st.expander("Agent settings & API keys", expanded=True):
        check_spoilers = st.checkbox(
            "Check spoilers (extra API calls)", value=False, key="check_spoilers"
        )
        st.caption("When off, rankings use blind quality only; spoiler safety is not checked.")
        st.selectbox(
            "Agent preset",
            ["Haiku recommended", "Free-tier", "Mixed"],
            key="agent_preset",
            on_change=apply_preset,
        )
        st.caption(
            "Recommended starting setup: Haiku for each agent. Free-tier choices depend on your provider account quota; paid billing is not disabled by this preset."
        )
        st.caption(
            "Keys come only from this dashboard. They stay in this browser session and worker memory, not .env or saved files. Reloading may require re-entry."
        )
        for provider in CATALOG:
            st.text_input(
                f"Shared {provider} API key", type="password", key=f"shared_key_{provider}"
            )
        st.button("Clear dashboard keys", on_click=clear_keys, key="clear_keys")
        for stage in STAGES:
            with st.container(border=True):
                st.markdown(f"**{LABELS[stage]}**")
                provider = st.selectbox(
                    "Provider",
                    list(CATALOG),
                    key=f"provider_{stage}",
                    on_change=provider_changed,
                    args=(stage,),
                )
                model = st.text_input("Model ID", key=f"model_{stage}")
                shared = st.checkbox(
                    "Use shared provider key", value=True, key=f"use_shared_{stage}"
                )
                own = st.text_input(
                    "Agent-specific API key", type="password", key=f"key_{stage}", disabled=shared
                )
                key = st.session_state.get(f"shared_key_{provider}", "") if shared else own
                routes[stage] = {"provider": provider, "model": model.strip()}
                if stage in (
                    "episode_analysis",
                    "candidate_discovery",
                    "blind_evaluation",
                    "spoiler_analysis",
                ):
                    eligible = (
                        provider == "Anthropic"
                        and "haiku" in model.lower()
                        and (stage != "spoiler_analysis" or check_spoilers)
                    )
                    batch = st.checkbox(
                        "Use Haiku batch API (50% discount; may take up to 24 hours)",
                        key=f"batch_{stage}",
                        disabled=not eligible,
                    )
                    routes[stage]["batch"] = bool(batch and eligible)
                if stage == "spoiler_analysis":
                    routes[stage]["enabled"] = check_spoilers
                keys[stage] = key.strip()
                st.caption("Key entered" if key.strip() else "Key required for model calls")
                if st.button(
                    "Check key (no generation)", key=f"check_{stage}", disabled=not key.strip()
                ):
                    try:
                        DashboardProvider(provider, model.strip(), key.strip()).check_key()
                        st.success(
                            "Connection accepted. Model access and available credits are checked when a generation runs."
                        )
                    except ProviderError as exc:
                        st.error(str(exc))
        st.caption(
            "Batch mode groups requests by agent, including blind and optional spoiler judging. Series analysis uses standard calls. Anthropic credits apply to the organization that issued the key."
        )
    return routes, keys
