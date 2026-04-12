"""Curses-based TUI for browsing and managing KiCad components."""

import curses
import subprocess
import time

from .jlcpcb import run_jlc2kicad
from .library import find_wrl, parse_components
from .project import (
    create_library,
    discover_libraries,
    is_lib_in_project,
    lib_dir,
    toggle_lib_in_project,
)
from .renderer import Renderer3D, parse_wrl


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
                    create_library(name)
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
            elif key == 27:
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
