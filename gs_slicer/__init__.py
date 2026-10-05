"""Splat Slicer: plan a printed block around a Gaussian splat and slice it into film layers.

slicer, page, writers and export need only NumPy, so they can be used and tested outside Blender.
"""

try:
    import bpy
except ImportError:          # imported as a plain package (tests, command-line use)
    bpy = None

if bpy is not None:
    from bpy.app.handlers import persistent

    from . import box, ops, overlay, props, ui

    @persistent
    def _on_load(_):
        box.fix_all_boxes()

    def _fix_after_register():
        try:
            box.fix_all_boxes()
        except AttributeError:          # data not reachable yet
            return 0.5
        return None

    def register():
        props.register()
        for cls in ops.classes + ui.classes:
            bpy.utils.register_class(cls)
        if not bpy.app.background:
            overlay.register()
        bpy.app.handlers.load_post.append(_on_load)
        bpy.app.timers.register(_fix_after_register, first_interval=0.5)

    def unregister():
        if _on_load in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(_on_load)
        overlay.unregister()
        for cls in reversed(ops.classes + ui.classes):
            bpy.utils.unregister_class(cls)
        props.unregister()
