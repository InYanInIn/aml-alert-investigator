from collections import defaultdict
from collections.abc import Sequence

RRF_K = 60


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for position, passage_id in enumerate(ranking, start=1):
            scores[passage_id] += 1 / (k + position)
    return sorted(scores, key=lambda passage_id: (-scores[passage_id], passage_id))
