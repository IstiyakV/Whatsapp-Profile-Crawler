from typing import Literal

from pydantic import BaseModel, Field


SpeedProfile = Literal["safe", "balanced", "fast"]
TaskStatus = Literal["queued", "running", "paused", "completed", "cancelled", "error"]
ResultStatus = Literal["pending", "success", "hidden_dp", "no_whatsapp", "error"]


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    numbers: list[str]
    speed: SpeedProfile = "balanced"
    skip_checked: bool = True
    max_errors: int = Field(default=0, ge=0)


class SingleLookup(BaseModel):
    phone: str
    force: bool = True


class RangeLookup(BaseModel):
    name: str = "Range crawl"
    prefix: str
    start: str
    end: str
    speed: SpeedProfile = "balanced"
    skip_checked: bool = True
    max_errors: int = Field(default=0, ge=0)


class TaskRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class QueueAction(BaseModel):
    force: bool = False
