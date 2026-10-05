import os
import time

import bpy

from . import box as boxmod
from . import jobs, optical, preview
from .export import ExportSettings, run_export, safe_name
from .props import PAPERS


def _settings(context):
    return context.scene.gs_slicer


def _active_splat(context):
    obj = context.active_object
    if obj is not None and obj.type == 'POINTCLOUD' and obj.data.attributes.get("position") is not None:
        return obj
    return None


def _fit(p, splat):
    """Fit the block to the splat (and, if asked, its height to the model). Returns a report line."""
    s, height = boxmod.fit_box(p.box, splat, p.block_size, p.fit_padding / 100.0,
                               fit_height=p.fit_height, pitch=p.pitch)
    msg = f"1 splat unit = {1000.0 / s:.2f} mm"
    if p.fit_height:
        p.height = height                       # rebuilds the box mesh at the new height
        msg += f"; height {height:.2f} mm = {p.layer_plan()[0]} layers"
    return msg


class GSSLICER_OT_create_block(bpy.types.Operator):
    bl_idname = "gs_slicer.create_block"
    bl_label = "Create Block"
    bl_description = "Add a box at the block's size and fit it around the splat"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = _settings(context)
        splat = p.splat or _active_splat(context)
        if splat is None:
            self.report({'ERROR'}, "Select a Gaussian splat (point cloud) first")
            return {'CANCELLED'}
        p.splat = splat
        if p.box is None or p.box.name not in context.scene.objects:
            p.box = boxmod.create_box(context, p.block_size)
        else:
            boxmod.update_box_mesh(p.box, p.block_size)
        boxmod.make_render_invisible(p.box)
        msg = _fit(p, splat)
        p.name = splat.name
        self.report({'INFO'}, msg)
        if p.auto_preview:
            preview.request(context)
        return {'FINISHED'}


class GSSLICER_OT_fit_block(bpy.types.Operator):
    bl_idname = "gs_slicer.fit_block"
    bl_label = "Fit to Splat"
    bl_description = ("Centre the block on the splat and scale it uniformly so the splat fits, keeping the "
                      "block's rotation. With Fit Height to Model, the height is set from the model too")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        p = _settings(context)
        return p.box is not None and p.splat is not None

    def execute(self, context):
        p = _settings(context)
        boxmod.make_render_invisible(p.box)
        self.report({'INFO'}, _fit(p, p.splat))
        if p.auto_preview:
            preview.request(context)
        return {'FINISHED'}


class GSSLICER_OT_preview(bpy.types.Operator):
    bl_idname = "gs_slicer.preview"
    bl_label = "Preview Layer"
    bl_description = "Render the chosen layer onto a plane in the block and into the Splat Slice images"

    @classmethod
    def poll(cls, context):
        p = _settings(context)
        return p.box is not None and p.splat is not None

    def execute(self, context):
        try:
            msg = preview.render_preview(context, use_cache=False)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GSSLICER_OT_step_layer(bpy.types.Operator):
    bl_idname = "gs_slicer.step_layer"
    bl_label = "Step Layer"
    bl_description = "Preview the next or previous layer"
    delta: bpy.props.IntProperty(default=1)

    def execute(self, context):
        p = _settings(context)
        count = p.layer_plan()[0]
        p.preview_layer = min(max(p.preview_layer + self.delta, 1), count)
        return {'FINISHED'}


