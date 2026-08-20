from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FollowUpQuestion(BaseModel):
    """A follow-up question to refine classification."""

    question: str
    format: Optional[str] = Field(default=None)
    options: Optional[List[str]] = Field(default=None)


class ClassificationResponse(BaseModel):
    """Response payload with aggregated legal problem code and follow-up questions."""

    legal_problem_code: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    is_eligible: Optional[bool] = Field(default=None)
    follow_up_questions: Optional[List[FollowUpQuestion]] = Field(default=None)
    # Debug fields (only populated when DEBUG mode is enabled)
    raw_provider_results: Optional[Dict[str, Any]] = Field(default=None)
    weighted_label_scores: Optional[Dict[str, float]] = Field(default=None)


class ProviderStatus(str, Enum):
    """Typed status for a single provider classification result."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    EMPTY = "empty"


class ProviderLabel(BaseModel):
    """A single validated label entry from a provider."""

    legal_problem_code: str
    confidence: float = 1.0


class ProviderQuestion(BaseModel):
    """A single validated question entry from a provider."""

    question: str
    format: str | None = None
    options: list[str] | None = None


@dataclass
class ProviderResult:
    """Typed envelope for a single provider classification result."""

    model_name: str
    status: ProviderStatus
    labels: list[ProviderLabel] = field(default_factory=list)
    questions: list[ProviderQuestion] = field(default_factory=list)
    error: str | None = None
