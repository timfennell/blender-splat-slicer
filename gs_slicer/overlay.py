"""Viewport overlay on the block: which way the sheets stack and how a print sits on the block.

- a ruler of layer ticks up the four vertical edges (longer every 10 layers, numbered),
- an arrow beside the front face from sheet 1 (bottom) to the top sheet,
- the previewed layer outlined in orange, with its image drawn in the block (any shading mode),
- labels for the bottom / top sheets, the FRONT edge (the bottom edge of every printed image)
  and the key corner that matches the orientation dot printed beside the top-left mark.
"""

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_handles = []

COL_TICK = (0.85, 0.85, 0.85, 0.55)
COL_MAJOR = (1.0, 1.0, 1.0, 0.9)
COL_ARROW = (0.30, 0.75, 1.0, 1.0)
COL_LAYER = (1.0, 0.55, 0.1, 1.0)
COL_FRONT = (0.45, 1.0, 0.45, 1.0)


def _geometry(p):
    """Line segments (box-local metres) grouped by colour, plus labels to draw in 2D."""
    count, z0, _ = p.layer_plan()
    L, W, H = p.length * 0.001, p.width * 0.001, p.height * 0.001
    hx, hy, hz = L / 2, W / 2, H / 2
    pitch = p.pitch * 0.001
    zb = -hz + z0 * 0.001                       # bottom of layer 1
    size = max(L, W, H)
    tick, major = 0.012 * size, 0.035 * size
    lines = {COL_TICK: [], COL_MAJOR: [], COL_ARROW: [], COL_LAYER: [], COL_FRONT: []}
    labels = []
    # Ruler: layer boundaries as ticks pointing out of each vertical edge.
    step = 1 if count <= 400 else max(1, count // 200)
    corners = [(-hx, -hy, (-1, 0), (0, -1)), (hx, -hy, (1, 0), (0, -1)),
               (hx, hy, (1, 0), (0, 1)), (-hx, hy, (-1, 0), (0, 1))]
    for k in range(0, count + 1, step):
        z = zb + k * pitch
        big = k % 10 == 0 or k == count
        col, ln = (COL_MAJOR, major) if big else (COL_TICK, tick)
        for x, y, (ax, ay), (bx, by) in corners:
            lines[col] += [(x, y, z), (x + ax * ln, y + ay * ln, z), (x, y, z), (x + bx * ln, y + by * ln, z)]
        if big and 0 < k < count and k % 10 == 0:
            labels.append(((-hx - major * 1.3, -hy, z), str(k), COL_MAJOR, 11))
    # Arrow beside the front face, bottom sheet to top sheet.
    ax, ay = 0.0, -hy - 0.08 * size
    top = zb + count * pitch
    head = 0.05 * size
    lines[COL_ARROW] += [(ax, ay, zb), (ax, ay, top),
                         (ax, ay, top), (ax - head * 0.5, ay, top - head),
                         (ax, ay, top), (ax + head * 0.5, ay, top - head),
                         (ax - head, ay, zb), (ax + head, ay, zb)]
    labels.append(((ax + head * 0.8, ay, zb), f"sheet 1 (bottom)", COL_ARROW, 13))
    labels.append(((ax + head * 0.8, ay, top), f"sheet {count} (top)", COL_ARROW, 13))
    labels.append(((ax + head * 0.8, ay, (zb + top) / 2), "stacking", COL_ARROW, 12))
    # Front edge = bottom edge of every printed image.
    lines[COL_FRONT] += [(-hx, -hy, -hz), (hx, -hy, -hz), (-hx, -hy, hz), (hx, -hy, hz)]
    labels.append(((hx * 0.55, -hy, -hz), "FRONT = print bottom edge", COL_FRONT, 12))
    # Key corner: image top-left (x min, y max), where the printed orientation dot sits.
    labels.append(((-hx, hy, hz), "● key corner (print top-left)", COL_MAJOR, 12))
    # The previewed layer.
    k = min(max(p.preview_layer, 1), count) - 1
    z = zb + (k + 0.5) * pitch
    rect = [(-hx, -hy, z), (hx, -hy, z), (hx, hy, z), (-hx, hy, z)]
    for i in range(4):
        lines[COL_LAYER] += [rect[i], rect[(i + 1) % 4]]
    labels.append(((hx + major * 1.3, -hy, z), f"layer {k + 1}", COL_LAYER, 13))
    return lines, labels


def _slicer(context):
    """Scene settings if the block is in the scene and visible."""
    p = getattr(context.scene, "gs_slicer", None)
    if p is None or p.box is None:
        return None
    if p.box.name not in context.view_layer.objects or not p.box.visible_get():
        return None
    return p


def _enabled(context):
    p = _slicer(context)
    return p if p is not None and p.show_overlay else None


def _draw_slice(context, p):
    """The previewed layer's image, on its layer inside the block."""
    from .preview import DISPLAY_IMG, _state
    img = bpy.data.images.get(DISPLAY_IMG)
    if img is None or not _state.get("layer") or img.size[0] == 0:
        return
    count, z0, _ = p.layer_plan()
    k = min(_state["layer"], count) - 1
    hx, hy = p.length * 0.0005, p.width * 0.0005
    z = (z0 + (k + 0.5) * p.pitch - p.height / 2) * 0.001
    mw = p.box.matrix_world
    corners = [tuple(mw @ Vector(c)) for c in ((-hx, -hy, z), (hx, -hy, z), (hx, hy, z), (-hx, hy, z))]
    try:
        tex = gpu.texture.from_image(img)
    except Exception:
        return
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    # A faint frosted sheet behind the ink, so the layer reads as a sheet of film over the splat.
    sheet = gpu.shader.from_builtin('UNIFORM_COLOR')
    sheet.bind()
    sheet.uniform_float("color", (1.0, 1.0, 1.0, 0.55))
    batch_for_shader(sheet, 'TRI_FAN', {"pos": corners}).draw(sheet)
    shader = gpu.shader.from_builtin('IMAGE')
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": corners, "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    shader.bind()
    shader.uniform_sampler("image", tex)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_lines():
    context = bpy.context
    p = _slicer(context)
    if p is not None and p.show_slice:
        _draw_slice(context, p)
    p = _enabled(context)
    if p is None:
        return
    lines, _ = _geometry(p)
    mw = p.box.matrix_world
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    region = context.region
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    for col, pts in lines.items():
        if not pts:
            continue
        world = [tuple(mw @ Vector(v)) for v in pts]
        batch = batch_for_shader(shader, 'LINES', {"pos": world})
        shader.bind()
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 2.5 if col in (COL_ARROW, COL_LAYER) else 1.5)
        shader.uniform_float("color", col)
        batch.draw(shader)
    gpu.state.blend_set('NONE')


def _draw_labels():
    context = bpy.context
    p = _enabled(context)
    if p is None:
        return
    _, labels = _geometry(p)
    mw = p.box.matrix_world
    region, rv3d = context.region, context.region_data
    font = 0
    scale = context.preferences.system.ui_scale
    for co, text, col, size in labels:
        xy = location_3d_to_region_2d(region, rv3d, mw @ Vector(co))
        if xy is None:
            continue
        blf.size(font, size * scale)
        blf.color(font, *col)
        blf.enable(font, blf.SHADOW)
        blf.shadow(font, 3, 0, 0, 0, 0.8)
        blf.position(font, xy.x + 4, xy.y - 4, 0)
        blf.draw(font, text)
        blf.disable(font, blf.SHADOW)


def register():
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_lines, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    for h in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _handles.clear()
