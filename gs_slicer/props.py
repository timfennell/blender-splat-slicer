import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

from .slicer import plan_layers


def _is_splat(self, obj):
    return obj.type == 'POINTCLOUD' and obj.data.attributes.get("position") is not None


def _is_box(self, obj):
    return obj.type == 'MESH'


def _size_changed(self, context):
    from . import box
    if self.box is not None:
        box.update_box_mesh(self.box, self.block_size)
    _preview_changed(self, context)


def _redraw(context):
    for area in context.screen.areas if context.screen else ():
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _preview_changed(self, context):
    _redraw(context)
    if self.auto_preview and self.box is not None and self.splat is not None:
        from . import preview
        preview.request(context)


# Optical presets: (label, description, film IOR, glue IOR, interface haze, side finish, ink scatter).
# Sides are polished in every preset, since the finished block is sanded and polished.
OPTICAL_PRESETS = {
    'PET_COATED': ("PET Film, Inkjet-Coated + LOCA",
                   "Inkjet transparency film (n 1.575) with optical glue (n 1.49). The ink-receptive coating "
                   "adds a little haze at every interface, and the aqueous ink scatters slightly",
                   1.575, 1.49, 0.03, 0.0, 0.15),
    'PET_CLEAR': ("PET Film, Uncoated + LOCA",
                  "Clear polyester film (n 1.575) printed with eco-solvent or UV ink, with optical glue (n 1.49)",
                  1.575, 1.49, 0.0, 0.0, 0.08),
    'ACRYLIC': ("Cast Acrylic + LOCA",
                "Cast acrylic sheet (n 1.491) with optical glue (n 1.49): indices almost match, so the "
                "interfaces nearly vanish. Printed with eco-solvent or UV ink",
                1.491, 1.49, 0.0, 0.0, 0.08),
}
_OPTICAL_FIELDS = ('film_ior', 'glue_ior', 'interface_roughness', 'side_roughness', 'ink_scatter')
_applying = [False]


def _preset_changed(self, context):
    values = OPTICAL_PRESETS.get(self.optical_material)
    if values is None:
        return
    _applying[0] = True
    try:
        for name, value in zip(_OPTICAL_FIELDS, values[2:]):
            setattr(self, name, value)
    finally:
        _applying[0] = False


def _optical_edited(self, context):
    """Editing a value by hand makes the preset Custom, so the menu never claims values it didn't set."""
    if not _applying[0] and self.optical_material != 'CUSTOM':
        values = OPTICAL_PRESETS[self.optical_material][2:]
        if any(abs(getattr(self, n) - v) > 1e-6 for n, v in zip(_OPTICAL_FIELDS, values)):
            self.optical_material = 'CUSTOM'


# Printer presets: what each kind of printer needs (mirror, outputs).
PRINT_TARGETS = {
    'INKJET': dict(mirror=False, out_pdf=True, out_png=True, out_rip=False, out_white=False),
    'WHITE_RIP': dict(mirror=True, out_pdf=False, out_png=False, out_rip=True, out_white=True),
}


def _brightness_changed(context):
    from . import optical
    optical.apply_brightness(context.scene)


def _lighting_changed(self, context):
    from . import optical
    if optical.is_built(self):
        optical.build_studio(context)
        optical._use_world(context.scene, optical.is_visible(self))


def _quality_changed(self, context):
    if context.scene.render.engine == 'CYCLES':
        from . import optical
        optical.apply_quality(context.scene)


def _target_changed(self, context):
    for name, value in PRINT_TARGETS.get(self.print_target, {}).items():
        setattr(self, name, value)


PAPERS = [
    ('FIT', "One Slice per Page", "Each page is exactly one slice tile"),
    ('LETTER', "Letter", "8.5 x 11 in"),
    ('LEGAL', "Legal", "8.5 x 14 in"),
    ('TABLOID', "Tabloid / Ledger", "11 x 17 in"),
    ('SUPER_B', "Super B (13 x 19)", "13 x 19 in"),
    ('A4', "A4", "210 x 297 mm"),
    ('A3', "A3", "297 x 420 mm"),
    ('A3_PLUS', "A3+", "329 x 483 mm"),
    ('CUSTOM', "Custom", "Your own sheet size"),
]

