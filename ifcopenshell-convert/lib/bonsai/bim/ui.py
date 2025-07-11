# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.

import os
import bpy
import platform
import bonsai.bim.helper
from pathlib import Path
from bpy.types import Panel
from bpy.props import StringProperty, IntProperty, BoolProperty
from ifcopenshell.util.doc import (
    get_entity_doc,
    get_property_set_doc,
    get_type_doc,
    get_attribute_doc,
)
from . import ifc
from bonsai import get_debug_info
import bonsai.bim
import bonsai.tool as tool
from ifcopenshell.util.file import IfcHeaderExtractor
from bonsai.bim.prop import Attribute
from typing import Optional, TYPE_CHECKING


class IFCFileSelector:
    # Avoid overriding blender prop annotations at runtime.
    if TYPE_CHECKING:
        filepath: str
        use_relative_path: bool

    def is_existing_ifc_file(self, filepath: Optional[str] = None) -> bool:
        """Check if file path exists and if it's an IFC file.

        If `filepath` is not provided, will use filepath property from the current operator.
        """
        if filepath is None:
            path = self.get_filepath_abs()
        else:
            path = Path(filepath)
        return path.exists() and path.is_file() and "ifc" in path.suffix.lower()

    def get_filepath_abs(self) -> Path:
        # self.filepath filled by fileselect_add is absolute
        # but we support relative paths provided by custom scripts.
        filepath = Path(self.filepath)
        if not filepath.is_absolute():
            filepath = Path(bpy.path.abspath("//")) / filepath
        return filepath

    def get_filepath(self) -> str:
        """get filepath taking into account relative paths"""
        filepath = self.get_filepath_abs()

        if self.use_relative_path:
            filepath = filepath.relative_to(bpy.path.abspath("//"))
        return filepath.as_posix().replace("\\", "/")

    def draw(self, context: bpy.types.Context) -> None:
        assert isinstance(context.space_data, bpy.types.SpaceFileBrowser)
        # Access filepath & Directory https://blender.stackexchange.com/a/207665
        params = context.space_data.params
        # Decode byte string https://stackoverflow.com/a/47737082/
        directory = Path(params.directory.decode("utf-8"))
        filepath = os.path.join(directory, params.filename)
        layout = self.layout
        if self.is_existing_ifc_file(filepath):
            box = layout.box()
            box.label(text="IFC Header Specifications", icon="INFO")
            header_data = IfcHeaderExtractor(filepath).extract()
            for key, value in header_data.items():
                if value != "":
                    split = box.split()
                    split.label(text=key.title().replace("_", " "))
                    split.label(text=str(value))
                    if key.lower() == "schema_name" and filepath[-4:].lower() == ".ifc":
                        schema_lower = str(value).lower()
                        if schema_lower == "ifc2x3":
                            row = box.row()
                            op = row.operator("bim.run_migrate_patch", text="Upgrade to IFC4")
                            op.infile = filepath
                            op.outfile = filepath[0:-4] + "-IFC4.ifc"
                            op.schema = "IFC4"
                        elif schema_lower == "ifc4":
                            row = box.row()
                            op = row.operator("bim.run_migrate_patch", text="Upgrade to IFC4X3")
                            op.infile = filepath
                            op.outfile = filepath[0:-4] + "-IFC4X3.ifc"
                            op.schema = "IFC4X3"

        if bpy.data.is_saved:
            layout.prop(self, "use_relative_path")
        else:
            self.use_relative_path = False
            layout.label(text="Save the .blend file first ")
            layout.label(text="to use relative paths for .ifc.")


class BIM_PT_section_plane(Panel):
    bl_label = "Temporary Section Cutaways"
    bl_idname = "BIM_PT_section_plane"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "BIM_PT_tab_sandbox"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        props = tool.Blender.get_bim_props()

        layout.prop(props, "should_section_selected_objects")
        layout.prop(props, "section_plane_colour")
        layout.prop(props, "section_line_decorator_width")

        row = layout.row(align=True)
        row.operator("bim.add_section_plane")
        row.operator("bim.remove_section_plane")


