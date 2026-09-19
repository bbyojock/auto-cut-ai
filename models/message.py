"""Conversation message model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    """Role of the entity that produced a message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(slots=True)
class Message:
    """A single message inside a conversation session.

    Attributes:
        role: Who produced the message (user, assistant or system).
        content: The raw text content of the message.
        timestamp: When the message was created (defaults to now).
    """

    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Serialize the message to a plain dictionary (runtime use only)."""
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }
