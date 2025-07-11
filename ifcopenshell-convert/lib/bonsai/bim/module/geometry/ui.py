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

import bpy
import bonsai.bim
import bonsai.tool as tool
from bpy.types import Panel, Menu, UIList
from bonsai.bim.helper import prop_with_search
from bonsai.bim.module.geometry.data import (
    RepresentationsData,
    RepresentationItemsData,
    ConnectionsData,
    PlacementData,
    DerivedCoordinatesData,
)
from bonsai.bim.module.layer.data import LayersData


class UIData:
    data = {}
    is_loaded = False

    @classmethod
    def load(cls):
        cls.data = {"menu_icon_color_mode": cls.icon_color_mode("user_interface.wcol_menu.text")}
        cls.is_loaded = True

    @classmethod
    def icon_color_mode(cls, color_path):
        return tool.Blender.detect_icon_color_mode(color_path)


def mode_menu(self, context):
    if not tool.Ifc.get():
        return
    if not UIData.is_loaded:
        UIData.load()
    ifc_icon = f"{UIData.data['menu_icon_color_mode']}_ifc"
    row = self.layout.row(align=True)
    props = tool.Geometry.get_geometry_props()
    if props.mode == "EDIT":
        row.operator("bim.override_mode_set_object", icon="CANCEL", text="Discard Changes").should_save = False
    row.prop(props, "mode", text="", icon_value=bonsai.bim.icons[ifc_icon].icon_id)


def object_menu(self, context):
    self.layout.separator()
    self.layout.operator("bim.override_object_duplicate_move", icon="PLUGIN")
    self.layout.operator("bim.override_object_delete", icon="PLUGIN")
    self.layout.operator("bim.override_paste_buffer", icon="PLUGIN")
    self.layout.menu("BIM_MT_object_set_origin", icon="PLUGIN")


def edit_mesh_menu(self, context):
    self.layout.separator()
    self.layout.menu("BIM_MT_separate", icon="PLUGIN")


class BIM_MT_separate(Menu):
    bl_idname = "BIM_MT_separate"
    bl_label = "IFC Separate"

    def draw(self, context):
        self.layout.operator("bim.override_mesh_separate", icon="PLUGIN", text="IFC Selection").type = "SELECTED"
        self.layout.operator("bim.override_mesh_separate", icon="PLUGIN", text="IFC By Material").type = "MATERIAL"
        self.layout.operator("bim.override_mesh_separate", icon="PLUGIN", text="IFC By Loose Parts").type = "LOOSE"


class BIM_MT_hotkey_separate(Menu):
    bl_idname = "BIM_MT_hotkey_separate"
    bl_label = "Separate"

    def draw(self, context):
        self.layout.label(text="IFC Separate", icon_value=bonsai.bim.icons["IFC"].icon_id)
        self.layout.operator("bim.override_mesh_separate", text="Selection").type = "SELECTED"
        self.layout.operator("bim.override_mesh_separate", text="By Material").type = "MATERIAL"
        self.layout.operator("bim.override_mesh_separate", text="By Loose Parts").type = "LOOSE"
        self.layout.separator()
        self.layout.label(text="Blender Separate", icon="BLENDER")
        self.layout.operator("mesh.separate", text="Selection").type = "SELECTED"
        self.layout.operator("mesh.separate", text="By Material").type = "MATERIAL"
        self.layout.operator("mesh.separate", text="By Loose Parts").type = "LOOSE"


class BIM_MT_object_set_origin(Menu):
    bl_idname = "BIM_MT_object_set_origin"
    bl_label = "IFC Set Origin"

    def draw(self, context):
        self.layout.operator("bim.override_origin_set", icon="PLUGIN", text="IFC Geometry to Origin").origin_type = (
            "GEOMETRY_ORIGIN"
        )
        self.layout.operator("bim.override_origin_set", icon="PLUGIN", text="IFC Origin to Geometry").origin_type = (
            "ORIGIN_GEOMETRY"
        )
        self.layout.operator("bim.override_origin_set", icon="PLUGIN", text="IFC Origin to 3D Cursor").origin_type = (
            "ORIGIN_CURSOR"
        )
        self.layout.operator(
            "bim.override_origin_set", icon="PLUGIN", text="IFC Origin to Center of Mass (Surface)"
        ).origin_type = "ORIGIN_CENTER_OF_MASS"
        self.layout.operator(
            "bim.override_origin_set", icon="PLUGIN", text="IFC Origin to Center of Mass (Volume)"
        ).origin_type = "ORIGIN_CENTER_OF_VOLUME"