class GSSLICER_OT_export(jobs.ModalJob, bpy.types.Operator):
    bl_idname = "gs_slicer.export"
    bl_label = "Export Slices"
    bl_description = "Render every layer and write the selected print and volume files"
    job_label = "Exporting slices"

    @classmethod
    def poll(cls, context):
        p = _settings(context)
        return p.box is not None and p.splat is not None and cls.poll_free()

    def _make_settings(self, context):
        p = _settings(context)
        out_dir = bpy.path.abspath(p.out_dir)
        if p.out_dir.startswith("//") and not bpy.data.filepath:
            raise ValueError("Save the .blend first, or choose an absolute output folder")
        count, z0, _ = p.layer_plan()
        paper_mm = (p.paper_w, p.paper_h)
        notes = {
            "Splat": f"{p.splat.name} ({len(p.splat.data.points)} splats)",
            "Colour": ("all sides average (SH DC)" if p.color_mode == 'ALL'
                       else f"SH averaged over a {p.cone_deg:g} deg cone facing {p.face.lower()}"),
            "Ink density": f"{p.density:g}, splats below opacity {p.min_opacity:g} skipped",
            "Pitch source": (f"measured: {p.cal_height:g} mm / {p.cal_sheets} sheets" if p.use_calibration
                             else f"film {p.film_mm:g} mm + bond line {p.bond_mm:g} mm"),
            "Sheet": next(label for key, label, _ in PAPERS if key == p.paper),
        }
        return ExportSettings(
            name=p.name, out_dir=out_dir, size_mm=p.block_size, pitch=p.pitch, count=count, z0=z0,
            dpi=p.dpi, margin_mm=p.margin_mm, mark_mm=p.mark_mm, marks=p.marks, cut_lines=p.cut_lines,
            labels=p.labels, mirror=p.mirror, png_print=p.out_png, png_raw=p.out_raw, png_white=p.out_white,
            png_rip=p.out_rip,
            pdf=p.out_pdf, paper=p.paper, paper_mm=paper_mm, paper_edge_mm=p.paper_edge, gap_mm=p.gap_mm,
            voxel_mm=p.voxel_mm, nrrd=p.out_nrrd, nifti=p.out_nifti, dicom=p.out_dicom,
            dicom_single=p.dicom_single, notes=notes)

    def start(self, context):
        s = self._make_settings(context)
        if not (s.wants_print or s.wants_volume):
            return "Tick at least one output"
        self._out = os.path.join(s.out_dir, safe_name(s.name))
        p = _settings(context)

        def job():
            yield 0.0, "Reading splats"
            vol = boxmod.build_volume(p, use_cache=False)
            yield from run_export(vol, s)
        self._job = job()
        return None

    def done(self, context, result, seconds):
        self.report({'INFO'}, f"Exported to {self._out} in {seconds:.0f} s")
        return {'FINISHED'}

    def execute(self, context):
        """Blocking export (scripts, background mode)."""
        try:
            s = self._make_settings(context)
            vol = boxmod.build_volume(_settings(context), use_cache=False)
            msg = ""
            for _, msg in run_export(vol, s):
                pass
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GSSLICER_OT_optical_build(jobs.ModalJob, bpy.types.Operator):
    bl_idname = "gs_slicer.optical_build"
    bl_label = "Rebuild Optical Block"
    bl_description = ("Build the laminated block as Cycles glass: film sheets, ink planes and every glue "
                      "interface, to see how the stack looks face-on and from its edges")
    job_label = "Building optical block"

    @classmethod
    def poll(cls, context):
        p = _settings(context)
        return p.box is not None and p.splat is not None and cls.poll_free()

    def start(self, context):
        self._job = optical.build_iter(context, _settings(context).optical_merge)
        return None

    def done(self, context, msg, seconds):
        # Glass only reads in Rendered shading: show it straight away.
        if context.screen is not None:
            optical.view(context, 'OBLIQUE')
            msg += ". Viewport switched to Rendered"
        self.report({'INFO'}, f"{msg} ({seconds:.0f} s)")
        return {'FINISHED'}

    def execute(self, context):
        """Blocking build (scripts, background mode)."""
        try:
            msg = optical.build(context, _settings(context).optical_merge)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GSSLICER_OT_optical_remove(bpy.types.Operator):
    bl_idname = "gs_slicer.optical_remove"
    bl_label = "Remove Optical Block"
    bl_description = "Delete the optical mock-up and light table, and show the splat again"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        optical.set_visible(context, False)        # puts the viewport shading back
        optical.remove(context)
        return {'FINISHED'}


class GSSLICER_OT_view_mode(bpy.types.Operator):
    bl_idname = "gs_slicer.view_mode"
    bl_label = "View Mode"
    bl_options = {'REGISTER', 'UNDO'}
    mode: bpy.props.EnumProperty(items=[
        ('SETUP', "Setup", "Plan the block: the splat, the slice preview and the stacking guides"),
        ('OPTICS', "Optics", "How the finished block will look: the laminated stack as glass in Cycles. "
                             "Built the first time; use Rebuild after changing settings"),
    ])

    @classmethod
    def description(cls, context, properties):
        return cls.bl_rna.properties['mode'].enum_items[properties.mode].description

    @classmethod
    def poll(cls, context):
        p = _settings(context)
        return p.box is not None and p.splat is not None

    def execute(self, context):
        p = _settings(context)
        if self.mode == 'SETUP':
            optical.set_visible(context, False)
        elif optical.is_built(p):
            optical.set_visible(context, True)
        else:
            return bpy.ops.gs_slicer.optical_build('INVOKE_DEFAULT')
        return {'FINISHED'}


class GSSLICER_OT_optical_view(bpy.types.Operator):
    bl_idname = "gs_slicer.optical_view"
    bl_label = "View Block"
    bl_description = "Look at the block from this direction in Rendered shading"
    which: bpy.props.EnumProperty(items=[
        ('TOP', "Face-On", "Straight down the stack, through the faces of the sheets"),
        ('OBLIQUE', "Oblique", "35 degrees off the stack axis"),
        ('GRAZING', "Grazing", "10 degrees above the layers, past the critical angle"),
        ('FRONT', "Edge-On", "Through the stacked edges of the sheets"),
    ])

    @classmethod
    def poll(cls, context):
        return _settings(context).box is not None

    def execute(self, context):
        optical.view(context, self.which)
        return {'FINISHED'}


class GSSLICER_OT_open_folder(bpy.types.Operator):
    bl_idname = "gs_slicer.open_folder"
    bl_label = "Open Output Folder"

    def execute(self, context):
        p = _settings(context)
        path = os.path.join(bpy.path.abspath(p.out_dir), safe_name(p.name))
        if not os.path.isdir(path):
            path = bpy.path.abspath(p.out_dir)
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


classes = (jobs.GSSLICER_OT_cancel_job, GSSLICER_OT_create_block, GSSLICER_OT_fit_block, GSSLICER_OT_preview,
           GSSLICER_OT_step_layer,
           GSSLICER_OT_export, GSSLICER_OT_optical_build, GSSLICER_OT_view_mode, GSSLICER_OT_optical_remove,
           GSSLICER_OT_optical_view, GSSLICER_OT_open_folder)