class BIM_PT_section_with_cappings(Panel):
    bl_label = "Section Cutaways With Cappings"
    bl_idname = "BIM_PT_section_with_cappings"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "BIM_PT_tab_sandbox"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        row = layout.row(align=True)
        row.operator("bim.clipping_plane_cut_with_cappings", icon="XRAY", text="Cut")
        row.operator("bim.revert_clipping_plane_cut", icon="FILE_REFRESH", text="Revert Cut")

        props = tool.Project.get_project_props()
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Clipping Planes")
        header.operator("bim.save_clipping_planes", text="", icon="EXPORT")
        header.operator("bim.load_clipping_planes", text="", icon="IMPORT")
        header.operator("bim.create_clipping_plane", text="", icon="ADD")

        box.template_list(
            "BIM_UL_clipping_plane",
            "",
            props,
            "clipping_planes",
            props,
            "clipping_planes_active",
        )

        if props.clipping_planes_active < len(props.clipping_planes):
            active_clipping_plane_obj = props.clipping_planes[props.clipping_planes_active].obj
            box.prop(active_clipping_plane_obj, "location")
            box.prop(active_clipping_plane_obj, "rotation_euler")


class BIM_UL_clipping_plane(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if item and item.obj:
            obj = item.obj
            row = layout.row(align=True)
            row.prop(obj, "name", text="", emboss=False)
            row.operator("bim.select_object", text="", icon="RESTRICT_SELECT_OFF").obj_name = obj.name
            row.context_pointer_set("active_object", obj)
            row.operator("bim.flip_clipping_plane", text="", icon="PASTEFLIPDOWN")
            row.operator("bim.delete_object", text="", icon="TRASH").obj_name = obj.name
        else:
            layout.label(text="", translate=False)


class BIM_UL_generic(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if item:
            layout.prop(item, "name", text="", emboss=False)
        else:
            layout.label(text="", translate=False)


class BIM_UL_topics(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        ob = data
        if item:
            layout.prop(item, "title", text="", emboss=False)
        else:
            layout.label(text="", translate=False)


class BIM_ADDON_preferences(bpy.types.AddonPreferences):
    bl_idname = tool.Blender.get_blender_addon_package_name()
    svg2pdf_command: StringProperty(
        name="SVG to PDF Command",
        description='print sheet to pdf together with svg. Leave blank svg is just created, E.g. [["path to application eg. /Applications/Inkscape.app/Contents/MacOS/inkscape", "svg", "-o", "pdf"]]',
    )
    svg2dxf_command: StringProperty(
        name="SVG to DXF Command",
        description='E.g. [["inkscape", "svg", "-o", "eps"], ["pstoedit", "-dt", "-f", "dxf:-polyaslines -mm", "eps", "dxf", "-psarg", "-dNOSAFER"]]',
    )
    svg_command: StringProperty(
        name="SVG Command",
        description='Software to open generated drawing and sheets. Leave blank system default for .svg is used E.g. [["path to application eg. /Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "path"]]',
    )
    layout_svg_command: StringProperty(
        name="Layout SVG Command",
        description='Software to open layouts, before generated. Leave blank system default for .svg is used E.g.  [["path to application eg. /Applications/Inkscape.app/Contents/MacOS/inkscape", "path"]]',
    )
    pdf_command: StringProperty(
        name="PDF Command",
        description='Software to open .pdf, leave blank uses system default. E.g. [["path to application eg. /Applications/Inkscape.app/Contents/MacOS/inkscape", "path"]]',
    )
    spreadsheet_command: StringProperty(name="Spreadsheet Command", description='E.g. [["libreoffice", "path"]]')
    should_hide_empty_props: BoolProperty(
        name="Hide Empty Properties",
        default=True,
        description="If disabled, this will show empty properties when displaying property sets contents",
    )
    should_setup_workspace: BoolProperty(
        name="Setup Workspace Layout for BIM",
        default=True,
        description="If enabled, this will add a default workspace dedicated to working with BIM models.\nIt is recommended to keep this `Enabled`",
    )
    activate_workspace: BoolProperty(
        name="Activate BIM Workspace on Startup",
        default=True,
        description="If enabled, this will automatically activate the BIM workspace when opening a project.\nIt is recommended to keep this `Enabled`",
    )
    should_setup_toolbar: BoolProperty(
        name="Always Show Toolbar In 3D Viewport",
        default=True,
        description="If disabled, the toolbar will only load when an IFC model is active",
    )
    should_use_snap: BoolProperty(
        name="Enable Snapping on Startup",
        default=True,
        description="If enabled, snapping will be enabled on new sessions.\nIt is recommended to keep this `Enabled`",
    )
    should_play_chaching_sound: BoolProperty(name="Play A Cha-Ching Sound When Project Costs Updates", default=False)
    tmp_dir: StringProperty(
        name="Temporary Directory",
        description="Path to create and store temporary files. If left blank, a system default will be used.",
    )
    spatial_elements_unselectable: BoolProperty(
        name="Make Spatial Elements Unselectable By Default",
        default=True,
        description="If disabled, it will be possible to select spatial elements in the 3D viewport.\nIt is recommended to keep this `Enabled`, as this can have unintended consequences",
    )
    decorations_colour: bpy.props.FloatVectorProperty(
        name="Decorations Color", subtype="COLOR", default=(1, 1, 1, 1), min=0.0, max=1.0, size=4
    )
    decorator_color_selected: bpy.props.FloatVectorProperty(
        name="Selected Elements Color",
        subtype="COLOR",
        default=(0.545, 0.863, 0, 1),  # green
        min=0.0,
        max=1.0,
        size=4,
        description="Color of selected verts/edges (used in profile editing mode)",
    )
    decorator_color_unselected: bpy.props.FloatVectorProperty(
        name="Not Selected Elements Color",
        subtype="COLOR",
        default=(1, 1, 1, 1),  # white
        min=0.0,
        max=1.0,
        size=4,
        description="Color of not selected verts/edges (used in profile editing mode)",
    )
    decorator_color_special: bpy.props.FloatVectorProperty(
        name="Special Elements Color",
        subtype="COLOR",
        default=(0.157, 0.565, 1, 1),  # blue
        min=0.0,
        max=1.0,
        size=4,
        description="Color of derived, parametric, or invisible geometry",
    )
    decorator_color_error: bpy.props.FloatVectorProperty(
        name="Warning Elements Color",
        subtype="COLOR",
        default=(1, 0.2, 0.322, 1),  # red
        min=0.0,
        max=1.0,
        size=4,
        description="Color of warning, error, or problem overlays",
    )
    decorator_color_background: bpy.props.FloatVectorProperty(
        name="Background Elements Color",
        subtype="COLOR",
        default=(0.2, 0.2, 0.2, 1),  # grey
        min=0.0,
        max=1.0,
        size=4,
        description="Color of background overlays",
    )
    opening_focus_opacity: bpy.props.IntProperty(
        default=100,
        min=0,
        max=100,
        subtype="PERCENTAGE",
        name="Non-Openings Opacity",
        description="When modifying openings, other elements of the model will display with some transparency.\n0 is fully transparent and 100 is fully opaque",
    )

    if TYPE_CHECKING:
        svg2pdf_command: str
        svg2dxf_command: str
        svg_command: str
        layout_svg_command: str
        pdf_command: str
        spreadsheet_command: str
        should_hide_empty_props: bool
        should_setup_workspace: bool
        activate_workspace: bool
        should_setup_toolbar: bool
        should_play_chaching_sound: bool
        spatial_elements_unselectable: bool
        tmp_dir: str
        decorations_colour: tuple[float, float, float, float]
        decorator_color_selected: tuple[float, float, float, float]
        decorator_color_unselected: tuple[float, float, float, float]
        decorator_color_special: tuple[float, float, float, float]
        decorator_color_error: tuple[float, float, float, float]
        decorator_color_background: tuple[float, float, float, float]
        opening_focus_opacity: int

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout

        row = layout.row()
        row.operator("bim.open_upstream", text="Visit Homepage").page = "home"
        row.operator("bim.open_upstream", text="Visit Documentation").page = "docs"
        row = layout.row()
        row.operator("bim.open_upstream", text="Visit Wiki").page = "wiki"
        row.operator("bim.open_upstream", text="Visit Community").page = "community"
        row = layout.row()
        row.operator("bim.file_associate", icon="LOCKVIEW_ON")
        row.operator("bim.file_unassociate", icon="LOCKVIEW_OFF")

        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Interface", self.draw_interface_settings)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Command Paths", self.draw_commands)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Model", self.draw_model_settings)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Colors", self.draw_decorator_colors)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Directories", self.draw_directories)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Drawing", self.draw_drawing_settings)
        bonsai.bim.helper.draw_expandable_panel(self.layout, context, "Other", self.draw_other_settings)

    def draw_commands(self, layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        layout.prop(self, "svg2pdf_command")
        layout.prop(self, "svg2dxf_command")
        layout.prop(self, "svg_command")
        layout.prop(self, "layout_svg_command")
        layout.prop(self, "pdf_command")
        layout.prop(self, "spreadsheet_command")

    def draw_interface_settings(self, layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        layout.prop(self, "should_hide_empty_props")
        layout.prop(self, "should_setup_workspace")
        layout.prop(self, "activate_workspace")
        layout.prop(self, "should_setup_toolbar")
        layout.prop(self, "should_use_snap")
        layout.prop(self, "should_play_chaching_sound")
        layout.prop(self, "spatial_elements_unselectable")

    def draw_model_settings(self, layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        props = tool.Model.get_model_props()
        layout.prop(props, "occurrence_name_style")
        if props.occurrence_name_style == "CUSTOM":
            layout.prop(props, "occurrence_name_function")

    def draw_directories(self, layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        props = tool.Blender.get_bim_props()
        row = layout.row(align=True)
        row.prop(props, "data_dir")
        row.operator("bim.select_dir", icon="FILE_FOLDER", text="").data_path = "scene.BIMProperties.data_dir"

        row = layout.row(align=True)
        row.prop(props, "cache_dir")
        row.operator("bim.select_dir", icon="FILE_FOLDER", text="").data_path = "scene.BIMProperties.cache_dir"

        row = layout.row(align=True)
        row.prop(self, "tmp_dir")
        row.operator("bim.select_dir", icon="FILE_FOLDER", text="").data_path = "preferences.tmp_dir"

    def draw_drawing_settings(self, layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
        props = tool.Blender.get_bim_props()
        layout.prop(props, "pset_dir")
        dprops = tool.Drawing.get_document_props()
        layout.prop(dprops, "sheets_dir")
        layout.prop(dprops, "layouts_dir")
        layout.prop(dprops, "titleblocks_dir")
        layout.prop(dprops, "drawings_dir")
        layout.prop(dprops, "stylesheet_path")
        layout.prop(dprops, "schedules_stylesheet_path")
        layout.prop(dprops, "markers_path")
        layout.prop(dprops, "symbols_path")
        layout.prop(dprops, "patterns_path")
        layout.prop(dprops, "shadingstyles_path")
        layout.prop(dprops, "shadingstyle_default")
        row = layout.row()
        row.prop(dprops, "drawing_font")
        row.prop(dprops, "magic_font_scale")
        layout.prop(dprops, "imperial_precision")
        layout.prop(dprops, "tolerance")
        layout.prop(dprops, "classes_to_wireframe")

    def draw_decorator_colors(self, layout, context):
        layout.row().prop(self, "decorations_colour")
        layout.row().prop(self, "decorator_color_selected")
        layout.row().prop(self, "decorator_color_unselected")
        layout.row().prop(self, "decorator_color_special")
        layout.row().prop(self, "decorator_color_error")
        layout.row().prop(self, "decorator_color_background")

    def draw_other_settings(self, layout, context):
        layout.prop(self, "opening_focus_opacity")
        props = tool.Project.get_project_props()
        layout.prop(props, "should_disable_undo_on_save")
        layout.prop(props, "should_stream")


# Scene panel groups
class BIM_PT_tabs(Panel):
    bl_label = "Bonsai"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 0
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        if not UIData.is_loaded:
            UIData.load()
        is_ifc_project = bool(tool.Ifc.get())
        aprops = tool.Blender.get_area_props(context)
        ifc_icon = f"{UIData.data['tabs_icon_color_mode']}_ifc"

        row = self.layout.row()
        row.alignment = "CENTER"
        row.operator(
            "bim.set_tab",
            text="",
            emboss=aprops.tab == "PROJECT",
            icon_value=bonsai.bim.icons[ifc_icon].icon_id,
        ).tab = "PROJECT"
        self.draw_tab_entry(row, "FILE_3D", "OBJECT", is_ifc_project, aprops.tab == "OBJECT")
        self.draw_tab_entry(row, "MATERIAL", "GEOMETRY", is_ifc_project, aprops.tab == "GEOMETRY")
        self.draw_tab_entry(row, "DOCUMENTS", "DRAWINGS", is_ifc_project, aprops.tab == "DRAWINGS")
        self.draw_tab_entry(row, "NETWORK_DRIVE", "SERVICES", is_ifc_project, aprops.tab == "SERVICES")
        self.draw_tab_entry(row, "EDITMODE_HLT", "STRUCTURE", is_ifc_project, aprops.tab == "STRUCTURE")
        self.draw_tab_entry(row, "NLA", "SCHEDULING", is_ifc_project, aprops.tab == "SCHEDULING")
        self.draw_tab_entry(row, "PACKAGE", "FM", True, aprops.tab == "FM")
        self.draw_tab_entry(row, "COMMUNITY", "QUALITY", True, aprops.tab == "QUALITY")
        row.operator("bim.switch_tab", text="", emboss=False, icon="UV_SYNC_SELECT")

        # Yes, that's right.
        row = self.layout.row()
        row.alignment = "CENTER"
        row.scale_y = 0.2
        for tab in [
            "PROJECT",
            "OBJECT",
            "GEOMETRY",
            "DRAWINGS",
            "SERVICES",
            "STRUCTURE",
            "SCHEDULING",
            "FM",
            "QUALITY",
            "SWITCH",
        ]:
            # Draw a little underscore below the active tab icon.
            if aprops.tab == tab:
                row.prop(aprops, "active_tab", text="", icon="BLANK1")
            else:
                row.prop(aprops, "inactive_tab", text="", icon="BLANK1", emboss=False)

        row = self.layout.row(align=True)
        row.prop(aprops, "tab", text="")

        if bonsai.REINSTALLED_BBIM_VERSION:
            box = self.layout.box()
            box.alert = True
            box.label(text="Bonsai requires Blender to restart.", icon="ERROR")
            box.label(text="Bonsai was reinstalled in the current session:")
            box.label(text=f"{bonsai.FIRST_INSTALLED_BBIM_VERSION} -> {bonsai.REINSTALLED_BBIM_VERSION}")
            box.operator("bim.restart_blender", text="Restart Blender", icon="BLENDER")

        if bonsai.last_error:
            box = self.layout.box()
            box.alert = True
            row = box.row(align=True)
            row.label(text="Bonsai experienced an error :(", icon="ERROR")
            row.operator("bim.close_error", text="", icon="CANCEL")
            if platform.system() == "Windows":
                box.operator("wm.console_toggle", text="View the console for full logs.", icon="CONSOLE")
            else:
                box.label(text="View the console for full logs.", icon="CONSOLE")
            box.operator("bim.copy_debug_information", text="Copy Error Message To Clipboard")
            op = box.operator("bim.open_uri", text="How Can I Fix This?")
            op.uri = "https://docs.bonsaibim.org/guides/troubleshooting.html"

        if not tool.Ifc.get():
            return

        bim_props = tool.Blender.get_bim_props()
        if bim_props.has_blend_warning:
            box = self.layout.box()
            box.alert = True
            row = box.row(align=True)
            row.label(text="Your model may be outdated", icon="ERROR")
            op = row.operator("bim.open_uri", text="", icon="QUESTION")
            op.uri = "https://docs.bonsaibim.org/guides/troubleshooting.html#saving-and-loading-blend-files"
            row.operator("bim.close_blend_warning", text="", icon="CANCEL")

        gprops = tool.Geometry.get_geometry_props()
        if context.mode == "OBJECT" and gprops.mode in ("OBJECT", "ITEM"):
            pass
        elif context.mode.startswith("EDIT") and gprops.mode == "EDIT":
            pass
        else:
            box = self.layout.box()
            box.alert = True
            row = box.row(align=True)
            row.label(text="Geometry changes will be lost", icon="ERROR")
            op = row.operator("bim.open_uri", text="", icon="QUESTION")
            op.uri = "https://docs.bonsaibim.org/guides/troubleshooting.html#incompatible-blender-features"

        if (o := context.active_object) and tool.Ifc.get_entity(o) and tool.Geometry.is_scaled(o):
            box = self.layout.box()
            box.alert = True
            row = box.row(align=True)
            row.label(text="Object scaling will be lost", icon="ERROR")
            op = row.operator("bim.open_uri", text="", icon="QUESTION")
            op.uri = "https://docs.bonsaibim.org/guides/troubleshooting.html#incompatible-blender-features"

    def draw_tab_entry(self, row, icon, tab_name, enabled=True, highlight=True):
        tab_entry = row.row(align=True)
        tab_entry.operator("bim.set_tab", text="", emboss=highlight, icon=icon).tab = tab_name
        tab_entry.enabled = enabled


class BIM_PT_tab_new_project_wizard(Panel):
    bl_label = "New Project Wizard"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        if not tool.Blender.is_tab(context, "PROJECT"):
            return False
        bim_props = tool.Blender.get_bim_props()
        pprops = tool.Project.get_project_props()
        if pprops.is_loading:
            return False
        elif tool.Ifc.get() or bim_props.ifc_file:
            return False
        return True

    def draw(self, context):
        pass


class BIM_PT_tab_project_info(Panel):
    bl_label = "Project Info"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        if not tool.Blender.is_tab(context, "PROJECT"):
            return False
        bim_props = tool.Blender.get_bim_props()
        pprops = tool.Project.get_project_props()
        if pprops.is_loading:
            return True
        elif tool.Ifc.get() or bim_props.ifc_file:
            return True
        return False

    def draw(self, context):
        pass


class BIM_PT_tab_spatial(Panel):
    bl_label = "Spatial"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "PROJECT") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_project_setup(Panel):
    bl_label = "Project Setup"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "PROJECT")

    def draw(self, context):
        pass


class BIM_PT_tab_stakeholders(Panel):
    bl_label = "Stakeholders"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "PROJECT") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_collaboration(Panel):
    bl_label = "Collaboration"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "QUALITY")

    def draw(self, context):
        pass


class BIM_PT_tab_grouping_and_filtering(Panel):
    bl_label = "Grouping and Filtering"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_options = {"HEADER_LAYOUT_EXPAND"}

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "PROJECT") and tool.Ifc.get()

    def draw(self, context):
        pass

    def draw_header(self, context):
        # Draws help button on the right
        row = self.layout.row(align=True)
        row.label(text="")  # empty text occupies the left of the row
        row.operator("bim.open_uri", text="", icon="HELP").uri = (
            "https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html"
        )


