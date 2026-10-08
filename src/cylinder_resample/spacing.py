"""Numeric spacing diagnostics for closed rings with fixed source columns."""

import math


_COUNT_TOLERANCE = 1.0e-7
_MIN_TARGET = 3
_MAX_TARGET = 4096


def _compatible_count(fractions, count):
    rounded_total = 0
    for fraction in fractions:
        quota = fraction * count
        rounded = int(math.floor(quota + 0.5))
        if rounded < 1 or abs(quota - rounded) > _COUNT_TOLERANCE:
            return False
        rounded_total += rounded
    return rounded_total == count


def analyze_spacing(lengths, pins, target, find_compatible_counts=True):
    """Describe whether equal measure intervals can retain every fixed column.

    ``lengths`` contains the positive angular or arc measure of each source
    edge. ``pins`` contains fixed source column indices. Numeric measures, not
    source index distances, determine compatibility. The quota tolerance is
    1e-7 subdivisions to absorb floating-point circle fitting noise.

    Nearby compatible counts are the nearest strictly smaller and larger
    counts within 3..4096, returned in ascending order. Disable that search
    when checking an already proposed count on another ring.
    """
    try:
        measures = [float(value) for value in lengths]
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Ring measures must be finite positive numbers.")
    n = len(measures)
    if n < 3 or any(not math.isfinite(value) or value <= 0.0 for value in measures):
        raise ValueError("A ring requires at least three finite positive measures.")
    if isinstance(target, bool) or not isinstance(target, int) or not _MIN_TARGET <= target <= _MAX_TARGET:
        raise ValueError("Target count must be an integer from 3 to 4096.")
    source_pins = list(pins) if pins is not None else []
    if any(isinstance(pin, bool) or not isinstance(pin, int) or not 0 <= pin < n
           for pin in source_pins):
        raise ValueError("Fixed columns must be integer source ring indices.")
    fixed = tuple(sorted(set(source_pins)))
    scale = max(measures)
    measures = [value / scale for value in measures]
    if any(value == 0.0 for value in measures):
        raise ValueError("Ring measure range exceeds floating-point precision.")
    period = math.fsum(measures)
    if len(fixed) < 2:
        fractions = (1.0,)
    else:
        ends = fixed[1:] + (fixed[0] + n,)
        # Sum each cyclic interval directly, avoiding subtraction of nearby
        # cumulative totals when a short interval follows a long one.
        fractions = tuple(math.fsum(measures[index % n] for index in range(start, end)) / period
                          for start, end in zip(fixed, ends))
    compatible = _compatible_count(fractions, target)
    nearby = []
    if find_compatible_counts:
        minimum = max(_MIN_TARGET, len(fixed))
        for candidate in range(target - 1, minimum - 1, -1):
            if _compatible_count(fractions, candidate):
                nearby.append(candidate)
                break
        for candidate in range(max(target + 1, minimum), _MAX_TARGET + 1):
            if _compatible_count(fractions, candidate):
                nearby.append(candidate)
                break
    return {"compatible": compatible, "fixed_pin_count": len(fixed),
            "interval_fractions": fractions,
            "ideal_interval_counts": tuple(fraction * target for fraction in fractions),
            "compatible_counts": tuple(nearby)}
