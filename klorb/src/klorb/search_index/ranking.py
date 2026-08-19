# © Copyright 2026 Aaron Kimball
"""Reciprocal Rank Fusion: merges independently-ranked result lists (BM25 lexical order, vector
KNN order) into one fused ranking, without needing to calibrate their scores onto a common
scale.
"""

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]], *, k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """Fuse `ranked_lists` -- each a list of chunk ids in descending-relevance order from one
    ranking method -- into a single ranking via RRF: every id's fused score is the sum, across
    every list it appears in, of `1 / (k + rank)`. Returns `(chunk_id, score)`
    pairs sorted by descending score, ties broken by first appearance across `ranked_lists`.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    position = 0
    for ranked_list in ranked_lists:
        for rank, chunk_id in enumerate(ranked_list, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            if chunk_id not in first_seen:
                first_seen[chunk_id] = position
            position += 1
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
