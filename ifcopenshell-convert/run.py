
import bpy
if not bpy.context.preferences.addons.get('bonsai'):
    bpy.ops.preferences.addon_enable(module='bonsai')
    bpy.ops.wm.save_userpref()

try:
    bpy.ops.bim.load_project(filepath='data/ifc/test1.ifc')
except Exception as e:
    print(f'Error importing IFC model: {e}')


        
bpy.ops.export_scene.fbx(
    filepath='outputs/file.fbx',
    use_selection=False,        # Set True to export selected objects only
    apply_unit_scale=True,      # Respects Blender's unit setup
    global_scale=1.0,           # Adjust scale if needed
    axis_forward='-Z',          # Common for game engines (e.g., Unity/Unreal)
    axis_up='Y'
)
