# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021, 2022 Dion Moult <dion@thinkmoult.com>
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
import json
import platformdirs
import ifcopenshell
import ifcopenshell.util.pset
import ifcopenshell.util.unit
from ifcopenshell.util.doc import (
    get_entity_doc,
    get_attribute_doc,
    get_property_set_doc,
    get_property_doc,
    get_predefined_type_doc,
)
import bonsai.bim
import bonsai.bim.schema
import bonsai.bim.handler
import bonsai.tool as tool
from bpy.types import PropertyGroup
from bpy.props import (
    PointerProperty,
    StringProperty,
    EnumProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
    FloatVectorProperty,
    CollectionProperty,
)
from typing import Any, Union, Literal, get_args, TYPE_CHECKING
from typing_extensions import assert_never

cwd = os.path.dirname(os.path.realpath(__file__))


def update_tab(self: "BIMAreaProperties", context: bpy.types.Context) -> None:
    # Some tabs are intended only for loaded IFC project.
    if self.tab not in ("PROJECT", "FM", "QUALITY", "BLENDER") and not tool.Ifc.get():
        enum_items = [i[0] for i in get_tab.enum_items if i]
        tool.Blender.show_info_message(f"Tab '{self.tab}' only available with loaded IFC project.", "ERROR")
        self["tab"] = enum_items.index(self.previous_tab)
        return
    self.alt_tab = self.previous_tab
    self.previous_tab = self.tab


def update_global_tab(self: "BIMTabProperties", context: bpy.types.Context) -> None:
    tool.Blender.setup_tabs()
    screen = context.id_data
    aprops = screen.BIMAreaProperties[screen.areas[:].index(context.area)]
    aprops.tab = self.tab


# If we don't cache strings, accents get mangled due to a Blender bug
# https://blender.stackexchange.com/questions/216230/is-there-a-workaround-for-the-known-bug-in-dynamic-enumproperty
# https://github.com/IfcOpenShell/IfcOpenShell/pull/1945
# https://github.com/IfcOpenShell/IfcOpenShell/issues/1941
# TODO: it would be nice to have some mechanism to clear that cache
# instead of storing it forever.
def cache_string(s: Any) -> str:
    # TODO: is it ever non-string?
    s = str(s)
    cache_string.data[s] = s
    return s


cache_string.data: dict[str, str] = {}


def get_attribute_enum_values(prop: "Attribute", context: bpy.types.Context) -> list[tuple[str, str, str]]:
    # Support weird buildingSMART dictionary mappings which behave like enums
    items = []
    data = json.loads(prop.enum_items)

    if isinstance(data, dict):
        for k, v in data.items():
            items.append(
                (
                    cache_string(k),
                    cache_string(v),
                    "",
                )
            )
    else:
        for e in data:
            cache_string(e)
            items.append(
                (
                    e,
                    e,
                    "",
                )
            )

    if prop.enum_descriptions:
        items = [(identifier, name, prop.enum_descriptions[i].name) for i, (identifier, name, _) in enumerate(items)]

    return items


def update_schema_dir(self: "BIMProperties", context: bpy.types.Context) -> None:
    import bonsai.bim.schema

    bim_props = tool.Blender.get_bim_props()
    bonsai.bim.schema.ifc.schema_dir = bim_props.schema_dir


def update_data_dir(self: "BIMProperties", context: bpy.types.Context) -> None:
    import bonsai.bim.schema

    bim_props = tool.Blender.get_bim_props()
    bonsai.bim.schema.ifc.data_dir = bim_props.data_dir


def update_cache_dir(self: "BIMProperties", context: bpy.types.Context) -> None:
    import bonsai.bim.schema

    bim_props = tool.Blender.get_bim_props()
    bonsai.bim.schema.ifc.cache_dir = bim_props.cache_dir


def update_section_color(self: "BIMProperties", context: bpy.types.Context) -> None:
    section_node_group = bpy.data.node_groups.get("Section Override")
    if section_node_group is None:
        return
    try:
        emission_node = next(n for n in section_node_group.nodes if isinstance(n, bpy.types.ShaderNodeEmission))
        emission_node.inputs[0].default_value = list(self.section_plane_colour) + [1]
    except StopIteration:
        pass


