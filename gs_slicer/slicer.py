"""Slice a Gaussian splat cloud into printable film layers (pure NumPy, no bpy).

Coordinates are the block's print frame in millimetres: x along the block length L, y along
its width W, z up its height H, origin at the block's bottom-left-front corner. Layer k covers
z in [z0 + k*pitch, z0 + (k+1)*pitch].

Density model
-------------
A 3DGS splat has a peak opacity o, not a physical density. Each splat is turned into an
absorbing density  rho(x) = tau / (sqrt(2 pi) * t) * exp(-1/2 (x-mu)^T S^-1 (x-mu))
with tau = -ln(1 - o) and t the splat's thinnest standard deviation. Looking straight through
the splat along its thinnest axis (face-on, the way the camera that trained it mostly saw it)
the optical depth is exactly tau, so the printed block reproduces the splat's opacity from
whichever side its surface faces, not only from the top.

Each layer's share is the integral of that density over the layer's slab. Per pixel the layer
keeps the summed optical depth T and the depth-weighted colour; its opacity is 1 - exp(-T).
Within one thin layer the mix is order independent, which is what ink on a single sheet is.

Colour
------
Printed ink looks the same from every direction, so each splat gets one colour. Averaged
over every view direction, all higher spherical-harmonic bands integrate to zero, leaving the
DC term: that is the default. (Six cameras on the six faces give exactly the same answer for
SH degree <= 3: odd bands cancel between opposite faces and band 2 sums to zero.) Optionally
the SH is averaged over a cone of view directions around one face instead, baking in the
sheen the capture saw from that side.
"""

import math

import numpy as np

SH_C0 = 0.28209479177387814
_C1 = 0.4886025119029199
_C2 = (1.0925484305920792, -1.0925484305920792, 0.31539156525252005,
       -1.0925484305920792, 0.5462742152960396)
_C3 = (-0.5900435899266435, 2.890611442640554, -0.4570457994644658, 0.3731763325901154,
       -0.4570457994644658, 1.445305721320277, -0.5900435899266435)

# Direction a viewer on each face looks along, in the block frame (camera -> splat).
FACE_VIEW_DIR = {
    'TOP': (0.0, 0.0, -1.0), 'BOTTOM': (0.0, 0.0, 1.0),
    'FRONT': (0.0, 1.0, 0.0), 'BACK': (0.0, -1.0, 0.0),
    'LEFT': (1.0, 0.0, 0.0), 'RIGHT': (-1.0, 0.0, 0.0),
}

MAX_OPACITY = 0.9995      # keeps tau finite
FOOTPRINT_SIGMAS = 3.0    # pixels further than this many sigma are skipped
_RADII = (1, 2, 3, 4, 6, 8, 11, 16, 22, 32, 45, 64, 90, 128, 180, 256)
_WORK = 1_500_000         # splat-pixel samples per vectorised batch


def erf(x):
    """Vectorised erf (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)."""
    s = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-a * a)
    return s * y


def _phi(x):
    return 0.5 * (1.0 + erf(x * (1.0 / math.sqrt(2.0))))


def sh_basis(d):
    """Real SH basis for bands 1..3 (15 values) at unit directions d (..., 3), 3DGS ordering."""
    x, y, z = d[..., 0], d[..., 1], d[..., 2]
    xx, yy, zz = x * x, y * y, z * z
    return np.stack([
        -_C1 * y, _C1 * z, -_C1 * x,
        _C2[0] * x * y, _C2[1] * y * z, _C2[2] * (2 * zz - xx - yy), _C2[3] * x * z, _C2[4] * (xx - yy),
        _C3[0] * y * (3 * xx - yy), _C3[1] * x * y * z, _C3[2] * y * (4 * zz - xx - yy),
        _C3[3] * z * (2 * zz - 3 * xx - 3 * yy), _C3[4] * x * (4 * zz - xx - yy),
        _C3[5] * z * (xx - yy), _C3[6] * x * (xx - 3 * yy),
    ], axis=-1)


