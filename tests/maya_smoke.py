"""Run with mayapy after pure tests; all scene fixtures are disposable."""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import maya.standalone
maya.standalone.initialize(name="python")

import maya.api.OpenMaya as om
import maya.cmds as cmds

from cylinder_resample import adapter
from test_resample import cylinder, distances, nonuniform_cylinder, sample_arc_lengths, strip_uvs


def make_mesh(points, faces, uv_sets, name="processedCylinder"):
    node = cmds.createNode("transform", name=name)
    mesh = om.MFnMesh()
    mesh.create([om.MPoint(*p) for p in points], [len(face) for face in faces],
                [index for face in faces for index in face], parent=adapter._dag(node).node())
    shape = cmds.rename(mesh.fullPathName(), name + "Shape")
    for set_name, data in uv_sets.items():
        if set_name not in mesh.getUVSetNames():
            mesh.createUVSet(set_name)
        mesh.setUVs(data["u"], data["v"], set_name)
        mesh.assignUVs(data["counts"], data["ids"], set_name)
    for edge in range(mesh.numEdges):
        mesh.setEdgeSmoothing(edge, True)
    mesh.cleanupEdgeSmoothing()
    cmds.sets(node, edit=True, forceElement="initialShadingGroup")
    return node, cmds.ls(shape, long=True)[0], mesh


def circumference_at_height(mesh, height=None):
    points = [tuple(p)[:3] for p in mesh.getPoints()]
    if height is None:
        heights = sorted({round(p[1], 6) for p in points})
        height = min(heights, key=abs)
    edge_ids, pairs = [], []
    for edge in range(mesh.numEdges):
        pair = mesh.getEdgeVertices(edge)
        if all(abs(points[vertex][1] - height) < 1e-5
               and math.hypot(points[vertex][0], points[vertex][2]) > 0.1 for vertex in pair):
            edge_ids.append(edge)
            pairs.append(tuple(pair))
    return edge_ids, pairs


def track_preview(session, node):
    """Associate a disposable preview with the source captured for its creation."""
    handle = om.MObjectHandle(adapter._dag(node).node())
    record = {
        "handle": handle, "source_handle": session.source_handle,
        "baseline": session.source_baseline, "snapshot": session.snapshot,
        "analysis": session.analysis, "offset": False, "pending": True,
    }
    session.preview_records.append(record)
    return record


