"""Drag into Maya after installation to check the local runtime."""


def onMayaDroppedPythonFile(*_args):
    import maya.cmds as cmds
    try:
        import cylinder_resample
        result = cylinder_resample.self_test()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        cmds.confirmDialog(title="圆柱重分段自检", message="自检未通过：{}".format(exc), button=["确定"])
    else:
        cmds.confirmDialog(title="圆柱重分段自检", message=result, button=["确定"])


if __name__ == "__main__":
    onMayaDroppedPythonFile()
