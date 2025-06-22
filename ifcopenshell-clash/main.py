import click
import json
from pathlib import Path
import subprocess


@click.command()
@click.option('--file-a', required=True, help='Path to first IFC file')
@click.option('--file-b', required=True, help='Path to second IFC file')
@click.option('--selector-a', default='', help='Selector for file A (default: all elements)')
@click.option('--selector-b', default='', help='Selector for file B (default: all elements)')
@click.option('--mode', default='intersection', help='Clash detection mode (default: intersection)')
@click.option('--tolerance', default=0.01, type=float, help='Tolerance value (default: 0.01)')
@click.option('--check-all', default=True, type=bool, help='Check all elements (default: True)')
def create_clash_set(file_a, file_b, selector_a, selector_b, mode, tolerance, check_all):
    """Create a clash sets JSON file for IFC clash detection."""

    clash_set = {
        "name": "Clash Set A",
        "a": [
            {
                "file": f'../{file_a}',
                "selector": selector_a
            }
        ],
        "b": [
            {
                "file": f'../{file_b}',
                "selector": selector_b
            }
        ],
        "mode": mode,
        "tolerance": tolerance,
        "check_all": check_all
    }

    # Write the JSON file
    with open('clash_sets.json', 'w') as f:
        json.dump([clash_set], f, indent=2)

    # Get the absolute path to the ifcclash directory
    # Adjust this path to your ifcclash directory
    ifcclash_dir = Path(__file__).parent / 'ifcclash'

    result = subprocess.run(['python3', '-m', 'ifcclash', '../clash_sets.json', '--output', '../output.json'],
                            capture_output=True,
                            text=True,
                            cwd=str(ifcclash_dir))
    # Check if command was successful
    if result.returncode == 0:
        print("Command succeeded!")
        print(result.stdout)
    else:
        print("Command failed!")
        print(result.stderr)


if __name__ == '__main__':
    create_clash_set()
