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
