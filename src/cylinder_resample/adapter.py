"""Maya integration. All geometry edits create a separate result object."""

import hashlib
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from contextlib import contextmanager

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import core


class ToolError(ValueError):
    pass


@contextmanager
def undo_chunk(name):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def _dag(path):
    selection = om.MSelectionList()
    selection.add(path)
    return selection.getDagPath(0)


def selected_edge_loop():
    selection = om.MGlobal.getActiveSelectionList()
    shape = None
    edges = set()
    for index in range(selection.length()):
        try:
            dag, component = selection.getComponent(index)
        except RuntimeError:
            raise ToolError("请选择一个模型上的完整圆周边环。")
        if component.isNull() or component.apiType() != om.MFn.kMeshEdgeComponent:
            raise ToolError("请切换到边模式，只选择一条完整的圆周边环。")
        if shape is not None and dag.fullPathName() != shape:
            raise ToolError("一次只能处理一个网格的圆周边环。")
        shape = dag.fullPathName()
        edges.update(om.MFnSingleIndexedComponent(component).getElements())
    if shape is None or not edges:
        raise ToolError("尚未选择圆周边环。")
    mesh = om.MFnMesh(_dag(shape))
    pairs = [tuple(mesh.getEdgeVertices(edge)) for edge in sorted(edges)]
    return snapshot_mesh(shape, pairs)


def snapshot_mesh(shape, seed_edges):
    if not cmds.objExists(shape):
        raise ToolError("原模型已被删除或改名，请重新分析。")
    dag = _dag(shape)
    mesh = om.MFnMesh(dag)
    points = [tuple(point)[:3] for point in mesh.getPoints(om.MSpace.kObject)]
    counts, vertices = mesh.getVertices()
    faces = []
    offset = 0
    for count in counts:
        faces.append(list(vertices[offset:offset + count]))
        offset += count
    uv_sets = {}
    for name in mesh.getUVSetNames():
        u, v = mesh.getUVs(name)
        uv_counts, ids = mesh.getAssignedUVs(name)
        uv_sets[name] = {"u": list(u), "v": list(v), "counts": list(uv_counts), "ids": list(ids)}
    shaders, assignments = mesh.getConnectedShaders(dag.instanceNumber())
    materials = [om.MFnDependencyNode(shader).name() for shader in shaders]
    shading = {}
    for edge in range(mesh.numEdges):
        pair = tuple(sorted(mesh.getEdgeVertices(edge)))
        shading[pair] = bool(mesh.isEdgeSmooth(edge))
    transform_dag = om.MDagPath(dag)
    transform_dag.pop()
    transform = transform_dag.fullPathName()
    payload = {"points": points, "faces": faces, "uv_sets": uv_sets,
               "materials": materials, "assignments": list(assignments),
               "smoothing": sorted((list(pair), value) for pair, value in shading.items())}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    warnings = []
    if mesh.getColorSetNames():
        warnings.append("原模型带顶点色；当前版本结果不传递顶点色。")
    if any(mesh.isNormalLocked(index) for index in range(mesh.numNormals)):
        warnings.append("原模型带锁定法线；结果会根据硬边重新计算法线。")
    history = cmds.listHistory(shape, pruneDagObjects=True) or []
    if any(cmds.nodeType(node) in ("skinCluster", "blendShape", "wrap", "ffd") for node in history):
        warnings.append("原模型带变形器；结果是当前姿态的静态副本。")
    return {"shape": shape, "transform": transform, "points": points, "faces": faces,
            "seed_edges": list(seed_edges), "uv_sets": uv_sets, "materials": materials,
            "assignments": list(assignments), "smoothing": shading,
            "matrix": cmds.xform(transform, query=True, worldSpace=True, matrix=True),
            "metric_points": [tuple(point)[:3] for point in mesh.getPoints(om.MSpace.kWorld)],
            "shape_handle": om.MObjectHandle(dag.node()), "instance_number": dag.instanceNumber(),
            "current_uv_set": mesh.currentUVSetName(dag.instanceNumber()),
            "display_smooth_mesh": cmds.getAttr(shape + ".displaySmoothMesh"),
            "smooth_level": cmds.getAttr(shape + ".smoothLevel"),
            "fingerprint": fingerprint, "warnings": warnings}


def live_shape(snapshot):
    handle = snapshot.get("shape_handle")
    if handle is None:
        return snapshot["shape"]
    if not handle.isValid() or not handle.isAlive():
        raise ToolError("原模型已不存在，请重新分析。")
    paths = om.MDagPath.getAllPathsTo(handle.object())
    matches = [path for path in paths if path.instanceNumber() == snapshot.get("instance_number", 0)]
    if not matches:
        raise ToolError("原模型的实例层级发生变化，请重新分析。")
    return matches[0].fullPathName()