class BIM_PT_tab_geometry(Panel):
    bl_label = "Geometry"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "PROJECT") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_status(Panel):
    bl_label = "Status"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SCHEDULING") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_qto(Panel):
    bl_label = "Quantity Take-off"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SCHEDULING") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_resources(Panel):
    bl_label = "Resources"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SCHEDULING") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_cost(Panel):
    bl_label = "Cost"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SCHEDULING") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_sequence(Panel):
    bl_label = "Construction Scheduling"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SCHEDULING") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_structural(Panel):
    bl_label = "Structural"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "STRUCTURE") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_services(Panel):
    bl_label = "Services"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SERVICES") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_lighting(Panel):
    bl_label = "Lighting"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SERVICES") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_zones(Panel):
    bl_label = "Zones"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SERVICES") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_solar_analysis(Panel):
    bl_label = "Solar Analysis"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "SERVICES") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_quality_control(Panel):
    bl_label = "Quality Control"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "QUALITY")

    def draw(self, context):
        pass


class BIM_PT_tab_clash_detection(Panel):
    bl_label = "Clash Detection"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "QUALITY")

    def draw(self, context):
        pass


class BIM_PT_tab_sandbox(Panel):
    bl_label = "Sandbox"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "QUALITY")

    def draw(self, context):
        row = self.layout.row()
        row.label(text="More Experimental Than Usual", icon="ERROR")


