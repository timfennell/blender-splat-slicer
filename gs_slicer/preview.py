"""Single-layer preview, drawn in the block at the layer's height by the viewport overlay
(so it shows in any shading mode), plus two images for the Image Editor:

"Splat Slice" is the layer itself (colour + opacity).
"Splat Slice Print" is the tile as it will print (over white, with marks).
"""

import time

import bpy
import numpy as np

from . import box as boxmod
from .export import layer_label, safe_name
from .page import TileLayout
from .slicer import Slicer, print_rgb

SLICE_IMG = "Splat Slice"
PRINT_IMG = "Splat Slice Print"
DISPLAY_IMG = ".Splat Slice Display"
PLANE_TAG = "gs_slicer_preview"

_slicer_cache = {}
_state = {"pending": False, "last": "", "seconds": 0.0, "layer": None}


def _set_image(name, rgba_top_down):
    h, w = rgba_top_down.shape[:2]
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (w, h):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=False)
        img.alpha_mode = 'STRAIGHT'
    # Blender stores rows bottom-up.
    img.pixels.foreach_set(np.ascontiguousarray(rgba_top_down[::-1], np.float32).ravel())
    img.update()
    return img


def remove_planes():
    """Remove preview planes left by version 0.1.0 (the slice is now drawn as an overlay)."""
    for o in [o for o in bpy.data.objects if o.get(PLANE_TAG)]:
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def render_preview(context, use_cache=True):
    p = context.scene.gs_slicer
    t = time.perf_counter()
    if p.box is not None:
        boxmod.make_render_invisible(p.box)
    vol = boxmod.build_volume(p, use_cache)
    count, z0, _ = p.layer_plan()
    k = min(max(p.preview_layer, 1), count) - 1
    # The slicer's per-splat setup is the slow part on big scans; reuse it while stepping layers.
    key = (p.block_size, round(p.pitch, 6), count, round(p.preview_mm, 6), round(z0, 6))
    if _slicer_cache.get("vol") is not vol or _slicer_cache.get("key") != key:
        _slicer_cache.update(vol=vol, key=key, slicer=Slicer(vol, p.block_size, p.pitch, count, p.preview_mm, z0))
    sl = _slicer_cache["slicer"]
    alpha, rgb = sl.render(k, k + 1)
    a, c = alpha[0], rgb[0]
    _set_image(SLICE_IMG, np.dstack([c, a]))
    # What the viewport overlay draws: true ink strength, or (Boost) faint ink lifted on screen only.
    _set_image(DISPLAY_IMG, np.dstack([c, np.power(a, 0.35) if p.preview_boost else a]))
    dpi = 25.4 / p.preview_mm
    lay = TileLayout(sl.cols, sl.rows, dpi, p.margin_mm, p.mark_mm)
    tile = lay.compose(print_rgb(a, c), layer_label(safe_name(p.name), k, count, z0, p.pitch) if p.labels else '',
                       p.marks, p.cut_lines, p.labels, p.mirror)
    _set_image(PRINT_IMG, np.dstack([tile / 255.0, np.ones(tile.shape[:2])]))
    _state["layer"] = k + 1
    remove_planes()
    _state["seconds"] = time.perf_counter() - t
    _state["last"] = f"Layer {k + 1}/{count}: {int((a > 0.01).sum())} inked px, {_state['seconds']:.2f} s"
    for area in context.screen.areas if context.screen else ():
        area.tag_redraw()
    return _state["last"]


def request(context):
    """Debounced live preview (property updates fire on every drag step)."""
    if _state["pending"]:
        return
    _state["pending"] = True

    def run():
        _state["pending"] = False
        try:
            render_preview(bpy.context)
        except Exception as exc:          # a live preview must never raise into the UI
            _state["last"] = f"Preview failed: {exc}"
        return None

    bpy.app.timers.register(run, first_interval=0.2)


def status():
    return _state["last"]
