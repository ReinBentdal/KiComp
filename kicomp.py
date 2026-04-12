#!/usr/bin/env python3
"""KiComp - KiCad Component Manager TUI

Browse, add, and update JLCPCB components in your KiCad library.
3D model preview spins in ASCII. Copy symbol/footprint names to clipboard.

Run in any KiCad project directory that has a lib/ folder.

Keys:
  j/k or Up/Down  Navigate component list
  a                Add component by LCSC code
  u                Update selected component from JLCPCB
  s                Copy symbol name to clipboard
  f                Copy footprint name to clipboard
  l                Switch / create library
  q                Quit
"""

import curses
import locale
import math
import re
import subprocess
import time
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────

PROJECT_DIR = Path.cwd()
LIB_DIR = PROJECT_DIR / "lib"
SYM_LIB_TABLE = PROJECT_DIR / "sym-lib-table"
FP_LIB_TABLE = PROJECT_DIR / "fp-lib-table"

SHADE = " .,:;=+*#%@"


# ── Library discovery ──────────────────────────────────────────

def discover_libraries():
    """Return sorted list of library names found under lib/symbol/."""
    sym_dir = LIB_DIR / "symbol"
    if not sym_dir.is_dir():
        return []
    return sorted(p.stem for p in sym_dir.glob("*.kicad_sym"))


# ── KiCad project lib-table management ────────────────────────

def _sym_uri_for(name):
    return f'${{KIPRJMOD}}/lib/symbol/{name}.kicad_sym'


def _fp_uri_for(name):
    return f'${{KIPRJMOD}}/lib/{name}'


def _find_entry_by_uri(path, uri):
    """Find a lib entry by URI. Returns the name field if found, else None."""
    if not path.exists():
        return None
    content = path.read_text()
    for m in re.finditer(
        r'\(lib\s+\(name\s+"([^"]+)"\).*?\(uri\s+"([^"]+)"\)', content
    ):
        if m.group(2) == uri:
            return m.group(1)
    return None


def _add_to_lib_table(path, tag, name, uri):
    """Add a library entry to a lib-table file, creating it if needed."""
    entry = f'  (lib (name "{name}")(type "KiCad")(uri "{uri}")(options "")(descr ""))\n'
    if not path.exists():
        path.write_text(f'({tag}\n  (version 7)\n{entry})\n')
        return
    content = path.read_text()
    idx = content.rfind(')')
    if idx == -1:
        return
    content = content[:idx] + entry + content[idx:]
    path.write_text(content)


def _remove_from_lib_table(path, entry_name):
    """Remove a library entry by its name field."""
    if not path.exists():
        return
    content = path.read_text()
    content = re.sub(
        r'\s*\(lib\s+\(name\s+"' + re.escape(entry_name) + r'"\)[^)]*\)\)',
        '', content,
    )
    path.write_text(content)


def is_lib_in_project(name):
    """Check if library's symbol file is referenced in sym-lib-table (by URI)."""
    return _find_entry_by_uri(SYM_LIB_TABLE, _sym_uri_for(name)) is not None


def toggle_lib_in_project(name):
    """Toggle library in/out of KiCad project tables. Returns new state."""
    sym_entry = _find_entry_by_uri(SYM_LIB_TABLE, _sym_uri_for(name))
    if sym_entry is not None:
        _remove_from_lib_table(SYM_LIB_TABLE, sym_entry)
        fp_entry = _find_entry_by_uri(FP_LIB_TABLE, _fp_uri_for(name))
        if fp_entry is not None:
            _remove_from_lib_table(FP_LIB_TABLE, fp_entry)
        return False
    else:
        _add_to_lib_table(SYM_LIB_TABLE, 'sym_lib_table', name, _sym_uri_for(name))
        fp_dir = LIB_DIR / name
        if fp_dir.is_dir():
            _add_to_lib_table(FP_LIB_TABLE, 'fp_lib_table', name, _fp_uri_for(name))
        return True


# ── Library Parsing ────────────────────────────────────────────

