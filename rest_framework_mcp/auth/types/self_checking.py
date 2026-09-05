from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SelfChecking(Protocol):
    """An auth backend that can say whether it is able to serve requests.

    Deliberately a **second** protocol rather than a method on
    :class:`~rest_framework_mcp.auth.types.auth_backend.MCPAuthBackend`. Adding
    it there would make it required, and a backend already written against the
    four-method protocol would stop satisfying it -- so the one thing this
    exists to prevent, a transport mounted with a backend that cannot run,
    would be bought by breaking every backend anyone has already written.

    Structural and runtime-checkable, so a backend opts in by having the method
    and nothing has to register anywhere. A backend that does not implement it
    is treated as ready, which is the right default: needing no setup is the
    ordinary case, and a backend that needs some is the one able to say so.

    **It is called when the server is mounted, and that is the whole point.**
    A backend becomes load-bearing at the moment HTTP requests become possible,
    not when the server object is built: an in-process server -- ``call_tool``,
    ``list_tools``, the in-process route django-ag-ui uses -- never
    authenticates anything, so requiring a backend's optional dependency to
    *construct* one would refuse a supported deployment. Mounting is the
    earliest point at which the requirement is real, and it is still startup:
    ``server.urls`` runs while the URLConf is imported, so ``manage.py check``
    reaches it and a missing dependency is a refusal at boot rather than a 500
    on the first request.

    ``check_configuration`` raises -- ``ImproperlyConfigured``, naming what is
    missing and how to supply it -- or returns ``None``. It must not return a
    bool: a caller that has to interpret a False has been given an error that
    knows more than it says.
    """

    def check_configuration(self) -> None: ...


__all__ = ["SelfChecking"]