def update_section_line_decorator(self: "BIMProperties", context: bpy.types.Context) -> None:
    compare_node_group = bpy.data.node_groups.get("Section Compare")
    if compare_node_group is None:
        return
    for node in compare_node_group.nodes:
        if not hasattr(node, "operation"):
            continue
        if node.operation == "COMPARE":
            node.inputs[2].default_value = self.section_line_decorator_width
            break


class StrProperty(PropertyGroup):
    pass


class ObjProperty(PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object)

    if TYPE_CHECKING:
        obj: Union[bpy.types.Object, None]


def update_single_file(self: "MultipleFileSelect", context: bpy.types.Context) -> None:
    self.file_list.clear()
    new = self.file_list.add()
    new.name = self.single_file


class MultipleFileSelect(PropertyGroup):
    single_file: bpy.props.StringProperty(name="Single File Path", description="", update=update_single_file)
    file_list: bpy.props.CollectionProperty(type=StrProperty)

    if TYPE_CHECKING:
        single_file: str
        file_list: bpy.types.bpy_prop_collection_idprop[StrProperty]

    def set_file_list(self, dirname: str, files: list[str]) -> None:
        self.file_list.clear()

        for f in files:
            new = self.file_list.add()
            new.name = os.path.join(dirname, f)

    def layout_file_select(self, layout: bpy.types.UILayout, filter_glob: str = "", text: str = "") -> None:
        # NOTE: current multifile selector design doesn't allow selecting files from different folders.
        column = layout.column(align=True)
        multiple_files = len(self.file_list) > 1
        row = column.row(align=True)
        if multiple_files:
            row.label(text=f"{len(self.file_list)} Files Selected")
        else:
            row.prop(self, "single_file", text=text)

        row.context_pointer_set("file_props", self)
        op = row.operator("bim.multiple_file_selector", icon="FILE_FOLDER", text="")
        op.filter_glob = filter_glob

        if not multiple_files:
            return

        for file in self.file_list:
            column.prop(file, "name", text="")


def update_attribute_value(self: "Attribute", context: bpy.types.Context) -> None:
    value_name = self.get_value_name()
    if value_name:
        value_names = [value_name]
    else:
        # We may not have a value name in <select> data types, so let's check everything
        value_names = ["string_value", "bool_value", "int_value", "float_value", "enum_value"]
    for name in value_names:
        if name == "enum_value" and not self.enum_items:
            continue
        if getattr(self, name, None):
            self.is_null = False


def update_is_null(self: "Attribute", context: bpy.types.Context) -> None:
    if self.is_null:
        if self.data_type != "enum" and self.get_value() != (default := self.get_value_default()):
            self.set_value(default)
        if self.is_null is not True:
            self.is_null = True
    if self.update:
        update = globals()
        for name in self.update.split("."):
            update = update[name] if isinstance(update, dict) else getattr(update, name)
        update(self, context)


def set_int_value(self: "Attribute", new_value: int) -> None:
    set_numerical_value(self, "int_value", new_value)


def set_float_value(self: "Attribute", new_value: float) -> None:
    set_numerical_value(self, "float_value", new_value)


def set_numerical_value(self: "Attribute", value_name: str, new_value: Union[float, int]) -> None:
    if self.value_min_constraint and new_value < self.value_min:
        new_value = self.value_min
    elif self.value_max_constraint and new_value > self.value_max:
        new_value = self.value_max
    self[value_name] = new_value


def get_length_value(self: "Attribute") -> float:
    si_conversion = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    return self.float_value * si_conversion


def set_length_value(self: "Attribute", value: float) -> None:
    si_conversion = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    self.float_value = value / si_conversion


def get_display_name(self: "Attribute") -> str:
    DISPLAY_UNIT_TYPES = ("AREA", "VOLUME", "FORCE")
    name = self.name
    if not self.special_type or self.special_type not in DISPLAY_UNIT_TYPES:
        return name

    unit_type = f"{self.special_type}UNIT"
    project_unit = ifcopenshell.util.unit.get_project_unit(tool.Ifc.get(), unit_type)
    if not project_unit:
        return name

    unit_symbol = ifcopenshell.util.unit.get_unit_symbol(project_unit)
    return f"{name}, {unit_symbol}"


AttributeDataType = Literal["string", "integer", "float", "boolean", "enum", "file"]
AttributeSpecialType = Literal["", "DATE", "DATETIME", "LENGTH", "AREA", "VOLUME", "FORCE", "LOGICAL"]


