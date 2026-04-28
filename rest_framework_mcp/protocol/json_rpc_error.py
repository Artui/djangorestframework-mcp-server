from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonRpcError:
    """JSON-RPC 2.0 error object.

    ``code`` is typed as ``int`` so server-defined codes outside the
    :class:`JsonRpcErrorCode` enum can still be represented faithfully.
    """

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


__all__ = ["JsonRpcError"]
