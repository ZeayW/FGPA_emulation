"""Timing-model shim for OpenPARF placement-only builds.

OpenPARF imports Hummingbird unconditionally even when timing optimization is
disabled. Placement-only configurations do not enable delay estimation, so
this small shim keeps that path importable without pretending to provide the
optional timing-model conversion API.
"""

from . import ml

__all__ = ["ml"]