def cone_directions(axis, half_angle_deg, count=256):
    """Evenly spread unit vectors in a cone around `axis` (Fibonacci spiral on the cap)."""
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    cos_max = math.cos(math.radians(max(0.0, min(half_angle_deg, 180.0))))
    i = np.arange(count) + 0.5
    cz = 1.0 - (1.0 - cos_max) * i / count
    r = np.sqrt(np.clip(1.0 - cz * cz, 0.0, None))
    phi = i * math.pi * (3.0 - math.sqrt(5.0))
    local = np.stack([r * np.cos(phi), r * np.sin(phi), cz], 1)
    helper = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    return local @ np.stack([u, v, axis])


def splat_colors(dc, sh, linear, mode='ALL', face='TOP', cone_deg=45.0):
    """One RGB per splat.

    dc: (N,3) SH DC coefficients. sh: (N,15,3) higher coefficients or None.
    linear: 3x3 linear part of the splat-object -> block mapping (SH lives in object space).
    mode 'ALL' = average over every direction (DC); 'FACE' = average over a cone around `face`.
    """
    rgb = 0.5 + SH_C0 * dc
    if mode == 'FACE' and sh is not None and sh.shape[1] >= 3:
        dirs = cone_directions(FACE_VIEW_DIR[face], cone_deg)
        obj_dirs = dirs @ np.linalg.inv(linear).T
        obj_dirs /= np.linalg.norm(obj_dirs, axis=1, keepdims=True)
        mean_basis = sh_basis(obj_dirs).mean(0)[:sh.shape[1]]
        rgb = rgb + np.einsum('k,nkc->nc', mean_basis, sh)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def quat_to_mat(q):
    """(N,4) w,x,y,z -> (N,3,3)."""
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
    ], 1).reshape(-1, 3, 3)


class SplatVolume:
    """Splats expressed in the block's millimetre frame, ready to slice."""

    def __init__(self, pos, scale, quat, opacity, rgb, to_block, density=1.0, min_opacity=0.0):
        """to_block: 4x4 matrix taking splat object coordinates to block millimetres."""
        M = np.asarray(to_block, np.float64)
        keep = opacity > max(min_opacity, 1e-4)
        pos, scale, quat, opacity, rgb = pos[keep], scale[keep], quat[keep], opacity[keep], rgb[keep]
        A = M[:3, :3]
        self.mu = pos.astype(np.float64) @ A.T + M[:3, 3]
        R = A @ quat_to_mat(quat.astype(np.float64))                 # (N,3,3)
        RS = R * scale.astype(np.float64)[:, None, :]
        cov = RS @ RS.transpose(0, 2, 1)
        self.cov = cov
        # Thinnest standard deviation: the splat's own thickness in block millimetres.
        thin = np.sqrt(np.clip(np.linalg.eigvalsh(cov)[:, 0], 1e-18, None))
        o = np.clip(opacity.astype(np.float64), 0.0, MAX_OPACITY)
        self.tau = -np.log1p(-o) * density
        self.thin = thin
        self.rgb = rgb.astype(np.float32)
        self.n = len(self.mu)
        # Slab (z) statistics, independent of pixel size.
        self.sz = np.sqrt(cov[:, 2, 2])

    def layer_span(self, z0, pitch, count):
        """First and last layer each splat can touch (3 sigma)."""
        lo = np.floor((self.mu[:, 2] - FOOTPRINT_SIGMAS * self.sz - z0) / pitch).astype(np.int64)
        hi = np.floor((self.mu[:, 2] + FOOTPRINT_SIGMAS * self.sz - z0) / pitch).astype(np.int64)
        return np.clip(lo, 0, count - 1), np.clip(hi, 0, count - 1), (hi >= 0) & (lo < count)


