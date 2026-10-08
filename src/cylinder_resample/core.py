"""Topology-based cylinder resampling without dependencies on Maya."""

import math
from bisect import bisect_right


class ResampleError(ValueError):
    pass


class ConstraintError(ResampleError):
    def __init__(self, minimum_count):
        self.minimum_count = minimum_count
        super().__init__("保留现有 UV 接缝和受保护边至少需要 %d 段，请提高目标段数。" % minimum_count)


_EPS = 1.0e-9


def _edge(a, b):
    return (a, b) if a < b else (b, a)


def _edge_direction(face, a, b):
    for i, vertex in enumerate(face):
        other = face[(i + 1) % len(face)]
        if vertex == a and other == b:
            return 1
        if vertex == b and other == a:
            return -1
    return 0


def analyze_mesh(points, faces, seed_edges):
    """Identify a closed circumference and its entire regular connected shell.

    Ring columns remain in correspondence throughout the shell. Bands contain
    one source face per column and refer to indices in the returned rings list.
    """
    if not points or not faces:
        raise ResampleError("模型没有可处理的多边形网格。")
    for point in points:
        if len(point) != 3 or not all(math.isfinite(float(x)) for x in point):
            raise ResampleError("模型中存在无效的顶点坐标。")
    edge_faces = {}
    neighbors = {}
    vertex_faces = {}
    for fi, face in enumerate(faces):
        if len(face) < 3 or len(set(face)) != len(face):
            raise ResampleError("模型中存在重复顶点或不足三个顶点的面。")
        if any(not isinstance(v, int) or v < 0 or v >= len(points) for v in face):
            raise ResampleError("模型中存在无效的面顶点编号。")
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            edge_faces.setdefault(_edge(a, b), []).append(fi)
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)
            vertex_faces.setdefault(a, set()).add(fi)
    selected = set()
    loop_neighbors = {}
    for pair in seed_edges:
        if len(pair) != 2 or pair[0] == pair[1]:
            raise ResampleError("请选择一整圈闭合的圆周边环。")
        a, b = pair
        key = _edge(a, b)
        if key not in edge_faces or key in selected:
            raise ResampleError("选择中存在无效边或重复边。")
        selected.add(key)
        loop_neighbors.setdefault(a, []).append(b)
        loop_neighbors.setdefault(b, []).append(a)
    if len(selected) < 3 or any(len(v) != 2 for v in loop_neighbors.values()):
        raise ResampleError("请选择一整圈闭合的圆周边环。")
    start = min(loop_neighbors)
    ring = [start]
    ring_vertices = {start}
    previous, current = start, min(loop_neighbors[start])
    while current != start:
        if current in ring_vertices:
            raise ResampleError("选择中包含多个边环，请只选择一整圈圆周边。")
        ring.append(current)
        ring_vertices.add(current)
        choices = loop_neighbors[current]
        previous, current = current, choices[0] if choices[0] != previous else choices[1]
    if len(ring) != len(selected):
        raise ResampleError("请只选择一个连续、闭合的圆周边环。")
    n = len(ring)
    component_vertices = set(ring)
    pending = list(ring)
    while pending:
        vertex = pending.pop()
        for other in neighbors.get(vertex, ()):
            if other not in component_vertices:
                component_vertices.add(other)
                pending.append(other)
    component_faces = set()
    for vertex in component_vertices:
        component_faces.update(vertex_faces.get(vertex, ()))
    for key, attached in edge_faces.items():
        if key[0] not in component_vertices:
            continue
        if len(attached) > 2:
            raise ResampleError("当前壳体有非流形边，请先修复拓扑。")
        if len(attached) == 2:
            a, b = key
            if _edge_direction(faces[attached[0]], a, b) == _edge_direction(faces[attached[1]], a, b):
                raise ResampleError("当前壳体的面朝向不一致，请先统一法线方向。")
    for vertex in component_vertices:
        attached_faces = vertex_faces[vertex]
        face_neighbors = {fi: set() for fi in attached_faces}
        boundary_count = 0
        for other in neighbors[vertex]:
            attached = edge_faces[_edge(vertex, other)]
            if len(attached) == 1:
                boundary_count += 1
            else:
                face_neighbors[attached[0]].add(attached[1])
                face_neighbors[attached[1]].add(attached[0])
        visited = {next(iter(attached_faces))}
        pending_faces = list(visited)
        while pending_faces:
            for fi in face_neighbors[pending_faces.pop()]:
                if fi not in visited:
                    visited.add(fi)
                    pending_faces.append(fi)
        if boundary_count not in (0, 2) or visited != attached_faces:
            raise ResampleError("当前壳体有非流形顶点，请先修复拓扑。")

    def classify(current_ring, per_edge):
        if all(fi is None for fi in per_edge):
            return "open", None
        if any(fi is None for fi in per_edge):
            raise ResampleError("圆柱侧面存在开孔或分支，第一版暂不支持此结构。")
        unique = set(per_edge)
        if len(unique) == 1:
            fi = per_edge[0]
            if len(faces[fi]) == n and set(faces[fi]) == set(current_ring):
                return "ngon", {"kind": "ngon", "faces": [fi]}
        if len(unique) == n and all(len(faces[fi]) == 3 for fi in per_edge):
            current_set = set(current_ring)
            centers = [set(faces[fi]) - current_set for fi in per_edge]
            if all(len(item) == 1 for item in centers) and len(set(next(iter(item)) for item in centers)) == 1:
                center = next(iter(centers[0]))
                for i, fi in enumerate(per_edge):
                    if set(faces[fi]) != {current_ring[i], current_ring[(i + 1) % n], center}:
                        break
                else:
                    return "fan", {"kind": "fan", "faces": list(per_edge), "center": center}
        if len(unique) != n or not all(len(faces[fi]) == 4 for fi in per_edge):
            raise ResampleError("第一版需要连续四边形环带，端盖需为单个多边形或单中心三角扇。")
        next_ring = [None] * n
        current_set = set(current_ring)
        for i, fi in enumerate(per_edge):
            a, b = current_ring[i], current_ring[(i + 1) % n]
            face = faces[fi]
            if len(current_set.intersection(face)) != 2:
                raise ResampleError("有四边形跨越圆周环或形成侧面分支，无法建立对应关系。")
            ia, ib = face.index(a), face.index(b)
            if (ia + 1) % 4 == ib:
                va, vb = face[(ia - 1) % 4], face[(ib + 1) % 4]
            elif (ib + 1) % 4 == ia:
                va, vb = face[(ia + 1) % 4], face[(ib - 1) % 4]
            else:
                raise ResampleError("所选边环没有连接规则的四边形环带。")
            for column, vertex in ((i, va), ((i + 1) % n, vb)):
                if next_ring[column] is not None and next_ring[column] != vertex:
                    raise ResampleError("相邻四边形的顶点对应不一致，无法追踪圆周环。")
                next_ring[column] = vertex
        if len(set(next_ring)) != n or set(next_ring).intersection(current_set):
            raise ResampleError("四边形环带发生自连接或段数变化，第一版要求各圈段数一致。")
        return "band", next_ring

    def trace(side):
        traced_rings, traced_bands = [], []
        current_ring = ring
        incoming = set()
        seen = set(ring)
        while True:
            per_edge = []
            for i, a in enumerate(current_ring):
                b = current_ring[(i + 1) % n]
                options = [fi for fi in edge_faces[_edge(a, b)] if fi not in incoming]
                if not incoming:
                    options = [fi for fi in options if _edge_direction(faces[fi], a, b) == side]
                if len(options) > 1:
                    raise ResampleError("当前壳体在圆周环处存在分支，第一版暂不支持。")
                per_edge.append(options[0] if options else None)
            kind, payload = classify(current_ring, per_edge)
            if kind != "band":
                return traced_rings, traced_bands, payload
            if seen.intersection(payload):
                raise ResampleError("第一版暂不支持闭合环面或首尾相连的管体。")
            seen.update(payload)
            traced_rings.append(payload)
            traced_bands.append(list(per_edge))
            incoming = set(per_edge)
            current_ring = payload

    negative_rings, negative_bands, negative_cap = trace(-1)
    positive_rings, positive_bands, positive_cap = trace(1)
    rings = list(reversed(negative_rings)) + [ring] + positive_rings
    band_faces = list(reversed(negative_bands)) + positive_bands
    bands = [{"rings": (i, i + 1), "faces": item} for i, item in enumerate(band_faces)]
    caps = []
    if negative_cap:
        negative_cap["ring"] = 0
        caps.append(negative_cap)
    if positive_cap:
        positive_cap["ring"] = len(rings) - 1
        caps.append(positive_cap)
    used = set(fi for item in band_faces for fi in item)
    for cap in caps:
        used.update(cap["faces"])
    if not bands:
        raise ResampleError("所选边环没有可处理的圆柱四边形环带。")
    if used != component_faces:
        raise ResampleError("当前壳体有额外几何、开孔或不支持的端盖布线，无法完整重建。")
    all_ring_vertices = [vertex for item in rings for vertex in item]
    if len(set(all_ring_vertices)) != len(all_ring_vertices):
        raise ResampleError("当前壳体存在环带自连接，第一版暂不支持。")
    return {"rings": rings, "bands": bands, "band_faces": band_faces, "caps": caps,
            "source_count": n, "segment_count": n, "ring_count": len(rings),
            "face_ids": sorted(component_faces), "component_vertices": sorted(component_vertices),
            "seed_ring": len(negative_rings)}


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
    n = analysis["source_count"]
    protected = set()
    for data in uv_data.values():
        corners = data["corners"]
        for band in analysis["bands"]:
            ra, rb = (analysis["rings"][i] for i in band["rings"])
            for i in range(n):
                left = corners[band["faces"][(i - 1) % n]]
                right = corners[band["faces"][i]]
                if (left is None) != (right is None):
                    protected.add(i)
                elif left is not None and (left[ra[i]] != right[ra[i]] or left[rb[i]] != right[rb[i]]):
                    protected.add(i)
        for cap in analysis["caps"]:
            if cap["kind"] != "fan":
                continue
            ring = analysis["rings"][cap["ring"]]
            for i in range(n):
                left = corners[cap["faces"][(i - 1) % n]]
                right = corners[cap["faces"][i]]
                if (left is None) != (right is None):
                    protected.add(i)
                elif left is not None:
                    left_center, right_center = left[cap["center"]], right[cap["center"]]
                    # Coincident fan-center UV indices need not pin the radial edge.
                    if (left[ring[i]] != right[ring[i]] or
                            data["u"][left_center] != data["u"][right_center] or
                            data["v"][left_center] != data["v"][right_center]):
                        protected.add(i)
    return protected


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
    return tuple(x - y for x, y in zip(a, b))