def analyze(snapshot):
    return core.analyze_mesh(snapshot["points"], snapshot["faces"], snapshot["seed_edges"])


def _protected_columns(snapshot, analysis, preserve_hard_edges):
    columns = set()
    rings = analysis["rings"]
    size = analysis["source_count"]
    assignments = snapshot["assignments"]
    for band in analysis["bands"]:
        face_ids = band["faces"]
        first, second = (rings[index] for index in band["rings"])
        for column in range(size):
            if assignments[face_ids[column]] != assignments[face_ids[(column - 1) % size]]:
                columns.add(column)
            edge = tuple(sorted((first[column], second[column])))
            if preserve_hard_edges and not snapshot["smoothing"].get(edge, True):
                columns.add(column)
    if preserve_hard_edges:
        for cap in analysis["caps"]:
            if cap["kind"] == "fan":
                for column, vertex in enumerate(rings[cap["ring"]]):
                    edge = tuple(sorted((vertex, cap["center"])))
                    if not snapshot["smoothing"].get(edge, True):
                        columns.add(column)
    for cap in analysis["caps"]:
        if cap["kind"] == "fan":
            for column, face in enumerate(cap["faces"]):
                if assignments[face] != assignments[cap["faces"][(column - 1) % size]]:
                    columns.add(column)
    return sorted(columns)


def constraint_summary(snapshot, analysis, preserve_uvs=True, preserve_hard_edges=True):
    uv_columns = set()
    if preserve_uvs:
        uv_columns = core._uv_constraints(analysis, core._parse_uvs(snapshot["uv_sets"], snapshot["faces"])) - {0}
    protected = set(_protected_columns(snapshot, analysis, preserve_hard_edges)) | uv_columns | {0}
    return {"protected_count": len(protected), "minimum_count": max(3, len(protected))}


def _component_ranges(node, face_ids):
    ranges = []
    start = end = face_ids[0]
    for index in face_ids[1:]:
        if index == end + 1:
            end = index
        else:
            ranges.append("{}.f[{}:{}]".format(node, start, end))
            start = end = index
    ranges.append("{}.f[{}:{}]".format(node, start, end))
    return ranges


def _edge_smoothing(snapshot, result, preserve_hard_edges):
    source_smoothing = snapshot["smoothing"]
    source_rings = result["analysis"]["rings"]
    new_rings = result["new_rings"]
    samples = result["samples"]
    size = result["analysis"]["source_count"]
    smooth = {}
    if not preserve_hard_edges:
        return smooth
    mapping = result.get("old_to_new", {})
    for edge, value in source_smoothing.items():
        if edge[0] in mapping and edge[1] in mapping:
            smooth[tuple(sorted((mapping[edge[0]], mapping[edge[1]])))] = value
    for source_ring, new_ring in zip(source_rings, new_rings):
        for column, start in enumerate(samples):
            finish = samples[(column + 1) % len(samples)]
            if finish <= start:
                finish += size
            source_edges = [tuple(sorted((source_ring[index % size], source_ring[(index + 1) % size])))
                            for index in range(int(math.floor(start)), int(math.ceil(finish - 1e-8)))]
            edge = tuple(sorted((new_ring[column], new_ring[(column + 1) % len(samples)])))
            smooth[edge] = all(source_smoothing.get(item, True) for item in source_edges)
    for band in result["analysis"]["bands"]:
        a, b = band["rings"]
        for column, sample in enumerate(samples):
            integer = int(round(sample)) % size
            edge = tuple(sorted((new_rings[a][column], new_rings[b][column])))
            if abs(sample - round(sample)) < 1e-8:
                old = tuple(sorted((source_rings[a][integer], source_rings[b][integer])))
                smooth[edge] = source_smoothing.get(old, True)
            else:
                smooth[edge] = True
    for cap in result["analysis"]["caps"]:
        if cap["kind"] != "fan":
            continue
        ring_index = cap["ring"]
        center = mapping[cap["center"]]
        for column, sample in enumerate(samples):
            edge = tuple(sorted((center, new_rings[ring_index][column])))
            if abs(sample - round(sample)) < 1e-8:
                old = tuple(sorted((cap["center"], source_rings[ring_index][int(round(sample)) % size])))
                smooth[edge] = source_smoothing.get(old, True)
            else:
                smooth[edge] = True
    return smooth