def outliner_menu(self, context):
    self.layout.separator()
    self.layout.operator("bim.override_outliner_delete", icon="X")
    self.layout.operator("bim.override_paste_buffer", icon="PLUGIN")


class BIM_PT_representations(Panel):
    bl_label = "Representations"
    bl_idname = "BIM_PT_representations"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_parent_id = "BIM_PT_tab_representations"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() and (obj := tool.Blender.get_active_object()) and tool.Ifc.get_entity(obj)

    def draw(self, context):
        layout = self.layout
        if not RepresentationsData.is_loaded:
            RepresentationsData.load()

        obj = context.active_object
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)

        row = self.layout.row(align=True)
        prop_with_search(row, props, "contexts", text="")
        row.operator("bim.add_representation", icon="ADD", text="")

        if not RepresentationsData.data["representations"]:
            self.layout.label(text="No Representations Found")
            return

        for representation in RepresentationsData.data["representations"]:
            row = self.layout.row(align=True)
            row.label(text=representation["ContextType"])
            row.label(text=representation["ContextIdentifier"])
            row.label(text=representation["TargetView"])
            row.label(text=representation["RepresentationType"])
            op = row.operator(
                "bim.switch_representation",
                icon="FILE_REFRESH" if representation["is_active"] else "OUTLINER_DATA_MESH",
                text="",
            )
            op.should_switch_all_meshes = True
            op.should_reload = True
            op.ifc_definition_id = representation["id"]
            op.disable_opening_subtractions = False
            row.operator("bim.remove_representation", icon="X", text="").representation_id = representation["id"]

        # Presentation layers.
        self.layout.separator()
        if not LayersData.is_loaded:
            LayersData.load()

        if active_layers := LayersData.data["active_layers"]:
            row = layout.row(align=True)
            row.label(text="Representation Presentation Layers:")
            if props.is_adding_representation_layer:
                row = layout.row(align=True)
                row.prop(props, "representation_layer", text="")
                op = row.operator("bim.assign_representation_layer", icon="CHECKMARK", text="")
                row.prop(props, "is_adding_representation_layer", icon="CANCEL", text="")
            else:
                row.prop(props, "is_adding_representation_layer", icon="ADD", text="")

            for layer_id, layer_name in active_layers.items():
                row = layout.row(align=True)
                row.label(text=layer_name, icon="STICKY_UVS_LOC")
                op = row.operator("bim.layer_ui_select", icon="ZOOM_SELECTED", text="")
                op.layer_id = layer_id
                op = row.operator("bim.unassign_representation_layer", icon="X", text="")
                op.layer_id = layer_id
        else:
            row = layout.row(align=True)
            if props.is_adding_representation_layer:
                row.prop(props, "representation_layer", text="")
                op = row.operator("bim.assign_representation_layer", icon="CHECKMARK", text="")
                row.prop(props, "is_adding_representation_layer", icon="CANCEL", text="")
            else:
                row.label(text="Representation Has No Presentation Layers", icon="STICKY_UVS_LOC")
                row.prop(props, "is_adding_representation_layer", icon="GREASEPENCIL", text="")


