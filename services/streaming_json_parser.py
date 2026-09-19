"""Incrementally parses the AI editing brain's JSON response while it streams.

The provider's output contract (see
:class:`ai.prompt_templates.PromptTemplates.OUTPUT_CONTRACT`) is one JSON
object shaped like::

    {"target_length": ..., "confidence": ..., "reasons": [...],
     "warnings": [...], "segments": [{"start":.., "end":.., "action":..,
     "reason":..}, ...]}

Waiting for the entire response before showing anything (the pre-Version-4
behavior) means the AI Progress Window has nothing concrete to show during
the (often longest) "Generating Edit Plan" stage. This parser recovers
*complete* ``segments`` array entries as they arrive in the character
stream, without needing the object to be complete or even
syntactically valid yet -- exactly the incremental recovery Feature 10
calls for. Final, authoritative parsing/validation of the complete
response still goes through :class:`ai.response_parser.ResponseParser`
unchanged; this class only ever powers live progress feedback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# Matches the start of the segments array so we know where to start
# scanning for complete `{...}` objects. Tolerant of whitespace and of
# the array not being complete yet.
_SEGMENTS_ARRAY_START_RE = re.compile(r'"segments"\s*:\s*\[')


@dataclass(slots=True)
class PartialSegment:
    """One segment successfully recovered from a still-incomplete stream."""

    start_seconds: Optional[float]
    end_seconds: Optional[float]
    action: Optional[str]
    reason: Optional[str]


@dataclass(slots=True)
class StreamingParseState:
    """What :class:`StreamingEditPlanParser` has recovered from the stream so far."""

    raw_text: str = ""
    segments: List[PartialSegment] = field(default_factory=list)
    target_length_seconds: Optional[float] = None
    confidence: Optional[float] = None


class StreamingEditPlanParser:
    """Feeds streamed text chunks in and recovers segments as they complete.

    Usage::

        parser = StreamingEditPlanParser()
        for chunk in stream:
            new_segments = parser.feed(chunk)
            for segment in new_segments:
                ... update progress UI ...
        full_text = parser.raw_text
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._segments_scan_offset = 0
        self._known_segment_count = 0

    @property
    def raw_text(self) -> str:
        return self._buffer

    def feed(self, chunk: str) -> List[PartialSegment]:
        """Append ``chunk`` and return any *newly* completed segments.

        Never raises -- a still-incomplete or momentarily malformed buffer
        (the normal state for most of the stream) simply yields no new
        segments yet rather than an error.
        """
        self._buffer += chunk
        return self._scan_for_new_segments()

    def current_target_length(self) -> Optional[float]:
        """Best-effort ``target_length`` value, if it's arrived yet."""
        match = re.search(r'"target_length"\s*:\s*(-?\d+(?:\.\d+)?)', self._buffer)
        return float(match.group(1)) if match else None

    def current_confidence(self) -> Optional[float]:
        """Best-effort ``confidence`` value, if it's arrived yet."""
        match = re.search(r'"confidence"\s*:\s*(-?\d+(?:\.\d+)?)', self._buffer)
        return float(match.group(1)) if match else None

    def _scan_for_new_segments(self) -> List[PartialSegment]:
        array_match = _SEGMENTS_ARRAY_START_RE.search(self._buffer)
        if not array_match:
            return []

        scan_from = max(array_match.end(), self._segments_scan_offset)
        new_segments: List[PartialSegment] = []
        cursor = scan_from

        while True:
            brace_start = self._buffer.find("{", cursor)
            if brace_start == -1:
                break
            object_text, end_index = self._extract_balanced_object(self._buffer, brace_start)
            if object_text is None:
                # Incomplete object at the tail of the buffer -- wait for more chunks.
                break
            parsed = self._try_parse_segment(object_text)
            if parsed is not None:
                new_segments.append(parsed)
                self._known_segment_count += 1
            cursor = end_index

        self._segments_scan_offset = cursor
        return new_segments

    @staticmethod
    def _extract_balanced_object(text: str, start: int):
        """Return ``(object_text, index_after_object)`` for the ``{`` at ``start``.

        Returns ``(None, start)`` if the object's closing brace hasn't
        arrived yet. Correctly ignores braces inside string literals
        (including escaped quotes) so a segment's ``reason`` text can
        safely contain ``{``/``}``/``"`` without breaking the scan.
        """
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1], index + 1
        return None, start

    @staticmethod
    def _try_parse_segment(object_text: str) -> Optional[PartialSegment]:
        try:
            data = json.loads(object_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return PartialSegment(
            start_seconds=_as_float(data.get("start")),
            end_seconds=_as_float(data.get("end")),
            action=data.get("action") if isinstance(data.get("action"), str) else None,
            reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
        )


def _as_float(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
