"""Undoable Maya API 2.0 command used by the modeling tool."""

import json
import re

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


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
        return syntax

    def isUndoable(self):
        return True

    def doIt(self, args):
        database = om.MArgDatabase(self.syntax(), args)
        data = json.loads(database.flagArgumentString("-data", 0))
        previous_selection = om.MGlobal.getActiveSelectionList()
        try:
            transform = om.MFnTransform()
            self._node = transform.create()
            transform.setName(data["name"])
            transform.setTransformation(om.MTransformationMatrix(om.MMatrix(data["matrix"])))
            mesh = om.MFnMesh()
            mesh.create([om.MPoint(*point) for point in data["points"]],
                        [len(face) for face in data["faces"]],
                        [vertex for face in data["faces"] for vertex in face], parent=self._node)
            mesh.setName(transform.name() + "Shape")
            for name, values in data["uv_sets"].items():
                if name not in mesh.getUVSetNames():
                    mesh.createUVSet(name)
                mesh.clearUVs(name)
                mesh.setUVs(values["u"], values["v"], name)
                mesh.assignUVs(values["counts"], values["ids"], name)
            if data["uv_sets"]:
                current = data.get("current_uv_set")
                mesh.setCurrentUVSetName(current if current in data["uv_sets"] else next(iter(data["uv_sets"])))
                if "map1" not in data["uv_sets"] and "map1" in mesh.getUVSetNames():
                    mesh.deleteUVSet("map1")
            smoothing = {tuple(pair): value for pair, value in data["smoothing"]}
            smooths = om.MIntArray()
            for edge in range(mesh.numEdges):
                pair = tuple(sorted(mesh.getEdgeVertices(edge)))
                smooths.append(int(smoothing.get(pair, True)))
            mesh.setEdgeSmoothings(om.MIntArray(range(mesh.numEdges)), smooths)
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
