"""File writers with no dependencies beyond NumPy and the standard library.

PNG (with print DPI), a streamed multi-page PDF at true physical size, and volume formats:
NRRD, NIfTI-1 and a DICOM series (one Secondary Capture RGB file per layer).
"""

import datetime
import gzip
import os
import struct
import uuid
import zlib

import numpy as np

PT_PER_MM = 72.0 / 25.4


# ---------------------------------------------------------------------------------- PNG
def write_png(path, img, dpi=None):
    """img: uint8 (h, w), (h, w, 3) or (h, w, 4)."""
    img = np.ascontiguousarray(img, np.uint8)
    h, w = img.shape[:2]
    channels = 1 if img.ndim == 2 else img.shape[2]
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    raw = np.empty((h, 1 + w * channels), np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = img.reshape(h, -1)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    out = [b'\x89PNG\r\n\x1a\n', chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, color_type, 0, 0, 0))]
    if dpi:
        ppm = int(round(dpi / 0.0254))
        out.append(chunk(b'pHYs', struct.pack('>IIB', ppm, ppm, 1)))
    out.append(chunk(b'IDAT', zlib.compress(raw.tobytes(), 6)))
    out.append(chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(b''.join(out))


# ---------------------------------------------------------------------------------- PDF
class PdfWriter:
    """Streams pages to disk so hundreds of full-resolution slices never sit in memory."""

    def __init__(self, path, title=''):
        self.f = open(path, 'wb')
        self.offsets = {}
        self.pages = []
        self.next_id = 3                      # 1 = catalog, 2 = page tree (written last)
        self.f.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        self.title = title

    def _obj(self, oid, body, stream=None):
        self.offsets[oid] = self.f.tell()
        self.f.write(b'%d 0 obj\n' % oid)
        if stream is None:
            self.f.write(body + b'\nendobj\n')
        else:
            self.f.write(body + b'\nstream\n')
            self.f.write(stream)
            self.f.write(b'\nendstream\nendobj\n')

    def _new(self):
        oid = self.next_id
        self.next_id += 1
        return oid

    def add_page(self, width_mm, height_mm, images):
        """images: list of (uint8 RGB array, x_mm, y_mm from top-left, w_mm, h_mm)."""
        names, ops = [], []
        ph = height_mm * PT_PER_MM
        for i, (img, x, y, w, h) in enumerate(images):
            img = np.ascontiguousarray(img, np.uint8)
            data = zlib.compress(img.tobytes(), 6)
            oid = self._new()
            self._obj(oid, b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB '
                           b'/BitsPerComponent 8 /Interpolate false /Filter /FlateDecode /Length %d >>'
                      % (img.shape[1], img.shape[0], len(data)), data)
            names.append((i, oid))
            ops.append(b'q %.4f 0 0 %.4f %.4f %.4f cm /Im%d Do Q' % (
                w * PT_PER_MM, h * PT_PER_MM, x * PT_PER_MM, ph - (y + h) * PT_PER_MM, i))
        content = zlib.compress(b'\n'.join(ops))
        cid = self._new()
        self._obj(cid, b'<< /Length %d /Filter /FlateDecode >>' % len(content), content)
        xobjs = b' '.join(b'/Im%d %d 0 R' % (i, oid) for i, oid in names)
        pid = self._new()
        self._obj(pid, b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.4f %.4f] '
                       b'/Resources << /XObject << %s >> >> /Contents %d 0 R >>'
                  % (width_mm * PT_PER_MM, ph, xobjs, cid))
        self.pages.append(pid)

    def close(self):
        kids = b' '.join(b'%d 0 R' % p for p in self.pages)
        self._obj(2, b'<< /Type /Pages /Kids [%s] /Count %d >>' % (kids, len(self.pages)))
        self._obj(1, b'<< /Type /Catalog /Pages 2 0 R >>')
        info = self._new()
        title = self.title.replace('\\', '').replace('(', '').replace(')', '').encode('latin-1', 'replace')
        self._obj(info, b'<< /Title (%s) /Producer (Splat Slicer) >>' % title)
        xref = self.f.tell()
        n = self.next_id
        self.f.write(b'xref\n0 %d\n0000000000 65535 f \n' % n)
        for oid in range(1, n):
            self.f.write(b'%010d 00000 n \n' % self.offsets[oid])
        self.f.write(b'trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (n, info, xref))
        self.f.close()


# ---------------------------------------------------------------------------------- volumes
class NrrdWriter:
    """RGBA uint8 volume, gzip encoded, millimetre spacing. Layers are appended bottom-up."""

    def __init__(self, path, cols, rows, count, px, pitch, z0):
        self.path = path
        header = (
            "NRRD0004\n# Splat Slicer layer volume (RGBA: splat colour + layer opacity)\n"
            "type: uint8\ndimension: 4\nspace: right-anterior-superior\n"
            f"sizes: 4 {cols} {rows} {count}\n"
            f"space directions: none ({px:.6f},0,0) (0,{px:.6f},0) (0,0,{pitch:.6f})\n"
            "kinds: RGBA-color domain domain domain\nendian: little\nencoding: gzip\n"
            'space units: "mm" "mm" "mm"\n'
            f"space origin: ({px / 2:.6f},{px / 2:.6f},{z0 + pitch / 2:.6f})\n\n")
        self.f = open(path, 'wb')
        self.f.write(header.encode('ascii'))
        self.gz = gzip.GzipFile(fileobj=self.f, mode='wb', compresslevel=6)

    def add(self, rgba_top_down):
        # Image row 0 is the block's far edge; the volume's j axis runs front -> back.
        self.gz.write(np.ascontiguousarray(rgba_top_down[::-1], np.uint8).tobytes())

    def close(self):
        self.gz.close()
        self.f.close()


class NiftiWriter:
    """NIfTI-1 single file (.nii or .nii.gz), RGBA32 voxels, mm units, sform/qform set."""

    def __init__(self, path, cols, rows, count, px, pitch, z0):
        hdr = bytearray(348)
        struct.pack_into('<i', hdr, 0, 348)
        struct.pack_into('<8h', hdr, 40, 3, cols, rows, count, 1, 1, 1, 1)
        struct.pack_into('<hh', hdr, 70, 2304, 32)                       # NIFTI_TYPE_RGBA32
        struct.pack_into('<8f', hdr, 76, 1.0, px, px, pitch, 1, 1, 1, 1)
        struct.pack_into('<f', hdr, 108, 352.0)                          # vox_offset
        struct.pack_into('<f', hdr, 112, 1.0)                            # scl_slope
        hdr[123] = 2                                                     # xyzt_units: mm
        desc = b'Splat Slicer layers: colour + opacity'
        hdr[148:148 + len(desc)] = desc
        struct.pack_into('<hh', hdr, 252, 2, 2)                          # qform, sform: aligned
        ox, oy, oz = px / 2, px / 2, z0 + pitch / 2
        struct.pack_into('<6f', hdr, 256, 0, 0, 0, ox, oy, oz)           # quatern b,c,d + offsets
        struct.pack_into('<4f', hdr, 280, px, 0, 0, ox)
        struct.pack_into('<4f', hdr, 296, 0, px, 0, oy)
        struct.pack_into('<4f', hdr, 312, 0, 0, pitch, oz)
        hdr[344:348] = b'n+1\x00'
        self.f = gzip.open(path, 'wb', 6) if path.endswith('.gz') else open(path, 'wb')
        self.f.write(bytes(hdr) + b'\x00' * 4)

    def add(self, rgba_top_down):
        self.f.write(np.ascontiguousarray(rgba_top_down[::-1], np.uint8).tobytes())

    def close(self):
        self.f.close()


def _uid():
    return '2.25.%d' % uuid.uuid4().int


def _sq(group, elem, items):
    """Explicit-length sequence of explicit-length items (each item is encoded element bytes)."""
    body = b''.join(struct.pack('<HHI', 0xFFFE, 0xE000, len(it)) + it for it in items)
    return struct.pack('<HH', group, elem) + b'SQ\x00\x00' + struct.pack('<I', len(body)) + body


class DicomMultiframeWriter:
    """All layers in ONE file: Multi-frame True Color Secondary Capture, explicit VR little endian.

    Spacing and orientation sit in the shared functional groups, each frame's position in the
    per-frame groups (as enhanced CT/MR do), and the classic top-level tags are filled in too, for
    viewers that read only those. Frames are streamed, so the volume never sits in memory.
    """
    SOP_CLASS = '1.2.840.10008.5.1.4.1.1.7.4'

    def __init__(self, path, name, cols, rows, count, px, pitch, z0):
        self.path = path
        self.count, self.frame_bytes = count, rows * cols * 3
        self.k = 0
        el = DicomSeriesWriter._el
        inst, study, series, frame_uid = _uid(), _uid(), _uid(), _uid()
        now = datetime.datetime.now()
        us = lambda v: struct.pack('<H', v)
        ul = lambda v: struct.pack('<I', v)
        ds = lambda v: '%.6g' % v
        x0, y0 = px / 2, rows * px - px / 2
        zs = [z0 + (k + 0.5) * pitch for k in range(count)]
        orient = '1\\0\\0\\0\\-1\\0'
        meta_body = b''.join([
            el(0x0002, 0x0001, 'OB', b'\x00\x01'),
            el(0x0002, 0x0002, 'UI', self.SOP_CLASS),
            el(0x0002, 0x0003, 'UI', inst),
            el(0x0002, 0x0010, 'UI', DicomSeriesWriter.TRANSFER),
            el(0x0002, 0x0012, 'UI', DicomSeriesWriter.IMPL_UID),
            el(0x0002, 0x0013, 'SH', 'SPLATSLICER'),
        ])
        meta = el(0x0002, 0x0000, 'UL', ul(len(meta_body))) + meta_body
        shared = _sq(0x5200, 0x9229, [b''.join([
            _sq(0x0020, 0x9116, [el(0x0020, 0x0037, 'DS', orient)]),
            _sq(0x0028, 0x9110, [b''.join([
                el(0x0018, 0x0050, 'DS', ds(pitch)),
                el(0x0018, 0x0088, 'DS', ds(pitch)),
                el(0x0028, 0x0030, 'DS', '%s\\%s' % (ds(px), ds(px))),
            ])]),
        ])])
        per_frame = _sq(0x5200, 0x9230, [b''.join([
            _sq(0x0020, 0x9111, [b''.join([
                el(0x0020, 0x9056, 'SH', '1'),
                el(0x0020, 0x9057, 'UL', ul(k + 1)),
            ])]),
            _sq(0x0020, 0x9113, [el(0x0020, 0x0032, 'DS', '\\'.join(ds(v) for v in (x0, y0, z)))]),
        ]) for k, z in enumerate(zs)])
        header = b''.join([
            el(0x0008, 0x0008, 'CS', 'DERIVED\\SECONDARY'),
            el(0x0008, 0x0016, 'UI', self.SOP_CLASS),
            el(0x0008, 0x0018, 'UI', inst),
            el(0x0008, 0x0020, 'DA', now.strftime('%Y%m%d')),
            el(0x0008, 0x0030, 'TM', now.strftime('%H%M%S')),
            el(0x0008, 0x0060, 'CS', 'OT'),
            el(0x0008, 0x0064, 'CS', 'WSD'),
            el(0x0008, 0x103E, 'LO', (name + ' layers')[:64]),
            el(0x0010, 0x0010, 'PN', name[:64]),
            el(0x0010, 0x0020, 'LO', name[:64]),
            el(0x0018, 0x0050, 'DS', ds(pitch)),
            el(0x0018, 0x0088, 'DS', ds(pitch)),
            el(0x0018, 0x2005, 'DS', '\\'.join(ds(z) for z in zs)),          # Slice Location Vector
            el(0x0020, 0x000D, 'UI', study),
            el(0x0020, 0x000E, 'UI', series),
            el(0x0020, 0x0011, 'IS', '1'),
            el(0x0020, 0x0013, 'IS', '1'),
            el(0x0020, 0x0032, 'DS', '\\'.join(ds(v) for v in (x0, y0, zs[0]))),
            el(0x0020, 0x0037, 'DS', orient),
            el(0x0020, 0x0052, 'UI', frame_uid),
            el(0x0028, 0x0002, 'US', us(3)),
            el(0x0028, 0x0004, 'CS', 'RGB'),
            el(0x0028, 0x0006, 'US', us(0)),
            el(0x0028, 0x0008, 'IS', str(count)),
            el(0x0028, 0x0009, 'AT', struct.pack('<HH', 0x0018, 0x2005)),  # frames step through slice locations
            el(0x0028, 0x0010, 'US', us(rows)),
            el(0x0028, 0x0011, 'US', us(cols)),
            el(0x0028, 0x0030, 'DS', '%s\\%s' % (ds(px), ds(px))),
            el(0x0028, 0x0100, 'US', us(8)),
            el(0x0028, 0x0101, 'US', us(8)),
            el(0x0028, 0x0102, 'US', us(7)),
            el(0x0028, 0x0103, 'US', us(0)),
            shared,
            per_frame,
        ])
        total = self.frame_bytes * count
        self.pad = total % 2
        self.f = open(path, 'wb')
        self.f.write(b'\x00' * 128 + b'DICM' + meta + header
                     + struct.pack('<HH', 0x7FE0, 0x0010) + b'OB\x00\x00' + struct.pack('<I', total + self.pad))

    def add(self, rgb_top_down):
        self.f.write(np.ascontiguousarray(rgb_top_down, np.uint8).tobytes())
        self.k += 1

    def close(self):
        if self.k != self.count:
            raise RuntimeError(f"DICOM file got {self.k} of {self.count} frames")
        self.f.write(b'\x00' * self.pad)
        self.f.close()


class DicomSeriesWriter:
    """One explicit-VR little-endian Secondary Capture file per layer (RGB, colour on black).

    Each file carries pixel spacing, slice thickness, position and orientation, so viewers
    such as 3D Slicer or Horos stack the series into a correctly scaled volume.
    """
    SOP_CLASS = '1.2.840.10008.5.1.4.1.1.7'
    TRANSFER = '1.2.840.10008.1.2.1'
    IMPL_UID = '2.25.240586314972251150963434781925137310577'

    def __init__(self, folder, name, cols, rows, count, px, pitch, z0):
        os.makedirs(folder, exist_ok=True)
        self.folder, self.name = folder, name
        self.cols, self.rows, self.count = cols, rows, count
        self.px, self.pitch, self.z0 = px, pitch, z0
        self.study, self.series, self.frame = _uid(), _uid(), _uid()
        now = datetime.datetime.now()
        self.date, self.time = now.strftime('%Y%m%d'), now.strftime('%H%M%S')
        self.k = 0

    @staticmethod
    def _el(group, elem, vr, value):
        if isinstance(value, str):
            raw = value.encode('ascii')
            if len(raw) % 2:
                raw += b'\x00' if vr == 'UI' else b' '
        else:
            raw = value
            if len(raw) % 2:
                raw += b'\x00'
        tag = struct.pack('<HH', group, elem) + vr.encode('ascii')
        if vr in ('OB', 'OW', 'OF', 'SQ', 'UT', 'UN'):
            return tag + b'\x00\x00' + struct.pack('<I', len(raw)) + raw
        return tag + struct.pack('<H', len(raw)) + raw

    def add(self, rgb_top_down):
        k = self.k
        self.k += 1
        inst = _uid()
        us = lambda v: struct.pack('<H', v)
        # Row 0 is the far edge: rows run toward the viewer (-y), columns along +x.
        pos = (self.px / 2, self.rows * self.px - self.px / 2, self.z0 + (k + 0.5) * self.pitch)
        el = self._el
        meta_body = b''.join([
            el(0x0002, 0x0001, 'OB', b'\x00\x01'),
            el(0x0002, 0x0002, 'UI', self.SOP_CLASS),
            el(0x0002, 0x0003, 'UI', inst),
            el(0x0002, 0x0010, 'UI', self.TRANSFER),
            el(0x0002, 0x0012, 'UI', self.IMPL_UID),
            el(0x0002, 0x0013, 'SH', 'SPLATSLICER'),
        ])
        meta = el(0x0002, 0x0000, 'UL', struct.pack('<I', len(meta_body))) + meta_body
        ds = b''.join([
            el(0x0008, 0x0008, 'CS', 'DERIVED\\SECONDARY'),
            el(0x0008, 0x0016, 'UI', self.SOP_CLASS),
            el(0x0008, 0x0018, 'UI', inst),
            el(0x0008, 0x0020, 'DA', self.date),
            el(0x0008, 0x0030, 'TM', self.time),
            el(0x0008, 0x0060, 'CS', 'OT'),
            el(0x0008, 0x0064, 'CS', 'WSD'),
            el(0x0008, 0x103E, 'LO', (self.name + ' layers')[:64]),
            el(0x0010, 0x0010, 'PN', self.name[:64]),
            el(0x0010, 0x0020, 'LO', self.name[:64]),
            el(0x0018, 0x0050, 'DS', '%.6g' % self.pitch),
            el(0x0018, 0x0088, 'DS', '%.6g' % self.pitch),
            el(0x0020, 0x000D, 'UI', self.study),
            el(0x0020, 0x000E, 'UI', self.series),
            el(0x0020, 0x0011, 'IS', '1'),
            el(0x0020, 0x0013, 'IS', str(k + 1)),
            el(0x0020, 0x0032, 'DS', '\\'.join('%.6g' % v for v in pos)),
            el(0x0020, 0x0037, 'DS', '1\\0\\0\\0\\-1\\0'),
            el(0x0020, 0x0052, 'UI', self.frame),
            el(0x0020, 0x1041, 'DS', '%.6g' % pos[2]),
            el(0x0028, 0x0002, 'US', us(3)),
            el(0x0028, 0x0004, 'CS', 'RGB'),
            el(0x0028, 0x0006, 'US', us(0)),
            el(0x0028, 0x0010, 'US', us(self.rows)),
            el(0x0028, 0x0011, 'US', us(self.cols)),
            el(0x0028, 0x0030, 'DS', '%.6g\\%.6g' % (self.px, self.px)),
            el(0x0028, 0x0100, 'US', us(8)),
            el(0x0028, 0x0101, 'US', us(8)),
            el(0x0028, 0x0102, 'US', us(7)),
            el(0x0028, 0x0103, 'US', us(0)),
            el(0x7FE0, 0x0010, 'OB', np.ascontiguousarray(rgb_top_down, np.uint8).tobytes()),
        ])
        path = os.path.join(self.folder, '%s_%04d.dcm' % (self.name, k + 1))
        with open(path, 'wb') as f:
            f.write(b'\x00' * 128 + b'DICM' + meta + ds)

    def close(self):
        pass