def _norm(values):
    # Two-argument hypot is available in Maya 2022's Python 3.7 and stays scaled.
    length = 0.0
    for value in values:
        length = math.hypot(length, value)
    return length


def _distance(a, b):
    return _norm(_sub(a, b))


def _ring_lengths(ring_points):
    lengths = [_distance(point, ring_points[(i + 1) % len(ring_points)])
               for i, point in enumerate(ring_points)]
    longest = max(lengths)
    if not math.isfinite(longest) or longest == 0.0 or min(lengths) <= longest * 1.0e-12:
        raise ResampleError("圆周环包含重合顶点或零长度边，请先修复模型。")
    return lengths


def _polyline_point(source, t):
    index, fraction = int(math.floor(t)) % len(source), t - math.floor(t)
    return tuple(source[index][j] * (1.0 - fraction) + source[(index + 1) % len(source)][j] * fraction
                 for j in range(3))


def _valid_face(face, points):
    local = [_sub(points[vertex], points[face[0]]) for vertex in face]
    scale = max(_norm(point) for point in local)
    if not math.isfinite(scale) or scale == 0.0:
        return False
    local = [tuple(value / scale for value in point) for point in local]
    normal = [0.0, 0.0, 0.0]
    for i, point in enumerate(local):
        cross = _cross(point, local[(i + 1) % len(local)])
        normal = [a + b for a, b in zip(normal, cross)]
    return _norm(normal) > 1.0e-14


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(value):
    length = _norm(value)
    if length < _EPS:
        raise ResampleError("有圆周环退化，无法拟合圆形。")
    return tuple(x / length for x in value)


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
    origin = tuple(sum(point[j] for point in ring_points) / n for j in range(3))
    scale = max(_distance(point, origin) for point in ring_points)
    if not math.isfinite(scale) or scale == 0.0:
        raise ResampleError("有圆周环退化，无法拟合圆形。")
    local = [tuple(value / scale for value in _sub(point, origin)) for point in ring_points]
    normal = [0.0, 0.0, 0.0]
    for i, point in enumerate(local):
        cross = _cross(point, local[(i + 1) % n])
        normal = [a + b for a, b in zip(normal, cross)]
    normal = _unit(normal)
    first = local[0]
    axis_x = _unit(tuple(first[j] - normal[j] * _dot(first, normal) for j in range(3)))
    axis_y = _cross(normal, axis_x)
    xy = [(_dot(point, axis_x), _dot(point, axis_y)) for point in local]
    # Fit the circle in its own plane so translated, tilted rings work equally.
    rows = [(x, y, 1.0) for x, y in xy]
    matrix = [[sum(row[i] * row[j] for row in rows) for j in range(3)] for i in range(3)]
    values = [sum(row[i] * (x * x + y * y) for row, (x, y) in zip(rows, xy)) for i in range(3)]
    a, b, c = _solve(matrix, values)
    cx, cy = a / 2.0, b / 2.0
    radius_squared = c + cx * cx + cy * cy
    if radius_squared <= _EPS:
        raise ResampleError("有圆周环半径无效，无法拟合圆形。")
    radius = math.sqrt(radius_squared)
    if max(abs(math.hypot(x - cx, y - cy) - radius) for x, y in xy) > radius * 0.025:
        raise ResampleError("拟合圆形模式要求各圈接近圆形，半径偏差需小于 2.5%。请使用保留轮廓模式。")
    if max(abs(_dot(point, normal)) for point in local) > radius * 0.01:
        raise ResampleError("拟合圆形模式要求各圈近似共面，离面偏差需小于半径的 1%。请使用保留轮廓模式。")
    angles = [math.atan2(y - cy, x - cx) for x, y in xy]
    deltas = [math.atan2(math.sin(angles[(i + 1) % n] - value), math.cos(angles[(i + 1) % n] - value)) for i, value in enumerate(angles)]
    if any(value <= _EPS for value in deltas) or abs(sum(deltas) - 2.0 * math.pi) > 0.01:
        raise ResampleError("拟合圆形模式需要顶点依次排列的完整圆周环。")
    def sample(t):
        i = int(math.floor(t)) % n
        angle = angles[i] + deltas[i] * (t - math.floor(t))
        x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        return tuple(origin[j] + scale * (axis_x[j] * x + axis_y[j] * y) for j in range(3))
    return (sample, deltas) if return_deltas else sample


