from __future__ import annotations

from .chunk_decoder import BaseChunkingDecoder


class ShutterDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "roller_module")


__all__ = ["ShutterDecoder"]
