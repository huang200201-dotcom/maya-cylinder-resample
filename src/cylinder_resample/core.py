"""Topology-based cylinder resampling without dependencies on Maya."""

import math
from bisect import bisect_right

from .spacing import analyze_spacing, find_common_compatible_counts
from .uv_transfer import RegionUVSampler, fixed_columns, plan_uv_reduction, region_boundaries

from .topology import ResampleError, _edge, _edge_direction, analyze_mesh


class ConstraintError(ResampleError):
    def __init__(self, minimum_count):
        self.minimum_count = minimum_count
        super().__init__("保留现有 UV 接缝和受保护边至少需要 %d 段，请提高目标段数。" % minimum_count)


_EPS = 1.0e-9


def _parse_uvs(uv_sets, faces):
    result = {}
    for name, data in (uv_sets or {}).items():
        u, v = list(data["u"]), list(data["v"])
        counts, ids = list(data["counts"]), list(data["ids"])
        if len(u) != len(v) or len(counts) != len(faces):
            raise ResampleError("UV 集 %s 的数据数量不一致。" % name)
        if not all(math.isfinite(float(x)) for x in u + v):
            raise ResampleError("UV 集 %s 中存在无效坐标。" % name)
        corners, offset = [], 0
        for face, count in zip(faces, counts):
            if count not in (0, len(face)):
                raise ResampleError("UV 集 %s 中存在仅部分顶点有 UV 的面，请先修复。" % name)
            face_ids = ids[offset:offset + count]
            if len(face_ids) != count or any(i < 0 or i >= len(u) for i in face_ids):
                raise ResampleError("UV 集 %s 中存在无效的 UV 编号。" % name)
            corners.append(dict(zip(face, face_ids)) if count else None)
            offset += count
        if offset != len(ids):
            raise ResampleError("UV 集 %s 的面角索引数量不正确。" % name)
        result[name] = {"u": u, "v": v, "corners": corners, "cache": {}, "counts": [], "ids": []}
    return result


def _uv_constraints(analysis, uv_data):
    return {column for data in uv_data.values()
            for columns in region_boundaries(analysis, data).values() for column in columns}


def _allocations(weights, target):
    """Allocate integer intervals with balanced rounding around the ring."""
    if target == len(weights):
        return [1] * len(weights)
    remaining, active_weight = target, math.fsum(weights)
    quotas = [None] * len(weights)
    for index in sorted(range(len(weights)), key=weights.__getitem__):
        multiplier = remaining / active_weight
        if weights[index] * multiplier >= 1.0:
            break
        quotas[index] = 1.0
        remaining -= 1
        active_weight -= weights[index]
    multiplier = remaining / active_weight
    quotas = [max(1.0, weight * multiplier) if quota is None else quota
              for weight, quota in zip(weights, quotas)]
    allocations, previous, cumulative = [], 0, 0.0
    for i, quota in enumerate(quotas):
        cumulative += quota
        boundary = target if i == len(quotas) - 1 else int(math.floor(cumulative + 0.5 + _EPS))
        allocations.append(boundary - previous)
        previous = boundary
    return allocations


