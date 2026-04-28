from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.implementation import Implementation
from rest_framework_mcp.protocol.server_capabilities import ServerCapabilities


@dataclass(frozen=True)
class InitializeResult:
    """The server's response to an ``initialize`` request."""

    protocol_version: str
    capabilities: ServerCapabilities
    server_info: Implementation
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "capabilities": self.capabilities.to_dict(),
            "serverInfo": self.server_info.to_dict(),
        }
        if self.instructions is not None:
            out["instructions"] = self.instructions
        return out


__all__ = ["InitializeResult"]
