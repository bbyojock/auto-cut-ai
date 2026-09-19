"""Runtime-only conversation memory.

Per the product requirements, conversation history must be remembered only
for the lifetime of the running application and must never be written to
disk. ``ConversationSession`` therefore keeps everything in a plain Python
list held in instance memory; there is intentionally no save()/load() here.
"""

from __future__ import annotations

from typing import List

from models.message import Message, MessageRole


class ConversationSession:
    """Holds the message history of the currently running conversation.

    A fresh :class:`ConversationSession` is created each time the application
    starts. Nothing inside this class ever touches the filesystem.
    """

    def __init__(self) -> None:
        self._history: List[Message] = []

    @property
    def history(self) -> List[Message]:
        """A read-only snapshot of the conversation so far."""
        return list(self._history)

    def add_user_message(self, content: str) -> Message:
        """Append a user message to the session and return it."""
        message = Message(role=MessageRole.USER, content=content)
        self._history.append(message)
        return message

    def add_assistant_message(self, content: str) -> Message:
        """Append an assistant message to the session and return it."""
        message = Message(role=MessageRole.ASSISTANT, content=content)
        self._history.append(message)
        return message

    def clear(self) -> None:
        """Forget the current conversation (e.g. user pressed "New chat")."""
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)
