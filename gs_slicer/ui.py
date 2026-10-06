import math

import bpy

from . import jobs, preview
from .page import PAPER_MM, TileLayout, sheet_grid


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Splat Slicer"


class GSSLICER_PT_main(_Base, bpy.types.Panel):
    bl_label = "Splat Slicer"

    def draw(self, context):
        p = context.scene.gs_slicer
        jobs.draw_progress(self.layout)
        col = self.layout.column()
        col.prop(p, "splat")
        col.prop(p, "box")
        row = col.row(align=True)
        row.operator("gs_slicer.create_block", icon='CUBE',
                     text="Create Block" if p.box is None else "Refit Block")
        if p.box is not None:
            row.operator("gs_slicer.fit_block", text="", icon='FULLSCREEN_ENTER')
            from . import optical
            optics = optical.is_visible(p)
            row = col.row(align=True)
            row.scale_y = 1.3
            row.operator("gs_slicer.view_mode", text="Setup", icon='TOOL_SETTINGS', depress=not optics).mode = 'SETUP'
            row.operator("gs_slicer.view_mode", text="Optics", icon='SHADING_RENDERED', depress=optics).mode = 'OPTICS'
            if optics:
                # The two settings you change while looking at the optics, kept in reach.
                box = col.box()
                box.prop(p, "optical_lighting", text="Lighting")
                box.prop(p, "optical_brightness")
                box.row().prop(p, "optical_quality", expand=True)


class GSSLICER_PT_block(_Base, bpy.types.Panel):
    bl_label = "Block Size (mm)"
    bl_parent_id = "GSSLICER_PT_main"

    def draw(self, context):
        p = context.scene.gs_slicer
        col = self.layout.column(align=True)
        col.prop(p, "length")
        col.prop(p, "width")
        col.prop(p, "height")
        self.layout.prop(p, "fit_mode")
        self.layout.prop(p, "fit_margin")
        self.layout.prop(p, "show_overlay")
        if p.box is not None:
            s = sum(p.box.matrix_world.to_scale()) / 3.0
            sc = p.box.matrix_world.to_scale()
            box = self.layout.box()
            box.label(text=f"1 scene unit = {1.0 / (s * 0.001):.2f} mm in print", icon='INFO')
            if max(sc) - min(sc) > 1e-4 * max(sc):
                box.label(text="Block is scaled unevenly: the model will be stretched", icon='ERROR')


class GSSLICER_PT_layers(_Base, bpy.types.Panel):
    bl_label = "Layers"
    bl_parent_id = "GSSLICER_PT_main"

    def draw(self, context):
        p = context.scene.gs_slicer
        col = self.layout.column(align=True)
        sub = col.column(align=True)
        sub.enabled = not p.use_calibration
        sub.prop(p, "film_mm", text="Film (mm)")
        sub.prop(p, "bond_mm", text="Bond Line (mm)")
        self.layout.prop(p, "use_calibration")
        if p.use_calibration:
            col = self.layout.column(align=True)
            col.prop(p, "cal_sheets")
            col.prop(p, "cal_height", text="Measured Height (mm)")
        count, z0, stack = p.layer_plan()
        box = self.layout.box()
        box.label(text=f"Pitch {p.pitch:.4f} mm  ->  {count} layers")
        box.label(text=f"Stack {stack:.2f} mm of {p.height:.2f} mm block")
        if p.height - stack > 1e-3:
            box.label(text=f"{p.height - stack:.3f} mm left over (split top and bottom)")


class GSSLICER_PT_colour(_Base, bpy.types.Panel):
    bl_label = "Colour & Density"
    bl_parent_id = "GSSLICER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.gs_slicer
        col = self.layout.column()
        col.prop(p, "color_mode")
        if p.color_mode == 'FACE':
            col.prop(p, "face")
            col.prop(p, "cone_deg")
        col.prop(p, "density")
        col.prop(p, "min_opacity")


class GSSLICER_PT_preview(_Base, bpy.types.Panel):
    bl_label = "Preview"
    bl_parent_id = "GSSLICER_PT_main"

    def draw(self, context):
        p = context.scene.gs_slicer
        count = p.layer_plan()[0]
        row = self.layout.row(align=True)
        row.operator("gs_slicer.step_layer", text="", icon='TRIA_DOWN').delta = -1
        row.prop(p, "preview_layer", text=f"Layer (of {count})")
        row.operator("gs_slicer.step_layer", text="", icon='TRIA_UP').delta = 1
        row = self.layout.row(align=True)
        row.prop(p, "preview_mm", text="Pixel (mm)")
        row.prop(p, "auto_preview", toggle=True)
        row = self.layout.row(align=True)
        row.operator("gs_slicer.preview", icon='RESTRICT_VIEW_OFF')
        row.prop(p, "show_slice", text="", icon='IMAGE_PLANE')
        self.layout.prop(p, "preview_boost")
        msg = preview.status()
        if msg:
            self.layout.label(text=msg)
        self.layout.label(text="Images: 'Splat Slice', 'Splat Slice Print'", icon='IMAGE_DATA')