FACES = [
    ('TOP', "Top", "Viewer above the block"),
    ('FRONT', "Front", "Viewer on the block's -Y side"),
    ('BACK', "Back", "Viewer on the block's +Y side"),
    ('LEFT', "Left", "Viewer on the block's -X side"),
    ('RIGHT', "Right", "Viewer on the block's +X side"),
    ('BOTTOM', "Bottom", "Viewer below the block"),
]


class GSSlicerSettings(bpy.types.PropertyGroup):
    splat: PointerProperty(name="Splat", type=bpy.types.Object, poll=_is_splat,
                           description="Gaussian splat point cloud to slice")
    box: PointerProperty(name="Block", type=bpy.types.Object, poll=_is_box,
                         description="Box that sets which part of the splat becomes the printed block")

    # Block
    length: FloatProperty(name="Length (X)", default=50.0, min=1.0, soft_max=500.0, precision=2,
                          update=_size_changed, description="Finished block length in mm")
    width: FloatProperty(name="Width (Y)", default=50.0, min=1.0, soft_max=500.0, precision=2,
                         update=_size_changed, description="Finished block width in mm")
    height: FloatProperty(name="Height (Z)", default=50.0, min=0.1, soft_max=500.0, precision=2,
                          update=_size_changed, description="Finished block height (stack) in mm")
    fit_mode: EnumProperty(name="Fit", default='ALL', items=[
        ('ALL', "Width && Height to Model",
         "Length sets the print size; Width and Height follow the model's proportions plus padding"),
        ('HEIGHT', "Height to Model", "Keep Length and Width; Height follows the model plus padding"),
        ('NONE', "Keep All Sizes", "Keep Length, Width and Height; the model is scaled to fit inside them"),
    ], description="Which block sizes Create / Refit Block takes from the model")
    fit_margin: FloatProperty(name="Margin (mm)", default=2.0, min=0.0, soft_max=20.0, precision=2,
                              description="Clear space left between the model and every side of the block when fitting")

    show_overlay: BoolProperty(name="Show Stacking", default=True, update=lambda self, ctx: _redraw(ctx),
                               description="Draw layer ticks, the stacking arrow, the front edge and the "
                                           "previewed layer on the block in the viewport")

    # Layers
    film_mm: FloatProperty(name="Film", default=0.2, min=0.01, max=5.0, precision=3, step=1,
                           update=_preview_changed, description="Thickness of one clear film sheet in mm")
    bond_mm: FloatProperty(name="Bond Line", default=0.03, min=0.0, max=2.0, precision=3, step=0.5,
                           update=_preview_changed,
                           description="Cured adhesive (LOCA / resin) thickness between sheets in mm")
    use_calibration: BoolProperty(name="Use Measured Stack", default=False, update=_preview_changed,
                                  description="Take the layer pitch from a measured test stack instead of film + bond line")
    cal_sheets: IntProperty(name="Sheets", default=5, min=1, max=1000, update=_preview_changed,
                            description="Number of sheets in the test stack")
    cal_height: FloatProperty(name="Measured Height", default=1.15, min=0.01, precision=3, step=1,
                              update=_preview_changed, description="Measured height of the cured test stack in mm")

    # Colour and density
    color_mode: EnumProperty(name="Colour", items=[
        ('ALL', "All Sides (SH Off)", "Average over every view direction. Mathematically this is the SH DC "
                                      "term, and also exactly what six cameras on the six faces would give"),
        ('FACE', "Favour a Face", "Average the full SH over a cone of view directions around one face, "
                                  "baking in the sheen seen from that side"),
    ], default='ALL', update=_preview_changed)
    face: EnumProperty(name="Face", items=FACES, default='FRONT', update=_preview_changed)
    cone_deg: FloatProperty(name="Cone", default=45.0, min=0.0, max=90.0, update=_preview_changed,
                            description="Half angle of the cone of view directions averaged")
    density: FloatProperty(name="Ink Density", default=1.0, min=0.0, soft_max=4.0, update=_preview_changed,
                           description="Multiplies every splat's optical depth. Lower it if the stacked block "
                                       "comes out too dark, raise it if too faint")
    min_opacity: FloatProperty(name="Ignore Below", default=0.02, min=0.0, max=0.99, update=_preview_changed,
                               description="Skip splats fainter than this opacity (haze, floaters)")

    # Preview
    preview_layer: IntProperty(name="Layer", default=1, min=1, update=_preview_changed,
                               description="Layer to preview (1 = bottom sheet)")
    show_slice: BoolProperty(name="Show Slice", default=True, update=lambda self, ctx: _redraw(ctx),
                             description="Draw the previewed layer's image inside the block in the viewport")
    preview_boost: BoolProperty(name="Boost Faint Ink", default=False, update=_preview_changed,
                                description="Exaggerate faint ink in the viewport so a thin layer is easier to see. "
                                            "Off: the layer is drawn at the strength it will print")
    preview_mm: FloatProperty(name="Preview Pixel", default=0.1, min=0.01, max=2.0, precision=3,
                              update=_preview_changed, description="Pixel size of the preview in mm")
    auto_preview: BoolProperty(name="Live", default=True,
                               description="Re-render the preview when a setting or the layer changes")

    # Optical mock-up (Cycles)
    optical_material: EnumProperty(
        name="Material", default='PET_COATED', update=_preset_changed,
        items=[(k, v[0], v[1]) for k, v in OPTICAL_PRESETS.items()]
              + [('CUSTOM', "Custom", "Your own values below")],
        description="Sheet and glue, setting the refractive indices, haze, side finish and ink scatter below")
    film_ior: FloatProperty(name="Film IOR", default=1.575, min=1.0, max=2.5, precision=3, update=_optical_edited,
                            description="Refractive index of the film (PET about 1.575, acrylic 1.49)")
    glue_ior: FloatProperty(name="Glue IOR", default=1.49, min=1.0, max=2.5, precision=3, update=_optical_edited,
                            description="Refractive index of the cured bond line (LOCA about 1.49)")
    interface_roughness: FloatProperty(name="Interface Haze", default=0.03, min=0.0, max=0.5, precision=3,
                                       update=_optical_edited,
                                       description="Roughness of each film/glue boundary (coatings, micro-texture)")
    side_roughness: FloatProperty(name="Side Finish", default=0.0, min=0.0, max=0.5, precision=3,
                                  update=_optical_edited,
                                  description="Roughness of the trimmed sides: 0 = polished, about 0.1 = fine sanded")
    ink_scatter: FloatProperty(name="Ink Scatter", default=0.15, min=0.0, max=1.0, update=_optical_edited,
                               description="How much printed ink scatters light instead of only absorbing it")
    optical_quality: EnumProperty(name="Quality", default='FAST', update=_quality_changed, items=[
        ('FAST', "Fast", "Ink planes and the block, half-resolution viewport, 16 samples, denoised: quick to orbit"),
        ('FINE', "Fine", "Adds every refracting glue interface (and their haze), full resolution, 64 samples"),
    ], description="Optical preview quality. Every sheet's ink is modelled either way")
    optical_px_mm: FloatProperty(name="Texture Pixel", default=0.3, min=0.02, max=2.0, precision=3,
                                 description="Pixel size of the ink textures in the mock-up (mm)")
    optical_merge: IntProperty(name="Layers per Sheet", default=1, min=1, max=50,
                               description="Model every N layers as one sheet. Faster, but fewer interfaces than "
                                           "the real stack, so it understates the edge effects")
    optical_brightness: FloatProperty(name="Brightness", default=3.0, min=0.0, soft_max=8.0, step=10,
                                      update=lambda self, ctx: _brightness_changed(ctx),
                                      description="Multiplier on every light of the display setup")
    optical_lighting: EnumProperty(name="Lighting", default='LIGHT_BASE', update=_lighting_changed, items=[
        ('LIGHT_BASE', "Light Base", "Bright light pad under the block, light grey surroundings: the classic display"),
        ('BACKLIGHT', "Backlight", "Bright panel behind the block, for front and side views"),
        ('FRONT_WHITE', "Front-Lit, White", "Soft light from the front, white backdrop (no light through the block)"),
        ('FRONT_BLACK', "Front-Lit, Black", "Soft light from the front, black backdrop: the hardest case without white ink"),
        ('EDGE', "Edge-Lit, Black", "Light strips against the two side faces, black backdrop: lights only what scatters"),
        ('SCENE', "Scene Lighting", "No display setup: your own lights and world"),
    ], description="How the optical preview is lit and what is behind the block")
    optical_setup_cycles: BoolProperty(name="Set Up Cycles", default=True,
                                       description="Switch to Cycles with enough bounces for every interface")

    # Print
    print_target: EnumProperty(name="Printer", default='INKJET', update=_target_changed, items=[
        ('INKJET', "Inkjet (No White)",
         "Pigment inkjet without white (e.g. Epson P700/P900): PDF and print PNGs over white, "
         "sheets laid printed side up. Display the block on a light base"),
        ('WHITE_RIP', "White-Ink RIP (DTF / UV)",
         "Printer with a white channel driven by a RIP (converted EcoTank DTF, UV): mirrored transparent "
         "tiles with matching white masks; sheets laid printed side down"),
    ], description="Sets mirroring and the outputs for the kind of printer (you can still change them after)")
    dpi: FloatProperty(name="DPI", default=720.0, min=72.0, max=5760.0, precision=0,
                       description="Print resolution. Images carry this DPI so they print at true size")
    margin_mm: FloatProperty(name="Margin", default=12.0, min=0.0, max=100.0,
                             description="Border around each slice for registration marks and the label (mm)")
    mark_mm: FloatProperty(name="Mark Size", default=6.0, min=1.0, max=50.0,
                           description="Registration mark size (mm)")
    marks: BoolProperty(name="Registration Marks", default=True)
    cut_lines: BoolProperty(name="Corner Cut Ticks", default=True,
                            description="Ticks at the block corners, outside the image")
    labels: BoolProperty(name="Slice Labels", default=True,
                         description="Name, slice number and height in the bottom margin")
    mirror: BoolProperty(name="Mirror Tile", default=False,
                         description="Mirror the whole tile (picture, marks and label) for sheets laid printed side "
                                     "down, as with white ink printed over colour. Seen through the film, everything "
                                     "then reads correctly")
    paper: EnumProperty(name="Sheet", items=PAPERS, default='FIT',
                        description="Page size for the PDF; several slices are tiled on larger sheets")
    paper_w: FloatProperty(name="Sheet Width", default=330.0, min=20.0, description="mm")
    paper_h: FloatProperty(name="Sheet Height", default=483.0, min=20.0, description="mm")
    paper_edge: FloatProperty(name="Sheet Edge", default=5.0, min=0.0, description="Unprintable edge (mm)")
    gap_mm: FloatProperty(name="Tile Gap", default=3.0, min=0.0, description="Space between tiles (mm)")

    # Outputs
    out_pdf: BoolProperty(name="PDF", default=True, description="Print-ready PDF at true physical size")
    out_png: BoolProperty(name="Print PNGs", default=True, description="One PNG tile per slice, with marks")
    out_raw: BoolProperty(name="Raw RGBA PNGs", default=False,
                          description="Slice colour + opacity, no marks (for other printers / pipelines)")
    out_white: BoolProperty(name="White Ink Mask", default=False,
                            description="Greyscale white-ink mask per slice, aligned with the tile "
                                        "(black = full white). Saved beside the RIP PNGs when those are on")
    out_rip: BoolProperty(name="RIP PNGs (Transparent)", default=False,
                          description="Tiles with marks on a transparent background (colour + opacity), for RIPs "
                                      "that build the white channel from transparency")
    out_nrrd: BoolProperty(name="NRRD", default=False, description="RGBA volume (.nrrd)")
    out_nifti: BoolProperty(name="NIfTI", default=False, description="RGBA volume (.nii.gz)")
    out_dicom: BoolProperty(name="DICOM", default=False,
                            description="RGB DICOM with spacing and position (3D Slicer, Horos, OsiriX...)")
    dicom_single: BoolProperty(name="Single File", default=True,
                               description="One multi-frame .dcm holding every layer. Off: one .dcm per layer "
                                           "in a dicom folder, for viewers that only read classic series")
    voxel_mm: FloatProperty(name="Voxel XY", default=0.1, min=0.005, max=5.0, precision=3,
                            description="In-plane voxel size of the volume files (mm); Z is the layer pitch")
    out_dir: StringProperty(name="Folder", default="//slices/", subtype='DIR_PATH')
    name: StringProperty(name="Name", default="block")

    @property
    def block_size(self):
        return (self.length, self.width, self.height)

    @property
    def pitch(self):
        if self.use_calibration:
            return self.cal_height / max(self.cal_sheets, 1)
        return self.film_mm + self.bond_mm

    def layer_plan(self):
        """(count, z0, stack height) in mm."""
        return plan_layers(self.height, self.pitch)


def register():
    bpy.utils.register_class(GSSlicerSettings)
    bpy.types.Scene.gs_slicer = PointerProperty(type=GSSlicerSettings)


def unregister():
    del bpy.types.Scene.gs_slicer
    bpy.utils.unregister_class(GSSlicerSettings)