def _ensure_mesh_command():
    from . import __version__
    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        raise ToolError("插件版本格式无效，无法创建结果。")
    source = os.path.join(os.path.dirname(__file__), "mesh_command.py")
    with open(source, "rb") as stream:
        content = stream.read()
    digest = hashlib.sha256(content).hexdigest()[:12]
    plugin_name = "cr_mesh_v{}_{}".format(__version__.replace(".", "_"), digest)
    cache = os.path.join(cmds.internalVar(userAppDir=True), "CylinderResample", "plugins")
    os.makedirs(cache, exist_ok=True)
    plugin = os.path.join(cache, plugin_name + ".py")
    if os.path.exists(plugin):
        with open(plugin, "rb") as stream:
            if stream.read() != content:
                raise ToolError("创建命令缓存校验失败，请重新安装插件。")
    else:
        fd, temporary = tempfile.mkstemp(prefix=".cr_mesh_", suffix=".tmp", dir=cache)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(temporary, plugin)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    try:
        loaded = cmds.pluginInfo(plugin_name, query=True, loaded=True)
    except RuntimeError:
        loaded = False
    if not loaded:
        try:
            cmds.loadPlugin(plugin, quiet=True)
        except Exception as error:
            raise ToolError("创建网格命令加载失败，请检查 Maya 脚本编辑器中的插件错误：{}".format(error)) from error
    command = "crCreateMesh_" + plugin_name
    if not callable(getattr(cmds, command, None)):
        raise ToolError("创建网格命令未成功注册，请检查 Maya 脚本编辑器中的插件错误，或更新并重新安装插件。")
    return command


def build_result(snapshot, target_count, shape_mode="contour", preserve_uvs=True,
                 preserve_hard_edges=True, name=None, _undo_chunk=True):
    current = snapshot_mesh(live_shape(snapshot), snapshot["seed_edges"])
    if current["fingerprint"] != snapshot["fingerprint"]:
        raise ToolError("模型在分析后发生了变化，请重新分析圆周边环。")
    analysis = analyze(current)
    protected = _protected_columns(current, analysis, preserve_hard_edges)
    result = core.resample_mesh(current["points"], current["faces"], current["seed_edges"],
                                target_count, uv_sets=current["uv_sets"], shape_mode=shape_mode,
                                preserve_uvs=preserve_uvs, protected_columns=protected,
                                metric_points=current["metric_points"], _analysis=analysis)
    leaf = current["transform"].split("|")[-1].replace(":", "_")
    create_command = _ensure_mesh_command()
    smoothing = _edge_smoothing(current, result, preserve_hard_edges)
    data = {"points": result["points"], "faces": result["faces"], "uv_sets": result["uv_sets"],
            "smoothing": [[list(pair), value] for pair, value in smoothing.items()],
            "name": name or leaf + "_resampled#", "matrix": current["matrix"],
            "current_uv_set": current["current_uv_set"]}
    node = None
    if _undo_chunk:
        cmds.undoInfo(openChunk=True, chunkName="CylinderResample")
    try:
        node = getattr(cmds, create_command)(data=json.dumps(data, separators=(",", ":")))
        cmds.sets(node, edit=True, forceElement="initialShadingGroup")
        by_material = defaultdict(list)
        for new_face, old_face in enumerate(result["face_sources"]):
            material_index = current["assignments"][old_face]
            if 0 <= material_index < len(current["materials"]):
                by_material[current["materials"][material_index]].append(new_face)
        for material, face_ids in by_material.items():
            components = _component_ranges(node, face_ids)
            cmds.sets(components, edit=True, forceElement=material)
        output_shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
        cmds.setAttr(output_shape + ".displaySmoothMesh", current["display_smooth_mesh"])
        cmds.setAttr(output_shape + ".smoothLevel", current["smooth_level"])
        cmds.addAttr(node, longName="cylinderResampleSource", dataType="string")
        cmds.setAttr(node + ".cylinderResampleSource", current["transform"], type="string")
        cmds.addAttr(node, longName="cylinderResampleSegments", attributeType="long")
        cmds.setAttr(node + ".cylinderResampleSegments", target_count)
        cmds.addAttr(node, longName="cylinderResamplePreview", attributeType="bool", defaultValue=False)
        cmds.select(node, replace=True)
    except Exception:
        if _undo_chunk:
            cmds.undoInfo(closeChunk=True)
            if node:
                cmds.undo()
        elif node and cmds.objExists(node):
            cmds.delete(node)
        raise
    else:
        if _undo_chunk:
            cmds.undoInfo(closeChunk=True)
    return {"node": cmds.ls(node, long=True)[0], "result": result,
            "warnings": current["warnings"] + result.get("warnings", [])}


def highlight(snapshot, analysis):
    cmds.select(_component_ranges(live_shape(snapshot), analysis["face_ids"]), replace=True)
