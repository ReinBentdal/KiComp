"""Parse KiCad symbol libraries and locate 3D model files."""

import re

from .project import lib_dir


def parse_components(lib_name):
    """Parse .kicad_sym for *lib_name* and return list of component dicts."""
    sym_file = lib_dir() / "symbol" / f"{lib_name}.kicad_sym"
    fp_dir = lib_dir() / lib_name
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
    path = lib_dir() / lib_name / "packages3d" / f"{fp_name}.wrl"
    return path if path.exists() else None
