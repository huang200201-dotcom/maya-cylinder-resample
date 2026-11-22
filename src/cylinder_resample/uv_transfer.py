"""Plan seam-aware cylinder reductions and select chart-local UV samples."""

import math
from bisect import bisect_left, bisect_right


def plan_uv_reduction(geometry_columns, uv_columns, target_count, uv_policy="strict"):
    """Describe reduction feasibility without parsing UVs or modifying a mesh."""
    if uv_policy not in ("strict", "adaptive"):
        raise ValueError("请选择严格保留 UV 或自适应保留 UV。")
    geometry = set(geometry_columns)
    strict_count = len(geometry.union(uv_columns))
    minimum = len(geometry) if uv_policy == "adaptive" else strict_count
    feasible = target_count >= max(3, minimum)
    transfer = feasible and uv_policy == "adaptive" and target_count < strict_count
    return {"minimum_count": max(3, minimum), "strict_minimum_count": max(3, strict_count),
            "transfer_required": transfer, "feasible": feasible,
            "relaxed_uv_count": max(0, strict_count - target_count) if transfer else 0}


def region_boundaries(analysis, data):
    """Collect longitudinal chart cuts separately for each band and fan."""
    result = {}
    corners = data["corners"]
    regions = list(analysis["bands"]) + [cap for cap in analysis["caps"] if cap["kind"] == "fan"]
    for region in regions:
        fan = "center" in region
        if fan:
            first = analysis["rings"][region["ring"]]
        else:
            first, second = (analysis["rings"][ri] for ri in region["rings"])
        cuts = []
        for column, face in enumerate(region["faces"]):
            left, right = corners[region["faces"][column - 1]], corners[face]
            if left is None or right is None:
                cut = (left is None) != (right is None)
            elif left[first[column]] != right[first[column]]:
                cut = True
            elif fan:
                a, b = left[region["center"]], right[region["center"]]
                cut = data["u"][a] != data["u"][b] or data["v"][a] != data["v"][b]
            else:
                cut = left[second[column]] != right[second[column]]
            if cut:
                cuts.append(column)
        result[region["faces"][0]] = tuple(cuts)
    return result


def fixed_columns(geometry, columns_by_set, region_cuts, budget, preferred_uv_set):
    """Fit the active set first, then complete sets with the fewest extra cuts."""
    pinned = set(geometry)
    remaining = dict(columns_by_set)
    preferred = remaining.pop(preferred_uv_set, set())
    if len(pinned | preferred) <= budget:
        pinned.update(preferred)
    while remaining:
        name = min(remaining, key=lambda key: (len(remaining[key] - pinned), key))
        candidate = pinned | remaining.pop(name)
        if len(candidate) > budget:
            break
        pinned = candidate
    # A fragmented cap can share a set with a clean sidewall. Preserve that
    # band's complete chart layout whenever the remaining budget allows it.
    for name in sorted(region_cuts, key=lambda key: (key != preferred_uv_set, key)):
        groups = {cuts for cuts in region_cuts[name].values() if 0 < len(cuts) <= budget}
        for cuts in sorted(groups, key=lambda item: (len(item), item)):
            candidate = pinned.union(cuts)
            if len(candidate) <= budget:
                pinned = candidate
    return pinned


def _interior_cuts(cuts, period, start, end):
    """Return cuts on an unwrapped interval, excluding its two endpoints."""
    epsilon = 1.0e-9
    for turn in range(int(math.floor(start / period)), int(math.floor(end / period)) + 1):
        offset = turn * period
        left = bisect_right(cuts, start - offset + epsilon)
        right = bisect_left(cuts, end - offset - epsilon)
        for column in cuts[left:right]:
            yield offset + column


def donor_interval(cuts, source_faces, corners, starts, ends, relaxed):
    """Choose one contiguous old chart for a face that crosses a removed cut.

    Unchanged faces keep the individual sampling parameters of their rings.
    A crossing face uses the longest single interval along its midline. On a
    tie a mapped interval wins, then the first interval in winding order.
    """
    n = len(source_faces)
    if not cuts:
        return None
    crossed = list(_interior_cuts(cuts, n, min(starts), max(ends)))
    if not crossed:
        return None
    relaxed.update(column % n for column in crossed)
    start, end = sum(starts) / len(starts), sum(ends) / len(ends)
    boundaries = [start] + list(_interior_cuts(cuts, n, start, end)) + [end]
    best = None
    for a, b in zip(boundaries, boundaries[1:]):
        face = source_faces[int(math.floor((a + b) * 0.5)) % n]
        width, mapped = b - a, corners[face] is not None
        if (best is None or width > best[0] + 1.0e-9 or
                (abs(width - best[0]) <= 1.0e-9 and mapped and not best[1])):
            best = (width, mapped, a, b, face)
    return best[2:]


class RegionUVSampler:
    """Sample per-corner UVs within a band, n-gon, or triangle fan."""

    def __init__(self, data, source_faces, cuts=()):
        self.data = data
        self.faces = source_faces
        self.cuts = cuts
        self.relaxed = set()
        self.transferred = 0

    def ring_uv(self, ring, t, end=False):
        nearest = round(t)
        if abs(t - nearest) <= 1.0e-9:
            column = int(nearest - 1 if end else nearest) % len(ring)
            fraction = 1.0 if end else 0.0
        else:
            index = math.floor(t)
            column, fraction = index % len(ring), t - index
        data = self.data
        corners = data["corners"][self.faces[column]]
        if corners is None:
            return None
        a, b = corners[ring[column]], corners[ring[(column + 1) % len(ring)]]
        if fraction <= 1.0e-9 or a == b:
            return a
        if fraction >= 1.0 - 1.0e-9:
            return b
        if a > b:
            a, b, fraction = b, a, 1.0 - fraction
        key = (a, b, round(fraction, 12))
        if key not in data["cache"]:
            data["cache"][key] = len(data["u"])
            for values in (data["u"], data["v"]):
                values.append(values[a] * (1 - fraction) + values[b] * fraction)
        return data["cache"][key]

    def side(self, old_a, old_b, a_start, a_end, b_start, b_end):
        donor = donor_interval(self.cuts, self.faces, self.data["corners"],
                               (a_start, b_start), (a_end, b_end), self.relaxed)
        if donor:
            a_start, a_end, _ = donor
            b_start, b_end = a_start, a_end
            self.transferred += 1
        return [self.ring_uv(old_a, a_start), self.ring_uv(old_a, a_end, True),
                self.ring_uv(old_b, b_end, True), self.ring_uv(old_b, b_start)]

    def fan(self, ring, center, source_face, start, end):
        donor = donor_interval(self.cuts, self.faces, self.data["corners"],
                               (start,), (end,), self.relaxed)
        if donor:
            start, end, source_face = donor
            self.transferred += 1
        corner = self.data["corners"][source_face]
        return [self.ring_uv(ring, start), self.ring_uv(ring, end, True),
                corner[center] if corner is not None else None]
