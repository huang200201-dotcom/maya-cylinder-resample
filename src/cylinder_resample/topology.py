"""Identify and validate regular ring topology on a polygon mesh."""

import math
from fractions import Fraction


class ResampleError(ValueError):
    pass


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


def _mesh_adjacency(points, faces):
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
            direction = 1 if a < b else -1
            edge_faces.setdefault(_edge(a, b), []).append((fi, direction))
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)
            vertex_faces.setdefault(a, set()).add(fi)
    return edge_faces, neighbors, vertex_faces


def _order_seed_ring(seed_edges, edge_faces):
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
    return ring


def _validate_shell(ring, edge_faces, neighbors, vertex_faces):
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
        if len(attached) == 2 and attached[0][1] == attached[1][1]:
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
                first, second = attached[0][0], attached[1][0]
                face_neighbors[first].add(second)
                face_neighbors[second].add(first)
        visited = {next(iter(attached_faces))}
        pending_faces = list(visited)
        while pending_faces:
            for fi in face_neighbors[pending_faces.pop()]:
                if fi not in visited:
                    visited.add(fi)
                    pending_faces.append(fi)
        if boundary_count not in (0, 2) or visited != attached_faces:
            raise ResampleError("当前壳体有非流形顶点，请先修复拓扑。")
    return component_vertices, component_faces


def _classify_ring(faces, current_ring, per_edge):
    n = len(current_ring)
    if all(fi is None for fi in per_edge):
        return "open", None
    if any(fi is None for fi in per_edge):
        raise ResampleError("圆周部分边连接侧面，其余为开放边界。请检查侧孔、缺面或分支。")
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
        raise ResampleError("此圈未形成逐列对应的四边形环带。请检查三角化侧面、局部倒角或端盖布线；端盖支持单个多边形或单中心三角扇。")
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
        raise ResampleError("四边形环带发生自连接或段数变化，当前环带要求各圈段数一致。")
    return "band", next_ring


def _trace_bands(ring, side, faces, edge_faces):
    n = len(ring)
    traced_rings, traced_bands = [], []
    current_ring = ring
    incoming = set()
    seen = set(ring)
    while True:
        per_edge = []
        for i, a in enumerate(current_ring):
            b = current_ring[(i + 1) % n]
            incidences = edge_faces[_edge(a, b)]
            if incoming:
                options = [fi for fi, _ in incidences if fi not in incoming]
            else:
                direction = side if a < b else -side
                options = [fi for fi, winding in incidences if winding == direction]
            if len(options) > 1:
                raise ResampleError("当前壳体在圆周环处存在分支，无法逐列重建。")
            per_edge.append(options[0] if options else None)
        kind, payload = _classify_ring(faces, current_ring, per_edge)
        if kind != "band":
            return traced_rings, traced_bands, payload, False
        # A complete inner/outer wall can return to the seed with all
        # columns aligned. Store the closing band, not a duplicate ring.
        if payload == ring and len(traced_rings) >= 2:
            traced_bands.append(list(per_edge))
            return traced_rings, traced_bands, None, True
        if seen.intersection(payload):
            raise ResampleError("环带回接时圆周列错位或部分重叠，无法按相同列闭合。")
        seen.update(payload)
        traced_rings.append(payload)
        traced_bands.append(list(per_edge))
        incoming = set(per_edge)
        current_ring = payload


def _flat_closed_profile(points, rings):
    # Exact binary coordinates distinguish a collapsed cycle from a thin washer.
    # Visit different profile rings first; an ordinary solid needs only four points.
    vertices = (vertex for column in zip(*rings) for vertex in column)
    origin = tuple(map(Fraction, points[next(vertices)]))
    vectors = (tuple(Fraction(value) - base for value, base in zip(points[vertex], origin))
               for vertex in vertices)
    axis = next((vector for vector in vectors if any(vector)), None)
    if axis is None:
        return True
    x, y, z = axis
    for a, b, c in vectors:
        normal = (y * c - z * b, z * a - x * c, x * b - y * a)
        if any(normal):
            return all(sum(v * k for v, k in zip(vector, normal)) == 0 for vector in vectors)
    return True


def analyze_mesh(points, faces, seed_edges):
    """Identify a closed circumference and its entire regular connected shell.

    Ring columns remain in correspondence throughout the shell. Bands contain
    one source face per column and refer to indices in the returned rings list.
    A closed profile connects the last band back to the seed at index zero.
    """
    edge_faces, neighbors, vertex_faces = _mesh_adjacency(points, faces)
    ring = _order_seed_ring(seed_edges, edge_faces)
    n = len(ring)
    component_vertices, component_faces = _validate_shell(ring, edge_faces, neighbors, vertex_faces)
    caps = []
    negative_rings, negative_bands, negative_cap, closed = _trace_bands(ring, -1, faces, edge_faces)
    if closed:
        seed_index = 0
        rings = [ring] + negative_rings
        if _flat_closed_profile(points, rings):
            raise ResampleError("闭合内外壁的所有顶点共面，壳体厚度为零。")
        band_faces = negative_bands
    else:
        positive_rings, positive_bands, positive_cap, _ = _trace_bands(ring, 1, faces, edge_faces)
        seed_index = len(negative_rings)
        rings = list(reversed(negative_rings)) + [ring] + positive_rings
        band_faces = list(reversed(negative_bands)) + positive_bands
        if negative_cap:
            negative_cap["ring"] = 0
            caps.append(negative_cap)
        if positive_cap:
            positive_cap["ring"] = len(rings) - 1
            caps.append(positive_cap)
    bands = [{"rings": (i, (i + 1) % len(rings)), "faces": item} for i, item in enumerate(band_faces)]
    used = set(fi for item in band_faces for fi in item)
    for cap in caps:
        used.update(cap["faces"])
    if not bands:
        raise ResampleError("所选边环没有可处理的圆柱四边形环带。")
    if used != component_faces:
        raise ResampleError("当前壳体有额外几何、开孔或不支持的端盖布线，无法完整重建。")
    all_ring_vertices = [vertex for item in rings for vertex in item]
    if len(set(all_ring_vertices)) != len(all_ring_vertices):
        raise ResampleError("当前壳体存在环带自连接，无法逐列重建。")
    return {"rings": rings, "bands": bands, "band_faces": band_faces, "caps": caps,
            "source_count": n, "segment_count": n, "ring_count": len(rings),
            "face_ids": sorted(component_faces), "component_vertices": sorted(component_vertices),
            "seed_ring": seed_index}
