"""VRML 2.0 parser and ASCII 3D renderer with z-buffer rasterisation."""

import math
import re
from pathlib import Path

SHADE = " .,:;=+*#%@"


def parse_wrl(filepath):
    """Parse VRML 2.0 file -> (vertices, triangle_faces)."""
    content = Path(filepath).read_text()
    all_verts, all_faces = [], []

    point_blocks = re.findall(r'point\s*\[(.*?)\]', content, re.DOTALL)
    coord_blocks = re.findall(r'coordIndex\s*\[(.*?)\]', content, re.DOTALL)

    for pb, cb in zip(point_blocks, coord_blocks):
        offset = len(all_verts)
        nums = re.findall(r'[-\d.eE+]+', pb)
        for i in range(0, len(nums) - 2, 3):
            try:
                all_verts.append(
                    (float(nums[i]), float(nums[i + 1]), float(nums[i + 2]))
                )
            except ValueError:
                pass

        indices = [int(x) for x in re.findall(r'-?\d+', cb)]
        face = []
        for idx in indices:
            if idx == -1:
                if len(face) >= 3:
                    for j in range(1, len(face) - 1):
                        all_faces.append(
                            (face[0] + offset, face[j] + offset, face[j + 1] + offset)
                        )
                face = []
            else:
                face.append(idx)

    return all_verts, all_faces


class Renderer3D:
    """Z-buffer triangle rasteriser that outputs ASCII shade characters."""

    def __init__(self, vertices, faces):
        self.faces = faces
        self.vertices = self._normalize(vertices)

    @staticmethod
    def _normalize(verts):
        if not verts:
            return []
        xs, ys, zs = zip(*verts)
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        cz = (min(zs) + max(zs)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1
        return [
            ((x - cx) / span * 2, (y - cy) / span * 2, (z - cz) / span * 2)
            for x, y, z in verts
        ]

    def render(self, width, height, spin, tilt=0.75):
        """Return list[str] of *height* rows, each *width* chars wide."""
        if not self.vertices or not self.faces:
            return self._empty(width, height)

        cos_s, sin_s = math.cos(spin), math.sin(spin)
        cos_t, sin_t = math.cos(tilt), math.sin(tilt)

        lx, ly, lz = 0.36, -0.80, 0.48
        ln = math.sqrt(lx * lx + ly * ly + lz * lz)
        lx, ly, lz = lx / ln, ly / ln, lz / ln

        xformed = []
        for x, y, z in self.vertices:
            x1 = x * cos_s - y * sin_s
            y1 = x * sin_s + y * cos_s
            y2 = y1 * cos_t - z * sin_t
            z2 = y1 * sin_t + z * cos_t
            xformed.append((x1, y2, z2))

        sy = min(width * 0.22, height * 0.42)
        sx = sy * 2.0

        zbuf = [[1e9] * width for _ in range(height)]
        cbuf = [[' '] * width for _ in range(height)]
        nv = len(xformed)

        for f in self.faces:
            if f[0] >= nv or f[1] >= nv or f[2] >= nv:
                continue
            v0, v1, v2 = xformed[f[0]], xformed[f[1]], xformed[f[2]]

            e1x = v1[0] - v0[0]; e1y = v1[1] - v0[1]; e1z = v1[2] - v0[2]
            e2x = v2[0] - v0[0]; e2y = v2[1] - v0[1]; e2z = v2[2] - v0[2]
            nx = e1y * e2z - e1z * e2y
            ny = e1z * e2x - e1x * e2z
            nz = e1x * e2y - e1y * e2x
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl < 1e-10:
                continue
            nx /= nl; ny /= nl; nz /= nl

            if ny > 0:
                nx, ny, nz = -nx, -ny, -nz

            bright = max(0.08, nx * lx + ny * ly + nz * lz)
            ci = min(int(bright * (len(SHADE) - 1) + 0.5), len(SHADE) - 1)
            ch = SHADE[ci]

            hw, hh = width / 2, height / 2
            p0 = (int(hw + v0[0] * sx), int(hh - v0[2] * sy), v0[1])
            p1 = (int(hw + v1[0] * sx), int(hh - v1[2] * sy), v1[1])
            p2 = (int(hw + v2[0] * sx), int(hh - v2[2] * sy), v2[1])

            self._fill(cbuf, zbuf, p0, p1, p2, ch, width, height)

        return [''.join(row) for row in cbuf]

    @staticmethod
    def _fill(cbuf, zbuf, p0, p1, p2, ch, w, h):
        pts = sorted((p0, p1, p2), key=lambda p: p[1])
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts

        if y0 == y2:
            return

        total_dy = float(y2 - y0)

        for y in range(max(0, y0), min(h, y2 + 1)):
            t_long = (y - y0) / total_dy
            xa = x0 + t_long * (x2 - x0)
            za = z0 + t_long * (z2 - z0)

            if y < y1:
                dy_short = float(y1 - y0) if y1 != y0 else 1.0
                t = (y - y0) / dy_short
                xb = x0 + t * (x1 - x0)
                zb = z0 + t * (z1 - z0)
            else:
                dy_short = float(y2 - y1) if y2 != y1 else 1.0
                t = (y - y1) / dy_short
                xb = x1 + t * (x2 - x1)
                zb = z1 + t * (z2 - z1)

            if xa > xb:
                xa, xb = xb, xa
                za, zb = zb, za

            ix0 = max(0, int(xa))
            ix1 = min(w - 1, int(xb))
            if ix0 > ix1:
                continue

            dx = xb - xa
            for x in range(ix0, ix1 + 1):
                zt = za + (zb - za) * ((x - xa) / dx) if dx > 0.5 else min(za, zb)
                if zt < zbuf[y][x]:
                    zbuf[y][x] = zt
                    cbuf[y][x] = ch

    @staticmethod
    def _empty(w, h):
        lines = [' ' * w] * h
        msg = "No 3D model"
        if h > 0 and w > len(msg):
            r = h // 2
            c = (w - len(msg)) // 2
            lines[r] = ' ' * c + msg + ' ' * (w - c - len(msg))
        return lines
