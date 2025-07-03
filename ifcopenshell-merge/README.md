# IFC Merge Tool

A command-line tool for detecting clashes in IFC (Industry Foundation Classes) files.

## Installation

1. Clone this repository
2. Run docker compose to start the container and the tool

```bash
docker compose up
```

### Options

- `input_files`: Path to your IFC files (required)
- `-o`: output

### Example

```bash
python3 main.py data/ifc/test1.ifc data/ifc/test2.ifc -o outputs/merged.ifc
```