class BIM_PT_representation_items(Panel):
    bl_label = "Representation Items"
    bl_idname = "BIM_PT_representation_items"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_parent_id = "BIM_PT_tab_representations"

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() and tool.Geometry.get_active_or_representation_obj()

    def draw(self, context):
        if not RepresentationItemsData.is_loaded:
            RepresentationItemsData.load()

        props = tool.Geometry.get_geometry_props()
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)

        row = self.layout.row(align=True)
        row.label(text=f"{RepresentationItemsData.data['total_items']} Items Found")

        if props.is_editing:
            row.operator("bim.disable_editing_representation_items", icon="CANCEL", text="")
        else:
            row.operator("bim.enable_editing_representation_items", icon="IMPORT", text="")
            return

        if props.active_item:
            row = self.layout.row(align=True)
            row.alignment = "RIGHT"
            op = row.operator("bim.select_representation_item", icon="RESTRICT_SELECT_OFF", text="")
            op = row.operator("bim.remove_representation_item", icon="X", text="")
            op.representation_item_id = props.active_item.ifc_definition_id

        self.layout.template_list("BIM_UL_representation_items", "", props, "items", props, "active_item_index")

        item_is_active = props.active_item_index < len(props.items)
        if not item_is_active:
            return

        active_item = props.items[props.active_item_index]
        surface_style = active_item.surface_style
        surface_style_id = active_item.surface_style_id
        shape_aspect = active_item.shape_aspect
        layer = active_item.layer

        # Style.
        row = self.layout.row(align=True)
        if props.is_editing_item_style:
            # NOTE: we currently support 1 item having just 1 style
            # when IfcStyledItem can actually have multiple styles
            prop_with_search(row, props, "representation_item_style", icon="SHADING_RENDERED", text="")
            row.operator("bim.edit_representation_item_style", icon="CHECKMARK", text="")
            row.operator("bim.disable_editing_representation_item_style", icon="CANCEL", text="")
        else:
            if surface_style:
                row.label(text=surface_style, icon="SHADING_RENDERED")
                op = row.operator("bim.styles_ui_select", icon="ZOOM_SELECTED", text="")
                op.style_id = surface_style_id
            else:
                row.label(text="No Surface Style", icon="MESH_UVSPHERE")
            row.operator("bim.enable_editing_representation_item_style", icon="GREASEPENCIL", text="")
            if surface_style:
                row.operator("bim.unassign_representation_item_style", icon="X", text="")

        # Presentation layer.
        row = self.layout.row(align=True)
        if props.is_editing_item_layer:
            prop_with_search(row, props, "representation_item_layer", icon="STICKY_UVS_LOC", text="")
            row.operator("bim.edit_representation_item_layer", icon="CHECKMARK", text="")
            row.prop(props, "is_editing_item_layer", icon="CANCEL", text="")
        else:
            row.label(text=layer or "No Presentation Layer", icon="STICKY_UVS_LOC")
            if layer:
                op = row.operator("bim.layer_ui_select", icon="ZOOM_SELECTED", text="")
                op.layer_id = active_item.layer_id
            row.prop(props, "is_editing_item_layer", icon="GREASEPENCIL", text="")
            if layer:
                row.operator("bim.unassign_representation_item_layer", icon="X", text="")

        # Mappings.
        if active_item.name.endswith("FaceSet"):
            if "UV" in active_item.tags:
                text = "Has UV mapping"
            else:
                text = "Has no UV mapping"
            self.layout.label(text=text, icon="UV")

            if "Colour" in active_item.tags:
                text = "Has colour mapping"
            else:
                text = "Has no colour mapping"
            self.layout.label(text=text, icon="COLOR")

        # Shape aspect.
        row = self.layout.row(align=True)
        if props.is_editing_item_shape_aspect:
            row.prop(props, "representation_item_shape_aspect", icon="SHAPEKEY_DATA", text="")
            row.operator("bim.edit_representation_item_shape_aspect", icon="CHECKMARK", text="")
            row.operator("bim.disable_editing_representation_item_shape_aspect", icon="CANCEL", text="")
            if props.representation_item_shape_aspect == "NEW":
                shape_aspect_attrs = props.shape_aspect_attrs
                self.layout.prop(shape_aspect_attrs, "name")
                self.layout.prop(shape_aspect_attrs, "description")
        else:
            row.label(text=shape_aspect or "No Shape Aspect", icon="SHAPEKEY_DATA")
            row.operator("bim.enable_editing_representation_item_shape_aspect", icon="GREASEPENCIL", text="")
            if shape_aspect:
                row.operator("bim.remove_representation_item_from_shape_aspect", icon="X", text="")