class Attribute(PropertyGroup):
    tooltip = "`Right Click > IFC Description` to read the attribute description and online documentation"
    name: StringProperty(name="Name")  # type: ignore [reportRedeclaration]
    display_name: StringProperty(name="Display Name", get=get_display_name)
    description: StringProperty(name="Description")  # type: ignore [reportRedeclaration]
    ifc_class: StringProperty(name="Ifc Class")  # type: ignore [reportRedeclaration]
    data_type: EnumProperty(  # type: ignore [reportRedeclaration]
        name="Data Type",
        items=[(i, i, "") for i in get_args(AttributeDataType)],
    )
    string_value: StringProperty(name="Value", update=update_attribute_value, description=tooltip)
    bool_value: BoolProperty(name="Value", update=update_attribute_value, description=tooltip)
    int_value: IntProperty(
        name="Value",
        description=tooltip,
        update=update_attribute_value,
        get=lambda self: int(self.get("int_value", 0)),
        set=set_int_value,
    )
    float_value: FloatProperty(
        name="Value",
        description=tooltip,
        update=update_attribute_value,
        get=lambda self: float(self.get("float_value", 0.0)),
        set=set_float_value,
    )
    length_value: FloatProperty(
        name="Value", description=tooltip, get=get_length_value, set=set_length_value, unit="LENGTH"
    )
    enum_items: StringProperty(name="Value")
    enum_descriptions: CollectionProperty(type=StrProperty)
    enum_value: EnumProperty(items=get_attribute_enum_values, name="Value", update=update_attribute_value)
    filepath_value: PointerProperty(type=MultipleFileSelect)
    filter_glob: StringProperty()
    is_null: BoolProperty(name="Is Null", update=update_is_null)
    is_optional: BoolProperty(name="Is Optional")
    is_uri: BoolProperty(name="Is Uri", default=False)
    is_selected: BoolProperty(name="Is Selected", default=False)
    value_min: FloatProperty(description="This is used to validate int_value and float_value")
    value_min_constraint: BoolProperty(default=False, description="True if the numerical value has a lower bound")
    value_max: FloatProperty(description="This is used to validate int_value and float_value")
    value_max_constraint: BoolProperty(default=False, description="True if the numerical value has an upper bound")
    special_type: StringProperty(name="Special Value Type", default="")
    metadata: StringProperty(name="Metadata", description="For storing some additional information about the attribute")
    update: StringProperty(name="Update", description="Custom update function to be executed")

    if TYPE_CHECKING:
        name: str
        display_name: str
        description: str
        ifc_class: str
        data_type: AttributeDataType
        special_type: AttributeSpecialType
        string_value: str
        bool_value: bool
        int_value: int
        float_value: float
        length_value: float
        enum_items: str
        enum_descriptions: bpy.types.bpy_prop_collection_idprop[StrProperty]
        enum_value: str
        filepath_value: MultipleFileSelect
        filter_glob: str
        is_null: bool
        is_optional: bool
        is_uri: bool
        is_selected: bool
        value_min: float
        value_min_constraint: bool
        value_max: float
        value_max_constraint: bool
        metadata: str
        update: str

    def get_value(self) -> Union[str, float, int, bool, None]:
        if self.is_optional and self.is_null:
            return None
        if self.data_type == "string":
            return self.string_value.replace("\\n", "\n")
        if self.data_type == "file":
            return [f.name for f in self.filepath_value.file_list]
        value = getattr(self, str(self.get_value_name()), None)
        if self.special_type == "LOGICAL" and value != "UNKNOWN":
            # IfcOpenShell expects bool if IfcLogical is True/False.
            value = value == "TRUE"
        return value

    def get_value_default(self) -> Union[str, float, int, bool]:
        data_type = self.data_type
        if data_type == "string":
            return ""
        elif data_type == "integer":
            return 0
        elif data_type == "float":
            return 0.0
        elif data_type == "boolean":
            return False
        elif data_type == "enum":
            return "0"
        elif data_type == "file":
            return ""
        else:
            assert_never(data_type)

    def get_value_name(self, display_only: bool = False) -> str:
        """Get name of the value attribute.

        :param display_only: Should be `True` if the value won't be accessed directly
            by this name, only through UI (e.g. with `layout.prop`).
        """
        data_type = self.data_type
        if data_type == "string":
            return "string_value"
        elif data_type == "boolean":
            return "bool_value"
        elif data_type == "integer":
            return "int_value"
        elif data_type == "float":
            if display_only and self.special_type == "LENGTH":
                return "length_value"
            return "float_value"
        elif data_type == "enum":
            return "enum_value"
        elif data_type == "file":
            return "filepath_value"
        else:
            assert_never(data_type)

    def set_value(self, value: Union[Any, str, float, bool, int]) -> None:
        if isinstance(value, str):
            self.data_type = "string"
        elif isinstance(value, float):
            self.data_type = "float"
        elif isinstance(value, bool):  # Make sure this is evaluated BEFORE integer
            self.data_type = "boolean"
        elif isinstance(value, int):
            self.data_type = "integer"
        else:
            self.data_type = "string"
            value = str(value)
        setattr(self, self.get_value_name(), value)


