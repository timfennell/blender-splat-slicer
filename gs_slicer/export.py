"""The export job: slices -> print tiles (PNG / PDF sheets) and layer volumes (NRRD / NIfTI / DICOM).

`run_export` is a generator yielding (fraction, message) so Blender can drive it from a modal
timer with a progress bar and Esc to cancel. It has no bpy dependency.
"""

import os
import re
from dataclasses import dataclass, field

import numpy as np

from .page import PAPER_MM, TileLayout, sheet_grid
from .slicer import Slicer, print_rgb
from .writers import DicomMultiframeWriter, DicomSeriesWriter, NiftiWriter, NrrdWriter, PdfWriter, write_png


@dataclass
class ExportSettings:
    name: str = "block"
    out_dir: str = "."
    size_mm: tuple = (50.0, 50.0, 50.0)
    pitch: float = 0.23
    count: int = 217
    z0: float = 0.0
    # print
    dpi: float = 720.0
    margin_mm: float = 12.0
    mark_mm: float = 6.0
    line_mm: float = 0.12
    marks: bool = True
    cut_lines: bool = True
    labels: bool = True
    mirror: bool = False
    png_print: bool = True
    png_raw: bool = False
    png_white: bool = False
    png_rip: bool = False          # transparent RGBA tiles with marks, for white-ink (DTF) RIPs
    pdf: bool = True
    paper: str = 'FIT'              # 'FIT' (one tile per page), a PAPER_MM key, or 'CUSTOM'
    paper_mm: tuple = (215.9, 279.4)
    paper_edge_mm: float = 5.0
    gap_mm: float = 3.0
    # volume
    voxel_mm: float = 0.1
    nrrd: bool = False
    nifti: bool = False
    dicom: bool = False
    dicom_single: bool = True       # one multi-frame file instead of a file per layer
    notes: dict = field(default_factory=dict)

    @property
    def wants_volume(self):
        return self.nrrd or self.nifti or self.dicom

    @property
    def wants_print(self):
        return self.png_print or self.png_raw or self.png_white or self.png_rip or self.pdf


RIP_NOTES = """White-ink (DTF / UV) print files for {name}

{name}_0001.png ...        colour tiles, transparent where there is no ink, marks in black.
{name}_0001_white.png ...  matching white masks (black = full white ink), if exported.
Printed at {dpi:g} dpi; print at 100 %, no scaling. Mirror: {mirror}.

In the RIP:
- Build white from the image's transparency as a GRADIENT (partial white where the model is faint),
  not a solid white under every non-transparent pixel, which turns the block milky. If the RIP
  can't, load each _white.png as the white layer / spot channel instead.
- No white "choke" or "spread" is needed; the layers are designed to stack.
- Print order is usually colour first, then white on top: that is why the tiles are mirrored.

Stacking:
- Lay every sheet PRINTED SIDE DOWN. Seen from above, colour then sits in front of its white,
  the label reads correctly and the key dot is top-left.
- Sheet 0001 is the bottom of the block.
"""


def safe_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('_') or 'block'


def layer_label(name, k, count, z0, pitch):
    lo = z0 + k * pitch
    return f"{name[:20]} {k + 1:03d}/{count:03d} Z{lo:.2f}"


