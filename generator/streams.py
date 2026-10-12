"""Named random streams.

Each generator module (helpdesk, knowledge base, ...) draws from its own stream, derived
from the project seed and the module's name. Adding or changing one module never shifts
the random draws of another (D-008).
"""
from __future__ import annotations

import zlib

import numpy as np


def named_stream(seed: int, name: str) -> np.random.Generator:
    key = zlib.crc32(name.encode("utf-8"))      # stable across runs and machines
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(key,)))
