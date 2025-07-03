# IFC Convert Tool

A command-line tool for detecting clashes in IFC (Industry Foundation Classes) files.

## Installation

1. Clone this repository
2. Run docker compose to start the container and the tool

```bash
docker compose up
```

### Options

- `--ifc-path`: Path to your IFC files (required)
- `--output`: output

### Example

```bash
#To export obj
python3 main.py --ifc-path data/ifc/test1.ifc --output outputs/file.obj
#To export fbx
python3 main.py --ifc-path data/ifc/test1.ifc --output outputs/file.fbx
```
