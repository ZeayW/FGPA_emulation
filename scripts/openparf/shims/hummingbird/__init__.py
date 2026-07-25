"""Timing-model shim for OpenPARF placement-only builds.

OpenPARF imports Hummingbird unconditionally even when timing optimization is
disabled. The proj169-2 validation does not enable delay estimation, so this
small shim keeps the placement-only path importable without pretending to
provide the optional timing-model conversion API.
"""

from . import ml

__all__ = ["ml"]