def parse_components(lib_name):
    """Parse .kicad_sym for *lib_name* and return list of component dicts."""
    sym_file = LIB_DIR / "symbol" / f"{lib_name}.kicad_sym"
    fp_dir = LIB_DIR / lib_name
    pkg_dir = fp_dir / "packages3d"

    if not sym_file.exists():
        return []

    content = sym_file.read_text()
    components = []

    all_names = re.findall(r'\(symbol\s+"([^"]+)"', content)
    top_names = [n for n in all_names if not re.search(r'_\d+_\d+$', n)]

    for name in top_names:
        marker = f'(symbol "{name}"'
        idx = content.find(marker)
        if idx == -1:
            continue

        depth, i = 0, idx
        while i < len(content):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        sym = content[idx:i + 1]

        comp = {
            'name': name, 'lcsc': '', 'footprint': '',
            'reference': 'U', 'pins': 0, 'pin_info': [],
            'step': '',
        }

        for m in re.finditer(r'\(property\s+"(\w+)"\s+"([^"]*)"', sym):
            key, val = m.group(1), m.group(2)
            if key == 'Footprint':
                comp['footprint'] = val
            elif key == 'LCSC':
                comp['lcsc'] = val
            elif key == 'ki_keywords' and not comp['lcsc']:
                comp['lcsc'] = val
            elif key == 'Reference':
                comp['reference'] = val

        # Find STEP file
        fp_name = comp['footprint'].split(':')[1] if ':' in comp['footprint'] else comp['footprint']
        if fp_name and pkg_dir.is_dir():
            step = pkg_dir / f"{fp_name}.step"
            if step.exists():
                comp['step'] = step.name

        pins = []
        for pm in re.finditer(
            r'\(pin\s+\w+\s+\w+.*?\(name\s+"([^"]+)".*?\(number\s+"([^"]+)"',
            sym, re.DOTALL,
        ):
            pins.append((pm.group(2), pm.group(1)))
        pins.sort(key=lambda p: int(p[0]) if p[0].isdigit() else 0)
        comp['pins'] = len(pins)
        comp['pin_info'] = pins
        components.append(comp)

    return components


def find_wrl(component, lib_name):
    """Locate the .wrl 3D model for a component."""
    fp = component.get('footprint', '')
    fp_name = fp.split(':')[1] if ':' in fp else fp
    if not fp_name:
        return None
    path = LIB_DIR / lib_name / "packages3d" / f"{fp_name}.wrl"
    return path if path.exists() else None


# ── VRML Parsing ───────────────────────────────────────────────

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


# ── 3D ASCII Renderer ─────────────────────────────────────────

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


# ── JLC2KiCadLib wrapper ───────────────────────────────────────