class BIM_PT_connections(Panel):
    bl_label = "Connections"
    bl_idname = "BIM_PT_connections"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_order = 1
    bl_parent_id = "BIM_PT_tab_geometric_relationships"

    @classmethod
    def poll(cls, context):
        if not (obj := context.active_object):
            return False
        if not tool.Ifc.get_object_by_identifier(tool.Blender.get_ifc_definition_id(obj)):
            return False
        return tool.Ifc.get()

    def draw(self, context):
        if not ConnectionsData.is_loaded:
            ConnectionsData.load()

        layout = self.layout

        if not ConnectionsData.data["connections"] and not ConnectionsData.data["is_connection_realization"]:
            layout.label(text="No connections found")

        for connection in ConnectionsData.data["connections"]:
            row = self.layout.row(align=True)
            row.label(text=connection["Name"], icon="SNAP_ON" if connection["is_relating"] else "SNAP_OFF")
            row.label(text=connection["ConnectionType"])
            op = row.operator("bim.select_connection", icon="RESTRICT_SELECT_OFF", text="")
            op.connection = connection["id"]
            op = row.operator("bim.remove_connection", icon="X", text="")
            op.connection = connection["id"]

            if connection["realizing_elements"]:
                row = self.layout.row(align=True)
                connection_type = connection["realizing_elements_connection_type"]
                connection_type = f" ({connection_type})" if connection_type else ""
                row.label(text=f"Realizing elements{connection_type}:")

                for element in connection["realizing_elements"]:
                    row = self.layout.row(align=True)
                    obj = tool.Ifc.get_object(element)
                    row.operator("bim.select_entity", text="", icon="RESTRICT_SELECT_OFF").ifc_id = element.id()
                    row.label(text=obj.name)

        # display connections where element is connection realization
        connections = ConnectionsData.data["is_connection_realization"]
        if not connections:
            return

        row = self.layout.row(align=True)
        row.label(text="Element is connections realization:")
        for connection in connections:
            # NOTE: not displayed yet
            connection_type = connection["realizing_elements_connection_type"]
            connection_type = f" ({connection_type})" if connection_type else ""

            row = self.layout.row(align=True)

            connected_from = connection["connected_from"]
            obj = tool.Ifc.get_object(connected_from)
            row.operator("bim.select_entity", text="", icon="RESTRICT_SELECT_OFF").ifc_id = connected_from.id()
            row.label(text=obj.name)

            row.label(text="", icon="FORWARD")

            connected_to = connection["connected_to"]
            obj = tool.Ifc.get_object(connected_to)
            row.operator("bim.select_entity", text="", icon="RESTRICT_SELECT_OFF").ifc_id = connected_to.id()
            row.label(text=obj.name)


class BIM_PT_mesh(Panel):
    bl_label = "Representation Utilities"
    bl_idname = "BIM_PT_mesh"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "BIM_PT_tab_representations"

    @classmethod
    def poll(cls, context):
        return (
            (obj := context.active_object) is not None
            and (mesh := obj.data)
            and isinstance(mesh, bpy.types.Mesh)
            and tool.Geometry.get_mesh_props(mesh).ifc_definition_id
        )

    def draw(self, context):
        obj = context.active_object
        assert obj
        mesh = obj.data
        assert isinstance(mesh, bpy.types.Mesh)

        row = self.layout.row()
        row.label(text="Advanced Users Only", icon="ERROR")

        layout = self.layout

        row = layout.row()
        text = "Manually Save Representation"
        if tool.Ifc.is_edited(obj):
            text += "*"
        row.operator("bim.update_representation", text=text)

        row = layout.row()
        row.operator("bim.copy_representation", text="Copy Mesh From Active To Selected")

        row = layout.row()
        op = row.operator("bim.update_representation", text="Convert To Tessellation")
        op.ifc_representation_class = "IfcTessellatedFaceSet"

        row = layout.row()
        op = row.operator("bim.update_representation", text="Convert To Rectangle Extrusion")
        op.ifc_representation_class = "IfcExtrudedAreaSolid/IfcRectangleProfileDef"

        row = layout.row()
        op = row.operator("bim.update_representation", text="Convert To Circle Extrusion")
        op.ifc_representation_class = "IfcExtrudedAreaSolid/IfcCircleProfileDef"

        row = layout.row()
        op = row.operator("bim.update_representation", text="Convert To Arbitrary Extrusion")
        op.ifc_representation_class = "IfcExtrudedAreaSolid/IfcArbitraryClosedProfileDef"

        row = layout.row()
        op = row.operator("bim.update_representation", text="Convert To Arbitrary Extrusion With Voids")
        op.ifc_representation_class = "IfcExtrudedAreaSolid/IfcArbitraryProfileDefWithVoids"

        if True:
            mprops = tool.Geometry.get_mesh_props(mesh)
            row = layout.row()
            row.operator("bim.get_representation_ifc_parameters")
            for index, ifc_parameter in enumerate(mprops.ifc_parameters):
                row = layout.row(align=True)
                row.prop(ifc_parameter, "name", text="")
                row.prop(ifc_parameter, "value", text="")
                row.operator("bim.update_parametric_representation", icon="FILE_REFRESH", text="").index = index