def get_tab(
    self: "Union[BIMAreaProperties, BIMTabProperties]", context: bpy.types.Context
) -> list[tuple[str, str, str, str, int]]:

    UIData = bonsai.bim.ui.UIData
    if not UIData.is_loaded:
        UIData.load()

    icon_key = f"{UIData.data['menu_icon_color_mode']}_ifc"

    if not hasattr(get_tab, "enum_items"):
        get_tab.enum_items = [
            ("PROJECT", "Project Overview", "", bonsai.bim.icons[icon_key].icon_id, 0),
            ("OBJECT", "Object Information", "", "FILE_3D", 1),
            ("GEOMETRY", "Geometry and Materials", "", "MATERIAL", 2),
            ("DRAWINGS", "Drawings and Documents", "", "DOCUMENTS", 3),
            ("SERVICES", "Services and Systems", "", "NETWORK_DRIVE", 4),
            ("STRUCTURE", "Structural Analysis", "", "EDITMODE_HLT", 5),
            ("SCHEDULING", "Costing and Scheduling", "", "NLA", 6),
            ("FM", "Facility Management", "", "PACKAGE", 7),
            ("QUALITY", "Quality and Coordination", "", "COMMUNITY", 8),
            None,
            ("BLENDER", "Blender Properties", "", "BLENDER", 9),
        ]
    return get_tab.enum_items


class BIMAreaProperties(PropertyGroup):
    tab: EnumProperty(default=0, items=get_tab, name="Tab", update=update_tab)
    previous_tab: StringProperty(default="PROJECT", name="Previous Tab")
    alt_tab: StringProperty(default="OBJECT", name="Alt Tab")
    active_tab: BoolProperty(default=True, name="Active Tab")
    inactive_tab: BoolProperty(default=False, name="Inactive Tab")

    if TYPE_CHECKING:
        tab: str
        previous_tab: str
        alt_tab: str
        active_tab: bool
        inactive_tab: bool


# BIMAreaProperties exists per area and is setup on load post. However, for new
# or temporary screens, they may not be setup yet, so this global tab
# properties is used as a fallback.
# Need it basically only for UI - to display those props and allow changing tab from the dropdown.
class BIMTabProperties(PropertyGroup):
    tab: EnumProperty(default=0, items=get_tab, name="Tab", update=update_global_tab)
    active_tab: BoolProperty(default=True, name="Active Tab")
    inactive_tab: BoolProperty(default=False, name="Inactive Tab")

    if TYPE_CHECKING:
        tab: str
        active_tab: bool
        inactive_tab: bool


