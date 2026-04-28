from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.client_capabilities import ClientCapabilities
from rest_framework_mcp.protocol.implementation import Implementation


@dataclass(frozen=True)
class InitializeParams:
    """Parsed ``initialize`` request params."""

    protocol_version: str
    capabilities: ClientCapabilities
    client_info: Implementation

    @classmethod
    def from_payload(cls, params: dict[str, Any]) -> InitializeParams:
        caps_raw: dict[str, Any] = params.get("capabilities") or {}
        info_raw: dict[str, Any] = params.get("clientInfo") or {}
        return cls(
            protocol_version=str(params.get("protocolVersion", "")),
            capabilities=ClientCapabilities(
                roots=caps_raw.get("roots"),
                sampling=caps_raw.get("sampling"),
                elicitation=caps_raw.get("elicitation"),
                experimental=caps_raw.get("experimental"),
            ),
            client_info=Implementation(
                name=str(info_raw.get("name", "")),
                version=str(info_raw.get("version", "")),
            ),
        )


__all__ = ["InitializeParams"]
