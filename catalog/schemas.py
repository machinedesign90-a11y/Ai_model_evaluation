"""Typed inputs and outputs of the catalog tools. These are the shapes the model sees."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

SourceType = Literal["official_docs", "provider_api", "scraped", "manual_review"]
VerificationStatus = Literal["verified", "unverified"]

Slug = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=200)]
CapabilityName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

MAX_SEARCH_LIMIT = 25
MAX_REQUIREMENTS = 10
MAX_COMPARE_CAPABILITIES = 40


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(_Out):
    """One recorded capability, always with where it came from and whether a person confirmed it."""

    name: str
    value: JsonValue
    source_type: SourceType | None = Field(description="null when the source record was removed")
    source_url: str | None
    verification_status: VerificationStatus
    last_verified_at: datetime | None


class ModelSummary(_Out):
    slug: str
    name: str
    provider: str
    modality: str


# --- search_models -----------------------------------------------------------------------------


class CapabilityRequirement(BaseModel):
    name: CapabilityName = Field(description="Capability name, e.g. 'output_languages' or 'audio_generation'.")
    value: JsonValue = Field(
        description=(
            'Required value, matched by JSON containment: "hi" matches a recorded ["hi", "en"], '
            'true matches true, {"a": 1} matches {"a": 1, "b": 2}.'
        )
    )


class SearchInput(BaseModel):
    modality: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=50)] | None = None
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list, max_length=MAX_REQUIREMENTS)
    limit: int = 10

    @field_validator("modality")
    @classmethod
    def _empty_modality_is_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, v: int) -> int:
        return max(1, min(v, MAX_SEARCH_LIMIT))


class SearchMatch(ModelSummary):
    facts: list[Fact] = Field(description="The recorded facts that satisfy the requirements.")


class UnconfirmedCandidate(ModelSummary):
    facts: list[Fact] = Field(description="Requirements this model does satisfy.")
    missing_capabilities: list[str] = Field(description="Required capabilities with no recorded fact: not recorded.")


class SearchResult(_Out):
    searched_modality: str | None
    total_models_in_modality: int
    matches: list[SearchMatch]
    total_matches: int
    unconfirmed: list[UnconfirmedCandidate]
    total_unconfirmed: int
    unknown_capability_names: list[str] = Field(
        description="Requested names that are not defined capabilities; the catalog uses different names."
    )
    known_capability_names: list[str] = Field(
        description="Every defined capability name; filled only when some requested name is unknown."
    )
    available_modalities: list[str]


# --- get_model ---------------------------------------------------------------------------------


class ModelDetail(ModelSummary):
    display_description: str | None = Field(
        description="Display text only, not a sourced fact. Never make claims from it."
    )
    facts: list[Fact] = Field(
        description="Every recorded fact, including pricing and api_available. Anything absent is not recorded."
    )


class GetModelResult(_Out):
    status: Literal["found", "not_found"]
    model: ModelDetail | None = None
    requested_slug: str
    suggestions: list[ModelSummary] = Field(default_factory=list)


# --- compare_models ----------------------------------------------------------------------------


class CompareInput(BaseModel):
    slugs: list[Slug] = Field(min_length=2, max_length=3)
    capability_names: list[CapabilityName] | None = Field(default=None, max_length=MAX_COMPARE_CAPABILITIES)

    @field_validator("slugs")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("slugs must be distinct")
        return v


class ComparedModel(ModelSummary):
    facts: list[Fact]
    not_recorded: list[str]


class NotFound(_Out):
    slug: str
    suggestions: list[ModelSummary]


class CompareResult(_Out):
    capability_names: list[str]
    models: list[ComparedModel]
    not_found: list[NotFound]