def _samples(n, target, protected, lengths=None, keep_identity=True):
    """Return source edge parameters using lengths as the sampling measure."""
    pins = sorted(set(protected)) or [0]
    if target < len(pins):
        raise ConstraintError(len(pins))
    if any(not isinstance(pin, int) or not 0 <= pin < n for pin in pins):
        raise ResampleError("受保护边的列编号必须对应原始圆周环。")
    measures = list(lengths) if lengths is not None else [1.0] * n
    if len(measures) != n or any(not math.isfinite(value) or value <= 0.0 for value in measures):
        raise ResampleError("圆周环包含零长度边或无效采样间距，请先修复模型。")
    scale = max(measures)
    measures = [value / scale for value in measures]
    if min(measures) <= max(measures) * 1.0e-12:
        raise ResampleError("圆周环包含退化的短边，请先合并重合顶点。")
    if max(measures) - min(measures) <= 1.0e-12:
        measures = [1.0] * n
    if target == n and keep_identity:
        return [float(i) for i in range(n)]
    cumulative = [0.0]
    for value in measures:
        cumulative.append(cumulative[-1] + value)
    # Repeat the measure for the interval that crosses source column zero.
    period = cumulative[-1]
    cumulative += [period + value for value in cumulative[1:]]
    ends = pins[1:] + [pins[0] + n]
    intervals = [cumulative[end] - cumulative[pin] for pin, end in zip(pins, ends)]
    allocations = _allocations(intervals, target)
    result = []
    for pin, interval, count in zip(pins, intervals, allocations):
        result.append(float(pin))
        for j in range(1, count):
            position = cumulative[pin] + interval * j / count
            column = min(2 * n - 1, bisect_right(cumulative, position) - 1)
            fraction = (position - cumulative[column]) / measures[column % n]
            value = column + fraction
            nearest = round(value)
            value = float(nearest) if abs(value - nearest) <= _EPS else value
            result.append(value % n)
    return sorted(result)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _remap_ring_samples(samples, protected, source_lengths, ring_lengths):
    """Keep shared pinned columns while distributing angles within each ring."""
    n = len(source_lengths)
    pin_set = set(protected) or {0}
    pins = sorted(pin_set)

    def cumulative(lengths):
        result = [0.0]
        for value in lengths:
            result.append(result[-1] + value)
        return result

    source_measure = cumulative(source_lengths)
    ring_measure = cumulative(ring_lengths)

    def measure_at(t, measure, lengths):
        turns = math.floor(t / n)
        local = t - turns * n
        column = min(n - 1, int(math.floor(local)))
        return turns * measure[-1] + measure[column] + (local - column) * lengths[column]

    def parameter_at(position):
        turns = math.floor(position / ring_measure[-1])
        local = position - turns * ring_measure[-1]
        column = min(n - 1, max(0, bisect_right(ring_measure, local) - 1))
        value = turns * n + column + (local - ring_measure[column]) / ring_lengths[column]
        nearest = round(value)
        return float(nearest) if abs(value - nearest) <= _EPS else value

    result = []
    for sample in samples:
        if sample in pin_set:
            result.append(float(sample))
            continue
        index = bisect_right(pins, sample) - 1
        start = pins[index] if index >= 0 else pins[-1] - n
        end = pins[index + 1] if index + 1 < len(pins) else pins[0] + n
        source_start = measure_at(start, source_measure, source_lengths)
        source_span = measure_at(end, source_measure, source_lengths) - source_start
        fraction = (measure_at(sample, source_measure, source_lengths) - source_start) / source_span
        ring_start = measure_at(start, ring_measure, ring_lengths)
        ring_span = measure_at(end, ring_measure, ring_lengths) - ring_start
        # Do not reduce modulo n: a nonzero seam can move the wrap across a sample.
        result.append(parameter_at(ring_start + fraction * ring_span))
    return result


def _norm(values):
    # Two-argument hypot is available in Maya 2022's Python 3.7 and stays scaled.
    return math.hypot(math.hypot(values[0], values[1]), values[2])


def _distance(a, b):
    return math.hypot(math.hypot(a[0] - b[0], a[1] - b[1]), a[2] - b[2])


def _ring_lengths(ring_points):
    n = len(ring_points)
    lengths = [_distance(ring_points[i], ring_points[(i + 1) % n]) for i in range(n)]
    longest = max(lengths)
    if not math.isfinite(longest) or longest == 0.0 or min(lengths) <= longest * 1.0e-12:
        raise ResampleError("圆周环包含重合顶点或零长度边，请先修复模型。")
    return lengths


def _polyline_point(source, t):
    floored = math.floor(t)
    index = int(floored) % len(source)
    fraction = t - floored
    p0 = source[index]
    p1 = source[(index + 1) % len(source)]
    one_minus = 1.0 - fraction
    return (p0[0] * one_minus + p1[0] * fraction,
            p0[1] * one_minus + p1[1] * fraction,
            p0[2] * one_minus + p1[2] * fraction)


