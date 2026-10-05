"""Long jobs (export, optical build) run step by step from a modal timer, with their progress
shown as a bar in the Splat Slicer panel instead of Blender's numeric progress cursor."""

import time

import bpy

state = {"active": False, "label": "", "frac": 0.0, "msg": "", "cancel": False}


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type in ('UI', 'HEADER'):
                        region.tag_redraw()


def busy():
    return state["active"]


class ModalJob:
    """Mixin for an operator that drives a (fraction, message) generator.

    Subclasses set `job_label`, build `self._job` in `start()` (returning an error string or
    None), and turn the generator's return value into a report in `done(result)`.
    """
    job_label = "Working"
    _timer = None
    _job = None

    @classmethod
    def poll_free(cls):
        return not state["active"]

    def invoke(self, context, event):
        if state["active"]:
            self.report({'WARNING'}, f"{state['label']} is already running")
            return {'CANCELLED'}
        try:
            error = self.start(context)
        except Exception as exc:
            error = str(exc)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        state.update(active=True, label=self.job_label, frac=0.0, msg="Starting", cancel=False)
        self._start = time.perf_counter()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        _redraw()
        return {'RUNNING_MODAL'}

    def _finish(self, context, cancelled=False):
        context.window_manager.event_timer_remove(self._timer)
        if cancelled and self._job is not None:
            self._job.close()
        state.update(active=False, cancel=False)
        _redraw()

    def modal(self, context, event):
        if event.type == 'ESC' or state["cancel"]:
            self._finish(context, cancelled=True)
            self.report({'WARNING'}, f"{self.job_label} cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        deadline = time.perf_counter() + 0.2
        try:
            while time.perf_counter() < deadline:
                frac, msg = next(self._job)
                state.update(frac=min(max(frac, 0.0), 1.0), msg=msg)
        except StopIteration as done:
            self._finish(context)
            seconds = time.perf_counter() - self._start
            return self.done(context, done.value, seconds)
        except Exception as exc:
            self._finish(context, cancelled=True)
            self.report({'ERROR'}, f"{self.job_label} failed: {exc}")
            return {'CANCELLED'}
        _redraw()
        return {'RUNNING_MODAL'}


class GSSLICER_OT_cancel_job(bpy.types.Operator):
    bl_idname = "gs_slicer.cancel_job"
    bl_label = "Cancel"
    bl_description = "Stop the running export or build (files already written are kept)"

    @classmethod
    def poll(cls, context):
        return state["active"]

    def execute(self, context):
        state["cancel"] = True
        return {'FINISHED'}


def draw_progress(layout):
    """Progress bar + cancel button, if a job is running. Returns True if drawn."""
    if not state["active"]:
        return False
    box = layout.box()
    box.label(text=state["label"], icon='TIME')
    row = box.row(align=True)
    row.progress(factor=state["frac"], type='BAR', text=f"{state['frac'] * 100:.0f}%")
    row.operator("gs_slicer.cancel_job", text="", icon='CANCEL')
    box.label(text=state["msg"])
    return True
