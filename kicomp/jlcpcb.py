"""JLC2KiCadLib subprocess wrapper."""

import subprocess

from .project import lib_dir


def run_jlc2kicad(lcsc_code, lib_name):
    """Download/update a component. Returns (ok, output_text)."""
    cmd = [
        'JLC2KiCadLib', lcsc_code,
        '-dir', str(lib_dir()),
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