class BIMProperties(PropertyGroup):
    is_dirty: BoolProperty(name="Is Dirty", default=False)
    schema_dir: StringProperty(
        default=os.path.join(cwd, "schema") + os.path.sep, name="Schema Directory", update=update_schema_dir
    )
    data_dir: StringProperty(
        default=(platformdirs.user_data_path("bonsai", roaming=True, ensure_exists=True) / "data").__str__(),
        name="Data Directory",
        update=update_data_dir,
    )
    cache_dir: StringProperty(
        default=platformdirs.user_cache_dir("bonsai"), name="Cache Directory", update=update_cache_dir
    )
    has_blend_warning: BoolProperty(name="Has Blend Warning", default=False)
    pset_dir: StringProperty(default=os.path.join("psets") + os.path.sep, name="Default Psets Directory")
    ifc_file: StringProperty(name="IFC File")
    last_transaction: StringProperty(name="Last Transaction")
    should_section_selected_objects: BoolProperty(name="Section Selected Objects", default=False)
    section_plane_colour: FloatVectorProperty(
        name="Cutaway Colour",
        subtype="COLOR",
        default=(1, 0, 0),
        min=0.0,
        max=1.0,
        update=update_section_color,
    )
    section_line_decorator_width: FloatProperty(
        name="Line Decorator Width",
        default=0.04,
        min=0.0,
        soft_max=1.0,
        update=update_section_line_decorator,
    )
    area_unit: EnumProperty(
        default="SQUARE_METRE",
        items=[
            ("NANO/SQUARE_METRE", "Square Nanometre", ""),
            ("MICRO/SQUARE_METRE", "Square Micrometre", ""),
            ("MILLI/SQUARE_METRE", "Square Millimetre", ""),
            ("DECI/SQUARE_METRE", "Square Decimetre", ""),
            ("CENTI/SQUARE_METRE", "Square Centimetre", ""),
            ("SQUARE_METRE", "Square Metre", ""),
            ("KILO/SQUARE_METRE", "Square Kilometre", ""),
            ("square inch", "Square Inch", ""),
            ("square foot", "Square Foot", ""),
            ("square yard", "Square Yard", ""),
            ("square mile", "Square Mile", ""),
        ],
        name="IFC Area Unit",
    )
    volume_unit: EnumProperty(
        default="CUBIC_METRE",
        items=[
            ("NANO/CUBIC_METRE", "Cubic Nanometre", ""),
            ("MICRO/CUBIC_METRE", "Cubic Micrometre", ""),
            ("MILLI/CUBIC_METRE", "Cubic Millimetre", ""),
            ("DECI/CUBIC_METRE", "Cubic Decimetre", ""),
            ("CENTI/CUBIC_METRE", "Cubic Centimetre", ""),
            ("CUBIC_METRE", "Cubic Metre", ""),
            ("KILO/CUBIC_METRE", "Cubic Kilometre", ""),
            ("cubic inch", "Cubic Inch", ""),
            ("cubic foot", "Cubic Foot", ""),
            ("cubic yard", "Cubic Yard", ""),
        ],
        name="IFC Volume Unit",
    )

    if TYPE_CHECKING:
        is_dirty: bool
        schema_dir: str
        data_dir: str
        cache_dir: str
        has_blend_warning: bool
        pset_dir: str
        ifc_file: str
        last_transaction: str
        should_section_selected_objects: bool
        section_plane_colour: tuple[float, float, float]
        section_line_decorator_width: float
        area_unit: str
        volume_unit: str


class IfcParameter(PropertyGroup):
    name: StringProperty(name="Name")
    step_id: IntProperty(name="STEP ID")
    index: IntProperty(name="Index")
    value: FloatProperty(name="Value")  # For now, only floats
    type: StringProperty(name="Type")

    if TYPE_CHECKING:
        name: str
        step_id: int
        index: int
        value: float
        type: str


class PsetQto(PropertyGroup):
    name: StringProperty(name="Name")
    properties: CollectionProperty(name="Properties", type=Attribute)
    is_expanded: BoolProperty(name="Is Expanded", default=True)
    is_editable: BoolProperty(name="Is Editable")

    if TYPE_CHECKING:
        properties: bpy.types.bpy_prop_collection_idprop[Attribute]
        is_expanded: bool
        is_editable: bool


class GlobalId(PropertyGroup):
    name: StringProperty(name="Name")
    ifc_definition_id: IntProperty(name="IFC Definition ID")


class BIMCollectionProperties(PropertyGroup):
    obj: PointerProperty(type=bpy.types.Object)

    if TYPE_CHECKING:
        obj: Union[bpy.types.Object, None]


BlenderOffsetType = Literal["NONE", "OBJECT_PLACEMENT", "CARTESIAN_POINT", "NOT_APPLICABLE"]


class BIMObjectProperties(PropertyGroup):
    collection: PointerProperty(type=bpy.types.Collection)
    ifc_definition_id: IntProperty(name="IFC Definition ID")
    blender_offset_type: EnumProperty(
        items=[(o, o, "") for o in get_args(BlenderOffsetType)],
        name="Blender Offset",
        default="NONE",
    )
    cartesian_point_offset: StringProperty(name="Cartesian Point Offset")
    is_reassigning_class: BoolProperty(name="Is Reassigning Class")
    is_renaming: BoolProperty(
        name="Is Renaming",
        description=(
            "Flag to ensure object name callback wouldn't write new name to IFC. "
            "Automatically reset to `False` after the next callback."
        ),
        default=False,
    )
    location_checksum: StringProperty(name="Location Checksum")
    rotation_checksum: StringProperty(name="Rotation Checksum")

    if TYPE_CHECKING:
        collection: Union[bpy.types.Collection, None]
        ifc_definition_id: int
        blender_offset_type: BlenderOffsetType
        cartesian_point_offset: str
        is_reassigning_class: bool
        is_renaming: bool
        location_checksum: str
        rotation_checksum: str


