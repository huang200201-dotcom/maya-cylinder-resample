"""Small in-session compatibility check using only temporary test objects."""

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import adapter
from .compat import ensure_supported


def run():
    year = ensure_supported()
    selection = om.MGlobal.getActiveSelectionList()
    created = []
    try:
        source = cmds.polyCylinder(name="CR_CompatibilityTest#", radius=1.0, height=3.0,
                                   subdivisionsAxis=16, subdivisionsHeight=3,
                                   subdivisionsCaps=0, constructionHistory=False)[0]
        created.append(om.MObjectHandle(adapter._dag(source).node()))
        shape = cmds.listRelatives(source, shapes=True, fullPath=True)[0]
        mesh = om.MFnMesh(adapter._dag(shape))
        points = mesh.getPoints()
        for point in points:
            factor = 1.0 if abs(point.y) > 1.0 else 0.72
            point.x *= factor
            point.z *= factor
        mesh.setPoints(points)
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet="CR_texture")
        cmds.polyUVSet(shape, currentUVSet=True, uvSet="CR_texture")
        seed = []
        minimum = min(point.y for point in points)
        for edge in range(mesh.numEdges):
            a, b = mesh.getEdgeVertices(edge)
            if abs(points[a].y - minimum) < 1e-7 and abs(points[b].y - minimum) < 1e-7:
                seed.append((a, b))
        snapshot = adapter.snapshot_mesh(shape, seed)
        for mode, target in (("contour", 24), ("uniform", 20), ("circle", 12), ("circle", 24)):
            built = adapter.build_result(snapshot, target, shape_mode=mode)
            node = built["node"]
            created.append(om.MObjectHandle(adapter._dag(node).node()))
            output_shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
            output = om.MFnMesh(adapter._dag(output_shape))
            if output.numVertices != target * 4 or output.numPolygons != target * 3 + 2:
                raise RuntimeError("自检发现结果网格数量异常。")
            for name, values in built["result"]["uv_sets"].items():
                counts, ids = output.getAssignedUVs(name)
                if list(counts) != values["counts"] or list(ids) != values["ids"]:
                    raise RuntimeError("自检发现 UV 写入异常。")
            if output.currentUVSetName() != snapshot["current_uv_set"]:
                raise RuntimeError("自检发现当前 UV 集异常。")
            if mode == "contour":
                pieces = [0] * 16
                for sample in built["result"]["samples"]:
                    pieces[int(sample) % 16] += 1
                if sorted(pieces) != [1] * 8 + [2] * 8 or any(
                        pieces[index] == pieces[(index + 1) % 16] for index in range(16)):
                    raise RuntimeError("自检发现新增段数分布异常。")
        if adapter.snapshot_mesh(shape, seed)["fingerprint"] != snapshot["fingerprint"]:
            raise RuntimeError("自检发现源模型发生了变化。")
        message = "Maya {} 兼容自检通过：三种重分段模式、均衡分布、UV 读写与当前 UV 集、源网格保留。".format(year)
        print(message)
        return message
    finally:
        for handle in reversed(created):
            if handle.isValid() and handle.isAlive():
                cmds.delete(om.MFnDagNode(handle.object()).fullPathName())
        om.MGlobal.setActiveSelectionList(selection)
