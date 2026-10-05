"""Print tiles: a slice image inside a margin with registration marks, cut lines and a label.

Everything is drawn in NumPy (Blender's font drawing needs a GPU context, which a background
export doesn't have), using a small built-in stroke font.
"""

import math

import numpy as np

MM_PER_INCH = 25.4

# Stroke font on a 4 x 6 grid (x right, y up). Each glyph is a list of polylines.
_FONT = {
    '0': [[(0, 0), (4, 0), (4, 6), (0, 6), (0, 0)], [(0, 0), (4, 6)]],
    '1': [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    '2': [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)]],
    '3': [[(0, 6), (4, 6), (2, 3.5), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    '4': [[(3, 0), (3, 6), (0, 2), (4, 2)]],
    '5': [[(4, 6), (0, 6), (0, 3.5), (3, 3.5), (4, 2.5), (4, 1), (3, 0), (0, 0)]],
    '6': [[(4, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2.5), (3, 3.5), (0, 3.5)]],
    '7': [[(0, 6), (4, 6), (1.5, 0)]],
    '8': [[(1, 3.2), (0, 4.2), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4.2), (3, 3.2), (1, 3.2),
           (0, 2.2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2.2), (3, 3.2)]],
    '9': [[(0, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 3.5), (1, 2.5), (4, 2.5)]],
    'A': [[(0, 0), (0, 4), (2, 6), (4, 4), (4, 0)], [(0, 2.5), (4, 2.5)]],
    'B': [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)], [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    'C': [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)]],
    'D': [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 1), (3, 0), (0, 0)]],
    'E': [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    'F': [[(4, 6), (0, 6), (0, 0)], [(0, 3), (3, 3)]],
    'G': [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)]],
    'H': [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    'I': [[(1, 6), (3, 6)], [(2, 6), (2, 0)], [(1, 0), (3, 0)]],
    'J': [[(4, 6), (4, 1), (3, 0), (1, 0), (0, 1)]],
    'K': [[(0, 0), (0, 6)], [(4, 6), (0, 2.5)], [(1.3, 3.5), (4, 0)]],
    'L': [[(0, 6), (0, 0), (4, 0)]],
    'M': [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    'N': [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    'O': [[(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)]],
    'P': [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)]],
    'Q': [[(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)], [(2.5, 1.5), (4, 0)]],
    'R': [[(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)], [(2, 3), (4, 0)]],
    'S': [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    'T': [[(0, 6), (4, 6)], [(2, 6), (2, 0)]],
    'U': [[(0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)]],
    'V': [[(0, 6), (2, 0), (4, 6)]],
    'W': [[(0, 6), (1, 0), (2, 3), (3, 0), (4, 6)]],
    'X': [[(0, 0), (4, 6)], [(0, 6), (4, 0)]],
    'Y': [[(0, 6), (2, 3), (4, 6)], [(2, 3), (2, 0)]],
    'Z': [[(0, 6), (4, 6), (0, 0), (4, 0)]],
    '/': [[(0, 0), (4, 6)]],
    '-': [[(1, 3), (3, 3)]],
    '_': [[(0, 0), (4, 0)]],
    '+': [[(1, 3), (3, 3)], [(2, 2), (2, 4)]],
    '=': [[(1, 2), (3, 2)], [(1, 4), (3, 4)]],
    '.': [[(1.8, 0), (2.2, 0), (2.2, 0.4), (1.8, 0.4), (1.8, 0)]],
    ',': [[(2.2, 0.4), (2.2, 0), (1.6, -1)]],
    ':': [[(2, 1), (2, 1.3)], [(2, 4), (2, 4.3)]],
    '(': [[(3, 6), (2, 5), (2, 1), (3, 0)]],
    ')': [[(1, 6), (2, 5), (2, 1), (1, 0)]],
    '#': [[(1, 0), (1.5, 6)], [(2.5, 0), (3, 6)], [(0, 2), (4, 2)], [(0, 4), (4, 4)]],
    "'": [[(2, 6), (2, 4.5)]],
    ' ': [],
}


def text_segments(text, x, y, height):
    """Line segments for `text` with its baseline-left at (x, y), y pointing down (pixels)."""
    s = height / 6.0
    segs = []
    cx = x
    for ch in text.upper():
        for line in _FONT.get(ch, _FONT['#'] if ch.strip() else []):
            for (ax, ay), (bx, by) in zip(line, line[1:]):
                segs.append((cx + ax * s, y - ay * s, cx + bx * s, y - by * s))
        cx += 6 * s
    return segs, cx - x


def circle_segments(cx, cy, r, n=72):
    t = np.linspace(0, 2 * math.pi, n + 1)
    xs, ys = cx + r * np.cos(t), cy + r * np.sin(t)
    return [(xs[i], ys[i], xs[i + 1], ys[i + 1]) for i in range(n)]


def draw_segments(cov, segs, width):
    """Anti-aliased strokes: raise coverage (rows, cols) along each segment."""
    h, w = cov.shape
    half = max(width, 1.0) / 2.0
    for x0, y0, x1, y1 in segs:
        xa, xb = int(math.floor(min(x0, x1) - half - 1)), int(math.ceil(max(x0, x1) + half + 1))
        ya, yb = int(math.floor(min(y0, y1) - half - 1)), int(math.ceil(max(y0, y1) + half + 1))
        xa, ya, xb, yb = max(xa, 0), max(ya, 0), min(xb, w), min(yb, h)
        if xa >= xb or ya >= yb:
            continue
        py, px = np.mgrid[ya:yb, xa:xb] + 0.5
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy
        t = np.clip(((px - x0) * dx + (py - y0) * dy) / ll, 0, 1) if ll > 0 else 0.0
        d = np.hypot(px - (x0 + t * dx), py - (y0 + t * dy))
        c = np.clip(half + 0.5 - d, 0, 1)
        np.maximum(cov[ya:yb, xa:xb], c, out=cov[ya:yb, xa:xb])


def fill_disc(cov, cx, cy, r):
    h, w = cov.shape
    ya, yb = max(int(cy - r - 2), 0), min(int(cy + r + 2), h)
    xa, xb = max(int(cx - r - 2), 0), min(int(cx + r + 2), w)
    py, px = np.mgrid[ya:yb, xa:xb] + 0.5
    c = np.clip(r + 0.5 - np.hypot(px - cx, py - cy), 0, 1)
    np.maximum(cov[ya:yb, xa:xb], c, out=cov[ya:yb, xa:xb])


class TileLayout:
    """Pixel geometry of one printed tile (the slice plus its marked margin)."""

    def __init__(self, cols, rows, dpi, margin_mm, mark_mm=6.0, line_mm=0.12, text_mm=2.0):
        self.dpi = dpi
        self.ppm = dpi / MM_PER_INCH                    # pixels per millimetre
        self.cols, self.rows = cols, rows
        self.m = int(round(margin_mm * self.ppm))
        self.width = cols + 2 * self.m
        self.height = rows + 2 * self.m
        self.mark = mark_mm * self.ppm
        self.line = max(line_mm * self.ppm, 1.0)
        self.text = text_mm * self.ppm

    @property
    def size_mm(self):
        return self.width / self.ppm, self.height / self.ppm

    def overlay(self, label, marks=True, cut_lines=True, labels=True):
        """Mark coverage (height, width) float32, 1 = ink. Same for every slice except the label."""
        cov = np.zeros((self.height, self.width), np.float32)
        m, W, H = self.m, self.width, self.height
        x0, y0, x1, y1 = m, m, m + self.cols, m + self.rows      # image rectangle
        segs = []
        if cut_lines:
            # Corner ticks on the image edges, kept out of the image itself.
            gap, tick = 0.8 * self.ppm, min(3.0 * self.ppm, m * 0.25)
            for cx, sx in ((x0, -1), (x1, 1)):
                for cy, sy in ((y0, -1), (y1, 1)):
                    segs.append((cx, cy + sy * gap, cx, cy + sy * (gap + tick)))
                    segs.append((cx + sx * gap, cy, cx + sx * (gap + tick), cy))
        if marks and m > 0:
            # Crosshair-in-circle at each corner, centred half a margin outside the image corner.
            off = m * 0.5
            arm = min(self.mark / 2.0, off * 0.9)
            for cx, cy in ((x0 - off, y0 - off), (x1 + off, y0 - off), (x0 - off, y1 + off), (x1 + off, y1 + off)):
                segs += [(cx - arm, cy, cx + arm, cy), (cx, cy - arm, cx, cy + arm)]
                segs += circle_segments(cx, cy, arm * 0.6)
            # Orientation key beside the top-left mark so a sheet can't go in flipped or turned.
            fill_disc(cov, x0 - off + arm * 1.4, y0 - off, self.line * 1.8)
            # Mid-edge ticks give a second alignment line along each side.
            # (None on the bottom edge: the label sits there.)
            for cx, cy, vertical in (((x0 + x1) / 2, y0 - off, True),
                                     (x0 - off, (y0 + y1) / 2, False), (x1 + off, (y0 + y1) / 2, False)):
                if vertical:
                    segs.append((cx, cy - arm * 0.6, cx, cy + arm * 0.6))
                else:
                    segs.append((cx - arm * 0.6, cy, cx + arm * 0.6, cy))
        draw_segments(cov, segs, self.line)
        if labels and label and m > 0:
            # Bottom margin on the lower marks' row, shrunk to fit between them.
            off = m * 0.5
            arm = min(self.mark / 2.0, off * 0.9)
            room = (x1 - x0) + 2 * off - 3.6 * arm
            th = min(self.text, m * 0.35)
            _, tw = text_segments(label, 0, 0, th)
            if tw > room > 0:
                th *= room / tw
            tsegs, tw = text_segments(label, 0, 0, th)
            tx = (W - tw) / 2.0
            ty = y1 + off + th / 2.0
            draw_segments(cov, [(a + tx, b + ty, c + tx, d + ty) for a, b, c, d in tsegs],
                          max(self.line, th / 14.0))
        return cov

    def compose(self, ink_rgb, label, marks=True, cut_lines=True, labels=True, mirror=False):
        """Tile as uint8 RGB (height, width, 3): white paper, slice ink, black marks.

        `mirror` flips the whole tile (picture, marks and label) for sheets laid printed side down:
        seen through the film, everything then reads correctly and the key dot is top-left.
        """
        tile = np.ones((self.height, self.width, 3), np.float32)
        tile[self.m:self.m + self.rows, self.m:self.m + self.cols] = ink_rgb
        cov = self.overlay(label, marks, cut_lines, labels)
        tile *= (1.0 - cov)[..., None]
        if mirror:
            tile = tile[:, ::-1]
        return (np.clip(tile, 0, 1) * 255 + 0.5).astype(np.uint8)

    def compose_rgba(self, alpha, rgb, label, marks=True, cut_lines=True, labels=True, mirror=False):
        """Tile for a white-ink RIP, uint8 RGBA: transparent where there is no ink.

        The slice is its colour with its opacity as alpha (standard PNG coverage, so composited
        over white it equals the normal print), with black marks over it. RIPs that build the
        white channel from transparency put white exactly where the model is.
        """
        a = np.zeros((self.height, self.width), np.float32)
        c = np.zeros((self.height, self.width, 3), np.float32)
        sl = (slice(self.m, self.m + self.rows), slice(self.m, self.m + self.cols))
        a[sl] = alpha
        c[sl] = rgb
        cov = self.overlay(label, marks, cut_lines, labels)
        # Black marks "over" the slice.
        out_a = cov + a * (1.0 - cov)
        with np.errstate(invalid='ignore', divide='ignore'):
            out_c = np.where(out_a[..., None] > 0, (c * (a * (1.0 - cov))[..., None]) / out_a[..., None], 0.0)
        tile = np.dstack([out_c, out_a])
        if mirror:
            tile = tile[:, ::-1]
        return (np.clip(tile, 0, 1) * 255 + 0.5).astype(np.uint8)

    def white_mask(self, alpha, mirror=False):
        """White-ink mask aligned with the tile: black = full white, white = none (margins: none)."""
        tile = np.ones((self.height, self.width), np.float32)
        tile[self.m:self.m + self.rows, self.m:self.m + self.cols] = 1.0 - alpha
        if mirror:
            tile = tile[:, ::-1]
        return (np.clip(tile, 0, 1) * 255 + 0.5).astype(np.uint8)


PAPER_MM = {
    'LETTER': (215.9, 279.4), 'LEGAL': (215.9, 355.6), 'TABLOID': (279.4, 431.8),
    'SUPER_B': (330.2, 482.6), 'A4': (210.0, 297.0), 'A3': (297.0, 420.0), 'A3_PLUS': (329.0, 483.0),
}


def sheet_grid(tile_mm, paper_mm, edge_mm, gap_mm):
    """How many tiles fit on a sheet (nx, ny), trying both paper orientations.

    Returns (nx, ny, paper_w, paper_h) or None if not even one fits.
    """
    best = None
    tw, th = tile_mm
    for pw, ph in (paper_mm, paper_mm[::-1]):
        uw, uh = pw - 2 * edge_mm, ph - 2 * edge_mm
        nx = int((uw + gap_mm) // (tw + gap_mm)) if uw >= tw else 0
        ny = int((uh + gap_mm) // (th + gap_mm)) if uh >= th else 0
        if nx and ny and (best is None or nx * ny > best[0] * best[1]):
            best = (nx, ny, pw, ph)
    return best
