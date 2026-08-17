#!/usr/bin/env python3

"""Numerically well-defined helpers for degenerate placement subspaces."""

import torch


def masked_l2_normalize(vector, disabled_mask):
    """Normalize only enabled entries, returning zero for an empty direction.

    Legalization can remove an entire resource type, or even every placement
    variable, from the active optimization subspace.  In that case the
    projected subgradient is exactly zero and the mathematically correct
    update is a no-op.  Masking after normalization would instead evaluate
    ``0 / 0`` before discarding the disabled entries.
    """

    if vector.shape != disabled_mask.shape:
        raise ValueError("normalization mask must match the vector shape")
    if not torch.isfinite(vector).all():
        raise FloatingPointError("placement subgradient is not finite")

    projected = vector.masked_fill(disabled_mask.bool(), 0)
    norm = projected.norm(p=2)
    if not torch.isfinite(norm):
        raise FloatingPointError("placement subgradient norm is not finite")
    if norm.item() == 0:
        return torch.zeros_like(projected)
    return projected / norm


def safe_l2_step_size(position_delta, gradient_delta, fallback):
    """Return a secant step, retaining ``fallback`` when curvature is zero."""

    numerator = position_delta.norm(p=2)
    denominator = gradient_delta.norm(p=2)
    fallback_tensor = torch.as_tensor(
        fallback, dtype=numerator.dtype, device=numerator.device
    )
    if not torch.isfinite(numerator) or not torch.isfinite(denominator):
        raise FloatingPointError("placement step-size inputs are not finite")
    if not torch.isfinite(fallback_tensor) or fallback_tensor.item() < 0:
        raise FloatingPointError("placement fallback step size is invalid")
    if denominator.item() == 0:
        return fallback_tensor.clone()
    step = numerator / denominator
    if not torch.isfinite(step):
        raise FloatingPointError("placement step size is not finite")
    return step
