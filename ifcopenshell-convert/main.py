import subprocess
import click


def convert_ifc_logic(ifc_path, output):

    content = f"""
import bpy
if not bpy.context.preferences.addons.get('bonsai'):
    bpy.ops.preferences.addon_enable(module='bonsai')
    bpy.ops.wm.save_userpref()

try:
    bpy.ops.bim.load_project(filepath='{ifc_path}')
except Exception as e:
    print(f'Error importing IFC model: {{e}}')


        """
    if output.endswith('.obj'):
        content += f"""
bpy.ops.wm.obj_export(filepath='{output}')
"""
    elif output.endswith('.fbx'):
        content += f"""
bpy.ops.export_scene.fbx(
    filepath='{output}',
    use_selection=False,        # Set True to export selected objects only
    apply_unit_scale=True,      # Respects Blender's unit setup
    global_scale=1.0,           # Adjust scale if needed
    axis_forward='-Z',          # Common for game engines (e.g., Unity/Unreal)
    axis_up='Y'
)
"""
    with open('run.py', 'w') as f:
        f.write(content)
    try:
        subprocess.run(['blender', '-b', '-P', 'run.py'])
    except Exception as e:
        print(f'Error importing IFC model: {e}')
        return False
    return True


@click.command()
@click.option('--ifc-path', type=click.Path(exists=True), required=True, help='Path to the input IFC file.')
@click.option('--output', type=click.Path(), required=True, help='Path to the output directory.')
def convert_ifc(ifc_path, output):
    # Delegate conversion logic to our converter module
    result = convert_ifc_logic(ifc_path, output)
    if result:
        click.echo('Conversion completed successfully.')
    else:
        click.echo('Conversion failed.')


if __name__ == '__main__':
    convert_ifc()
