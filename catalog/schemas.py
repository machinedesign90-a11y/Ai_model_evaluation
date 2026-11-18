"""Typed inputs and outputs of the catalog tools. These are the shapes the model sees."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

SourceType = Literal["official_docs", "provider_api", "scraped", "manual_review"]
VerificationStatus = Literal["verified", "unverified"]
Operator = Literal["contains", "equals", "at_least", "at_most"]
Importance = Literal["must", "nice"]
Confidence = Literal["exact", "alias", "approximate"]

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Scalar = str | int | float | bool

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


class Unresolved(_Out):
    """A word the tool could not map to the catalog's terms. Ask the user or call list_capabilities."""

    requirement: int | None = Field(description="Index into the requirements, or null for modality/model names.")
    term: str
    part: Literal["capability", "value", "operator", "modality", "model"]
    reason: str
    suggestions: list[str]


# --- search_models -----------------------------------------------------------------------------


class Requirement(BaseModel):
    capability: Term
    value: Scalar | None = None
    operator: Operator | None = None
    importance: Importance = "must"


class SearchInput(BaseModel):
    modality: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    requirements: list[Requirement] = Field(default_factory=list, max_length=MAX_REQUIREMENTS)
    limit: int = 10

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, v: int) -> int:
        return max(1, min(v, MAX_SEARCH_LIMIT))


class Interpretation(_Out):
    requirement: int
    input_capability: str
    input_value: Scalar | None
    status: Literal["resolved", "ambiguous", "unresolved"]
    capability: str | None
    capability_label: str | None
    operator: Operator | None
    value: JsonValue | None = Field(description="Canonical value; null means any recorded value counts.")
    importance: Importance
    confidence: Confidence | None = Field(description="Weakest of the capability and value readings.")
    note: str = Field(description="How the words were read, in one line, for telling the user.")


class RequirementResult(_Out):
    requirement: int
    capability: str
    status: Literal["satisfied", "not_satisfied", "not_recorded"]
    fact: Fact | None = Field(description="The recorded fact the status is based on; null when not recorded.")


class ModelMatch(ModelSummary):
    results: list[RequirementResult]
    met: list[str]
    missed: list[str]
    not_recorded: list[str] = Field(description="Can't tell: no fact recorded. Never report these as a 'no'.")


class SearchResult(_Out):
    searched_modality: str | None
    modality_note: str | None
    total_models_in_modality: int
    models_with_any_recorded_fact: int
    interpretation: list[Interpretation]
    unresolved: list[Unresolved]
    full_matches: list[ModelMatch] = Field(description="Every 'must' requirement satisfied.")
    close_matches: list[ModelMatch] = Field(
        description="No 'must' requirement contradicted, but at least one is not recorded: can't be ruled out."
    )
    partial_matches: list[ModelMatch] = Field(
        description="At least one 'must' requirement not satisfied; ranked by what they do meet."
    )
    total_full_matches: int
    total_close_matches: int
    total_partial_matches: int
    other_models: list[ModelSummary] = Field(
        description="Only when no requirement could be read: models in the modality, so the answer is never empty."
    )


# --- list_capabilities -------------------------------------------------------------------------


class CapabilityInfo(_Out):
    name: str
    label: str
    description: str
    value_type: Literal["boolean", "number", "text", "enum", "enum_list", "json"]
    unit: str | None
    allowed_values: JsonValue | None
    modalities: list[str] = Field(description="Empty means it applies to every modality.")
    example_aliases: list[str]


class ListCapabilitiesResult(_Out):
    modality: str | None
    modality_note: str | None
    capabilities: list[CapabilityInfo]


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
    requested: str
    matched_by: Literal["slug", "name", "approximate"] | None = None
    model: ModelDetail | None = None
    suggestions: list[str] = Field(default_factory=list, description="Close model slugs when not found.")


# --- compare_models ----------------------------------------------------------------------------


class CompareInput(BaseModel):
    slugs: list[Term] = Field(min_length=2, max_length=3, description="Slugs or names.")
    capability_names: list[Term] | None = Field(default=None, max_length=MAX_COMPARE_CAPABILITIES)

    @field_validator("slugs")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if len({s.lower() for s in v}) != len(v):
            raise ValueError("slugs must be distinct")
        return v


class ComparedModel(ModelSummary):
    requested: str
    matched_by: Literal["slug", "name", "approximate"]
    facts: list[Fact]
    not_recorded: list[str]


class CapabilityReading(_Out):
    input: str
    capability: str
    confidence: Confidence


class CompareResult(_Out):
    capability_names: list[str]
    capability_interpretation: list[CapabilityReading]
    models: list[ComparedModel]
    unresolved: list[Unresolved]
