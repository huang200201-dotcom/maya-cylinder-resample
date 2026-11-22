"""Copy Maya texture relationships for additional UV sets on a new mesh."""


def _set_plugs(cmds, shape):
    indices = cmds.polyUVSet(shape, query=True, allUVSetsIndices=True) or []
    plugs = []
    for index in sorted(indices):
        plug = "{}.uvSet[{}].uvSetName".format(shape, index)
        plugs.append((cmds.getAttr(plug), plug))
    return plugs


def _textures(cmds, plug):
    names = cmds.uvLink(query=True, uvSet=plug) or []
    return (names,) if isinstance(names, str) else names


def copy_texture_uv_links(cmds, source_shape, output_shape, uv_sets, shading_groups):
    """Recreate links only for textures in shading groups assigned to the copy."""
    groups = sorted({group for group in shading_groups if group})
    if len(uv_sets) < 2 or not groups:
        return 0
    if source_shape == output_shape:
        raise ValueError("UV 关联的源和结果必须是不同的网格。")
    pending = {}
    # The first set is Maya's default. A default texture needs no explicit link.
    for name, plug in _set_plugs(cmds, source_shape)[1:]:
        if name in uv_sets:
            textures = _textures(cmds, plug)
            if textures:
                pending[name] = textures
    if not pending:
        return 0

    upstream = {node.lstrip(":") for node in
                cmds.listHistory(groups, allConnections=True, pruneDagObjects=True) or []}
    links = {}
    for name, textures in pending.items():
        for texture in textures:
            key = texture.lstrip(":")
            if key not in upstream:
                continue
            if key in links and links[key][0] != name:
                raise RuntimeError("贴图 {} 在源模型上有多个 UV 集关联，请检查选中实例的 UV 链接。".format(texture))
            links[key] = (name, texture)
    if not links:
        return 0
    destination = dict(_set_plugs(cmds, output_shape))
    by_set = {}
    for key, (name, texture) in links.items():
        if name not in destination:
            raise RuntimeError("结果缺少贴图所需的 UV 集 {}。".format(name))
        by_set.setdefault(name, {})[key] = texture

    copied = 0
    for name, required in by_set.items():
        plug = destination[name]
        existing = {texture.lstrip(":") for texture in _textures(cmds, plug)}
        for texture in sorted(required.keys() - existing):
            cmds.uvLink(make=True, uvSet=plug, texture=required[texture])
            copied += 1
        verified = {texture.lstrip(":") for texture in _textures(cmds, plug)}
        missing = sorted(required.keys() - verified)
        if missing:
            raise RuntimeError("结果 UV 集 {} 的贴图关联校验失败：{}。".format(name, "、".join(missing)))
    return copied
