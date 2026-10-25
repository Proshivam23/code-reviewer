from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewRequest(BaseModel):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pr_number: int = Field(gt=0)
    logic: bool = True


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    line: int = Field(ge=1)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    source: Literal["bandit", "ruff", "logic"]
    rule_id: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    fix: str = Field(min_length=1)
    context_refs: list[str] = Field(default_factory=list)
    in_changed_lines: bool = False


class LogicResult(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approved: bool


class ReviewResponse(BaseModel):
    review_id: str
    status: str
    head_sha: str = ""
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    context: list[dict] = Field(default_factory=list)
    comment_url: str | None = None