class Slicer:
    """Renders the layers of one block at one pixel size.

    Image layout: column = x (block length), row 0 = far edge (y = W), so the image reads like
    the block seen from above with the front edge at the bottom.
    """

    def __init__(self, vol, size_mm, pitch, count, pixel_mm, z0=0.0):
        self.vol = vol
        self.L, self.W, self.H = size_mm
        self.pitch = float(pitch)
        self.count = int(count)
        self.z0 = float(z0)
        self.px = float(pixel_mm)
        self.cols = max(1, int(round(self.L / self.px)))
        self.rows = max(1, int(round(self.W / self.px)))
        self._prep()

    def _prep(self):
        v, px = self.vol, self.px
        cov = v.cov
        # Pixel-space footprint: the xy marginal widened by a small low-pass filter so
        # splats smaller than a pixel still land with their full weight (no aliasing).
        sxx = cov[:, 0, 0] / (px * px)
        syy = cov[:, 1, 1] / (px * px)
        sxy = cov[:, 0, 1] / (px * px)
        det0 = sxx * syy - sxy * sxy
        blur = 0.3  # px^2: enough width that sampling at pixel centres keeps the integral
        bxx, byy = sxx + blur, syy + blur
        det = bxx * byy - sxy * sxy
        self.amp = np.sqrt(np.clip(det0, 0, None) / det)          # integral kept after blur
        self.ixx, self.iyy, self.ixy = byy / det, bxx / det, -sxy / det
        # Conditional z given xy (in mm, slope per pixel): m = mu_z + b.(r - mu_xy).
        d2 = cov[:, 0, 0] * cov[:, 1, 1] - cov[:, 0, 1] ** 2
        d2 = np.where(d2 > 1e-24, d2, 1e-24)
        czx, czy = cov[:, 2, 0], cov[:, 2, 1]
        bx = (cov[:, 1, 1] * czx - cov[:, 0, 1] * czy) / d2
        by = (cov[:, 0, 0] * czy - cov[:, 0, 1] * czx) / d2
        s2 = cov[:, 2, 2] - (bx * czx + by * czy)
        self.cs = np.sqrt(np.clip(s2, 1e-18, None))                # conditional sigma (mm)
        self.bx, self.by = bx * px, by * px                        # mm of z per pixel step
        # Optical depth scale: tau * cs / thin * G_xy * F_slab (see module docstring).
        self.k = v.tau * self.cs / v.thin * self.amp
        # Splat centre in pixel coordinates (column, row-from-bottom).
        self.cx = v.mu[:, 0] / px - 0.5
        self.cy = v.mu[:, 1] / px - 0.5
        major = 0.5 * (bxx + byy) + np.sqrt(0.25 * (bxx - byy) ** 2 + sxy * sxy)
        rad = FOOTPRINT_SIGMAS * np.sqrt(major)
        radii = np.array(_RADII)
        self.rbin = np.minimum(np.searchsorted(radii, rad), len(radii) - 1)
        self.lo, self.hi, inside = v.layer_span(self.z0, self.pitch, self.count)
        on = ((self.cx + rad >= 0) & (self.cx - rad < self.cols)
              & (self.cy + rad >= 0) & (self.cy - rad < self.rows) & inside & (self.k > 1e-7))
        self.active = np.nonzero(on)[0]

    def render(self, first, last):
        """Layers first..last-1 -> (opacity (n,rows,cols), rgb (n,rows,cols,3)) float32."""
        it = self.render_iter(first, last)
        while True:
            try:
                next(it)
            except StopIteration as done:
                return done.value

    def render_iter(self, first, last):
        """Generator form of render: yields after each batch, returns the result."""
        n = last - first
        npx = self.rows * self.cols
        T = np.zeros(n * npx, np.float64)
        C = np.zeros((3, n * npx), np.float64)
        idx = self.active[(self.hi[self.active] >= first) & (self.lo[self.active] < last)]
        radii = np.array(_RADII)
        self._pending, self._pending_n = [], 0
        for b in np.unique(self.rbin[idx]):
            group = idx[self.rbin[idx] == b]
            r = int(radii[b])
            oy, ox = np.mgrid[-r:r + 1, -r:r + 1]
            ox, oy = ox.ravel(), oy.ravel()
            per = max(1, _WORK // len(ox))
            for s in range(0, len(group), per):
                self._splat_batch(group[s:s + per], ox, oy, first, last, T, C)
                yield
        self._flush(T, C)
        T = T.reshape(n, self.rows, self.cols)
        with np.errstate(invalid='ignore', divide='ignore'):
            rgb = np.where(T[None] > 1e-12, C.reshape(3, n, self.rows, self.cols) / T[None], 1.0)
        alpha = -np.expm1(-T)
        # Row 0 of the image is the far edge of the block.
        return alpha[:, ::-1].astype(np.float32), np.moveaxis(rgb, 0, -1)[:, ::-1].astype(np.float32)

    def chunk_layers(self, budget_bytes=700_000_000):
        """How many layers to render at once within a memory budget."""
        per_layer = self.rows * self.cols * 64
        return int(max(1, min(self.count, budget_bytes // per_layer)))

    def _splat_batch(self, g, ox, oy, first, last, T, C):
        cx, cy = self.cx[g], self.cy[g]
        X = np.floor(cx).astype(np.int64)[:, None] + ox[None]          # (n,P) pixel indices
        Y = np.floor(cy).astype(np.int64)[:, None] + oy[None]
        dx = X - cx[:, None]
        dy = Y - cy[:, None]
        q = self.ixx[g, None] * dx * dx + 2 * self.ixy[g, None] * dx * dy + self.iyy[g, None] * dy * dy
        ok = (q < FOOTPRINT_SIGMAS ** 2) & (X >= 0) & (X < self.cols) & (Y >= 0) & (Y < self.rows)
        si, pi = np.nonzero(ok)                                         # live splat-pixel pairs
        if not len(si):
            return
        q, dx, dy = q[si, pi], dx[si, pi], dy[si, pi]
        pix = Y[si, pi] * self.cols + X[si, pi]
        gs = g[si]
        G = np.exp(-0.5 * q) * self.k[gs]
        m = self.vol.mu[gs, 2] + self.bx[gs] * dx + self.by[gs] * dy
        inv_s = 1.0 / self.cs[gs]
        lo = np.maximum(self.lo[gs], first)
        span = np.minimum(self.hi[gs], last - 1) - lo + 1               # layers each pair touches
        # One cumulative-normal value per layer boundary, differenced into per-layer mass.
        nb = span + 1
        start = np.cumsum(nb) - nb
        pair = np.repeat(np.arange(len(si)), nb)
        off = np.arange(len(pair)) - np.repeat(start, nb)
        layer = lo[pair] + off
        cdf = _phi((self.z0 + layer * self.pitch - m[pair]) * inv_s[pair])
        inner = off < np.repeat(span, nb)                               # boundary with a layer above it
        idx = np.nonzero(inner)[0]
        w = G[pair[idx]] * (cdf[idx + 1] - cdf[idx])
        keep = w > 1e-9
        idx, w = idx[keep], w[keep]
        p = pair[idx]
        flat = (layer[idx] - first) * (self.rows * self.cols) + pix[p]
        self._pending.append((flat, w, gs[p]))
        self._pending_n += len(flat)
        # Adding into the whole chunk costs its full size, so do it once per many batches.
        if self._pending_n >= T.size // 2:
            self._flush(T, C)

    def _flush(self, T, C):
        if not self._pending:
            return
        flat = np.concatenate([e[0] for e in self._pending])
        w = np.concatenate([e[1] for e in self._pending])
        rgb = self.vol.rgb[np.concatenate([e[2] for e in self._pending])]
        self._pending, self._pending_n = [], 0
        T += np.bincount(flat, w, minlength=T.size)
        for c in range(3):
            C[c] += np.bincount(flat, w * rgb[:, c], minlength=T.size)


def print_rgb(alpha, rgb):
    """Ink image for a clear sheet: the layer composited over white (white = no ink)."""
    return 1.0 - alpha[..., None] * (1.0 - rgb)


def plan_layers(height_mm, pitch_mm):
    """Whole layers that fit the block height, centred in it: (count, z0, stack height)."""
    count = max(1, int(math.floor(height_mm / pitch_mm + 1e-4)))   # tolerate float error in exact multiples
    stack = count * pitch_mm
    return count, (height_mm - stack) / 2.0, stack