class BIM_PT_placement(Panel):
    bl_label = "Placement"
    bl_idname = "BIM_PT_placement"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 1
    bl_parent_id = "BIM_PT_tab_placement"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return (obj := context.active_object) and tool.Blender.get_ifc_definition_id(obj)

    def draw(self, context):
        if not PlacementData.is_loaded:
            PlacementData.load()

        obj = context.active_object
        assert obj
        props = tool.Blender.get_object_bim_props(obj)

        if not PlacementData.data["has_placement"]:
            row = self.layout.row()
            row.label(text="No Object Placement Found")
            return

        row = self.layout.row()
        row.prop(context.active_object, "location", text="Location")
        row = self.layout.row()
        row.prop(context.active_object, "rotation_euler", text="Rotation")

        if props.blender_offset_type != "NONE":
            row = self.layout.row(align=True)
            row.label(text="Blender Offset", icon="TRACKING_REFINE_FORWARDS")
            row.label(text=props.blender_offset_type)

            if props.blender_offset_type != "NOT_APPLICABLE":
                row = self.layout.row(align=True)
                row.label(text=PlacementData.data["original_x"], icon="EMPTY_AXIS")
                row.label(text=PlacementData.data["original_y"])
                row.label(text=PlacementData.data["original_z"])


class BIM_PT_derived_coordinates(Panel):
    bl_label = "Derived Coordinates"
    bl_idname = "BIM_PT_derived_coordinates"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 3
    bl_parent_id = "BIM_PT_tab_placement"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        if not DerivedCoordinatesData.is_loaded:
            DerivedCoordinatesData.load()

        row = self.layout.row()
        text = "Edit Object Placement"
        if tool.Ifc.is_moved(context.active_object):
            text += "*"
        row.operator("bim.edit_object_placement", text=text, icon="EXPORT")

        row = self.layout.row()
        row.label(text="XYZ Dimensions")

        row = self.layout.row(align=True)
        row.enabled = False
        row.prop(context.active_object, "dimensions", text="X", index=0, slider=True)
        row.prop(context.active_object, "dimensions", text="Y", index=1, slider=True)
        row.prop(context.active_object, "dimensions", text="Z", index=2, slider=True)

        row = self.layout.row(align=True)
        row.label(text="Min Global Z")
        row.label(text=DerivedCoordinatesData.data["min_global_z"])
        row = self.layout.row(align=True)
        row.label(text="Max Global Z")
        row.label(text=DerivedCoordinatesData.data["max_global_z"])

        if DerivedCoordinatesData.data["has_collection"]:
            row = self.layout.row(align=True)
            row.label(text="Min Decomposed Z")
            row.label(text=DerivedCoordinatesData.data["min_decomposed_z"])
            row = self.layout.row(align=True)
            row.label(text="Max Decomposed Z")
            row.label(text=DerivedCoordinatesData.data["max_decomposed_z"])

        if DerivedCoordinatesData.data["is_storey"]:
            row = self.layout.row(align=True)
            row.label(text="Storey Height")
            row.label(text=DerivedCoordinatesData.data["storey_height"])


class BIM_PT_workarounds(Panel):
    bl_label = "Vendor Workarounds"
    bl_idname = "BIM_PT_workarounds"
    bl_options = {"DEFAULT_CLOSED"}
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"
    bl_parent_id = "BIM_PT_tab_geometric_relationships"

    @classmethod
    def poll(cls, context):
        return (
            (obj := context.active_object) is not None
            and (mesh := obj.data)
            and isinstance(mesh, bpy.types.Mesh)
            and tool.Geometry.get_mesh_props(mesh).ifc_definition_id
        )

    def draw(self, context):
        props = tool.Geometry.get_geometry_props()
        row = self.layout.row()
        row.prop(props, "should_force_faceted_brep")
        row = self.layout.row()
        row.prop(props, "should_force_triangulation")
        row = self.layout.row()
        row.prop(props, "should_use_presentation_style_assignment")


class BIM_UL_representation_items(UIList):
    def draw_item(self, context, layout: bpy.types.UILayout, data, item, icon, active_data, active_propname):
        if item:
            icon = "MATERIAL" if item.surface_style else "MESH_UVSPHERE"
            row = layout.row(align=True)
            item_name = item.name
            if item.shape_aspect:
                item_name = f"{item.shape_aspect} {item_name}"
            row.label(text=item_name, icon=icon)
            if item.layer:
                row.label(text="", icon="STICKY_UVS_LOC")
