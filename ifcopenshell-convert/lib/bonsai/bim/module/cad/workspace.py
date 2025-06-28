# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2022 Dion Moult <dion@thinkmoult.com>
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
import bonsai.tool as tool
import bonsai.bim.module.type.prop as type_prop
import ifcopenshell.util.unit
from bpy.types import WorkSpaceTool
from bonsai.bim.module.model.data import AuthoringData, RailingData, RoofData
from functools import partial


def load_custom_icons():
    global custom_icon_previews, display_mode
    if display_mode is None:
        display_mode = tool.Blender.detect_icon_color_mode("user_interface.wcol_tool.text")

    icons_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "icons")
    custom_icon_previews = bpy.utils.previews.new()

    prefix = f"{display_mode}_"

    for entry in os.scandir(icons_dir):
        if entry.name.endswith(".png") and entry.name.startswith(prefix):
            name = os.path.splitext(entry.name)[0].replace(prefix, "", 1)
            custom_icon_previews.load(name.upper(), entry.path, "IMAGE")


def unload_custom_icons():
    global custom_icon_previews
    if custom_icon_previews:
        bpy.utils.previews.remove(custom_icon_previews)
        custom_icon_previews = None


class CadTool(WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "EDIT_MESH"
    bl_idname = "bim.cad_tool"
    bl_label = "CAD Tool"
    bl_description = "Gives you CAD authoring related superpowers"
    bl_icon = os.path.join(os.path.dirname(__file__), "ops.authoring.cad")
    bl_widget = None
    bl_keymap = tool.Blender.get_default_selection_keypmap() + (
        ("bim.cad_hotkey", {"type": "C", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_C")]}),
        ("bim.cad_hotkey", {"type": "E", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_E")]}),
        ("bim.cad_hotkey", {"type": "F", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_F")]}),
        ("bim.cad_hotkey", {"type": "O", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_O")]}),
        ("bim.cad_hotkey", {"type": "Q", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_Q")]}),
        ("bim.cad_hotkey", {"type": "R", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_R")]}),
        ("bim.cad_hotkey", {"type": "T", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_T")]}),
        ("bim.cad_hotkey", {"type": "V", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_V")]}),
        ("bim.cad_hotkey", {"type": "X", "value": "PRESS", "shift": True}, {"properties": [("hotkey", "S_X")]}),
    )

    def draw_settings(
        context: bpy.types.Context, layout: bpy.types.UILayout, workspace_tool: bpy.types.WorkSpaceTool
    ) -> None:
        ui_context = str(context.region.type)
        obj = context.active_object
        if not obj or not (data := obj.data):
            return
        is_profile = tool.Geometry.is_profile_object_active()
        if is_profile:
            element = tool.Ifc.get_entity(obj)
            if element:
                if element.is_a("IfcProfileDef"):
                    add_header_apply_button(
                        layout,
                        "Edit Profile",
                        "bim.edit_arbitrary_profile",
                        "bim.disable_editing_arbitrary_profile",
                        ui_context,
                    )
                elif element.is_a("IfcRelSpaceBoundary"):
                    add_header_apply_button(
                        layout,
                        "Edit Boundary Geometry",
                        "bim.edit_boundary_geometry",
                        "bim.disable_editing_boundary_geometry",
                        ui_context,
                    )
                else:
                    add_header_apply_button(
                        layout,
                        "Edit Profile",
                        "bim.edit_extrusion_profile",
                        "bim.disable_editing_extrusion_profile",
                        ui_context,
                    )

            row = layout.row(align=True)
            add_layout_hotkey_operator(row, "Extend", "S_E", "Extends/reduces element to 3D cursor", ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(
                row, "Join", "S_T", "Joins two non-parallel paths at their intersection", ui_context
            )
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Fillet", "S_F", bpy.ops.bim.add_ifcarcindex_fillet.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Offset", "S_O", bpy.ops.bim.cad_offset.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Rectangle", "S_R", bpy.ops.bim.add_rectangle.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Circle", "S_C", bpy.ops.bim.add_ifccircle.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "3-Point Arc", "S_V", bpy.ops.bim.set_arc_index.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Reset Vertex", "S_X", bpy.ops.bim.reset_vertex.__doc__, ui_context)

        elif (
            isinstance(data, tool.Geometry.TYPES_WITH_MESH_PROPERTIES)
            and tool.Geometry.get_mesh_props(data).subshape_type == "AXIS"
        ):
            add_header_apply_button(
                layout, "Edit Axis", "bim.edit_extrusion_axis", "bim.disable_editing_extrusion_axis", ui_context
            )
            row = layout.row(align=True)
            add_layout_hotkey_operator(row, "Extend", "S_E", "Extends/reduces element to 3D cursor", ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(
                row, "Join", "S_T", "Joins two non-parallel paths at their intersection", ui_context
            )
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Fillet", "S_F", bpy.ops.bim.cad_fillet.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Offset", "S_O", bpy.ops.bim.cad_offset.__doc__, ui_context)

        else:
            if (
                (RailingData.is_loaded or not RailingData.load())
                and RailingData.data["pset_data"]
                and tool.Model.get_railing_props(obj).is_editing_path
            ):
                add_header_apply_button(
                    layout,
                    "Edit Railing Path",
                    "bim.finish_editing_railing_path",
                    "bim.cancel_editing_railing_path",
                    ui_context,
                )

            elif (
                (RoofData.is_loaded or not RoofData.load())
                and RoofData.data["pset_data"]
                and tool.Model.get_roof_props(obj).is_editing_path
            ):
                add_header_apply_button(
                    layout, "Edit Roof Path", "bim.finish_editing_roof_path", "bim.cancel_editing_roof_path", ui_context
                )
                row = layout.row(align=True)
                add_layout_hotkey_operator(row, "Set Gable Roof Angle", "S_R", "Set Gable Roof Angle", ui_context)

            row = layout.row(align=True)
            add_layout_hotkey_operator(row, "Extend", "S_E", "Extends/reduces element to 3D cursor", ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(
                row, "Join", "S_T", "Joins two non-parallel paths at their intersection", ui_context
            )
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Fillet", "S_F", bpy.ops.bim.add_ifcarcindex_fillet.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "Offset", "S_O", bpy.ops.bim.cad_offset.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "2-Point Arc", "S_C", bpy.ops.bim.cad_arc_from_2_points.__doc__, ui_context)
            row = row if ui_context == "TOOL_HEADER" else layout.row(align=True)
            add_layout_hotkey_operator(row, "3-Point Arc", "S_V", bpy.ops.bim.cad_arc_from_3_points.__doc__, ui_context)


class CadHotkey(bpy.types.Operator):
    bl_idname = "bim.cad_hotkey"
    bl_label = ""
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    hotkey: bpy.props.StringProperty()
    description: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, operator):
        return operator.description or ""

    def execute(self, context):
        self.props = tool.Cad.get_cad_props()
        getattr(self, f"hotkey_{self.hotkey}")()
        return {"FINISHED"}

    def draw(self, context):
        props = tool.Cad.get_cad_props()
        obj = context.active_object

        if self.hotkey == "S_C":
            if tool.Geometry.is_profile_object_active():
                row = self.layout.row()
                row.prop(props, "radius")

        elif self.hotkey == "S_F":
            if not tool.Geometry.is_profile_object_active():
                row = self.layout.row()
                row.prop(props, "resolution")
            row = self.layout.row()
            row.prop(props, "radius")

        elif self.hotkey == "S_O":
            row = self.layout.row()
            row.prop(props, "distance")

        elif self.hotkey == "S_R":
            if tool.Geometry.is_profile_object_active():
                row = self.layout.row()
                row.prop(props, "x")
                row = self.layout.row()
                row.prop(props, "y")
            elif (
                (RoofData.is_loaded or not RoofData.load())
                and RoofData.data["pset_data"]
                and tool.Model.get_roof_props(obj).is_editing_path
            ):
                self.layout.row().prop(props, "gable_roof_edge_angle")

        elif self.hotkey == "S_V":
            if not tool.Geometry.is_profile_object_active():
                row = self.layout.row()
                row.prop(props, "resolution")

    def hotkey_S_C(self):
        if tool.Geometry.is_profile_object_active():
            bpy.ops.bim.add_ifccircle(radius=self.props.radius)
        else:
            bpy.ops.bim.cad_arc_from_2_points()

    def hotkey_S_E(self):
        bpy.ops.bim.cad_trim_extend()

    def hotkey_S_F(self):
        if tool.Geometry.is_profile_object_active():
            bpy.ops.bim.add_ifcarcindex_fillet(radius=self.props.radius)
        else:
            bpy.ops.bim.cad_fillet(resolution=self.props.resolution, radius=self.props.radius)

    def hotkey_S_O(self):
        bpy.ops.bim.cad_offset(distance=self.props.distance)

    def hotkey_S_Q(self):
        obj = bpy.context.active_object

        if not obj:
            return

        if not tool.Geometry.has_mesh_properties(data := obj.data):
            return

        element = tool.Ifc.get_entity(obj)
        if not element:
            return

        mprops = tool.Geometry.get_mesh_props(data)
        if mprops.subshape_type == "PROFILE":
            if element.is_a("IfcProfileDef"):
                bpy.ops.bim.edit_arbitrary_profile()
            elif element.is_a("IfcRelSpaceBoundary"):
                bpy.ops.bim.edit_boundary_geometry()
            else:
                bpy.ops.bim.edit_extrusion_profile()
        elif mprops.subshape_type == "AXIS":
            bpy.ops.bim.edit_extrusion_axis()

    def hotkey_S_R(self):
        obj = bpy.context.active_object
        if not obj:
            return

        if tool.Geometry.is_profile_object_active():
            bpy.ops.bim.add_rectangle(x=self.props.x, y=self.props.y)
        elif (
            (RoofData.is_loaded or not RoofData.load())
            and RoofData.data["pset_data"]
            and tool.Model.get_roof_props(obj).is_editing_path
        ):
            bpy.ops.bim.set_gable_roof_edge_angle(angle=self.props.gable_roof_edge_angle)

    def hotkey_S_T(self):
        bpy.ops.bim.cad_mitre()

    def hotkey_S_V(self):
        if tool.Geometry.is_profile_object_active():
            bpy.ops.bim.set_arc_index()
        else:
            bpy.ops.bim.cad_arc_from_3_points(resolution=self.props.resolution)

    def hotkey_S_X(self):
        if tool.Geometry.is_profile_object_active():
            bpy.ops.bim.reset_vertex()


def add_header_apply_button(
    layout: bpy.types.UILayout, text: str, apply_operator: str, cancel_operator: str, ui_context: str = ""
) -> None:
    custom_icon = custom_icon_previews.get(text.upper().replace(" ", "_"), custom_icon_previews["IFC"]).icon_id
    row = layout.row(align=True)
    row.label(text=f"{text} Mode", icon_value=custom_icon)
    if apply_operator in ("bim.edit_arbitrary_profile", "bim.edit_extrusion_profile"):
        row.operator(
            "bim.align_view_to_profile", text="Align View" if ui_context != "TOOL_HEADER" else "", icon="AXIS_FRONT"
        )

    row = layout.row(align=True)
    row.operator(apply_operator, text="Apply" if ui_context != "TOOL_HEADER" else "", icon="CHECKMARK")
    row.label(text="", icon="EVENT_TAB") if ui_context != "TOOL_HEADER" else row
    row = layout.row(align=True) if ui_context != "TOOL_HEADER" else row
    row.operator(cancel_operator, text="Cancel" if ui_context != "TOOL_HEADER" else "", icon="CANCEL")

    row = layout.row(align=True)
    row.label(text="Tools")


add_layout_hotkey_operator = partial(tool.Blender.add_layout_hotkey_operator, tool_name="cad", module_name=__name__)


custom_icon_previews = None
display_mode = None