def summary(s, cols=None, rows=None):
    L, W, H = s.size_mm
    lines = [
        f"Splat Slicer export: {s.name}",
        f"Block L x W x H: {L:.2f} x {W:.2f} x {H:.2f} mm",
        f"Layer pitch (film + bond line): {s.pitch:.4f} mm",
        f"Layers: {s.count}  (stack height {s.count * s.pitch:.3f} mm, first layer starts {max(s.z0, 0.0):.3f} mm up)",
        "Layer 1 is the BOTTOM sheet. Images are the block seen from above, front edge at the bottom.",
    ]
    if cols:
        lines.append(f"Print: {s.dpi:g} dpi, slice image {cols} x {rows} px, margin {s.margin_mm:g} mm"
                     + (", mirrored" if s.mirror else ""))
    for k, v in s.notes.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def run_export(vol, s):
    """Generator: renders every layer and writes the selected outputs."""
    name = safe_name(s.name)
    root = os.path.join(s.out_dir, name)
    os.makedirs(root, exist_ok=True)
    passes = (1 if s.wants_print else 0) + (1 if s.wants_volume else 0)
    print_px = (None, None)
    if not passes:
        raise ValueError("Nothing selected to export")
    done_passes = 0
    written = []

    if s.wants_print:
        px = 25.4 / s.dpi
        sl = Slicer(vol, s.size_mm, s.pitch, s.count, px, s.z0)
        lay = TileLayout(sl.cols, sl.rows, s.dpi, s.margin_mm, s.mark_mm, s.line_mm)
        print_px = (sl.cols, sl.rows)
        dirs = {}
        # With RIP tiles, each white mask sits beside its colour tile; otherwise in its own folder.
        for key, flag in (('print', s.png_print), ('raw', s.png_raw), ('rip', s.png_rip),
                          ('white', s.png_white and not s.png_rip)):
            if flag:
                dirs[key] = os.path.join(root, key)
                os.makedirs(dirs[key], exist_ok=True)
        pdf = sheet = None
        pending = []
        tile_mm = lay.size_mm
        if s.pdf:
            pdf_path = os.path.join(root, f"{name}_print.pdf")
            pdf = PdfWriter(pdf_path, f"{name} slices")
            written.append(pdf_path)
            if s.paper != 'FIT':
                paper = PAPER_MM.get(s.paper, s.paper_mm)
                sheet = sheet_grid(tile_mm, paper, s.paper_edge_mm, s.gap_mm)
                if sheet is None:
                    s.notes['PDF'] = "tile larger than the paper: one slice per page instead"

        def flush_sheet():
            nx, ny, pw, ph = sheet
            uw = nx * tile_mm[0] + (nx - 1) * s.gap_mm
            uh = ny * tile_mm[1] + (ny - 1) * s.gap_mm
            ox, oy = (pw - uw) / 2, (ph - uh) / 2
            imgs = []
            for i, t in enumerate(pending):
                cx, cy = i % nx, i // nx
                imgs.append((t, ox + cx * (tile_mm[0] + s.gap_mm), oy + cy * (tile_mm[1] + s.gap_mm),
                             tile_mm[0], tile_mm[1]))
            pdf.add_page(pw, ph, imgs)
            pending.clear()

        chunk = sl.chunk_layers()
        for first in range(0, s.count, chunk):
            last = min(first + chunk, s.count)
            msg = f"Slicing layers {first + 1}-{last} of {s.count}"
            it = sl.render_iter(first, last)
            while True:
                try:
                    next(it)
                except StopIteration as done:
                    alpha, rgb = done.value
                    break
                yield (done_passes + first / s.count) / passes, msg
            for j in range(last - first):
                k = first + j
                label = layer_label(name, k, s.count, s.z0, s.pitch) if s.labels else ''
                tile = lay.compose(print_rgb(alpha[j], rgb[j]), label, s.marks, s.cut_lines, s.labels, s.mirror)
                stem = f"{name}_{k + 1:04d}.png"
                if 'print' in dirs:
                    write_png(os.path.join(dirs['print'], stem), tile, s.dpi)
                if 'raw' in dirs:
                    a8 = (alpha[j] * 255 + 0.5).astype(np.uint8)
                    c8 = (rgb[j] * 255 + 0.5).astype(np.uint8)
                    raw = np.dstack([c8, a8])
                    write_png(os.path.join(dirs['raw'], stem), raw[:, ::-1] if s.mirror else raw, s.dpi)
                if 'rip' in dirs:
                    write_png(os.path.join(dirs['rip'], stem),
                              lay.compose_rgba(alpha[j], rgb[j], label, s.marks, s.cut_lines, s.labels, s.mirror),
                              s.dpi)
                if s.png_white:
                    # White-ink mask aligned with the tile (black = full white ink).
                    folder = dirs['rip'] if 'rip' in dirs else dirs['white']
                    name_w = stem[:-4] + "_white.png" if 'rip' in dirs else stem
                    write_png(os.path.join(folder, name_w), lay.white_mask(alpha[j], s.mirror), s.dpi)
                if pdf is not None:
                    if sheet is None:
                        pdf.add_page(tile_mm[0], tile_mm[1], [(tile, 0, 0, tile_mm[0], tile_mm[1])])
                    else:
                        pending.append(tile)
                        if len(pending) == sheet[0] * sheet[1]:
                            flush_sheet()
                yield (done_passes + (k + 1) / s.count) / passes, f"Writing layer {k + 1} of {s.count}"
        if pdf is not None:
            if pending:
                flush_sheet()
            pdf.close()
        if 'rip' in dirs:
            with open(os.path.join(dirs['rip'], "RIP_README.txt"), 'w') as f:
                f.write(RIP_NOTES.format(name=name, dpi=s.dpi, mirror="ON" if s.mirror else "OFF"))
        written += [d for d in dirs.values()]
        done_passes += 1

    if s.wants_volume:
        sl = Slicer(vol, s.size_mm, s.pitch, s.count, s.voxel_mm, s.z0)
        args = (sl.cols, sl.rows, s.count, s.voxel_mm, s.pitch, s.z0)
        outs = []
        if s.nrrd:
            outs.append(NrrdWriter(os.path.join(root, f"{name}.nrrd"), *args))
            written.append(outs[-1].path)
        if s.nifti:
            p = os.path.join(root, f"{name}.nii.gz")
            outs.append(NiftiWriter(p, *args))
            written.append(p)
        dicom = None
        if s.dicom and s.dicom_single:
            p = os.path.join(root, f"{name}.dcm")
            dicom = DicomMultiframeWriter(p, name, *args)
            written.append(p)
        elif s.dicom:
            folder = os.path.join(root, 'dicom')
            dicom = DicomSeriesWriter(folder, name, *args)
            written.append(folder)
        chunk = sl.chunk_layers()
        for first in range(0, s.count, chunk):
            last = min(first + chunk, s.count)
            it = sl.render_iter(first, last)
            while True:
                try:
                    next(it)
                except StopIteration as done:
                    alpha, rgb = done.value
                    break
                yield (done_passes + first / s.count) / passes, f"Volume layers {first + 1}-{last}"
            for j in range(last - first):
                rgba = np.dstack([rgb[j] * 255, alpha[j] * 255]) + 0.5
                rgba = np.clip(rgba, 0, 255).astype(np.uint8)
                for w in outs:
                    w.add(rgba)
                if dicom is not None:
                    pre = np.clip(rgb[j] * alpha[j][..., None] * 255 + 0.5, 0, 255).astype(np.uint8)
                    dicom.add(pre)
            yield (done_passes + last / s.count) / passes, f"Volume layers {first + 1}-{last}"
        for w in outs:
            w.close()
        if dicom is not None:
            dicom.close()
        s.notes['Volume'] = f"{sl.cols} x {sl.rows} x {s.count} voxels, {s.voxel_mm:g} x {s.voxel_mm:g} x {s.pitch:.4f} mm"
    info = os.path.join(root, f"{name}_info.txt")
    with open(info, 'w') as f:
        f.write(summary(s, *print_px))
    written.append(info)
    yield 1.0, "Wrote " + ", ".join(os.path.relpath(p, s.out_dir) for p in written)