# Object panel groups
class BIM_PT_tab_object_metadata(Panel):
    bl_label = "Object Metadata"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        props = tool.Project.get_project_props()
        return (
            tool.Blender.is_tab(context, "OBJECT")
            and tool.Ifc.get()
            and (obj := context.active_object)
            # Hide links empty handles.
            and (
                obj.type != "EMPTY"
                or not obj.instance_collection
                or not any(l.empty_handle == obj for l in props.links)
            )
        )

    def draw(self, context):
        pass


class BIM_PT_tab_placement(Panel):
    bl_label = "Placement"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return (
            tool.Blender.is_tab(context, "GEOMETRY")
            and tool.Ifc.get()
            and (obj := context.active_object)
            and tool.Ifc.get_entity(obj)
        )

    def draw(self, context):
        pass


class BIM_PT_tab_representations(Panel):
    bl_label = "Representations"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return (
            tool.Blender.is_tab(context, "GEOMETRY")
            and tool.Ifc.get()
            and tool.Geometry.get_active_or_representation_obj()
        )

    def draw(self, context):
        pass


class BIM_PT_tab_geometric_relationships(Panel):
    bl_label = "Geometric Relationships"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "GEOMETRY") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_parametric_geometry(Panel):
    bl_label = "Parametric Geometry"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return (
            tool.Blender.is_tab(context, "GEOMETRY")
            and tool.Ifc.get()
            and (obj := context.active_object)
            and tool.Ifc.get_entity(obj)
        )

    def draw(self, context):
        pass


