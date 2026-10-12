"""Fill kb_article_id on helpdesk tickets from the knowledge base.

Called by generator.helpdesk.build(). Runs on its own named stream ("kb_links"), so the
tickets themselves never change; only the kb_article_id column is filled.

Rules:
- About `tickets.kb_link_rate` of tickets used an article (exactly round(rate x n)).
- A linked article covers the ticket's topic (written for it, or a general guide listing
  it), existed when the ticket was opened, and was not yet superseded then.
- QI-09: exactly `QI-09_tickets_linking_superseded_article` tickets on the superseded
  article's topic, opened after it was superseded, link to the old article anyway.
- The true link and an outdated flag are kept in the ground truth, so step 1.9 can remove
  links from the raw file (QI-08) without losing them.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from generator.kb import available_on, load_kb
from generator.streams import named_stream


def apply_kb_links(cfg: dict[str, Any], raw: pd.DataFrame, truth: pd.DataFrame,
                   seed: int | None = None, docs: list[dict] | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    docs = docs if docs is not None else load_kb()
    kbc = cfg["knowledge_base"]
    rng = named_stream(cfg["seed"] if seed is None else seed, "kb_links")
    choose_rng, outdated_rng, pick_rng = rng.spawn(3)

    articles = [d for d in docs if d["type"] == "article" and d["topic_ids"]]
    old = [d for d in articles if d["status"] == "superseded"]
    opened = pd.to_datetime(raw["opened_at"]).dt.date
    topics = truth.set_index("ticket_id").topic_id.reindex(raw.ticket_id).to_numpy()

    def eligible(i: int) -> list[dict]:
        return [d for d in articles if topics[i] in d["topic_ids"] and available_on(d, opened.iloc[i])]

    n = len(raw)
    links = [""] * n
    outdated = [False] * n

    # QI-09 first: tickets after the supersede date that still link to the old article.
    k_old = cfg["quality_issues"]["QI-09_tickets_linking_superseded_article"]
    for d in old:
        cand = [i for i in range(n) if topics[i] in d["topic_ids"] and opened.iloc[i] >= d["superseded_on"]]
        for i in outdated_rng.choice(cand, size=k_old, replace=False):
            links[int(i)], outdated[int(i)] = d["id"], True

    # Then the rest, to reach exactly round(rate x n) linked tickets.
    target = round(cfg["tickets"]["kb_link_rate"] * n)
    pool = [i for i in range(n) if not links[i] and eligible(i)]
    chosen = choose_rng.choice(pool, size=target - sum(1 for x in links if x), replace=False)
    for i in sorted(int(x) for x in chosen):
        cands = eligible(i)
        w = np.array([kbc["specific_article_weight"] if len(d["topic_ids"]) == 1 else kbc["general_guide_weight"]
                      for d in cands], dtype=float)
        links[i] = cands[int(pick_rng.choice(len(cands), p=w / w.sum()))]["id"]

    raw = raw.copy()
    raw["kb_article_id"] = links
    truth = truth.copy()
    truth["kb_article_id"] = truth.ticket_id.map(dict(zip(raw.ticket_id, links)))
    truth["kb_link_outdated"] = truth.ticket_id.map(dict(zip(raw.ticket_id, outdated)))
    return raw, truth
