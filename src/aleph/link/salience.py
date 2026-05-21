"""Single-file fan-in salience scoring (Phase 0).

Phase 2 will extend to project-wide cross-module reach.
"""

from __future__ import annotations

from aleph.model.symbol import Symbol


class SalienceScorer:
    """Computes salience scores from local call graph fan-in.

    Salience is normalized to [0, 1] based on fan-in count.
    """

    def score(self, symbols: list[Symbol]) -> dict[str, float]:
        """Compute salience scores for all symbols. Returns id_str -> score."""
        if not symbols:
            return {}

        # Count fan-in (how many other symbols call each symbol)
        fan_in: dict[str, int] = {}
        for sym in symbols:
            id_str = str(sym.id)
            fan_in[id_str] = len(sym.called_by)

        # Normalize to [0, 1]
        max_fan_in = max(fan_in.values()) if fan_in else 0
        if max_fan_in == 0:
            return {id_str: 0.0 for id_str in fan_in}

        scores = {
            id_str: count / max_fan_in
            for id_str, count in fan_in.items()
        }

        # Apply scores to symbol objects
        by_id = {str(s.id): s for s in symbols}
        for id_str, score in scores.items():
            if id_str in by_id:
                by_id[id_str].salience = score

        return scores
