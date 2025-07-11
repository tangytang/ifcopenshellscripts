# IFC Clash Detection Tool

A command-line tool for detecting clashes in IFC (Industry Foundation Classes) files.

## Installation

1. Clone this repository
2. Run docker compose to start the container and the tool

```bash
docker compose up
```

### Options

- `--file-a`: Path to your IFC file (required)
- `--file-b`: Path to your IFC file (required)
- `--selector-a`: Selector for file A (default: all elements)
- `--selector-b`: Selector for file B (default: all elements)
- `--mode`: Clash detection mode (default: intersection)
- `--tolerance`: Clash detection tolerance in meters (default: 0.01)
- `--check-all`: Check all elements (default: True)

### Example

```bash
python3 main.py --file-a test1.ifc --file-b test2.ifc --selector-b IfcWall --mode intersection --tolerance 0.01 --check-all True
```

### Results

The results will be saved in the `output.json` file.

```json
[
  {
    "name": "Clash Set A",
    "a": [
      {
        "file": "../test1.ifc",
        "selector": ""
      }
    ],
    "b": [
      {
        "file": "../test2.ifc",
        "selector": "IfcWall"
      }
    ],
    "mode": "intersection",
    "tolerance": 0.01,
    "check_all": true,
    "clashes": {
      "32ZzzEy_X1peUnHIWvlyL8-21hdCoPOb0VQJppqAio$IV": {
        "a_global_id": "32ZzzEy_X1peUnHIWvlyL8",
        "b_global_id": "21hdCoPOb0VQJppqAio$IV",
        "a_ifc_class": "IfcWallStandardCase",
        "b_ifc_class": "IfcWallStandardCase",
        "a_name": "Basic Wall:Generic - 8\":1357164",
        "b_name": "Basic Wall:Generic - 8\":1357410",
        "type": "pierce",
        "p1": [2.378772155173652, 2.0064220970262663, 5.412403743570689],
        "p2": [2.1414949624291832, 2.039769231750415, 5.331704474049742],
        "distance": 0.2032000000000016
      }
    }
  }
]
```
