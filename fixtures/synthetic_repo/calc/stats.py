"""Small statistics helpers."""


def mean(values: list[float]) -> float:
    """Arithmetic mean. Raises ValueError for an empty list."""
    if not values:
        raise ValueError("mean() of empty list")
    return sum(values) / len(values)