def resample_mesh(points, faces, seed_edges, target_count, uv_sets=None,
                  shape_mode="contour", preserve_uvs=True, protected_columns=None,
                  metric_points=None, _analysis=None):
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
    circle_samplers = {}
    if shape_mode == "circle":
        circle_samplers[seed_index], lengths = _circle_sampler(seed_points, return_deltas=True)
    uv_data = _parse_uvs(uv_sets, faces) if preserve_uvs else {}
    constraints = _uv_constraints(analysis, uv_data)
    for column in protected_columns or ():
        if not isinstance(column, int) or not 0 <= column < n:
            raise ResampleError("受保护边的列编号必须对应原始圆周环。")
        constraints.add(column)
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
    new_rings = []
    for ri, ring in enumerate(analysis["rings"]):
        ring_points = [points[vertex] for vertex in ring]
        if shape_mode == "circle":
            sampler = circle_samplers[ri] if ri in circle_samplers else _circle_sampler(ring_points)
        else:
            def sampler(t, source=ring_points):
                return _polyline_point(source, t)
        new_ring = []
        for t in samples:
            new_ring.append(len(new_points))
            point = sampler(t)
            if not all(math.isfinite(value) for value in point):
                raise ResampleError("重采样产生无效顶点，模型坐标范围过大或圆周环退化。")
            new_points.append(point)
        new_rings.append(new_ring)
    new_faces, face_sources = [], []

    def uv_between(data, first_id, second_id, fraction):
        if fraction <= _EPS:
            return first_id
        if fraction >= 1.0 - _EPS:
            return second_id
        if first_id == second_id:
            return first_id
        if first_id > second_id:
            first_id, second_id, fraction = second_id, first_id, 1.0 - fraction
        key = (first_id, second_id, round(fraction, 12))
        if key not in data["cache"]:
            data["cache"][key] = len(data["u"])
            data["u"].append(data["u"][first_id] * (1 - fraction) + data["u"][second_id] * fraction)
            data["v"].append(data["v"][first_id] * (1 - fraction) + data["v"][second_id] * fraction)
        return data["cache"][key]

    def column_at(t, end=False):
        rounded = round(t)
        if abs(t - rounded) <= _EPS:
            return ((int(rounded) - 1) % n, 1.0) if end else (int(rounded) % n, 0.0)
        base = math.floor(t)
        return int(base) % n, t - base

    def ring_uv(data, source_faces, source_ring, t, end=False):
        column, fraction = column_at(t, end)
        corners = data["corners"][source_faces[column]]
        if corners is None:
            return None
        return uv_between(data, corners[source_ring[column]], corners[source_ring[(column + 1) % n]], fraction)

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
        for j, ta in enumerate(samples):
            next_j = (j + 1) % target
            tb = samples[next_j] if next_j else samples[0] + float(n)
            column = int(math.floor((ta + tb) / 2.0)) % n
            fi = band["faces"][column]
            face = [new_rings[ra][j], new_rings[ra][next_j], new_rings[rb][next_j], new_rings[rb][j]]
            uvs = {name: [ring_uv(data, band["faces"], old_a, ta), ring_uv(data, band["faces"], old_a, tb, True),
                          ring_uv(data, band["faces"], old_b, tb, True), ring_uv(data, band["faces"], old_b, ta)] for name, data in uv_data.items()}
            if _edge_direction(faces[fi], old_a[column], old_a[(column + 1) % n]) < 0:
                face.reverse()
                for ids in uvs.values():
                    ids.reverse()
            emit(face, fi, uvs)
    for cap in analysis["caps"]:
        ri = cap["ring"]
        old_ring, new_ring = analysis["rings"][ri], new_rings[ri]
        if cap["kind"] == "ngon":
            fi = cap["faces"][0]
            face = list(new_ring)
            uvs = {name: [ring_uv(data, [fi] * n, old_ring, t) for t in samples] for name, data in uv_data.items()}
            if _edge_direction(faces[fi], old_ring[0], old_ring[1]) < 0:
                face.reverse()
                for ids in uvs.values():
                    ids.reverse()
            emit(face, fi, uvs)
        else:
            for j, ta in enumerate(samples):
                next_j = (j + 1) % target
                tb = samples[next_j] if next_j else samples[0] + float(n)
                column = int(math.floor((ta + tb) / 2.0)) % n
                fi = cap["faces"][column]
                face = [new_ring[j], new_ring[next_j], old_to_new[cap["center"]]]
                uvs = {}
                for name, data in uv_data.items():
                    corner = data["corners"][fi]
                    uvs[name] = [ring_uv(data, cap["faces"], old_ring, ta), ring_uv(data, cap["faces"], old_ring, tb, True),
                                 corner[cap["center"]] if corner is not None else None]
                if _edge_direction(faces[fi], old_ring[column], old_ring[(column + 1) % n]) < 0:
                    face.reverse()
                    for ids in uvs.values():
                        ids.reverse()
                emit(face, fi, uvs)
    measured_ring = [new_points[vertex] for vertex in new_rings[seed_index]]
    if metric_points is not None and shape_mode != "circle":
        measured_source = [metric_points[vertex] for vertex in seed_vertices]
        measured_ring = [_polyline_point(measured_source, t) for t in samples]
    new_lengths = _ring_lengths(measured_ring)
    shortest, longest = min(new_lengths), max(new_lengths)
    stats = {"min_segment_length": shortest, "max_segment_length": longest,
             "mean_segment_length": math.fsum(new_lengths) / target,
             "spacing_ratio": longest / shortest, "protected_count": len(protected),
             "constraint_columns_count": len(constraints), "segment_count": target,
             "seed_ring": seed_index, "shape_mode": shape_mode,
             "measurement_space": "world" if metric_points is not None and shape_mode != "circle" else "object",
             "sampling_measure": "angle" if shape_mode == "circle" else "arc_length"}
    warnings = []
    if target < n:
        warnings.append("减少段数会近似原表面，保留采样点之间的几何和 UV 插值可能发生变化。")
    if shape_mode == "uniform":
        warnings.append("均匀间距按所选边环的弧长重采样，未受保护的原角点可能移动，原轮廓与 UV 插值会产生近似变化。")
    elif shape_mode == "circle":
        warnings.append("拟合圆形按所选边环的角度重采样，顶点会投影到拟合圆，其他环沿原对应关系采样。")
    if shape_mode == "contour" and target > n and stats["spacing_ratio"] > 1.1:
        warnings.append("原角点已固定且新增段已沿圆周分散；非整数倍增段或原边长不同仍会有宽窄差异，可使用均匀重采样模式。")
    if constraints and shape_mode != "contour" and stats["spacing_ratio"] > 1.1:
        warnings.append("UV 接缝、材质边界或硬边会固定采样列，约束区间的整数分段可能限制间距均匀程度。")
    if preserve_uvs and analysis["caps"]:
        warnings.append("端盖的 UV 边界和分岛已保留，内部插值可能随拓扑变化。")
    return {"points": new_points, "faces": new_faces,
            "uv_sets": {name: {key: data[key] for key in ("u", "v", "counts", "ids")} for name, data in uv_data.items()},
            "face_sources": face_sources, "analysis": analysis, "samples": samples,
            "new_rings": new_rings, "old_to_new": old_to_new,
            "protected_columns": sorted(protected), "warnings": warnings,
            "stats": stats, "diagnostics": dict(stats)}
