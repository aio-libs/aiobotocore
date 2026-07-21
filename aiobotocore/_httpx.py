"""Centralised import of the optional httpx backend dependency.

Kept in a dedicated module with no intra-package imports so that low-level
modules (e.g. ``_endpoint_helpers``, which is itself imported by
``httpxsession``) can share it without creating an import cycle.
"""

try:
    import httpx
except ImportError:
    httpx = None
