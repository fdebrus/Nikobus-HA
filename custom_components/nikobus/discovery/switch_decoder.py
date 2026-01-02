from __future__ import annotations

from .chunk_decoder import BaseChunkingDecoder


class SwitchDecoder(BaseChunkingDecoder):
    def __init__(self, coordinator):
        super().__init__(coordinator, "switch_module")


__all__ = ["SwitchDecoder"]
