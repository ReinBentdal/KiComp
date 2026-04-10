# KiComp

Terminal UI for managing KiCad components from JLCPCB. Browse your library, add/update parts by LCSC code, preview 3D models in ASCII.

![screenshot](screenshot.png)

## Install

```
pip install -e .
```

Requires [JLC2KiCadLib](https://github.com/TousstNicolas/JLC2KiCad_lib) for downloading components.

## Usage

Run `kicomp` in a KiCad project directory containing a `lib/` folder.

| Key | Action |
|-----|--------|
| `j`/`k` | Navigate |
| `a` | Add component by LCSC code |
| `u` | Update selected component |
| `s` | Copy symbol name |
| `f` | Copy footprint name |
| `q` | Quit |