class GSSLICER_PT_optical(_Base, bpy.types.Panel):
    bl_label = "Optical Preview (Cycles)"
    bl_parent_id = "GSSLICER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.gs_slicer
        self.layout.prop(p, "optical_material", text="")
        col = self.layout.column(align=True)
        col.prop(p, "film_ior")
        col.prop(p, "glue_ior")
        if p.glue_ior >= p.film_ior:
            self.layout.label(text="Glue index >= film: no total reflection")
        else:
            crit = math.degrees(math.asin(p.glue_ior / p.film_ior))
            self.layout.label(text=f"Light within {90 - crit:.0f} deg of layers stays in its sheet")
        col = self.layout.column(align=True)
        col.prop(p, "interface_roughness")
        col.prop(p, "side_roughness")
        col.prop(p, "ink_scatter")
        col = self.layout.column(align=True)
        col.prop(p, "optical_px_mm", text="Texture Pixel (mm)")
        col.prop(p, "optical_merge")
        self.layout.row().prop(p, "optical_quality", expand=True)
        self.layout.prop(p, "optical_lighting")
        self.layout.prop(p, "optical_brightness")
        self.layout.prop(p, "optical_setup_cycles", toggle=True)
        from . import optical
        row = self.layout.row(align=True)
        row.scale_y = 1.3
        row.operator("gs_slicer.optical_build", icon='FILE_REFRESH',
                     text="Rebuild Optical Block" if optical.is_built(p) else "Build Optical Block")
        if optical.is_built(p):
            row.operator("gs_slicer.optical_remove", text="", icon='TRASH')
        grid = self.layout.grid_flow(columns=2, align=True)
        for key, text in (('TOP', "Face-On"), ('OBLIQUE', "Oblique"), ('GRAZING', "Grazing"), ('FRONT', "Edge-On")):
            grid.operator("gs_slicer.optical_view", text=text).which = key


class GSSLICER_PT_print(_Base, bpy.types.Panel):
    bl_label = "Print Layout"
    bl_parent_id = "GSSLICER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.gs_slicer
        self.layout.prop(p, "print_target", text="")
        col = self.layout.column()
        col.prop(p, "dpi")
        col.prop(p, "margin_mm", text="Margin (mm)")
        col.prop(p, "mark_mm", text="Mark Size (mm)")
        col.prop(p, "marks")
        col.prop(p, "cut_lines")
        col.prop(p, "labels")
        col.prop(p, "mirror")
        col.separator()
        col.prop(p, "paper")
        if p.paper == 'CUSTOM':
            row = col.row(align=True)
            row.prop(p, "paper_w", text="W")
            row.prop(p, "paper_h", text="H")
        if p.paper != 'FIT':
            row = col.row(align=True)
            row.prop(p, "paper_edge", text="Edge")
            row.prop(p, "gap_mm", text="Gap")
        px = 25.4 / p.dpi
        cols, rows = max(1, round(p.length / px)), max(1, round(p.width / px))
        lay = TileLayout(cols, rows, p.dpi, p.margin_mm)
        tw, th = lay.size_mm
        count = p.layer_plan()[0]
        box = self.layout.box()
        box.label(text=f"Slice {cols} x {rows} px, tile {tw:.1f} x {th:.1f} mm")
        if p.paper != 'FIT':
            paper = PAPER_MM.get(p.paper, (p.paper_w, p.paper_h))
            grid = sheet_grid((tw, th), paper, p.paper_edge, p.gap_mm)
            if grid is None:
                box.label(text="Tile doesn't fit this sheet: one per page", icon='ERROR')
            else:
                per = grid[0] * grid[1]
                box.label(text=f"{per} per sheet ({grid[0]} x {grid[1]}), {-(-count // per)} sheets")


class GSSLICER_PT_export(_Base, bpy.types.Panel):
    bl_label = "Export"
    bl_parent_id = "GSSLICER_PT_main"

    def draw(self, context):
        p = context.scene.gs_slicer
        col = self.layout.column()
        col.prop(p, "name")
        col.prop(p, "out_dir")
        grid = col.grid_flow(columns=2, align=True)
        grid.prop(p, "out_pdf")
        grid.prop(p, "out_png")
        grid.prop(p, "out_raw")
        grid.prop(p, "out_rip")
        grid.prop(p, "out_white")
        col.label(text="Volume")
        grid = col.grid_flow(columns=3, align=True)
        grid.prop(p, "out_dicom", text="DICOM")
        grid.prop(p, "out_nifti")
        grid.prop(p, "out_nrrd")
        if p.out_dicom:
            col.prop(p, "dicom_single", text="DICOM as a Single File")
        if p.out_dicom or p.out_nifti or p.out_nrrd:
            col.prop(p, "voxel_mm", text="Voxel XY (mm)")
        row = self.layout.row(align=True)
        row.scale_y = 1.4
        row.operator("gs_slicer.export", icon='EXPORT')
        row.operator("gs_slicer.open_folder", text="", icon='FILE_FOLDER')


classes = (GSSLICER_PT_main, GSSLICER_PT_block, GSSLICER_PT_layers, GSSLICER_PT_colour, GSSLICER_PT_preview,
           GSSLICER_PT_optical, GSSLICER_PT_print, GSSLICER_PT_export)
