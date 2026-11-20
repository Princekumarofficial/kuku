import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STAGES = (
    "episode_analysis",
    "series_analysis",
    "candidate_discovery",
    "blind_evaluation",
    "spoiler_analysis",
)


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderConfig(SettingsModel):
    base_url: str
    api_key_env: str
    model: str
    structured_output: Literal["json_schema", "json_object"] = "json_schema"


class LLMConfig(SettingsModel):
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=8192, gt=0)
    timeout_seconds: float = Field(default=120, gt=0)
    attempts_per_provider: int = Field(default=2, ge=1, le=3)


class PipelineConfig(SettingsModel):
    series_id: str = "series_001"
    max_candidates_per_episode: int = Field(default=12, ge=1)
    top_k_episode: int = Field(default=5, ge=1)
    top_k_series: int = Field(default=10, ge=1)
    cross_episode: bool = False
    max_cross_candidates: int = Field(default=8, ge=1)


class Config(SettingsModel):
    providers: dict[str, ProviderConfig]
    routes: dict[str, list[str]]
    llm: LLMConfig = Field(default_factory=LLMConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    @model_validator(mode="after")
    def check_routes(self):
        if set(self.routes) != set(STAGES):
            raise ValueError(f"Routes must define exactly: {STAGES}")
        for stage, route in self.routes.items():
            if not route or any(name not in self.providers for name in route):
                raise ValueError(f"Empty route or unknown provider for {stage}")
        return self

    @classmethod
    def load(cls, path: Path) -> "Config":
        with path.open("rb") as file:
            return cls.model_validate(tomllib.load(file))