class BIM_PT_tab_object_materials(Panel):
    bl_label = "Object Materials"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return (
            tool.Blender.is_tab(context, "GEOMETRY")
            and tool.Ifc.get()
            and (obj := context.active_object)
            and tool.Ifc.get_entity(obj)
        )

    def draw(self, context):
        pass


class BIM_PT_tab_materials(Panel):
    bl_label = "Materials"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "GEOMETRY") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_styles(Panel):
    bl_label = "Styles"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "GEOMETRY") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_profiles(Panel):
    bl_label = "Profiles"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "GEOMETRY") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_sheets(Panel):
    bl_label = "Sheets"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "DRAWINGS") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_drawings(Panel):
    bl_label = "Drawings"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "DRAWINGS") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_schedules(Panel):
    bl_label = "Schedules"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "DRAWINGS") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_references(Panel):
    bl_label = "References"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "DRAWINGS") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_misc(Panel):
    bl_label = "Misc."
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "OBJECT") and tool.Ifc.get()

    def draw(self, context):
        pass


class BIM_PT_tab_handover(Panel):
    bl_label = "Commissioning and Handover"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "FM")

    def draw(self, context):
        pass


class BIM_PT_tab_operations(Panel):
    bl_label = "Operations and Maintenance"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return tool.Blender.is_tab(context, "FM")

    def draw(self, context):
        pass


