"""请求 / 响应数据模型。"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.config import MAX_HISTORY_MESSAGES, MAX_MESSAGE_CHARS


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    provider: str = ""
    model: Optional[str] = None
    messages: List[Message] = Field(min_length=1, max_length=MAX_HISTORY_MESSAGES + 10)
    session_id: Optional[str] = Field(default=None, max_length=64)
    think: Optional[bool] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.5)
    regenerate: bool = False

    @field_validator("messages")
    @classmethod
    def _trim_history(cls, value: List[Message]) -> List[Message]:
        return value[-MAX_HISTORY_MESSAGES:]


class ProviderRequest(BaseModel):
    provider: str


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class DevTaskIn(BaseModel):
    title: str = Field(default="", max_length=120)
    requirement: str = Field(min_length=8, max_length=4000)


class DevApproveIn(BaseModel):
    action: Literal["merge", "reject"] = "merge"
    note: str = Field(default="", max_length=1000)
