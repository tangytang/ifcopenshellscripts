# Potree Tool

A command-line tool for converting point cloud files (.las/.laz) to Potree format for web visualization.

## Installation

1. Clone this repository
2. Run docker compose to start the container and run the conversion

```bash
docker compose up
```

### Example

```bash
python3 main.py --input_file_path ./data/test.las --method poisson
```
