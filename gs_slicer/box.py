"""The block box and reading splats into the block's millimetre frame.

The box mesh is built at the block's true size in metres (1 mm = 0.001 m of mesh), centred on
its origin. Move, rotate or scale the box object to frame the splat: whatever falls inside the
box is what gets printed, and the box's local axes are the block's L (x), W (y) and H (z).
"""

import math

import bpy
import numpy as np
from mathutils import Matrix

from .slicer import SplatVolume, splat_colors

BOX_TAG = "gs_slicer_block"
HIDDEN_ATTR = "gsp_hidden"      # splats erased with Gaussian Splat Patch (kept, opacity 0)


def update_box_mesh(obj, size_mm):
    L, W, H = (s * 0.001 / 2 for s in size_mm)
    me = obj.data
    verts = [(-L, -W, -H), (L, -W, -H), (L, W, -H), (-L, W, -H),
             (-L, -W, H), (L, -W, H), (L, W, H), (-L, W, H)]
    if len(me.vertices) == 8:
        for v, co in zip(me.vertices, verts):
            v.co = co
        me.update()
    else:
        me.clear_geometry()
        me.from_pydata(verts, [], [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
                                   (2, 3, 7, 6), (3, 0, 4, 7)])
        me.update()


def make_render_invisible(obj):
    """A guide object: drawn in Solid view, invisible to Cycles (including the Rendered viewport,
    which ignores hide_render and would otherwise draw the box as an opaque grey shell)."""
    obj.hide_render = True
    for attr in ('visible_camera', 'visible_diffuse', 'visible_glossy', 'visible_transmission',
                 'visible_volume_scatter', 'visible_shadow'):
        if hasattr(obj, attr):
            setattr(obj, attr, False)


def fix_all_boxes():
    """Make every slicing box render-invisible (boxes made by 0.1.0/0.1.1 rendered as grey shells)."""
    for scene in bpy.data.scenes:
        p = getattr(scene, "gs_slicer", None)
        box = p.box if p is not None else None
        if box is not None and getattr(box, "visible_camera", False):
            make_render_invisible(box)
    for obj in bpy.data.objects:
        if obj.get(BOX_TAG) and getattr(obj, "visible_camera", False):
            make_render_invisible(obj)


def create_box(context, size_mm, name="Slice Block"):
    me = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, me)
    update_box_mesh(obj, size_mm)
    obj.display_type = 'WIRE'
    obj.show_in_front = True
    obj.show_axis = True
    make_render_invisible(obj)
    obj[BOX_TAG] = True
    context.collection.objects.link(obj)
    return obj


def world_points(splat, sample=200_000):
    pc = splat.data
    n = len(pc.points)
    pos = np.empty(n * 3, np.float32)
    pc.attributes["position"].data.foreach_get("vector", pos)
    pos = pos.reshape(n, 3)
    if n > sample:
        pos = pos[np.random.default_rng(0).choice(n, sample, replace=False)]
    mw = np.array(splat.matrix_world)
    return pos @ mw[:3, :3].T + mw[:3, 3]


def fit_box(box, splat, size_mm, padding=0.05, trim=0.5, fit_height=False, pitch=None):
    """Centre the box on the splat and scale it uniformly so the splat fits inside.

    Works in the box's current orientation; `trim` percent of points at each end are ignored
    so a few floaters don't shrink the model. With `fit_height`, the scale comes from length and
    width only and the height is derived from the model (plus padding), rounded up to whole
    layers of `pitch`. Returns (scale, height_mm).
    """
    pts = world_points(splat)
    rot = box.matrix_world.to_3x3().normalized()
    R = np.array(rot)
    local = pts @ R                                  # rows: point coords along the box axes
    lo = np.percentile(local, trim, axis=0)
    hi = np.percentile(local, 100 - trim, axis=0)
    ext = hi - lo
    keep = 1.0 - 2.0 * padding
    size_m = np.array(size_mm) * 0.001 * keep
    axes = slice(0, 2) if fit_height else slice(0, 3)
    s = float(np.max(ext[axes] / size_m[axes]))
    height = size_mm[2]
    if fit_height:
        height = ext[2] / s * 1000.0 / keep          # model height in print mm, plus padding
        if pitch:
            height = math.ceil(height / pitch - 1e-6) * pitch
    centre = R @ ((lo + hi) / 2)
    box.matrix_world = (Matrix.Translation(centre.tolist()) @ rot.to_4x4()
                        @ Matrix.Diagonal((s, s, s, 1.0)))
    return s, height


def to_block_matrix(box, splat, size_mm):
    """4x4 taking splat object coordinates to block millimetres (origin at the bottom-left-front corner)."""
    L, W, H = size_mm
    m = (Matrix.Translation((L / 2, W / 2, H / 2)) @ Matrix.Diagonal((1000.0, 1000.0, 1000.0, 1.0))
         @ box.matrix_world.inverted() @ splat.matrix_world)
    return np.array(m)


def _attr(pc, name, width, key):
    a = pc.attributes.get(name)
    if a is None:
        return None
    n = len(pc.points)
    dtype = bool if a.data_type == 'BOOLEAN' else np.float32
    buf = np.empty(n * width, dtype)
    a.data.foreach_get(key, buf)
    return buf.reshape(n, width) if width > 1 else buf


def read_splats(splat, with_sh):
    pc = splat.data
    n = len(pc.points)
    pos = _attr(pc, "position", 3, "vector")
    scale = _attr(pc, "scale", 3, "vector")
    quat = _attr(pc, "rotation", 4, "value")
    base = _attr(pc, "radiance:base", 4, "vector")
    if scale is None or quat is None or base is None:
        raise ValueError(f"'{splat.name}' is not a Gaussian splat (needs scale, rotation and radiance:base)")
    sh = None
    if with_sh:
        coeffs = []
        for k in range(15):
            c = _attr(pc, f"radiance:sh_{k}", 3, "vector")
            if c is None:
                break
            coeffs.append(c)
        if coeffs:
            sh = np.stack(coeffs, 1)
    opacity = base[:, 3].copy()
    hidden = _attr(pc, HIDDEN_ATTR, 1, "value")
    if hidden is not None:
        opacity[hidden] = 0.0
    return dict(n=n, pos=pos, scale=scale, quat=quat, dc=base[:, :3], opacity=opacity, sh=sh)


_cache = {"key": None, "vol": None}


def _key(p):
    return (p.splat.as_pointer(), len(p.splat.data.points), tuple(map(tuple, p.splat.matrix_world)),
            tuple(map(tuple, p.box.matrix_world)), p.block_size, p.color_mode, p.face, round(p.cone_deg, 3),
            round(p.density, 5), round(p.min_opacity, 5))


def build_volume(p, use_cache=True):
    """SplatVolume for the scene settings `p` (cached between previews)."""
    if p.splat is None or p.box is None:
        raise ValueError("Pick a splat and create a block first")
    key = _key(p)
    if use_cache and _cache["key"] == key:
        return _cache["vol"]
    M = to_block_matrix(p.box, p.splat, p.block_size)
    d = read_splats(p.splat, with_sh=p.color_mode == 'FACE')
    rgb = splat_colors(d["dc"], d["sh"], M[:3, :3], p.color_mode, p.face, p.cone_deg)
    vol = SplatVolume(d["pos"], d["scale"], d["quat"], d["opacity"], rgb, M,
                      density=p.density, min_opacity=p.min_opacity)
    _cache.update(key=key, vol=vol)
    return vol


def clear_cache():
    _cache.update(key=None, vol=None)