def run_jlc2kicad(lcsc_code, lib_name):
    """Download/update a component.  Returns (ok, output_text)."""
    cmd = [
        'JLC2KiCadLib', lcsc_code,
        '-dir', str(LIB_DIR),
        '-symbol_lib', lib_name,
        '-footprint_lib', lib_name,
        '-models', 'STEP', 'WRL',
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, "Timed out"
    except FileNotFoundError:
        return False, "JLC2KiCadLib not found"


# ── TUI ────────────────────────────────────────────────────────

class KiCompTUI:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.lib_name = None
        self.components = []
        self.sel = 0
        self.angle = 0.0
        self.renderer = None
        self.spin_start = 0.0
        self.cached_frame = None
        self.msg = ""
        self.msg_time = 0.0
        self.msg_err = False

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(5, curses.COLOR_RED, -1)
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)
        stdscr.timeout(80)

        self._quit = False

        libs = discover_libraries()
        if len(libs) == 1:
            self.lib_name = libs[0]
            self._reload()
        else:
            if self._library_menu() == 'quit':
                self._quit = True

    # ── data ──

    def _reload(self):
        if not self.lib_name:
            self.components = []
        else:
            self.components = parse_components(self.lib_name)
        if self.sel >= len(self.components):
            self.sel = max(0, len(self.components) - 1)
        self._load_model()

    def _load_model(self):
        if not self.components or not self.lib_name:
            self.renderer = None
            self.spin_start = time.time()
            self.cached_frame = None
            self.angle = 0.0
            return
        wrl = find_wrl(self.components[self.sel], self.lib_name)
        if wrl:
            try:
                v, f = parse_wrl(wrl)
                self.renderer = Renderer3D(v, f)
            except Exception:
                self.renderer = None
        else:
            self.renderer = None
        self.spin_start = time.time()
        self.cached_frame = None
        self.angle = 0.0

    def _flash(self, text, err=False):
        self.msg = text
        self.msg_time = time.time()
        self.msg_err = err

    # ── drawing ──

    def _safe(self, row, col, text, attr=0):
        h, w = self.scr.getmaxyx()
        if row < 0 or row >= h or col >= w:
            return
        try:
            self.scr.addnstr(row, col, text, w - col - 1, attr)
        except curses.error:
            pass

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 12 or w < 50:
            self._safe(0, 0, "Terminal too small (need 50x12)")
            self.scr.noutrefresh()
            return

        list_w = max(30, w * 2 // 5)
        det_col = list_w + 2
        det_w = w - list_w - 3

        lib_label = self.lib_name or "no library"
        title = f" KiComp [{lib_label}]  {len(self.components)} component{'s' if len(self.components) != 1 else ''} "
        self._safe(0, max(0, (w - len(title)) // 2), title,
                   curses.color_pair(1) | curses.A_BOLD)

        self._safe(1, 0, "\u2500" * w, curses.color_pair(1))

        for r in range(2, h - 2):
            self._safe(r, list_w, "\u2502", curses.color_pair(1))

        self._draw_list(2, 0, h - 4, list_w)
        self._draw_details(2, det_col, det_w)

        preview_top = 12
        preview_h = h - preview_top - 3
        if preview_h >= 4:
            self._draw_3d(preview_top, det_col, preview_h, det_w)

        bar = " a:Add  u:Update  s:Symbol  f:Footprint  Esc:Libraries  q:Quit "
        self._safe(h - 2, 0, bar.center(w), curses.color_pair(4))

        if self.msg and time.time() - self.msg_time < 3.0:
            cp = curses.color_pair(5) if self.msg_err else curses.color_pair(3)
            self._safe(h - 1, 1, self.msg[:w - 2], cp | curses.A_BOLD)

        self.scr.noutrefresh()

    def _draw_list(self, top, left, height, width):
        self._safe(top, left + 1, "Components", curses.A_BOLD | curses.A_UNDERLINE)

        if not self.lib_name:
            self._safe(top + 2, left + 2, "No library selected.", curses.A_DIM)
            self._safe(top + 3, left + 2, "Press 'l' to choose one.", curses.A_DIM)
            return

        if not self.components:
            self._safe(top + 2, left + 2, "Empty library.", curses.A_DIM)
            self._safe(top + 3, left + 2, "Press 'a' to add a part.", curses.A_DIM)
            return

        avail = height - 1
        n = len(self.components)
        if n <= avail:
            start = 0
        else:
            start = max(0, min(self.sel - avail // 2, n - avail))

        for i in range(start, min(start + avail, n)):
            row = top + 1 + (i - start)
            comp = self.components[i]
            is_sel = i == self.sel
            name = comp['name']
            lcsc = comp.get('lcsc', '')

            max_name = width - 15
            if len(name) > max_name:
                name = name[:max_name - 1] + "\u2026"

            arrow = "\u25b6" if is_sel else " "
            line = f" {arrow} {name}"
            if lcsc:
                pad = width - len(line) - len(lcsc) - 1
                if pad > 0:
                    line += " " * pad + lcsc

            attr = curses.color_pair(2) | curses.A_BOLD if is_sel else curses.A_NORMAL
            self._safe(row, left, line[:width], attr)

    def _draw_details(self, top, col, width):
        if not self.components:
            return
        comp = self.components[self.sel]

        self._safe(top, col, "Details", curses.A_BOLD | curses.A_UNDERLINE)

        fields = [
            ("Name",      comp['name']),
            ("LCSC",      comp.get('lcsc', '-')),
            ("Footprint", comp.get('footprint', '-')),
            ("Type",      comp.get('reference', '-')),
            ("Pins",      str(comp.get('pins', 0))),
            ("3D Model",  comp.get('step', '-') or '-'),
        ]
        for i, (label, val) in enumerate(fields):
            text = f" {label}: {val}"
            if len(text) > width:
                text = text[:width - 1] + "\u2026"
            self._safe(top + 1 + i, col, text)

        pin_info = comp.get('pin_info', [])
        if pin_info:
            self._safe(top + 8, col, " Pins:", curses.A_DIM)
            pin_str = "  ".join(f"{n}:{nm}" for n, nm in pin_info[:6])
            if len(pin_info) > 6:
                pin_str += f"  (+{len(pin_info) - 6})"
            if len(pin_str) > width - 2:
                pin_str = pin_str[:width - 3] + "\u2026"
            self._safe(top + 9, col + 1, pin_str, curses.A_DIM)

    def _draw_3d(self, top, col, height, width):
        self._safe(top, col, "3D Preview", curses.A_BOLD | curses.A_UNDERLINE)
        pw = min(width, 60)
        ph = height - 1

        if self.renderer and ph >= 3 and pw >= 10:
            spinning = (time.time() - self.spin_start) < 60
            if spinning:
                lines = self.renderer.render(pw, ph, self.angle)
                self.cached_frame = lines
            elif self.cached_frame:
                lines = self.cached_frame
            else:
                lines = self.renderer.render(pw, ph, self.angle)
                self.cached_frame = lines
            for i, line in enumerate(lines):
                self._safe(top + 1 + i, col, line[:width], curses.color_pair(6))
        else:
            self._safe(top + ph // 2, col + (width - 14) // 2,
                       "No 3D model", curses.A_DIM)

    # ── input dialog ──

    def _input_dialog(self, prompt):
        h, w = self.scr.getmaxyx()
        bw = min(52, w - 4)
        bh = 5
        by = h // 2 - 2
        bx = (w - bw) // 2

        win = curses.newwin(bh, bw, by, bx)
        win.bkgd(' ', curses.color_pair(1))
        win.box()
        try:
            win.addnstr(1, 2, prompt, bw - 4)
            win.addnstr(3, 2, "Enter=OK  Esc=Cancel", bw - 4, curses.A_DIM)
        except curses.error:
            pass
        win.refresh()

        curses.echo()
        curses.curs_set(1)
        self.scr.timeout(-1)

        buf = ""
        while True:
            try:
                win.move(2, 2)
                win.clrtoeol()
                win.box()
                win.addnstr(2, 2, buf, bw - 4)
                win.refresh()
                ch = win.getch()
            except curses.error:
                break

            if ch == 27:
                buf = ""
                break
            elif ch in (10, 13):
                break
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif 32 <= ch < 127:
                if len(buf) < bw - 6:
                    buf += chr(ch)

        curses.noecho()
        curses.curs_set(0)
        self.scr.timeout(80)
        del win
        self.scr.touchwin()
        return buf.strip()

    # ── library menu ──

    def _library_menu(self):
        """Show library picker. Returns 'quit' to exit program, else None."""
        libs = discover_libraries()
        sel = 0
        if self.lib_name and self.lib_name in libs:
            sel = libs.index(self.lib_name)

        self.scr.timeout(-1)
        result = None

        while True:
            self.scr.erase()
            h, w = self.scr.getmaxyx()

            title = " Libraries "
            self._safe(0, max(0, (w - len(title)) // 2), title,
                       curses.color_pair(1) | curses.A_BOLD)
            self._safe(1, 0, "\u2500" * w, curses.color_pair(1))

            if not libs:
                self._safe(3, 2, "No libraries found.", curses.A_DIM)
                self._safe(4, 2, "Press 'n' to create one.", curses.A_DIM)
            else:
                for i, name in enumerate(libs):
                    arrow = "\u25b6" if i == sel else " "
                    check = "\u2713" if is_lib_in_project(name) else " "
                    attr = curses.color_pair(2) | curses.A_BOLD if i == sel else curses.A_NORMAL
                    self._safe(3 + i, 2, f" {arrow} [{check}] {name}", attr)

            bar = " Enter:Select  t:Toggle in project  n:New  Esc:Back  q:Quit "
            self._safe(h - 2, 0, bar.center(w), curses.color_pair(4))

            self.scr.refresh()
            key = self.scr.getch()

            if key == ord('q'):
                result = 'quit'
                break
            elif key == 27:  # Esc
                if not self.lib_name:
                    result = 'quit'
                break
            elif key in (10, 13) and libs:
                self.lib_name = libs[sel]
                self.sel = 0
                self._reload()
                break
            elif key in (curses.KEY_UP, ord('k')) and libs:
                sel = (sel - 1) % len(libs)
            elif key in (curses.KEY_DOWN, ord('j')) and libs:
                sel = (sel + 1) % len(libs)
            elif key == ord('t') and libs:
                toggle_lib_in_project(libs[sel])
            elif key == ord('n'):
                self.scr.timeout(80)
                name = self._input_dialog("New library name:")
                self.scr.timeout(-1)
                if name:
                    sym_dir = LIB_DIR / "symbol"
                    sym_dir.mkdir(parents=True, exist_ok=True)
                    sym_file = sym_dir / f"{name}.kicad_sym"
                    if not sym_file.exists():
                        sym_file.write_text(
                            '(kicad_symbol_lib (version 20210201) (generator kicomp)\n)\n'
                        )
                    if not is_lib_in_project(name):
                        toggle_lib_in_project(name)
                    libs = discover_libraries()
                    if name in libs:
                        sel = libs.index(name)

        self.scr.timeout(80)
        self.scr.touchwin()
        return result

    # ── actions ──

    def _add(self):
        if not self.lib_name:
            self._flash("Select a library first (l)", err=True)
            return
        lcsc = self._input_dialog("LCSC Part # (e.g. C17548754):")
        if not lcsc:
            return
        self._flash(f"Downloading {lcsc} ...")
        self.draw()
        curses.doupdate()

        ok, out = run_jlc2kicad(lcsc, self.lib_name)
        if ok:
            self._reload()
            for i, c in enumerate(self.components):
                if c.get('lcsc') == lcsc:
                    self.sel = i
                    self._load_model()
                    break
            self._flash(f"Added {lcsc}")
        else:
            self._flash(f"Failed: {out[:60]}", err=True)

    def _update(self):
        if not self.components:
            return
        comp = self.components[self.sel]
        lcsc = comp.get('lcsc', '')
        if not lcsc:
            self._flash("No LCSC code", err=True)
            return
        self._flash(f"Updating {comp['name']} ...")
        self.draw()
        curses.doupdate()

        ok, out = run_jlc2kicad(lcsc, self.lib_name)
        if ok:
            self._reload()
            self._flash(f"Updated {comp['name']}")
        else:
            self._flash(f"Failed: {out[:60]}", err=True)

    def _clip(self, text):
        try:
            p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            p.communicate(text.encode())
            return True
        except Exception:
            return False

    def _copy_symbol(self):
        if not self.components or not self.lib_name:
            return
        comp = self.components[self.sel]
        ref = f"{self.lib_name}:{comp['name']}"
        if self._clip(ref):
            self._flash(f"Copied: {ref}")
        else:
            self._flash("Clipboard failed", err=True)

    def _copy_footprint(self):
        if not self.components:
            return
        comp = self.components[self.sel]
        fp = comp.get('footprint', '')
        if not fp:
            self._flash("No footprint", err=True)
            return
        if self._clip(fp):
            self._flash(f"Copied: {fp}")
        else:
            self._flash("Clipboard failed", err=True)

    # ── main loop ──

    def run(self):
        if self._quit:
            return

        prev_sel = -1
        while True:
            if self.sel != prev_sel:
                self._load_model()
                prev_sel = self.sel

            self.draw()
            curses.doupdate()
            if (time.time() - self.spin_start) < 60:
                self.angle += 0.04

            key = self.scr.getch()
            if key == ord('q'):
                break
            elif key == 27:  # Esc → back to library menu
                if self._library_menu() == 'quit':
                    break
                prev_sel = -1
            elif key in (curses.KEY_UP, ord('k')):
                if self.components:
                    self.sel = (self.sel - 1) % len(self.components)
            elif key in (curses.KEY_DOWN, ord('j')):
                if self.components:
                    self.sel = (self.sel + 1) % len(self.components)
            elif key == ord('a'):
                self._add()
            elif key == ord('u'):
                self._update()
            elif key == ord('s'):
                self._copy_symbol()
            elif key == ord('f'):
                self._copy_footprint()
            elif key == ord('l'):
                if self._library_menu() == 'quit':
                    break
                prev_sel = -1


# ── Entry point ────────────────────────────────────────────────

def main():
    locale.setlocale(locale.LC_ALL, '')
    curses.wrapper(lambda stdscr: KiCompTUI(stdscr).run())


if __name__ == "__main__":
    main()
