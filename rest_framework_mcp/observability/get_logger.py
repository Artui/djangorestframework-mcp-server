from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    """``logging.getLogger`` for a module inside this package.

    A thin wrapper so every site spells the namespace the same way and the
    convention has one documented home — see the package docstring for what is
    logged at which level, and for the two categories that must never be.

    Module-level use is fine: a ``Logger`` is a constant reference, not the
    mutable module state this repo's rules forbid.
    """
    return logging.getLogger(name)


__all__ = ["get_logger"]
