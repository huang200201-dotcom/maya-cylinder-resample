"""Native Maya controls for explicit analysis, preview, and commit."""

import traceback

import maya.cmds as cmds

from . import adapter
from . import __version__


WINDOW = "CylinderResampleWindow"
MODES = ("contour", "uniform", "circle")
_SESSION = None
_UPDATING = False


class Session:
    def __init__(self):
        self.snapshot = None
        self.analysis = None
        self.source_handle = None
        self.source_baseline = None
        # Retain deleted records: Maya undo can resurrect their objects.
        self.preview_records = []
        self.controls = {}
        self._busy = False

    def message(self, text, error=False):
        if cmds.control(self.controls.get("status", ""), exists=True):
            cmds.scrollField(self.controls["status"], edit=True, text=text)
        if error:
            cmds.warning(text)

    def run(self, action):
        if self._busy or _UPDATING:
            return
        self._busy = True
        try:
            cmds.waitCursor(state=True)
            action()
        except (ValueError, RuntimeError) as exc:
            self.message(str(exc), error=True)
        except Exception as exc:
            traceback.print_exc()
            self.message("操作失败：{}".format(exc), error=True)
        finally:
            cmds.waitCursor(state=False)
            self._busy = False

    @staticmethod
    def _handle_path(handle):
        if handle is None or not handle.isValid() or not handle.isAlive():
            return None
        try:
            return adapter.om.MFnDagNode(handle.object()).fullPathName()
        except RuntimeError:
            return None

    def _active_records(self):
        active = []
        for record in self.preview_records:
            node = self._handle_path(record["handle"])
            attr = node + ".cylinderResamplePreview" if node else None
            if attr and cmds.objExists(attr):
                record["pending"] = bool(cmds.getAttr(attr))
                if record["pending"]:
                    active.append(record)
        return active

    def _preview_record(self):
        active = self._active_records()
        return active[-1] if active else None

    def _restorable_records(self):
        active = self._active_records()
        return [record for record in self.preview_records
                if record in active or (record.get("pending", True)
                                        and not self._handle_path(record["handle"]))]

    def _preview_node(self):
        record = self._preview_record()
        return self._handle_path(record["handle"]) if record else None

    def _source_shape(self, record=None):
        return self._handle_path(record["source_handle"] if record else self.source_handle)

    def _can_set_visibility(self, shape):
        attr = shape + ".visibility" if shape else None
        return bool(attr and cmds.objExists(attr)
                    and cmds.getAttr(attr, settable=True)
                    and not cmds.referenceQuery(shape, isNodeReferenced=True)
                    and not adapter.om.MFnDagNode(adapter._dag(shape)).isInstanced())

    def _set_record_visibility(self, record, visible):
        shape = self._source_shape(record)
        if self._can_set_visibility(shape):
            attr = shape + ".visibility"
            if bool(cmds.getAttr(attr)) != bool(visible):
                cmds.setAttr(attr, visible)

    def _restore_source(self, records=None):
        restored = set()
        for record in reversed(self._restorable_records() if records is None else records):
            shape = self._source_shape(record)
            if shape and shape not in restored:
                self._set_record_visibility(record, record["baseline"])
                restored.add(shape)

    def _delete_preview(self):
        for record in self._restorable_records():
            node = self._handle_path(record["handle"])
            if node:
                cmds.delete(node)
            record["pending"] = False

    def _params(self):
        return (cmds.intSliderGrp(self.controls["count"], query=True, value=True),
                MODES[cmds.radioButtonGrp(self.controls["mode"], query=True, select=True) - 1],
                cmds.checkBox(self.controls["uv"], query=True, value=True),
                cmds.checkBox(self.controls["hard"], query=True, value=True))

    def _summary(self):
        if not self.snapshot:
            return "等待分析"
        _, _, keep_uv, keep_hard = self._params()
        constraints = adapter.constraint_summary(self.snapshot, self.analysis, keep_uv, keep_hard)
        shape = adapter.live_shape(self.snapshot)
        return "{}\n圆周：{} 段    截面：{} 圈    处理面数：{}\n约束列：{}    最低目标段数：{}\n{}".format(
            shape.split("|")[-2] if "|" in shape else shape,
            self.analysis["source_count"], len(self.analysis["rings"]),
            len(self.analysis["face_ids"]), constraints["protected_count"],
            constraints["minimum_count"], "\n".join(self.snapshot["warnings"]))

    def read_selection(self):
        snapshot = adapter.selected_edge_loop()
        analysis = adapter.analyze(snapshot)
        self.cancel(silent=True)
        self.snapshot, self.analysis = snapshot, analysis
        self.source_handle = snapshot.get("shape_handle") or adapter.om.MObjectHandle(adapter._dag(snapshot["shape"]).node())
        self.source_baseline = bool(cmds.getAttr(adapter.live_shape(snapshot) + ".visibility"))
        cmds.intSliderGrp(self.controls["count"], edit=True, value=analysis["source_count"])
        for key in ("preview", "region", "original", "half", "double"):
            if key in self.controls:
                cmds.button(self.controls[key], edit=True, enable=True)
        self.message(self._summary())

    def highlight(self):
        if self.snapshot:
            adapter.highlight(self.snapshot, self.analysis)

    def set_count(self, factor):
        if self.analysis:
            count = min(4096, max(3, int(round(self.analysis["source_count"] * factor))))
            cmds.intSliderGrp(self.controls["count"], edit=True, value=count)
            self.params_changed()

    def params_changed(self):
        for key, name in (("uv", "CylinderResampleUV"), ("hard", "CylinderResampleHard"),
                          ("beside", "CylinderResampleBeside")):
            cmds.optionVar(intValue=(name, int(cmds.checkBox(self.controls[key], query=True, value=True))))
        cmds.optionVar(intValue=("CylinderResampleMode", cmds.radioButtonGrp(self.controls["mode"], query=True, select=True)))
        text = self._summary()
        if self._preview_node():
            text += "\n参数已更改，当前预览尚未更新。"
        self.message(text)

    @staticmethod
    def _place_beside(node):
        box = cmds.exactWorldBoundingBox(node)
        cmds.move(max(box[3] - box[0], 0.1) * 1.25, 0, 0, node, relative=True, worldSpace=True)

    def preview(self):
        if not self.snapshot:
            raise adapter.ToolError("请先分析圆周边环。")
        count, mode, keep_uv, keep_hard = self._params()
        with adapter.undo_chunk("CylinderResamplePreview"):
            built = adapter.build_result(self.snapshot, count, mode, keep_uv, keep_hard, _undo_chunk=False)
            self._restore_source()
            self._delete_preview()
            source = self._source_shape()
            if not source:
                cmds.delete(built["node"])
                raise adapter.ToolError("原模型已不存在，请重新分析。")
            record = {"handle": adapter.om.MObjectHandle(adapter._dag(built["node"]).node()),
                      "source_handle": self.source_handle,
                      "baseline": bool(cmds.getAttr(source + ".visibility")),
                      "snapshot": self.snapshot, "analysis": self.analysis, "offset": False, "pending": True}
            self.preview_records.append(record)
            cmds.setAttr(built["node"] + ".cylinderResamplePreview", True)
            if not self._can_set_visibility(source):
                self._place_beside(built["node"])
                record["offset"] = True
                built["warnings"].append("原模型的显示状态受保护，预览已并排放置。")
            self._set_record_visibility(record, False)
        self._preview_controls(True)
        stats = built["result"].get("stats", {})
        ratio = stats.get("spacing_ratio")
        spacing = "\n所选圈最宽 / 最窄边长：{:.3f}".format(ratio) if ratio else ""
        self.message("预览：{} → {} 段{}\nUV 集：{}\n{}".format(
            self.analysis["source_count"], count, spacing,
            ", ".join(built["result"]["uv_sets"]) if keep_uv else "关闭", "\n".join(built["warnings"])))

    def compare(self):
        record = self._preview_record()
        if not record:
            return
        original = cmds.checkBox(self.controls["compare"], query=True, value=True)
        with adapter.undo_chunk("CylinderResampleCompare"):
            self._set_record_visibility(record, original)
            cmds.setAttr(self._handle_path(record["handle"]) + ".visibility", not original)

    def keep(self):
        record = self._preview_record()
        if not record:
            raise adapter.ToolError("预览已不存在，请重新生成。")
        node = self._handle_path(record["handle"])
        with adapter.undo_chunk("CylinderResampleKeep"):
            cmds.setAttr(node + ".visibility", True)
            if cmds.checkBox(self.controls["beside"], query=True, value=True) and not record.get("offset"):
                self._place_beside(node)
            self._restore_source([record])
            cmds.setAttr(node + ".cylinderResamplePreview", False)
            cmds.select(node, replace=True)
            record["pending"] = False
        self._preview_controls(False)
        self.message("已保留：{}\n原模型已恢复原显示状态；结果为独立副本。".format(node.split("|")[-1]))

    def _preview_controls(self, enabled):
        for key in ("keep", "cancel"):
            if cmds.control(self.controls.get(key, ""), exists=True):
                cmds.button(self.controls[key], edit=True, enable=enabled)
        if cmds.control(self.controls.get("compare", ""), exists=True):
            cmds.checkBox(self.controls["compare"], edit=True, enable=enabled, value=False)

    def cancel(self, silent=False):
        if self._restorable_records():
            with adapter.undo_chunk("CylinderResampleCancel"):
                self._restore_source()
                self._delete_preview()
        self._preview_controls(False)
        if not silent:
            self.message("预览已取消。")

    def sync_controls(self):
        for record in self.preview_records:
            if self._handle_path(record["handle"]):
                continue
            source = self._source_shape(record)
            # Undoing creation restores visibility; do not claim later user edits.
            if source and bool(cmds.getAttr(source + ".visibility")) == record["baseline"]:
                record["pending"] = False
        node = self._preview_node()
        self._preview_controls(bool(node))
        if node and cmds.control(self.controls.get("compare", ""), exists=True):
            cmds.checkBox(self.controls["compare"], edit=True, value=not bool(cmds.getAttr(node + ".visibility")))

    def scene_changed(self):
        self.preview_records = []
        self.snapshot = self.analysis = self.source_handle = self.source_baseline = None
        self._preview_controls(False)
        for key in ("preview", "region", "original", "half", "double"):
            if cmds.control(self.controls.get(key, ""), exists=True):
                cmds.button(self.controls[key], edit=True, enable=False)
        self.message("等待分析")

    def close(self):
        try:
            self.cancel(silent=True)
        except RuntimeError:
            pass

    def check_update(self):
        from .update_ui import check
        check(self)

    @staticmethod
    def _preference(name, default):
        return cmds.optionVar(query=name) if cmds.optionVar(exists=name) else default

    def make_window(self):
        cmds.window(WINDOW, title="圆柱重分段 {} | Maya 2024".format(__version__), widthHeight=(480, 680), sizeable=True)
        cmds.scrollLayout(childResizable=True)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=10, columnAttach=("both", 12))
        cmds.separator(height=6, style="none")
        cmds.text(label="圆柱重分段", align="left", font="boldLabelFont")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(300, 110), columnAttach2=("both", "both"))
        cmds.button(label="分析选中边环", height=32, annotation="边模式下选择一条完整的圆周边环，然后分析。",
                    command=lambda *_: self.run(self.read_selection))
        self.controls["region"] = cmds.button(label="选中处理范围", height=32, enable=False,
                                              command=lambda *_: self.run(self.highlight))
        cmds.setParent("..")
        cmds.separator(style="in", height=8)
        self.controls["count"] = cmds.intSliderGrp(label="目标段数", field=True, minValue=3, maxValue=128,
                                                   fieldMinValue=3, fieldMaxValue=4096, value=16,
                                                   columnWidth3=(72, 60, 260), adjustableColumn=3,
                                                   changeCommand=lambda *_: self.run(self.params_changed))
        cmds.rowLayout(numberOfColumns=3, columnWidth3=(140, 140, 140), adjustableColumn=3)
        for key, label, factor in (("original", "原段数", 1), ("half", "减半", 0.5), ("double", "加倍", 2)):
            self.controls[key] = cmds.button(label=label, enable=False, height=26,
                                              command=lambda *_, f=factor: self.run(lambda: self.set_count(f)))
        cmds.setParent("..")
        self.controls["mode"] = cmds.radioButtonGrp(numberOfRadioButtons=3,
                                                     labelArray3=("保留原轮廓", "均匀重采样", "圆形拟合"),
                                                     select=min(3, max(1, int(self._preference("CylinderResampleMode", 1)))),
                                                     columnWidth3=(145, 145, 130),
                                                     annotation="分段按所选边环分配，所有截面同步；UV 接缝、材质边界和硬边仍会保留。",
                                                     changeCommand=lambda *_: self.run(self.params_changed))
        self.controls["uv"] = cmds.checkBox(label="保留 UV", value=bool(self._preference("CylinderResampleUV", 1)),
                                             annotation="插值所有 UV 集，并锁定接缝列；段数过少无法保留时会提示。",
                                             changeCommand=lambda *_: self.run(self.params_changed))
        self.controls["hard"] = cmds.checkBox(label="保留硬边", value=bool(self._preference("CylinderResampleHard", 1)),
                                               annotation="保留边的软硬状态；纵向硬边会占用目标段数。锁定法线会重新计算。",
                                               changeCommand=lambda *_: self.run(self.params_changed))
        self.controls["preview"] = cmds.button(label="生成 / 更新预览", height=36, enable=False,
                                                command=lambda *_: self.run(self.preview))
        self.controls["compare"] = cmds.checkBox(label="查看原模型", value=False, enable=False,
                                                  changeCommand=lambda *_: self.run(self.compare))
        self.controls["beside"] = cmds.checkBox(label="保留时并排放置", value=bool(self._preference("CylinderResampleBeside", 1)),
                                                 changeCommand=lambda *_: self.run(self.params_changed))
        self.controls["status"] = cmds.scrollField(editable=False, wordWrap=True, height=115, text="等待分析",
                                                   font="smallPlainLabelFont")
        cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnWidth2=(300, 110), columnAttach2=("both", "both"))
        self.controls["keep"] = cmds.button(label="保留结果副本", height=32, enable=False,
                                            command=lambda *_: self.run(self.keep))
        self.controls["cancel"] = cmds.button(label="取消预览", height=32, enable=False,
                                              command=lambda *_: self.run(self.cancel))
        cmds.setParent("..")
        cmds.separator(height=4, style="none")
        self.controls["update"] = cmds.button(label="检查 GitHub 更新", height=28,
                                              annotation="仅安装正式发布版本；校验下载内容并保留旧版备份。",
                                              command=lambda *_: self.run(self.check_update))
        cmds.scriptJob(uiDeleted=(WINDOW, self.close), runOnce=True)
        for event in ("Undo", "Redo"):
            cmds.scriptJob(event=(event, self.sync_controls), parent=WINDOW)
        for event in ("SceneOpened", "NewSceneOpened"):
            cmds.scriptJob(event=(event, self.scene_changed), parent=WINDOW)
        cmds.showWindow(WINDOW)
        return WINDOW


def show():
    global _SESSION
    if _UPDATING:
        cmds.warning("插件正在更新，请稍候。")
        return WINDOW
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    records = _SESSION.preview_records if _SESSION else []
    if _SESSION:
        _SESSION.cancel(silent=True)
    _SESSION = Session()
    _SESSION.preview_records = records
    return _SESSION.make_window()
