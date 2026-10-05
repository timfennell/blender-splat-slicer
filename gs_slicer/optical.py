"""Optical mock-up of the laminated block, for Cycles.

The block is built the way it will be made: film sheets (glass, n_film) with each sheet's ink
printed on its top face, and a glue line (n_glue) between sheets.

What it shows:
- the model through the faces of the sheets (face-on), as printed;
- edge-on, the ink sits on planes one pitch apart, so the model breaks into stripes;
- haze from rough interfaces (inkjet coatings) and from unpolished sides.

Near grazing, light inside the higher-index film is totally reflected at the lower-index glue
(critical angle asin(n_glue / n_film), about 71 deg for PET + LOCA). That light stays in its own
sheet, which is where a straight ray would have gone anyway, so side views stay readable: it only
mirrors the image within strips one film thick.

Quality: Fast models the ink planes and the block; Fine adds every glue interface (two refracting
planes per glue line). Cycles takes IOR as the ratio across a face, entering from the side the normal
points to: a film-below / glue-above boundary faces up, a glue-below / film-above boundary faces
down, both with IOR n_film / n_glue.
"""

import math

import bpy
import numpy as np

from . import box as boxmod
from .slicer import Slicer, print_rgb

OBJ_NAME = "Optical Block"
OBJ_TAG = "gs_slicer_optical"
IFACE_NAME = "Optical Glue Interfaces"
IFACE_TAG = "gs_slicer_optical_interfaces"
ATLAS = "Splat Stack Atlas"
TABLE_TAG = "gs_slicer_light_table"      # display lighting and backdrop objects


def _glass(name, ior, roughness):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    g = nt.nodes.new('ShaderNodeBsdfGlass')
    g.distribution = 'MULTI_GGX'
    g.inputs['IOR'].default_value = ior
    g.inputs['Roughness'].default_value = roughness
    nt.links.new(g.outputs[0], out.inputs['Surface'])
    return mat


def _ink_material(img, scatter):
    """Ink on a sheet: transmission tinted by the printed colour, plus a little pigment scatter."""
    mat = bpy.data.materials.get("Optical Ink") or bpy.data.materials.new("Optical Ink")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.interpolation = 'Linear'
    tex.extension = 'EXTEND'
    clear = nt.nodes.new('ShaderNodeBsdfTransparent')
    diffuse = nt.nodes.new('ShaderNodeBsdfDiffuse')
    fac = nt.nodes.new('ShaderNodeMath')
    fac.operation = 'MULTIPLY'
    fac.inputs[1].default_value = scatter
    mix = nt.nodes.new('ShaderNodeMixShader')
    nt.links.new(tex.outputs['Color'], clear.inputs['Color'])
    nt.links.new(tex.outputs['Color'], diffuse.inputs['Color'])
    nt.links.new(tex.outputs['Alpha'], fac.inputs[0])
    nt.links.new(fac.outputs[0], mix.inputs['Fac'])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(diffuse.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs['Surface'])
    return mat


def _atlas(tiles, rows, cols):
    """Pack (n, rows, cols, 4) tiles into one image; returns the image and each tile's UV rect."""
    n = len(tiles)
    gx = max(1, math.ceil(math.sqrt(n * rows / cols)))
    gy = math.ceil(n / gx)
    W, H = gx * cols, gy * rows
    buf = np.zeros((H, W, 4), np.float32)
    uvs = []
    for i, t in enumerate(tiles):
        cx, cy = i % gx, i // gx
        y0 = H - (cy + 1) * rows                       # Blender rows run bottom-up
        buf[y0:y0 + rows, cx * cols:(cx + 1) * cols] = t[::-1]
        uvs.append((cx * cols / W, y0 / H, (cx + 1) * cols / W, (y0 + rows) / H))
    img = bpy.data.images.get(ATLAS)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(ATLAS, W, H, alpha=True, float_buffer=False)
    img.alpha_mode = 'CHANNEL_PACKED'
    img.pixels.foreach_set(buf.ravel())
    img.update()
    img.pack()
    return img, uvs


def _quad(verts, faces, mats, uv_list, z, hx, hy, mat, uv=(0, 0, 1, 1), down=False):
    i = len(verts)
    verts += [(-hx, -hy, z), (hx, -hy, z), (hx, hy, z), (-hx, hy, z)]
    u0, v0, u1, v1 = uv
    if down:
        faces.append((i, i + 3, i + 2, i + 1))
        uv_list.append(((u0, v0), (u0, v1), (u1, v1), (u1, v0)))
    else:
        faces.append((i, i + 1, i + 2, i + 3))
        uv_list.append(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))
    mats.append(mat)