def refresh():
    UIData.is_loaded = False


class UIData:
    data = {}
    is_loaded = False

    @classmethod
    def load(cls):
        cls.data = {
            "version": cls.version(),
            "tabs_icon_color_mode": cls.icon_color_mode("user_interface.wcol_regular.text"),
            "menu_icon_color_mode": cls.icon_color_mode("user_interface.wcol_menu.text"),
        }
        cls.is_loaded = True

    @classmethod
    def version(cls):
        return tool.Blender.get_bonsai_version()

    @classmethod
    def icon_color_mode(cls, color_path):
        return tool.Blender.detect_icon_color_mode(color_path)


def draw_statusbar(self, context):
    if not UIData.is_loaded:
        UIData.load()
    text = f"Bonsai v{UIData.data['version']}"
    self.layout.label(text=text)


def draw_custom_context_menu(self: bpy.types.Menu, context: bpy.types.Context) -> None:
    # https://blender.stackexchange.com/a/275555/86891
    if (
        not hasattr(context, "button_pointer")
        or not hasattr(context, "button_prop")
        or not hasattr(context.button_prop, "identifier")
    ):
        return
    prop = context.button_pointer
    prop_name = context.button_prop.identifier
    prop_value = getattr(prop, prop_name, None)
    if not isinstance(prop_value, str):
        return
    version = tool.Ifc.get_schema()
    layout = self.layout

    if isinstance(context.button_pointer, Attribute):
        attr = context.button_pointer
        description = attr.description
        ifc_class = attr.ifc_class
        if ifc_class:
            try:
                url = get_entity_doc(version, ifc_class).get("spec_url", "")
            except RuntimeError:  # It's not an Entity Attribute. Let's try a Property Set attribute.
                doc = get_property_set_doc(version, ifc_class)
                if doc:
                    url = doc.get("spec_url", "")
                else:  # It's a custom property set. No URL available
                    url = ""
        attr_name = attr.name
        if description:
            layout.separator()
            op_description = layout.operator("bim.show_description", text="IFC Description", icon="INFO")
            op_description.attr_name = attr_name
            op_description.description = description
            op_description.url = url
        if attr_name:
            op = layout.operator("bim.copy_text_to_clipboard", text="Copy Attribute Name", icon="COPYDOWN")
            op.text = attr_name
    else:
        # Ugly but we can't know which type of data is under the cursor so we test everything until it clicks
        try:
            docs = get_entity_doc(version, prop_value)
            if docs is None:
                raise RuntimeError
        except (RuntimeError, AttributeError):
            try:
                docs = get_type_doc(version, prop_value)
                if docs is None:
                    raise RuntimeError
            except (RuntimeError, AttributeError):
                try:
                    docs = get_property_set_doc(version, prop_value)
                    if docs is None:
                        raise RuntimeError
                except (RuntimeError, AttributeError):
                    pass
        if docs:
            url = docs.get("spec_url", "")
            if url:
                layout.separator()
                url_op = layout.operator("bim.open_uri", icon="URL", text="Online IFC Documentation")
                url_op.uri = url


