"""Pydantic models shared by the agent pipeline and the web UI."""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["person", "brand", "song_or_work", "business_or_location", "identifier"]
Risk = Literal["RED", "AMBER", "GREEN"]


class Entity(BaseModel):
    id: str
    text: str  # as written in the script
    category: Category
    scene: str  # slugline
    page_hint: int | None = None
    context: str  # the line it appears in
    attributes: dict = Field(default_factory=dict)  # e.g. {"profession": "cardiologist", "city": "Denver"}


class Evidence(BaseModel):
    title: str
    url: str
    excerpt: str


class Finding(BaseModel):
    entity_id: str
    risk: Risk
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)
    recommended_action: str
    suggested_substitution: str | None = None


class EntityList(BaseModel):
    entities: list[Entity]


class FindingList(BaseModel):
    findings: list[Finding]
