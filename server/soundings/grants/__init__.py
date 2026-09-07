"""Reusable grants intelligence layer.

This package deliberately has no MCP/HTTP dependencies. Soundings tools use it
in-process today; it can later be extracted behind a dedicated grants MCP
without moving the query semantics again.
"""

from soundings.grants.store import GrantStore

__all__ = ["GrantStore"]
