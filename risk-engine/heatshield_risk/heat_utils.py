"""Simplified heat index for demo when not provided. Labeled as calculated in observations."""


def approximate_heat_index_c(temp_c: float, humidity_pct: float) -> float:
    """Rothfusz-style approximation (valid for temp >= 26°C). Demo use only."""
    t = temp_c
    rh = max(0.0, min(100.0, humidity_pct))
    if t < 26:
        return t
    hi = (
        -8.78469475556
        + 1.61139411 * t
        + 2.33854883889 * rh
        - 0.14611605 * t * rh
        - 0.012308094 * t * t
        - 0.0164248277778 * rh * rh
        + 0.002211732 * t * t * rh
        + 0.00072546 * t * rh * rh
        - 0.000003582 * t * t * rh * rh
    )
    return round(hi, 1)
