class ExpirationEstimator:
    """Deterministic baseline estimator used for local-first operation."""

    def estimate(self, item_name: str, category: str | None) -> int:
        normalized = (category or "").strip().lower()
        if normalized in {"produce", "dairy"}:
            return 7
        if normalized in {"frozen", "dry_goods"}:
            return 180
        return 30