def get_profiles(self: "BIMMeshProperties", context: bpy.types.Context):
    from bonsai.bim.module.model.data import ItemData

    if not ItemData.is_loaded:
        ItemData.load()
    return ItemData.data["profiles_enum"]


SubshapeType = Literal["-", "PROFILE", "AXIS"]


class BIMMeshProperties(PropertyGroup):
    ifc_definition_id: IntProperty(name="IFC Definition ID")
    ifc_boolean_id: IntProperty(name="IFC Boolean ID")
    obj: bpy.props.PointerProperty(type=bpy.types.Object, description="Object that using this mesh as a boolean")
    has_openings_applied: BoolProperty(name="Has Openings Applied", default=True)
    is_native: BoolProperty(name="Is Native", default=False)
    is_swept_solid: BoolProperty(name="Is Swept Solid")
    is_parametric: BoolProperty(name="Is Parametric", default=False)
    subshape_type: EnumProperty(name="Subshape Type", items=[(i, i, "") for i in get_args(SubshapeType)])
    ifc_parameters: CollectionProperty(name="IFC Parameters", type=IfcParameter)
    item_attributes: CollectionProperty(name="Item Attributes", type=Attribute)
    item_profile: EnumProperty(name="Item Profile", items=get_profiles)
    material_checksum: StringProperty(name="Material Checksum", default="[]")
    mesh_checksum: StringProperty(name="Mesh Checksum", default="")
    replaced_mesh: PointerProperty(type=bpy.types.Mesh, description="Original mesh to revert section cutaway")

    if TYPE_CHECKING:
        ifc_definition_id: int
        ifc_boolean_id: int
        obj: Union[bpy.types.Object, None]
        has_openings_applied: bool
        is_native: bool
        is_swept_solid: bool
        is_parametric: bool
        subshape_type: SubshapeType
        ifc_parameters: bpy.types.bpy_prop_collection_idprop[IfcParameter]
        item_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        item_profile: str
        material_checksum: str
        mesh_checksum: str
        replaced_mesh: Union[bpy.types.Mesh, None]


class BIMFacet(PropertyGroup):
    name: StringProperty(name="Name")
    pset: StringProperty(name="Pset")
    value: StringProperty(name="Value")
    type: StringProperty(name="Type")
    comparison: EnumProperty(
        items=[
            ("=", "equal to", ""),
            ("!=", "not equal to", ""),
            (">=", "greater than or equal to", ""),
            ("<=", "lesser than or equal to", ""),
            (">", "greater than", ""),
            ("<", "less than", ""),
            ("*=", "contains", ""),
            ("!*=", "does not contain", ""),
        ],
    )

    if TYPE_CHECKING:
        pset: str
        value: str
        type: str
        comparison: Literal["=", "!=", ">=", "<=", ">", "<", "*=", "!*="]


class BIMFilterGroup(PropertyGroup):
    filters: CollectionProperty(type=BIMFacet, name="filters")

    if TYPE_CHECKING:
        filters: bpy.types.bpy_prop_collection_idprop[BIMFacet]


class BIMSnapGroups(PropertyGroup):
    object: BoolProperty(name="Object", default=True)
    polyline: BoolProperty(name="Polyline", default=True)
    measure: BoolProperty(name="Measure", default=True)

    if TYPE_CHECKING:
        object: bool
        polyline: bool
        measure: bool


class BIMSnapProperties(PropertyGroup):
    vertex: BoolProperty(name="Vertex", default=True)
    edge: BoolProperty(name="Edge", default=True)
    edge_center: BoolProperty(name="Edge Center", default=True)
    edge_intersection: BoolProperty(name="Edge Intersection", default=True)
    face: BoolProperty(name="Face", default=True)

    if TYPE_CHECKING:
        vertex: bool
        edge: bool
        edge_center: bool
        edge_intersection: bool
        face: bool
