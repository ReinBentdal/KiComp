"""KiCad project paths, library discovery, and lib-table management."""

import re
from pathlib import Path


def project_dir():
    return Path.cwd()


def lib_dir():
    return project_dir() / "lib"


def sym_lib_table_path():
    return project_dir() / "sym-lib-table"


def fp_lib_table_path():
    return project_dir() / "fp-lib-table"


# ── Library discovery ──────────────────────────────────────────

def discover_libraries():
    """Return sorted list of library names found under lib/symbol/."""
    sym_dir = lib_dir() / "symbol"
    if not sym_dir.is_dir():
        return []
    return sorted(p.stem for p in sym_dir.glob("*.kicad_sym"))


# ── lib-table read/write ──────────────────────────────────────

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
    """Check if library's symbol file is referenced in sym-lib-table."""
    return _find_entry_by_uri(sym_lib_table_path(), _sym_uri_for(name)) is not None


def toggle_lib_in_project(name):
    """Toggle library in/out of KiCad project tables. Returns new active state."""
    sym_entry = _find_entry_by_uri(sym_lib_table_path(), _sym_uri_for(name))
    if sym_entry is not None:
        _remove_from_lib_table(sym_lib_table_path(), sym_entry)
        fp_entry = _find_entry_by_uri(fp_lib_table_path(), _fp_uri_for(name))
        if fp_entry is not None:
            _remove_from_lib_table(fp_lib_table_path(), fp_entry)
        return False
    else:
        _add_to_lib_table(sym_lib_table_path(), 'sym_lib_table', name, _sym_uri_for(name))
        fp_dir = lib_dir() / name
        if fp_dir.is_dir():
            _add_to_lib_table(fp_lib_table_path(), 'fp_lib_table', name, _fp_uri_for(name))
        return True


def create_library(name):
    """Create an empty symbol library file and add it to the project."""
    sym_dir = lib_dir() / "symbol"
    sym_dir.mkdir(parents=True, exist_ok=True)
    sym_file = sym_dir / f"{name}.kicad_sym"
    if not sym_file.exists():
        sym_file.write_text(
            '(kicad_symbol_lib (version 20210201) (generator kicomp)\n)\n'
        )
    if not is_lib_in_project(name):
        toggle_lib_in_project(name)
