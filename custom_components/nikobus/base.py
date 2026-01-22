from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DecodedCommand:
    module_type: str
    raw_message: str
    prefix_hex: str | None = None
    chunk_hex: str | None = None
    payload_hex: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Decoder(Protocol):
    module_type: str

    def can_handle(self, module_type: str) -> bool:
        """Return True if this decoder can process the given module type."""

    def decode(self, message: str) -> list[DecodedCommand]:
        """Decode the incoming message or chunk into structured commands."""
