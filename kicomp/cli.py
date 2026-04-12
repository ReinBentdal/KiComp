"""CLI entry point."""

import curses
import locale

from .tui import KiCompTUI


def main():
    locale.setlocale(locale.LC_ALL, '')
    curses.wrapper(lambda stdscr: KiCompTUI(stdscr).run())
