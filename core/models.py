"""Shared data contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Scene(BaseModel):
    scene_id: int = Field(ge=1)
    narration: str = Field(min_length=1)
    search_keywords: list[str] = Field(min_length=1)
    visual_style: str = "cinematic"
    highlight_words: list[str] = Field(default_factory=list)
    source_start: float | None = None
    source_end: float | None = None
    asset_path: str | None = None
    asset_kind: Literal["video", "image", "generated"] | None = None
    audio_path: str | None = None
    actual_duration: float | None = None
    start: float | None = None
    end: float | None = None

    @field_validator("narration")
    @classmethod
    def clean_narration(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("旁白为空")
        return text

    @field_validator("search_keywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        keywords = [item.strip() for item in value if item and item.strip()]
        if not keywords:
            raise ValueError("缺少检索关键词")
        return keywords

    @field_validator("highlight_words")
    @classmethod
    def clean_highlights(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class Storyboard(BaseModel):
    title: str = Field(min_length=1)
    aspect_ratio: Literal["9:16", "16:9", "4:3"]
    scenes: list[Scene] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("标题为空")
        return text[:40]


class WordStamp(BaseModel):
    text: str
    start: float
    end: float
    scene_id: int | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class CaptionGroup(BaseModel):
    start: float
    duration: float
    words: list[WordStamp]


class ProjectTimeline(BaseModel):
    task_id: str
    mode: Literal["create", "highlight"]
    storyboard: Storyboard
    words: list[WordStamp]
    narration_path: str
    total_duration: float
    width: int
    height: int
    notes: list[str] = Field(default_factory=list)