def _valid_face(face, points):
    p0 = points[face[0]]
    p0x, p0y, p0z = p0[0], p0[1], p0[2]
    local = [(points[v][0] - p0x, points[v][1] - p0y, points[v][2] - p0z) for v in face]
    scale = max(math.hypot(math.hypot(x, y), z) for x, y, z in local)
    if not math.isfinite(scale) or scale == 0.0:
        return False
    scaled = [(x / scale, y / scale, z / scale) for x, y, z in local]
    nx = ny = nz = 0.0
    m = len(scaled)
    for i in range(m):
        ax, ay, az = scaled[i]
        bx, by, bz = scaled[(i + 1) % m]
        nx += ay * bz - az * by
        ny += az * bx - ax * bz
        nz += ax * by - ay * bx
    return math.hypot(math.hypot(nx, ny), nz) > 1.0e-14


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(value):
    length = _norm(value)
    if length < _EPS:
        raise ResampleError("有圆周环退化，无法拟合圆形。")
    return (value[0] / length, value[1] / length, value[2] / length)


def _solve(matrix, values):
    rows = [list(row) + [value] for row, value in zip(matrix, values)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda i: abs(rows[i][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < _EPS:
            raise ResampleError("有圆周环无法拟合圆形，请使用保留轮廓模式。")
        divisor = rows[col][col]
        rows[col] = [value / divisor for value in rows[col]]
        for i in range(3):
            if i != col:
                factor = rows[i][col]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[col])]
    return [row[3] for row in rows]


def _circle_sampler(ring_points, return_deltas=False):
    n = len(ring_points)
    origin = (math.fsum(p[0] for p in ring_points) / n,
              math.fsum(p[1] for p in ring_points) / n,
              math.fsum(p[2] for p in ring_points) / n)
    scale = max(_distance(point, origin) for point in ring_points)
    if not math.isfinite(scale) or scale == 0.0:
        raise ResampleError("有圆周环退化，无法拟合圆形。")
    ox, oy, oz = origin
    local = [((p[0] - ox) / scale, (p[1] - oy) / scale, (p[2] - oz) / scale)
             for p in ring_points]
    nx = ny = nz = 0.0
    for i in range(n):
        ax, ay, az = local[i]
        bx, by, bz = local[(i + 1) % n]
        nx += ay * bz - az * by
        ny += az * bx - ax * bz
        nz += ax * by - ay * bx
    normal = _unit((nx, ny, nz))
    # Pick the vertex with largest in-plane projection to avoid degenerate axis_x.
    best_proj = None
    best_len = -1.0
    for pt in local:
        dn = _dot(pt, normal)
        proj = (pt[0] - normal[0] * dn, pt[1] - normal[1] * dn, pt[2] - normal[2] * dn)
        plen = _norm(proj)
        if plen > best_len:
            best_len = plen
            best_proj = proj
    axis_x = _unit(best_proj)
    axis_y = _cross(normal, axis_x)
    xy = [(_dot(point, axis_x), _dot(point, axis_y)) for point in local]
    # Weight each sample by adjacent chord lengths so clustered vertices do not bias the fit.
    chords = [math.hypot(xy[(i + 1) % n][0] - xy[i][0], xy[(i + 1) % n][1] - xy[i][1]) for i in range(n)]
    weights = [0.5 * (chords[(i - 1) % n] + chords[i]) for i in range(n)]
    rows = [(x, y, 1.0) for x, y in xy]
    matrix = [[math.fsum(w * row[i] * row[j] for w, row in zip(weights, rows)) for j in range(3)] for i in range(3)]
    values = [math.fsum(w * row[i] * (x * x + y * y) for w, row, (x, y) in zip(weights, rows, xy)) for i in range(3)]
    a, b, c = _solve(matrix, values)
    cx, cy = a / 2.0, b / 2.0
    radius_squared = c + cx * cx + cy * cy
    if radius_squared <= _EPS:
        raise ResampleError("有圆周环半径无效，无法拟合圆形。")
    radius = math.sqrt(radius_squared)
    radial_dev = max(abs(math.hypot(x - cx, y - cy) - radius) for x, y in xy) / radius
    if radial_dev > 0.025:
        raise ResampleError(
            "拟合圆形模式要求各圈接近圆形，半径偏差需小于 2.5%（当前最大 {:.2f}%）。请使用保留轮廓模式。".format(
                radial_dev * 100.0))
    plane_dev = max(abs(_dot(point, normal)) for point in local) / radius
    if plane_dev > 0.01:
        raise ResampleError(
            "拟合圆形模式要求各圈近似共面，离面偏差需小于半径的 1%（当前最大 {:.2f}%）。请使用保留轮廓模式。".format(
                plane_dev * 100.0))
    angles = [math.atan2(y - cy, x - cx) for x, y in xy]
    deltas = [math.atan2(math.sin(angles[(i + 1) % n] - value), math.cos(angles[(i + 1) % n] - value)) for i, value in enumerate(angles)]
    if any(value <= _EPS for value in deltas) or abs(sum(deltas) - 2.0 * math.pi) > 0.01:
        raise ResampleError("拟合圆形模式需要顶点依次排列的完整圆周环。")
    ax_x, ax_y, ax_z = axis_x
    ay_x, ay_y, ay_z = axis_y
    def sample(t):
        floored = math.floor(t)
        i = int(floored) % n
        angle = angles[i] + deltas[i] * (t - floored)
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        return (ox + scale * (ax_x * x + ay_x * y),
                oy + scale * (ax_y * x + ay_y * y),
                oz + scale * (ax_z * x + ay_z * y))
    return (sample, deltas) if return_deltas else sample


def resample_mesh(points, faces, seed_edges, target_count, uv_sets=None,
                  shape_mode="contour", preserve_uvs=True, protected_columns=None,
                  metric_points=None, _analysis=None, uv_policy="strict", preferred_uv_set=None):
    """Rebuild the selected shell while retaining disconnected mesh components.

    protected_columns are source ring column indices to retain exactly, useful
    for material borders or hard longitudinal edges. UV seams add constraints.
    """
    analysis = _analysis if _analysis is not None else analyze_mesh(points, faces, seed_edges)
    try:
        target = int(target_count)
    except (TypeError, ValueError, OverflowError):
        raise ResampleError("目标段数必须是 3 到 4096 之间的整数。")
    if isinstance(target_count, bool) or target != target_count or not 3 <= target <= 4096:
        raise ResampleError("目标段数必须是 3 到 4096 之间的整数。")
    if shape_mode not in ("contour", "uniform", "circle"):
        raise ResampleError("请选择保留轮廓、均匀间距或拟合圆形模式。")
    n = analysis["source_count"]
    if metric_points is not None and (len(metric_points) != len(points) or any(
            len(point) != 3 or not all(math.isfinite(float(x)) for x in point) for point in metric_points)):
        raise ResampleError("采样距离坐标与原模型不一致或包含无效数据。")
    for ring in analysis["rings"]:
        _ring_lengths([points[vertex] for vertex in ring])
    if any(not _valid_face(faces[fi], points) for fi in analysis["face_ids"]):
        raise ResampleError("当前壳体包含零面积或退化的面，请先修复模型。")
    seed_index = analysis["seed_ring"]
    seed_vertices = analysis["rings"][seed_index]
    seed_points = [points[vertex] for vertex in seed_vertices]
    measure_points = metric_points if metric_points is not None else points
    lengths = _ring_lengths([measure_points[vertex] for vertex in seed_vertices])
    circle_samplers, circle_lengths = {}, {}
    if shape_mode == "circle":
        circle_samplers[seed_index], lengths = _circle_sampler(seed_points, return_deltas=True)
        circle_lengths[seed_index] = lengths
    uv_data = _parse_uvs(uv_sets, faces) if preserve_uvs else {}
    region_cuts = {name: region_boundaries(analysis, data) for name, data in uv_data.items()}
    set_columns = {name: {col for cuts in regions.values() for col in cuts}
                   for name, regions in region_cuts.items()}
    uv_columns = set().union(*set_columns.values())
    geometry = set()
    for column in protected_columns or ():
        if not isinstance(column, int) or not 0 <= column < n:
            raise ResampleError("受保护边的列编号必须对应原始圆周环。")
        geometry.add(column)
    plan = plan_uv_reduction(geometry, uv_columns, target, uv_policy)
    if not plan["feasible"]:
        raise ConstraintError(plan["minimum_count"])
    transfer = plan["transfer_required"]
    constraints = (fixed_columns(geometry, set_columns, region_cuts, target, preferred_uv_set) if transfer
                   else geometry | uv_columns)
    protected = set(constraints) if constraints else {0}
    if shape_mode == "contour" and target >= n:
        protected.update(range(n))
    samples = _samples(n, target, protected, lengths=lengths, keep_identity=shape_mode == "contour")
    removed = set(analysis["face_ids"])
    outside = [fi for fi in range(len(faces)) if fi not in removed]
    preserved = set(vertex for fi in outside for vertex in faces[fi])
    preserved.update(cap["center"] for cap in analysis["caps"] if cap["kind"] == "fan")
    old_to_new = {vertex: i for i, vertex in enumerate(sorted(preserved))}
    new_points = [tuple(points[vertex]) for vertex in sorted(preserved)]
    new_rings, ring_samples = [], []
    for ri, ring in enumerate(analysis["rings"]):
        ring_points = [points[vertex] for vertex in ring]
        if shape_mode == "circle":
            if ri not in circle_samplers:
                circle_samplers[ri], circle_lengths[ri] = _circle_sampler(ring_points, return_deltas=True)
            sampler = circle_samplers[ri]
            parameters = samples if ri == seed_index else _remap_ring_samples(
                samples, protected, lengths, circle_lengths[ri])
        else:
            def sampler(t, source=ring_points):
                return _polyline_point(source, t)
            parameters = samples
        ring_samples.append(list(parameters))
        new_ring = []
        for t in parameters:
            new_ring.append(len(new_points))
            point = sampler(t)
            if not all(math.isfinite(value) for value in point):
                raise ResampleError("重采样产生无效顶点，模型坐标范围过大或圆周环退化。")
            new_points.append(point)
        new_rings.append(new_ring)
    new_faces, face_sources = [], []

    samplers_by_set = {name: [] for name in uv_data}

    def region_samplers(region):
        sources = region["faces"]
        if region.get("kind") == "ngon":
            sources = sources * n
        samplers = {}
        for name, data in uv_data.items():
            cuts = region_cuts[name].get(sources[0], ()) if transfer else ()
            sampler = RegionUVSampler(data, sources, cuts)
            samplers[name] = sampler
            samplers_by_set[name].append(sampler)
        return samplers


    def emit(face, source, uvs):
        if source in removed and not _valid_face(face, new_points):
            raise ResampleError("目标段数产生零面积或退化的面，请调整段数或修复原始轮廓。")
        new_faces.append(face)
        face_sources.append(source)
        for name, data in uv_data.items():
            corners = uvs[name]
            if corners is None or any(value is None for value in corners):
                data["counts"].append(0)
            else:
                data["counts"].append(len(face))
                data["ids"].extend(corners)

    for fi in outside:
        emit([old_to_new[v] for v in faces[fi]], fi,
             {name: [data["corners"][fi][v] for v in faces[fi]] if data["corners"][fi] is not None else None for name, data in uv_data.items()})
    for band in analysis["bands"]:
        ra, rb = band["rings"]
        old_a, old_b = analysis["rings"][ra], analysis["rings"][rb]
        samplers = region_samplers(band)
        for j, ta in enumerate(samples):
            next_j = (j + 1) % target
            tb = samples[next_j] if next_j else samples[0] + float(n)
            column = int(math.floor((ta + tb) / 2.0)) % n
            fi = band["faces"][column]
            face = [new_rings[ra][j], new_rings[ra][next_j], new_rings[rb][next_j], new_rings[rb][j]]
            a_start, b_start = ring_samples[ra][j], ring_samples[rb][j]
            a_end = ring_samples[ra][next_j] if next_j else ring_samples[ra][0] + float(n)
            b_end = ring_samples[rb][next_j] if next_j else ring_samples[rb][0] + float(n)
            uvs = {name: sampler.side(old_a, old_b, a_start, a_end, b_start, b_end)
                   for name, sampler in samplers.items()}
            if _edge_direction(faces[fi], old_a[column], old_a[(column + 1) % n]) < 0:
                face.reverse()
                for ids in uvs.values():
                    ids.reverse()
            emit(face, fi, uvs)
    for cap in analysis["caps"]:
        ri = cap["ring"]
        old_ring, new_ring = analysis["rings"][ri], new_rings[ri]
        samplers = region_samplers(cap)
        if cap["kind"] == "ngon":
            fi = cap["faces"][0]
            face = list(new_ring)
            uvs = {name: [sampler.ring_uv(old_ring, t) for t in ring_samples[ri]]
                   for name, sampler in samplers.items()}
            if _edge_direction(faces[fi], old_ring[0], old_ring[1]) < 0:
                face.reverse()
                for ids in uvs.values():
                    ids.reverse()
            emit(face, fi, uvs)
        else:
            for j, ta in enumerate(ring_samples[ri]):
                next_j = (j + 1) % target
                tb = ring_samples[ri][next_j] if next_j else ring_samples[ri][0] + float(n)
                column = int(math.floor((ta + tb) / 2.0)) % n
                fi = cap["faces"][column]
                face = [new_ring[j], new_ring[next_j], old_to_new[cap["center"]]]
                uvs = {name: sampler.fan(old_ring, cap["center"], fi, ta, tb)
                       for name, sampler in samplers.items()}
                if _edge_direction(faces[fi], old_ring[column], old_ring[(column + 1) % n]) < 0:
                    face.reverse()
                    for ids in uvs.values():
                        ids.reverse()
                emit(face, fi, uvs)
    per_ring_spacing = []
    for ri, new_ring in enumerate(new_rings):
        measured_ring = [new_points[vertex] for vertex in new_ring]
        if metric_points is not None and shape_mode != "circle":
            measured_source = [metric_points[vertex] for vertex in analysis["rings"][ri]]
            measured_ring = [_polyline_point(measured_source, t) for t in ring_samples[ri]]
        new_lengths = _ring_lengths(measured_ring)
        shortest, longest = min(new_lengths), max(new_lengths)
        per_ring_spacing.append({"ring": ri, "min_segment_length": shortest,
                                 "max_segment_length": longest,
                                 "mean_segment_length": math.fsum(new_lengths) / target,
                                 "spacing_ratio": longest / shortest})
    seed_spacing = per_ring_spacing[seed_index]
    worst_spacing = max(per_ring_spacing, key=lambda item: item["spacing_ratio"])
    stats = {"min_segment_length": seed_spacing["min_segment_length"],
             "max_segment_length": seed_spacing["max_segment_length"],
             "mean_segment_length": seed_spacing["mean_segment_length"],
             "spacing_ratio": seed_spacing["spacing_ratio"], "protected_count": len(protected),
             "constraint_columns_count": len(constraints), "segment_count": target,
             "seed_ring": seed_index, "shape_mode": shape_mode,
             "measurement_space": "world" if metric_points is not None and shape_mode != "circle" else "object",
             "sampling_measure": "angle" if shape_mode == "circle" else "arc_length",
             "per_ring_spacing": tuple(per_ring_spacing),
             "worst_spacing_ratio": worst_spacing["spacing_ratio"],
             "worst_ring": worst_spacing["ring"]}
    if shape_mode == "circle":
        pins = sorted(protected)
        positions = {sample: index for index, sample in enumerate(samples)}
        pin_indices = [positions[float(pin)] for pin in pins]
        shared_counts = tuple((pin_indices[(i + 1) % len(pins)] - index) % target or target
                              for i, index in enumerate(pin_indices))
        fraction_sets = []
        for ri, ring_spacing in enumerate(per_ring_spacing):
            details = analyze_spacing(
                circle_lengths[ri], pins, target, find_compatible_counts=False)
            fraction_sets.append(details["interval_fractions"])
            limited = any(abs(ideal - actual) > 1.0e-7 for ideal, actual in zip(
                details["ideal_interval_counts"], shared_counts))
            ring_spacing.update({"spacing_limited_by_constraints": limited,
                                 "interval_fractions": details["interval_fractions"],
                                 "ideal_interval_counts": details["ideal_interval_counts"],
                                 "actual_interval_counts": shared_counts})
        compatible_counts = find_common_compatible_counts(fraction_sets, target, len(pins))
        stats.update({"spacing_limited_by_constraints": any(
            ring_spacing["spacing_limited_by_constraints"] for ring_spacing in per_ring_spacing),
                      "compatible_counts": compatible_counts})
    else:
        stats.update({"spacing_limited_by_constraints": False, "compatible_counts": ()})
    warnings = []
    relaxed = {column for samplers in samplers_by_set.values()
               for sampler in samplers for column in sampler.relaxed}
    affected = {name: sum(sampler.transferred for sampler in samplers)
                for name, samplers in samplers_by_set.items()}
    stats.update(uv_policy=uv_policy, uv_transfer_used=transfer,
                 relaxed_uv_count=len(relaxed), uv_transfer_faces=affected)
    if transfer:
        warnings.append("自适应 UV 减段跨越了 {} 列原接缝。每个跨缝面采用覆盖较多的一侧 UV，"
                        "部分原 UV 岛或贴图细节可能丢失。各 UV 集近似面数：{}。".format(
                            len(relaxed), "、".join("{} {} 面".format(name, affected[name])
                                                   for name in sorted(affected))))
    if target < n:
        warnings.append("减少段数会近似原表面，保留采样点之间的几何和 UV 插值可能发生变化。")
    if shape_mode == "uniform":
        warnings.append("均匀间距按所选边环的弧长重采样，未受保护的原角点可能移动，原轮廓与 UV 插值会产生近似变化。")
    elif shape_mode == "circle":
        warnings.append("拟合圆形按各环的角度重采样，固定列保持对应，顶点会投影到各自的拟合圆。")
    if shape_mode == "contour" and target > n and stats["spacing_ratio"] > 1.1:
        warnings.append("原角点已固定且新增段已沿圆周分散；非整数倍增段或原边长不同仍会有宽窄差异，可使用均匀重采样模式。")
    if (shape_mode == "circle" and stats["spacing_limited_by_constraints"] or
            constraints and shape_mode == "uniform" and stats["worst_spacing_ratio"] > 1.1):
        warnings.append("UV 接缝、材质边界或硬边会固定采样列，约束区间的整数分段可能限制间距均匀程度。")
    if preserve_uvs and analysis["caps"] and not transfer:
        warnings.append("端盖的 UV 边界和分岛已保留，内部插值可能随拓扑变化。")
    compact_uvs = {}
    for name, data in uv_data.items():
        remap = {}
        new_u, new_v, new_ids = [], [], []
        src_u, src_v = data["u"], data["v"]
        for uv_id in data["ids"]:
            mapped = remap.get(uv_id)
            if mapped is None:
                mapped = len(new_u)
                remap[uv_id] = mapped
                new_u.append(src_u[uv_id])
                new_v.append(src_v[uv_id])
            new_ids.append(mapped)
        compact_uvs[name] = {"u": new_u, "v": new_v, "counts": data["counts"], "ids": new_ids}
    return {"points": new_points, "faces": new_faces,
            "uv_sets": compact_uvs,
            "face_sources": face_sources, "analysis": analysis, "samples": samples,
            "ring_samples": ring_samples,
            "new_rings": new_rings, "old_to_new": old_to_new,
            "protected_columns": sorted(protected), "warnings": warnings,
            "stats": stats, "diagnostics": dict(stats)}