def build(context, layers_per_sheet=1):
    """Blocking build (scripts, background mode). Returns a status string."""
    it = build_iter(context, layers_per_sheet)
    while True:
        try:
            next(it)
        except StopIteration as done:
            return done.value


def build_iter(context, layers_per_sheet=1):
    """(Re)build the optical block as a child of the slicing box.

    Generator: yields (fraction, message) as it goes and returns a status string.
    """
    p = context.scene.gs_slicer
    boxmod.make_render_invisible(p.box)        # boxes made by 0.1.0 still render in the viewport
    yield 0.0, "Reading splats"
    vol = boxmod.build_volume(p)
    count, z0, _ = p.layer_plan()
    sl = Slicer(vol, p.block_size, p.pitch, count, p.optical_px_mm, z0)
    # Each modelled sheet carries the combined transmission of `layers_per_sheet` layers.
    m = max(1, int(layers_per_sheet))
    groups = [(a, min(a + m, count)) for a in range(0, count, m)]
    tiles = []
    chunk = sl.chunk_layers()
    trans = np.empty((count, sl.rows, sl.cols, 3), np.float32)
    cover = np.empty((count, sl.rows, sl.cols), np.float32)
    for first in range(0, count, chunk):
        last = min(first + chunk, count)
        it = sl.render_iter(first, last)
        while True:
            try:
                next(it)
            except StopIteration as done:
                a, c = done.value
                break
            yield 0.05 + 0.85 * first / count, f"Slicing layers {first + 1}-{last} of {count}"
        trans[first:last] = print_rgb(a, c)
        cover[first:last] = a
    yield 0.92, "Building sheets and glue interfaces"
    for a, b in groups:
        t = np.prod(trans[a:b], axis=0)
        cov = 1.0 - np.prod(1.0 - cover[a:b], axis=0)
        tiles.append(np.dstack([t, cov]))
    img, uvs = _atlas(tiles, sl.rows, sl.cols)

    hx, hy, hz = p.length * 0.0005, p.width * 0.0005, p.height * 0.0005
    pitch = p.pitch * 0.001
    film = min(p.film_mm, p.pitch) * 0.001
    zb = -hz + z0 * 0.001
    eps = 0.02 * film
    # The block: ink planes and the outer shell. Slots: 0 ink, 1 block top & bottom, 2 block sides.
    verts, faces, mats, uv_list = [], [], [], []
    # The glue interfaces, as a separate object shown only at Fine quality. They are thin and
    # parallel, so refracting into one and out again leaves a ray's direction unchanged; Fast skips them.
    iverts, ifaces, imats, iuvs = [], [], [], []
    for gi, (a, b) in enumerate(groups):
        top_film = zb + (b - 1) * pitch + film          # ink on the top face of the group's last film
        _quad(verts, faces, mats, uv_list, top_film - eps, hx, hy, 0, uvs[gi])
        if b < count and pitch - film > 1e-9:
            _quad(iverts, ifaces, imats, iuvs, top_film, hx, hy, 0)                  # film -> glue
            _quad(iverts, ifaces, imats, iuvs, zb + b * pitch, hx, hy, 0, down=True)  # glue -> film
    i = len(verts)
    verts += [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
              (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    for f, mat in (((0, 3, 2, 1), 1), ((4, 5, 6, 7), 1), ((0, 1, 5, 4), 2), ((1, 2, 6, 5), 2),
                   ((2, 3, 7, 6), 2), ((3, 0, 4, 7), 2)):
        faces.append(tuple(i + v for v in f))
        mats.append(mat)
        uv_list.append(((0, 0), (1, 0), (1, 1), (0, 1)))
    _attach(p, OBJ_TAG, OBJ_NAME, _mesh(OBJ_NAME, verts, faces, mats, uv_list, [
        _ink_material(img, p.ink_scatter),
        _glass("Optical Film Faces", p.film_ior, 0.0),
        _glass("Optical Block Sides", p.film_ior, p.side_roughness)]))
    _attach(p, IFACE_TAG, IFACE_NAME, _mesh(IFACE_NAME, iverts, ifaces, imats, iuvs, [
        _glass("Optical Interface", p.film_ior / p.glue_ior, p.interface_roughness)]))
    build_studio(context)
    _use_world(context.scene, True)
    _hide_others(p, True)
    if p.optical_setup_cycles:
        setup_cycles(context.scene, len(groups))
    apply_quality(context.scene)
    sheets = len(groups)
    return (f"Optical block: {sheets} sheets, {2 * (sheets - 1)} glue interfaces, "
            f"critical angle {math.degrees(math.asin(min(1.0, p.glue_ior / p.film_ior))):.1f} deg")


def _mesh(name, verts, faces, mats, uv_list, materials):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    uv_layer = me.uv_layers.new(name="UVMap")
    for poly, uvq in zip(me.polygons, uv_list):
        for li, uv in zip(poly.loop_indices, uvq):
            uv_layer.data[li].uv = uv
    me.polygons.foreach_set("material_index", mats)
    for mat in materials:
        me.materials.append(mat)
    me.update()
    return me


def _attach(p, tag, name, me):
    """Put `me` on the box's child object tagged `tag`, creating it if needed."""
    obj = next((o for o in bpy.data.objects if o.get(tag) and o.parent == p.box), None)
    if obj is None:
        obj = bpy.data.objects.new(name, me)
        obj[tag] = True
        for coll in p.box.users_collection:
            coll.objects.link(obj)
        obj.parent = p.box
        obj.hide_select = True
    else:
        old = obj.data
        obj.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)
    obj.matrix_parent_inverse.identity()
    obj.location, obj.rotation_euler, obj.scale = (0, 0, 0), (0, 0, 0), (1, 1, 1)
    obj.hide_render = False
    obj.hide_set(False)
    return obj


def _find(box):
    return next((o for o in bpy.data.objects if o.get(OBJ_TAG) and o.parent == box), None)


def _parts(p):
    """The optical block and its light table, if built."""
    return [o for o in bpy.data.objects
            if (o.get(OBJ_TAG) or o.get(IFACE_TAG) or o.get(TABLE_TAG)) and o.parent == p.box]


def is_built(p):
    return p.box is not None and _find(p.box) is not None


def is_visible(p):
    obj = _find(p.box) if p.box is not None else None
    return obj is not None and obj.name in bpy.context.view_layer.objects and not obj.hide_get()


# Viewport shading before the mock-up was shown, per 3D view, so hiding it puts the view back.
_prev_shading = {}


def _view3d_spaces(context):
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            yield area.spaces.active


def set_visible(context, on):
    """Show or hide the built mock-up: swaps it with the splat and the slice overlay."""
    p = context.scene.gs_slicer
    for o in _parts(p):
        o.hide_set(not on)
        o.hide_render = not on
    _hide_others(p, on)
    _use_world(context.scene, on)
    if on and context.scene.render.engine == 'CYCLES':
        apply_quality(context.scene)
    for space in _view3d_spaces(context):
        key = space.as_pointer()
        if on:
            if space.shading.type != 'RENDERED':
                _prev_shading[key] = space.shading.type
                space.shading.type = 'RENDERED'
        else:
            if space.shading.type == 'RENDERED':
                space.shading.type = _prev_shading.pop(key, 'SOLID')


# Display setups. Every light is an emissive panel: its brightness doesn't depend on the scene's
# scale (the block box is often scaled up tens of times). Panels that stand in for lamps are hidden
# from the camera, so you see their light, not the panel.
#   floor/wall: backdrop grey level, or None for none; base/back: emissive strength of the light pad
#   under the block / the panel behind it; key: front lamp strength; edge: side-strip strength.
LIGHTING = {
    'LIGHT_BASE': dict(floor=0.55, wall=0.55, base=4.0, world=0.5),
    'BACKLIGHT': dict(floor=0.6, wall=None, back=4.0, world=0.5),
    'FRONT_WHITE': dict(floor=0.85, wall=0.85, key=8.0, world=0.6),
    'FRONT_BLACK': dict(floor=0.03, wall=0.03, key=8.0, world=0.03),
    'EDGE': dict(floor=0.03, wall=0.03, edge=16.0, world=0.03),
}
WORLD_NAME = "Splat Optics World"
_PREV_WORLD = "gs_slicer_prev_world"


_BASE_STRENGTH = "gs_slicer_base_strength"


def _emission(name, strength):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat[_BASE_STRENGTH] = strength
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new('ShaderNodeEmission')
    em.name = "Emission"
    em.inputs['Strength'].default_value = strength * bpy.context.scene.gs_slicer.optical_brightness
    nt.links.new(em.outputs[0], nt.nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
    return mat


def _matte(name, grey):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    d = nt.nodes.new('ShaderNodeBsdfDiffuse')
    d.inputs['Color'].default_value = (grey, grey, grey, 1.0)
    nt.links.new(d.outputs[0], nt.nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
    return mat


def _panel(p, name, corners, mat, camera=True):
    me = bpy.data.meshes.new(name)
    me.from_pydata(corners, [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    obj[TABLE_TAG] = True
    obj.hide_select = True
    obj.visible_camera = camera
    for coll in p.box.users_collection:
        coll.objects.link(obj)
    obj.parent = p.box
    obj.matrix_parent_inverse.identity()
    return obj


def _clear_studio(p):
    for o in [o for o in bpy.data.objects if o.get(TABLE_TAG) and o.parent == p.box]:
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def build_studio(context):
    """(Re)build the display lighting for the current Lighting choice."""
    p = context.scene.gs_slicer
    _clear_studio(p)
    cfg = LIGHTING.get(p.optical_lighting)
    if cfg is None:                                  # 'SCENE': the user's own lights and world
        return
    hx, hy, hz = p.length * 0.0005, p.width * 0.0005, p.height * 0.0005
    S = max(hx, hy, hz)
    z0 = -hz - 0.01 * S                              # the block sits on the floor
    far, back, top = 8 * S, hy + 1.5 * S, z0 + 8 * S
    if cfg.get('floor') is not None:
        _panel(p, "Optics Floor", [(-far, -far, z0), (far, -far, z0), (far, back, z0), (-far, back, z0)],
               _matte("Optics Floor", cfg['floor']))
    if cfg.get('wall') is not None:
        _panel(p, "Optics Back Wall", [(-far, back, z0), (far, back, z0), (far, back, top), (-far, back, top)],
               _matte("Optics Back Wall", cfg['wall']))
    if 'base' in cfg:                                # light pad just under the block, a little larger
        bx, by, zb = hx * 1.5, hy * 1.5, z0 + 0.005 * S
        _panel(p, "Optics Light Base", [(-bx, -by, zb), (bx, -by, zb), (bx, by, zb), (-bx, by, zb)],
               _emission("Optics Light Base", cfg['base']))
    if 'back' in cfg:                                # panel behind the block, filling the view
        _panel(p, "Optics Backlight", [(-far, back, z0), (far, back, z0), (far, back, top), (-far, back, top)],
               _emission("Optics Backlight", cfg['back']))
    if 'key' in cfg:                                 # soft lamp in front of and above the block
        cy, cz, k = -4 * S, 3 * S, 2 * S
        _panel(p, "Optics Key Light", [(-k, cy, cz - k), (k, cy, cz - k), (k, cy + k, cz + k), (-k, cy + k, cz + k)],
               _emission("Optics Key Light", cfg['key']), camera=False)
    if 'edge' in cfg:                                # LED strips against the two side faces
        g = 0.02 * S
        for side in (-1, 1):
            x = side * (hx + g)
            _panel(p, f"Optics Edge Light {'L' if side < 0 else 'R'}",
                   [(x, -hy, -hz), (x, hy, -hz), (x, hy, hz), (x, -hy, hz)],
                   _emission("Optics Edge Light", cfg['edge']), camera=False)
    # Built while in Setup view (lighting changed there): keep it out of sight until Optics.
    if not is_visible(p):
        for o in bpy.data.objects:
            if o.get(TABLE_TAG) and o.parent == p.box:
                o.hide_set(True)
                o.hide_render = True


def apply_brightness(scene):
    """Scale every display light by the Brightness setting (no rebuild)."""
    k = scene.gs_slicer.optical_brightness
    legacy = {"Optics Light Base": 4.0, "Optics Backlight": 4.0, "Optics Key Light": 8.0, "Optics Edge Light": 16.0}
    for mat in bpy.data.materials:
        base = mat.get(_BASE_STRENGTH, legacy.get(mat.name))     # lights built by 0.2.0 didn't record it
        if base is not None and mat.node_tree and "Emission" in mat.node_tree.nodes:
            mat.node_tree.nodes["Emission"].inputs['Strength'].default_value = base * k


def _use_world(scene, on):
    """Optics mode gets its own background; Setup puts the scene's world back."""
    p = scene.gs_slicer
    cfg = LIGHTING.get(p.optical_lighting)
    if on and cfg is not None:
        world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs['Color'].default_value = (cfg['world'],) * 3 + (1.0,)
            bg.inputs['Strength'].default_value = 1.0
        if scene.world is not world:
            scene[_PREV_WORLD] = scene.world.name if scene.world else ""
            scene.world = world
    elif scene.world is not None and scene.world.name == WORLD_NAME:
        prev = bpy.data.worlds.get(scene.get(_PREV_WORLD, ""))
        scene.world = prev


def _hide_others(p, hide):
    """The splat and the slice overlay sit inside the block; hide them while the mock-up shows."""
    if p.splat is not None:
        p.splat.hide_set(hide)
        p.splat.hide_render = hide
    p.show_slice = not hide


def remove(context):
    p = context.scene.gs_slicer
    for o in _parts(p):
        me = o.data
        bpy.data.objects.remove(o)
        if me.users == 0:
            bpy.data.meshes.remove(me)
    img = bpy.data.images.get(ATLAS)
    if img is not None:
        bpy.data.images.remove(img)
    _hide_others(p, False)


def setup_cycles(scene, sheets):
    """Enough bounces to get through every sheet and interface, with caustics allowed."""
    scene.render.engine = 'CYCLES'
    c = scene.cycles
    # Use the GPU when one is enabled in Preferences > System > Cycles Render Devices.
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.get_devices()                    # the device list is empty until refreshed
        if prefs.compute_device_type != 'NONE' and any(d.use and d.type != 'CPU' for d in prefs.devices):
            c.device = 'GPU'
    except (KeyError, AttributeError):
        pass
    c.use_preview_denoising = True             # hundreds of glass interfaces are noisy without it
    need = min(1024, 4 * sheets + 32)
    c.max_bounces = max(c.max_bounces, need)
    c.transmission_bounces = max(c.transmission_bounces, need)
    c.transparent_max_bounces = max(c.transparent_max_bounces, min(1024, 2 * sheets + 32))
    # Every total internal reflection at a glue line is a glossy bounce. Light trapped in a film layer
    # reflects many times; a low limit cuts those paths off and paints them black (a false "dark band").
    c.glossy_bounces = max(c.glossy_bounces, need)
    c.caustics_refractive = True
    c.caustics_reflective = True
    apply_quality(scene)


# Viewport settings per Optics quality: (pixel size, samples). All sheets and interfaces are always
# modelled; merging sheets loses the light trapped in each thin film layer (the grazing mirror band),
# so speed comes from rendering fewer pixels and samples and letting the denoiser clean up.
QUALITY = {'FAST': ('2', 16), 'FINE': ('AUTO', 64)}


def apply_quality(scene):
    """Fast: ink planes and the block only. Fine: also every refracting glue interface."""
    p = scene.gs_slicer
    fast = p.optical_quality != 'FINE'
    if p.box is not None:
        block = _find(p.box)
        shown = block is not None and not block.hide_get()
        for o in bpy.data.objects:
            if o.get(IFACE_TAG) and o.parent == p.box:
                o.hide_set(fast or not shown)
                o.hide_render = fast or not shown
    pixel, samples = QUALITY.get(p.optical_quality, QUALITY['FAST'])
    scene.render.preview_pixel_size = pixel
    c = scene.cycles
    c.preview_samples = samples
    c.use_preview_denoising = True
    if hasattr(c, "preview_denoising_start_sample"):
        c.preview_denoising_start_sample = 1


def view(context, which):
    """Point the 3D view at the block face-on, from the front, or at a grazing angle to the layers."""
    p = context.scene.gs_slicer
    from mathutils import Euler
    rot = p.box.matrix_world.to_quaternion()
    angles = {'TOP': (0.0, 0.0, 0.0), 'FRONT': (90.0, 0.0, 0.0), 'OBLIQUE': (55.0, 0.0, 25.0),
              'GRAZING': (80.0, 0.0, 15.0)}[which]
    local = Euler(tuple(math.radians(a) for a in angles), 'XYZ').to_quaternion()
    area = context.area if context.area and context.area.type == 'VIEW_3D' else next(
        (a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        return
    space = area.spaces.active
    r3 = space.region_3d
    r3.view_perspective = 'PERSP'
    r3.view_rotation = rot @ local
    r3.view_location = p.box.matrix_world.translation
    size = max(p.box.matrix_world.to_scale()) * max(p.block_size) * 0.001
    r3.view_distance = 2.2 * size
    if space.shading.type != 'RENDERED':
        _prev_shading[space.as_pointer()] = space.shading.type
        space.shading.type = 'RENDERED'
