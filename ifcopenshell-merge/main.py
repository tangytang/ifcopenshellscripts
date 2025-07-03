import ifcpatch
import ifcopenshell
import click
import os


@click.command()
@click.argument("input_files", nargs=-1, type=click.Path(exists=True))
@click.option("--output", "-o", required=True, type=click.Path(), help="Path to save merged IFC file.")
def merge_ifc(input_files, output):
    """
    Merge multiple IFC files into a single IFC file using ifcpatch MergeProjects.
    """

    if not input_files:
        click.echo("❌ No IFC files provided.")
        return

    if len(input_files) < 2:
        click.echo("❌ At least 2 IFC files are required for merging.")
        return

    click.echo(f"🔄 Merging {len(input_files)} IFC files using ifcpatch...")

    # Use the last file as the base
    base_file_path = input_files[-1]
    click.echo(f"📁 Using {os.path.basename(base_file_path)} as base file")

    ifc_file = ifcopenshell.open(base_file_path)

    # Merge other files in reverse order (excluding the last one)
    files_to_merge = list(input_files)

    ifc_file = ifcpatch.execute({
        "input": "input.ifc",  # This is just a placeholder name
        "file": ifc_file,
        "recipe": "MergeProjects",
        "arguments": [files_to_merge],
    })

    # Write the final merged file
    try:
        ifc_file.write(output)
        click.echo(f"✅ Merged IFC saved as: {output}")

        # Show some stats
        products = ifc_file.by_type("IfcProduct")
        click.echo(f"📊 Total products in merged file: {len(products)}")

    except Exception as e:
        click.echo(f"❌ Failed to write merged file: {e}")


if __name__ == "__main__":
    merge_ifc()