def create_demo():
    cmds.file(new=True, force=True)
    profile = ((0.80, -1.40), (0.94, -1.34), (1.00, -1.24), (1.00, -0.70),
               (0.72, -0.54), (0.72, 0.54), (1.00, 0.70), (1.00, 1.24),
               (0.94, 1.34), (0.80, 1.40))
    points, faces, seed = cylinder(n=24, profile=profile, caps="ngon")
    uv_sets = strip_uvs(faces, n=24, ring_count=len(profile))
    node, shape, mesh = make_mesh(points, faces, uv_sets, name="Processed_Cylinder_24")
    shader = cmds.shadingNode("lambert", asShader=True, name="UV_Checker_Material")
    checker = cmds.shadingNode("checker", asTexture=True, name="UV_Checker")
    placement = cmds.shadingNode("place2dTexture", asUtility=True, name="Checker_Placement")
    cmds.connectAttr(placement + ".outUV", checker + ".uvCoord", force=True)
    cmds.connectAttr(placement + ".outUvFilterSize", checker + ".uvFilterSize", force=True)
    cmds.setAttr(placement + ".repeatUV", 8, 8, type="double2")
    cmds.setAttr(checker + ".color1", 0.85, 0.85, 0.85, type="double3")
    cmds.setAttr(checker + ".color2", 0.18, 0.18, 0.18, type="double3")
    group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="UV_Checker_MaterialSG")
    cmds.connectAttr(checker + ".outColor", shader + ".color", force=True)
    cmds.connectAttr(shader + ".outColor", group + ".surfaceShader", force=True)
    cmds.sets(node, edit=True, forceElement=group)
    cmds.xform(node, translation=(-3.0, 0, 0), rotation=(-90, 0, 0))
    snapshot = adapter.snapshot_mesh(shape, seed)
    reduced = adapter.build_result(snapshot, 16, shape_mode="circle", preserve_hard_edges=False,
                                   name="Resampled_Cylinder_16")["node"]
    increased = adapter.build_result(snapshot, 36, shape_mode="circle", preserve_hard_edges=False,
                                     name="Resampled_Cylinder_36")["node"]
    cmds.xform(reduced, translation=(0, 0, 0))
    cmds.xform(increased, translation=(3, 0, 0))
    for obj, color in ((node, (0.72, 0.74, 0.77)), (reduced, (0.25, 0.70, 0.50)),
                       (increased, (0.20, 0.65, 0.80))):
        cmds.setAttr(obj + ".overrideEnabled", True)
        cmds.setAttr(obj + ".overrideRGBColors", True)
        cmds.setAttr(obj + ".overrideColorRGB", *color, type="double3")
    cmds.xform("persp", translation=(8, 4, 11), rotation=(-16.39, 36.03, 0))
    cmds.setAttr("perspShape.focalLength", 35)
    cmds.setAttr("defaultResolution.width", 1280)
    cmds.setAttr("defaultResolution.height", 720)
    cmds.setAttr("defaultResolution.deviceAspectRatio", 16.0 / 9.0)
    seed_keys = {frozenset(pair) for pair in seed}
    edge_ids = [edge for edge in range(mesh.numEdges) if frozenset(mesh.getEdgeVertices(edge)) in seed_keys]
    cmds.select(["{}.e[{}]".format(node, edge) for edge in edge_ids], replace=True)
    cmds.fileInfo("CylinderResampleDemo", "24-segment source with deleted history, 16/36-segment UV-preserving copies")
    cmds.fileInfo("CylinderResampleSeed", "The source circumference edge loop is selected; use Analyze in the tool")
    output = ROOT / "examples" / "demo_maya2024.ma"
    cmds.file(rename=str(output))
    cmds.file(save=True, type="mayaAscii", force=True)
    print("DEMO_SAVED", output)


class MayaIntegrationTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)

    def test_deleted_history_processed_mesh_uv_material_transform_and_source(self):
        profile = ((0.8, 0.0), (1.0, 0.2), (1.0, 0.9), (0.7, 1.1), (0.7, 2.0))
        points, faces, seed = cylinder(profile=profile, caps="ngon")
        uv_sets = strip_uvs(faces, ring_count=len(profile))
        uv_sets["detailUV"] = strip_uvs(faces, ring_count=len(profile), seams=(0, 6))["map1"]
        node, shape, mesh = make_mesh(points, faces, uv_sets)
        shader = cmds.shadingNode("lambert", asShader=True, name="sectorMaterial")
        group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="sectorMaterialSG")
        cmds.connectAttr(shader + ".outColor", group + ".surfaceShader", force=True)
        cmds.sets(["{}.f[{}]".format(node, face) for face in range(6)], edit=True, forceElement=group)
        cmds.xform(node, translation=(3.0, 4.0, 5.0), rotation=(17.0, 28.0, 9.0), scale=(1.2, 0.8, 1.1))
        cmds.delete(node, constructionHistory=True)
        source = adapter.snapshot_mesh(shape, seed)
        analysis = adapter.analyze(source)
        self.assertEqual(analysis["ring_count"], 5)
        built = adapter.build_result(source, 18, shape_mode="circle", preserve_hard_edges=True)
        new_node = built["node"]
        new_shape = cmds.listRelatives(new_node, shapes=True, fullPath=True)[0]
        new_mesh = om.MFnMesh(adapter._dag(new_shape))
        self.assertEqual(new_mesh.numVertices, 90)
        self.assertEqual(new_mesh.numPolygons, 74)
        self.assertEqual(set(new_mesh.getUVSetNames()), {"map1", "detailUV"})
        for name, data in built["result"]["uv_sets"].items():
            u, v = new_mesh.getUVs(name)
            counts, ids = new_mesh.getAssignedUVs(name)
            self.assertEqual(list(counts), data["counts"])
            self.assertEqual(list(ids), data["ids"])
            self.assertEqual(len(u), len(data["u"]))
            self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(u, data["u"])))
            self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(v, data["v"])))
        self.assertEqual(adapter.snapshot_mesh(shape, seed)["fingerprint"], source["fingerprint"])
        matrix = cmds.xform(new_node, query=True, worldSpace=True, matrix=True)
        self.assertTrue(all(abs(a - b) < 1e-7 for a, b in zip(matrix, source["matrix"])))
        shaders, assignments = new_mesh.getConnectedShaders(0)
        names = [om.MFnDependencyNode(shader).name() for shader in shaders]
        for source_id, material in zip(built["result"]["face_sources"], assignments):
            expected = source["materials"][source["assignments"][source_id]]
            self.assertEqual(names[material], expected)
        self.assertEqual(cmds.getAttr(new_node + ".cylinderResampleSegments"), 18)
        cmds.undo()
        self.assertFalse(cmds.objExists(new_node))
        self.assertTrue(cmds.objExists(shape))
        cmds.redo()
        self.assertTrue(cmds.objExists(new_node))
        restored_shape = cmds.listRelatives(new_node, shapes=True, fullPath=True)[0]
        restored = om.MFnMesh(adapter._dag(restored_shape))
        self.assertEqual(restored.numVertices, 90)
        self.assertEqual(restored.numPolygons, 74)
        self.assertEqual(set(restored.getUVSetNames()), {"map1", "detailUV"})
        restored_counts, restored_ids = restored.getAssignedUVs("map1")
        self.assertEqual(list(restored_counts), built["result"]["uv_sets"]["map1"]["counts"])
        self.assertEqual(list(restored_ids), built["result"]["uv_sets"]["map1"]["ids"])
        cmds.undo()
        self.assertFalse(cmds.objExists(new_node))
        self.assertTrue(cmds.objExists(shape))
        cmds.redo()
        second_shape = cmds.listRelatives(new_node, shapes=True, fullPath=True)[0]
        self.assertEqual(om.MFnMesh(adapter._dag(second_shape)).numVertices, 90)

    def test_edge_selection_snapshot_and_hard_edge_boundary(self):
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        edge_ids = []
        longitudinal = None
        seed_pairs = {frozenset(pair) for pair in seed}
        for edge in range(mesh.numEdges):
            pair = mesh.getEdgeVertices(edge)
            if frozenset(pair) in seed_pairs:
                edge_ids.append(edge)
            if frozenset(pair) == frozenset((5, 17)):
                longitudinal = edge
        mesh.setEdgeSmoothing(longitudinal, False)
        mesh.cleanupEdgeSmoothing()
        cmds.select(["{}.e[{}]".format(node, edge) for edge in edge_ids], replace=True)
        snapshot = adapter.selected_edge_loop()
        self.assertEqual(len(snapshot["seed_edges"]), 12)
        built = adapter.build_result(snapshot, 17, preserve_hard_edges=True)
        new_mesh = om.MFnMesh(adapter._dag(cmds.listRelatives(built["node"], shapes=True, fullPath=True)[0]))
        old_boundary = points[5]
        new_points = [tuple(p)[:3] for p in new_mesh.getPoints()]
        vertex = next(i for i, p in enumerate(new_points) if all(abs(p[d] - old_boundary[d]) < 1e-7 for d in range(3)))
        matches = [edge for edge in range(new_mesh.numEdges)
                   if vertex in new_mesh.getEdgeVertices(edge)
                   and abs(new_points[new_mesh.getEdgeVertices(edge)[0]][2] -
                           new_points[new_mesh.getEdgeVertices(edge)[1]][2]) > 1]
        self.assertEqual(len(matches), 1)
        self.assertFalse(new_mesh.isEdgeSmooth(matches[0]))

    def test_stale_snapshot_blocks_creation(self):
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        snapshot = adapter.snapshot_mesh(shape, seed)
        cmds.move(0.1, 0, 0, node + ".vtx[0]", relative=True)
        with self.assertRaises(adapter.ToolError):
            adapter.build_result(snapshot, 18)
        self.assertEqual(len(cmds.ls(type="mesh")), 1)

    def test_session_cancel_restores_source_after_rename(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        session = Session()
        session.snapshot = adapter.snapshot_mesh(shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(shape).node())
        session.source_baseline = True
        preview = adapter.build_result(session.snapshot, 18)["node"]
        track_preview(session, preview)
        cmds.setAttr(preview + ".cylinderResamplePreview", True)
        session._set_record_visibility(session._preview_record(), False)
        renamed = cmds.rename(node, "renamedSource")
        renamed_shape = cmds.listRelatives(renamed, shapes=True, fullPath=True)[0]
        self.assertFalse(cmds.getAttr(renamed_shape + ".visibility"))
        session.cancel(silent=True)
        self.assertTrue(cmds.getAttr(renamed_shape + ".visibility"))
        self.assertFalse(cmds.objExists(preview))

    def test_session_close_restores_originally_hidden_source(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        cmds.setAttr(shape + ".visibility", False)
        session = Session()
        session.snapshot = adapter.snapshot_mesh(shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(shape).node())
        session.source_baseline = False
        preview = adapter.build_result(session.snapshot, 18)["node"]
        track_preview(session, preview)
        cmds.setAttr(preview + ".cylinderResamplePreview", True)
        session._set_record_visibility(session._preview_record(), True)
        self.assertTrue(cmds.getAttr(shape + ".visibility"))
        session.close()
        self.assertFalse(cmds.getAttr(shape + ".visibility"))

    def test_session_preview_update_undo_cancel_removes_revived_preview(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        session = Session()
        session.snapshot = adapter.snapshot_mesh(shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(shape).node())
        session.source_baseline = True
        cmds.undoInfo(openChunk=True, chunkName="firstPreview")
        try:
            first = adapter.build_result(session.snapshot, 18, _undo_chunk=False)["node"]
            track_preview(session, first)
            cmds.setAttr(first + ".cylinderResamplePreview", True)
            session._set_record_visibility(session._preview_record(), False)
        finally:
            cmds.undoInfo(closeChunk=True)
        cmds.undoInfo(openChunk=True, chunkName="updatedPreview")
        try:
            second = adapter.build_result(session.snapshot, 24, _undo_chunk=False)["node"]
            session._delete_preview()
            track_preview(session, second)
            cmds.setAttr(second + ".cylinderResamplePreview", True)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.assertFalse(cmds.objExists(first))
        self.assertTrue(cmds.objExists(second))
        cmds.undo()
        self.assertEqual(session._preview_node(), first)
        self.assertFalse(cmds.objExists(second))
        session.cancel(silent=True)
        self.assertFalse(cmds.objExists(first))
        self.assertFalse(cmds.objExists(second))
        self.assertTrue(cmds.getAttr(shape + ".visibility"))

    def test_session_undo_keep_then_cancel_restores_source_baseline(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        session = Session()
        session.snapshot = adapter.snapshot_mesh(shape, seed)
        session.source_handle = om.MObjectHandle(adapter._dag(shape).node())
        session.source_baseline = True
        preview = adapter.build_result(session.snapshot, 18)["node"]
        track_preview(session, preview)
        cmds.setAttr(preview + ".cylinderResamplePreview", True)
        session._set_record_visibility(session._preview_record(), False)
        cmds.undoInfo(openChunk=True, chunkName="keptPreview")
        try:
            session._restore_source()
            cmds.setAttr(preview + ".cylinderResamplePreview", False)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.assertIsNone(session._preview_node())
        self.assertTrue(cmds.objExists(preview))
        cmds.undo()
        self.assertEqual(session._preview_node(), preview)
        self.assertFalse(cmds.getAttr(shape + ".visibility"))
        session.cancel(silent=True)
        self.assertFalse(cmds.objExists(preview))
        self.assertTrue(cmds.getAttr(shape + ".visibility"))

    def test_triangle_fan_hard_radial_edge_is_preserved(self):
        points, faces, seed = cylinder(caps="fan")
        node, shape, mesh = make_mesh(points, faces, {})
        radial = next(edge for edge in range(mesh.numEdges)
                      if frozenset(mesh.getEdgeVertices(edge)) == frozenset((5, 24)))
        mesh.setEdgeSmoothing(radial, False)
        mesh.cleanupEdgeSmoothing()
        snapshot = adapter.snapshot_mesh(shape, seed)
        built = adapter.build_result(snapshot, 9, preserve_hard_edges=True)
        new_shape = cmds.listRelatives(built["node"], shapes=True, fullPath=True)[0]
        new_mesh = om.MFnMesh(adapter._dag(new_shape))
        new_points = [tuple(p)[:3] for p in new_mesh.getPoints()]
        boundary = next(i for i, p in enumerate(new_points)
                        if all(abs(p[d] - points[5][d]) < 1e-7 for d in range(3)))
        center = next(i for i, p in enumerate(new_points)
                      if all(abs(p[d]) < 1e-7 for d in range(3)))
        new_radial = next(edge for edge in range(new_mesh.numEdges)
                          if frozenset(new_mesh.getEdgeVertices(edge)) == frozenset((boundary, center)))
        self.assertFalse(new_mesh.isEdgeSmooth(new_radial))

    def test_uniform_output_preserves_current_uv_set_and_rebalances_source_spacing(self):
        points, faces, seed = nonuniform_cylinder()
        uv_sets = strip_uvs(faces)
        uv_sets["detailUV"] = strip_uvs(faces)["map1"]
        node, shape, mesh = make_mesh(points, faces, uv_sets)
        mesh.setCurrentUVSetName("detailUV")
        snapshot = adapter.snapshot_mesh(shape, seed)
        built = adapter.build_result(snapshot, 18, shape_mode="uniform")
        new_shape = cmds.listRelatives(built["node"], shapes=True, fullPath=True)[0]
        new_mesh = om.MFnMesh(adapter._dag(new_shape))
        self.assertEqual(new_mesh.currentUVSetName(), "detailUV")
        self.assertEqual(set(new_mesh.getUVSetNames()), {"map1", "detailUV"})
        self.assertEqual(new_mesh.numVertices, 36)
        self.assertTrue(any(abs(value * 1.5 - round(value * 1.5)) > 1e-5
                            for value in built["result"]["samples"]))
        self.assertEqual(om.MFnMesh(adapter._dag(shape)).currentUVSetName(), "detailUV")

    def test_snapshot_rename_supports_rebuild_and_region_highlight(self):
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        snapshot = adapter.snapshot_mesh(shape, seed)
        analysis = adapter.analyze(snapshot)
        renamed = cmds.rename(node, "SourceAfterRename")
        renamed_shape = cmds.listRelatives(renamed, shapes=True, fullPath=True)[0]
        adapter.highlight(snapshot, analysis)
        selected = cmds.ls(selection=True, flatten=True, long=True)
        self.assertEqual(len(selected), 12)
        self.assertTrue(all(component.startswith(renamed_shape + ".f[") for component in selected))
        built = adapter.build_result(snapshot, 18, shape_mode="uniform")
        self.assertTrue(cmds.objExists(built["node"]))
        self.assertEqual(cmds.getAttr(built["node"] + ".cylinderResampleSource"), cmds.ls(renamed, long=True)[0])
        self.assertEqual(adapter.snapshot_mesh(renamed_shape, seed)["fingerprint"], snapshot["fingerprint"])

    def test_uniform_world_spacing_on_nonuniform_transform_and_smooth_preview_mode(self):
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        cmds.xform(node, translation=(20, -4, 6), rotation=(10, 20, 30), scale=(3, 0.7, 1.2))
        cmds.setAttr(shape + ".displaySmoothMesh", 2)
        cmds.setAttr(shape + ".smoothLevel", 3)
        snapshot = adapter.snapshot_mesh(shape, seed)
        built = adapter.build_result(snapshot, 18, shape_mode="uniform")
        new_shape = cmds.listRelatives(built["node"], shapes=True, fullPath=True)[0]
        new_mesh = om.MFnMesh(adapter._dag(new_shape))
        world_points = [tuple(point)[:3] for point in new_mesh.getPoints(om.MSpace.kWorld)]
        result = built["result"]
        ring = result["new_rings"][result["analysis"]["seed_ring"]]
        lengths = distances([world_points[vertex] for vertex in ring])
        self.assertAlmostEqual(result["stats"]["min_segment_length"], min(lengths), places=5)
        self.assertAlmostEqual(result["stats"]["max_segment_length"], max(lengths), places=5)
        actual = sample_arc_lengths(result, snapshot["metric_points"][:12])
        self.assertLess(max(actual) - min(actual), 1e-7)
        self.assertEqual(cmds.getAttr(new_shape + ".displaySmoothMesh"), 2)
        self.assertEqual(cmds.getAttr(new_shape + ".smoothLevel"), 3)

    def test_session_cancel_after_source_switch_and_undo_restores_both_sources(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        first_node, first_shape, first_mesh = make_mesh(points, faces, strip_uvs(faces), name="First")
        second_node, second_shape, second_mesh = make_mesh(points, faces, strip_uvs(faces), name="Second")
        session = Session()
        session.snapshot = adapter.snapshot_mesh(first_shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(first_shape).node())
        session.source_baseline = True
        preview = adapter.build_result(session.snapshot, 18)["node"]
        track_preview(session, preview)
        cmds.setAttr(preview + ".cylinderResamplePreview", True)
        session._set_record_visibility(session._preview_record(), False)
        session.cancel(silent=True)
        session.snapshot = adapter.snapshot_mesh(second_shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(second_shape).node())
        session.source_baseline = True
        cmds.undo()
        self.assertEqual(session._preview_node(), cmds.ls(preview, long=True)[0])
        self.assertFalse(cmds.getAttr(first_shape + ".visibility"))
        session.cancel(silent=True)
        self.assertFalse(cmds.objExists(preview))
        self.assertTrue(cmds.getAttr(first_shape + ".visibility"))
        self.assertTrue(cmds.getAttr(second_shape + ".visibility"))

    def test_scene_change_invalidates_session_without_touching_reused_names(self):
        from cylinder_resample.ui import Session
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces))
        session = Session()
        session.snapshot = adapter.snapshot_mesh(shape, seed)
        session.analysis = adapter.analyze(session.snapshot)
        session.source_handle = om.MObjectHandle(adapter._dag(shape).node())
        session.source_baseline = True
        preview = adapter.build_result(session.snapshot, 18)["node"]
        track_preview(session, preview)
        cmds.setAttr(preview + ".cylinderResamplePreview", True)
        session._set_record_visibility(session._preview_record(), False)
        cmds.file(new=True, force=True)
        new_node, new_shape, new_mesh = make_mesh(points, faces, strip_uvs(faces))
        cmds.setAttr(new_shape + ".visibility", False)
        session.scene_changed()
        self.assertIsNone(session.snapshot)
        session.close()
        self.assertTrue(cmds.objExists(new_node))
        self.assertFalse(cmds.getAttr(new_shape + ".visibility"))

    def test_missing_uv_face_and_no_uv_switch(self):
        points, faces, seed = cylinder()
        node, shape, mesh = make_mesh(points, faces, strip_uvs(faces, missing_faces=(2,)))
        snapshot = adapter.snapshot_mesh(shape, seed)
        built = adapter.build_result(snapshot, 24)
        new_shape = cmds.listRelatives(built["node"], shapes=True, fullPath=True)[0]
        new_mesh = om.MFnMesh(adapter._dag(new_shape))
        counts, ids = new_mesh.getAssignedUVs("map1")
        self.assertEqual(sum(count == 0 for count in counts), 2)
        disabled = adapter.build_result(snapshot, 6, preserve_uvs=False, preserve_hard_edges=False)
        disabled_shape = cmds.listRelatives(disabled["node"], shapes=True, fullPath=True)[0]
        disabled_mesh = om.MFnMesh(adapter._dag(disabled_shape))
        self.assertEqual(disabled_mesh.numVertices, 12)
        self.assertEqual(disabled_mesh.numUVs, 0)

    def test_real_polycylinder_default_cap_variants(self):
        for caps in (0, 1):
            with self.subTest(caps=caps):
                cmds.file(new=True, force=True)
                node = cmds.polyCylinder(subdivisionsAxis=24, subdivisionsHeight=3,
                                         subdivisionsCaps=caps, radius=1, height=2,
                                         constructionHistory=False)[0]
                shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
                mesh = om.MFnMesh(adapter._dag(shape))
                edges, pairs = circumference_at_height(mesh)
                self.assertEqual(len(edges), 24)
                cmds.select(["{}.e[{}]".format(node, edge) for edge in edges], replace=True)
                snapshot = adapter.selected_edge_loop()
                before = snapshot["fingerprint"]
                analysis = adapter.analyze(snapshot)
                self.assertEqual(analysis["source_count"], 24)
                self.assertEqual(analysis["ring_count"], 4)
                built = adapter.build_result(snapshot, 16, shape_mode="circle", preserve_hard_edges=False)
                self.assertEqual(len(built["result"]["new_rings"]), 4)
                self.assertTrue(all(len(ring) == 16 for ring in built["result"]["new_rings"]))
                self.assertEqual(adapter.snapshot_mesh(shape, pairs)["fingerprint"], before)

    def test_real_polybevel_deleted_history(self):
        node = cmds.polyCylinder(subdivisionsAxis=24, subdivisionsHeight=2, subdivisionsCaps=0,
                                 radius=1, height=2, constructionHistory=False)[0]
        shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
        mesh = om.MFnMesh(adapter._dag(shape))
        upper, _ = circumference_at_height(mesh, 1)
        lower, _ = circumference_at_height(mesh, -1)
        rim = ["{}.e[{}]".format(node, edge) for edge in upper + lower]
        try:
            cmds.polyBevel3(rim, offset=0.08, offsetAsFraction=False, segments=2, constructionHistory=False)
        except (AttributeError, RuntimeError):
            cmds.polyBevel(rim, fraction=0.08, segments=2, constructionHistory=False)
        cmds.delete(node, constructionHistory=True)
        shape = cmds.listRelatives(node, shapes=True, fullPath=True)[0]
        mesh = om.MFnMesh(adapter._dag(shape))
        edges, pairs = circumference_at_height(mesh, 0)
        self.assertEqual(len(edges), 24)
        snapshot = adapter.snapshot_mesh(shape, pairs)
        analysis = adapter.analyze(snapshot)
        self.assertGreaterEqual(analysis["ring_count"], 5)
        built = adapter.build_result(snapshot, 16, preserve_hard_edges=False)
        self.assertTrue(all(len(ring) == 16 for ring in built["result"]["new_rings"]))
        self.assertEqual(adapter.snapshot_mesh(shape, pairs)["fingerprint"], snapshot["fingerprint"])


if __name__ == "__main__":
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(MayaIntegrationTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if result.wasSuccessful() and "--demo" in sys.argv:
            create_demo()
    finally:
        maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
