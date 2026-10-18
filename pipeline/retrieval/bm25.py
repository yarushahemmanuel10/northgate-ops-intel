"""Okapi BM25 over knowledge-base sections, in plain Python (step 5.2, D-035).

score(q, d) = sum over query terms t of  idf(t) * f(t,d) * (k1 + 1) / (f(t,d) + k1 * (1 - b + b * |d| / avgdl))
idf(t) = ln((N - n(t) + 0.5) / (n(t) + 0.5) + 1)
"""
from __future__ import annotations

import math
from collections import Counter


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avgdl = sum(self.len) / len(docs) if docs else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log((n - c + 0.5) / (c + 0.5) + 1) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t in query:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out
