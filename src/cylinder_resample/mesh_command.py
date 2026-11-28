"""Undoable Maya API 2.0 command used by the modeling tool."""

import importlib
import json
import re
import sys

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


def _load_payload(database):
    if database.isFlagSet("-key"):
        key = database.flagArgumentString("-key", 0)
        adapter = sys.modules.get("cylinder_resample.adapter") or importlib.import_module("cylinder_resample.adapter")
        return adapter._consume_mesh_payload(key)
    if database.isFlagSet("-data"):
        return json.loads(database.flagArgumentString("-data", 0))
    raise RuntimeError("创建网格命令缺少输入数据。")


def _write_uv_sets(mesh, sets, current, default):
    if not sets:
        return
    first = default if default in sets else next(iter(sets))
    original = mesh.getUVSetNames()
    if original and original[0] != first:
        mesh.renameUVSet(original[0], first)
    for name in [first] + [item for item in sets if item != first]:
        values = sets[name]
        if name not in mesh.getUVSetNames():
            mesh.createUVSet(name)
        mesh.clearUVs(name)
        mesh.setUVs(values["u"], values["v"], name)
        mesh.assignUVs(values["counts"], values["ids"], name)
    mesh.setCurrentUVSetName(current if current in sets else first)


class CreateMeshCommand(om.MPxCommand):
    def __init__(self):
        super().__init__()
        self._node = None
        self._deletion = None
        self._deleted = False

    @staticmethod
    def creator():
        return CreateMeshCommand()

    @staticmethod
    def syntax_creator():
        syntax = om.MSyntax()
        syntax.addFlag("-d", "-data", om.MSyntax.kString)
        syntax.addFlag("-k", "-key", om.MSyntax.kString)
        return syntax

    def isUndoable(self):
        return True

    def doIt(self, args):
        database = om.MArgDatabase(self.syntax(), args)
        data = _load_payload(database)
        previous_selection = om.MGlobal.getActiveSelectionList()
        try:
            transform = om.MFnTransform()
            self._node = transform.create()
            transform.setName(data["name"])
            transform.setTransformation(om.MTransformationMatrix(om.MMatrix(data["matrix"])))
            if data.get("rotate_pivot") is not None:
                transform.setRotatePivot(om.MPoint(*data["rotate_pivot"]), om.MSpace.kTransform, False)
            if data.get("scale_pivot") is not None:
                transform.setScalePivot(om.MPoint(*data["scale_pivot"]), om.MSpace.kTransform, False)
            mesh = om.MFnMesh()
            mesh.create([om.MPoint(*point) for point in data["points"]],
                        [len(face) for face in data["faces"]],
                        [vertex for face in data["faces"] for vertex in face], parent=self._node)
            mesh.setName(transform.name() + "Shape")
            _write_uv_sets(mesh, data["uv_sets"], data.get("current_uv_set"), data.get("default_uv_set"))
            smoothing = {tuple(pair): value for pair, value in data["smoothing"]}
            edge_count = mesh.numEdges
            if smoothing:
                smooths = om.MIntArray(edge_count, 1)
                for edge in range(edge_count):
                    pair = tuple(sorted(mesh.getEdgeVertices(edge)))
                    if not smoothing.get(pair, True):
                        smooths[edge] = 0
            else:
                smooths = om.MIntArray(edge_count, 1)
            mesh.setEdgeSmoothings(om.MIntArray(range(edge_count)), smooths)
            mesh.cleanupEdgeSmoothing()
            mesh.updateSurface()
            self.setResult(transform.fullPathName())
        except Exception:
            if self._node is not None:
                cleanup = om.MDagModifier()
                cleanup.deleteNode(self._node)
                cleanup.doIt()
            self._node = None
            raise
        finally:
            om.MGlobal.setActiveSelectionList(previous_selection)

    def undoIt(self):
        # Undoing the deletion restores the entire subtree, including mesh data.
        self._deletion = om.MDagModifier()
        self._deletion.deleteNode(self._node)
        self._deletion.doIt()
        self._deleted = True

    def redoIt(self):
        if self._deleted:
            self._deletion.undoIt()
            self._deleted = False


def initializePlugin(plugin):
    function = om.MFnPlugin(plugin, "CylinderResample", "0.0.0", "Any")
    # Maya's script plug-in loader does not always provide __file__.
    plugin_name = function.name()
    version = re.match(r"cr_mesh_v(\d+)_(\d+)_(\d+)_", plugin_name)
    if version:
        function.version = ".".join(version.groups())
    function.registerCommand("crCreateMesh_" + plugin_name,
                             CreateMeshCommand.creator, CreateMeshCommand.syntax_creator)


def uninitializePlugin(plugin):
    function = om.MFnPlugin(plugin)
    function.deregisterCommand("crCreateMesh_" + function.name())