class BIM_PT_decorators_overlay(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_parent_id = "VIEW3D_PT_overlay"
    bl_label = "Bonsai Decorators"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        layout = self.layout

        view = context.space_data
        overlay = view.overlay

        georeference_props = tool.Georeference.get_georeference_props()
        aggregate_props = tool.Aggregate.get_aggregate_props()
        nest_props = tool.Nest.get_nest_props()
        model_props = tool.Model.get_model_props()
        display_all = overlay.show_overlays

        col = layout.column()
        col.active = display_all

        row = col.row(align=True)
        row.prop(georeference_props, "should_visualise", text="Georeference")
        row.prop(georeference_props, "visualization_scale", text="Size", slider=True)
        row = col.row(align=True)
        row.prop(aggregate_props, "aggregate_decorator", text="Aggregate")
        row = col.row(align=True)
        row.prop(nest_props, "nest_decorator", text="Nest")
        row = col.row(align=True)
        row.prop(model_props, "show_wall_axis", text="Wall Axis")
        row = col.row(align=True)
        row.prop(model_props, "show_slab_direction", text="Slab Direction")


class BIM_PT_snappping(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_parent_id = "VIEW3D_PT_snapping"
    bl_label = "Bonsai Snap Target"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        prop = context.scene.BIMSnapProperties
        layout = self.layout
        col = layout.column(align=True)
        col.prop(prop, "vertex", toggle=True, icon="SNAP_VERTEX")
        col.prop(prop, "edge", toggle=True, icon="SNAP_EDGE")
        col.prop(prop, "edge_center", toggle=True, icon="SNAP_MIDPOINT")
        col.prop(prop, "edge_intersection", toggle=True, icon="SNAP_GRID")
        col.prop(prop, "face", toggle=True, icon="SNAP_FACE")
        groups = context.scene.BIMSnapGroups
        row = layout.row(align=True)
        row.label(text="Bonsai Target Selection")
        row = layout.row(align=True)
        row.prop(groups, "object", toggle=True)
        row.prop(groups, "polyline", toggle=True)
        row.prop(groups, "measure", toggle=True)
