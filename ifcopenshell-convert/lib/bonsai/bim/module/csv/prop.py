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
from bonsai.bim.prop import StrProperty, BIMFilterGroup
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
from typing import TYPE_CHECKING, Literal


class CsvAttribute(PropertyGroup):
    name: StringProperty(name="Query", default="class")
    header: StringProperty(name="Header Value", default="IFC Class")
    sort: EnumProperty(items=[("NONE", "None", ""), ("ASC", "Ascending", ""), ("DESC", "Descending", "")])
    group: EnumProperty(
        items=[
            ("NONE", "None", "Don't group any rows"),
            ("GROUP", "Group", "All rows where this value is identical will be merged"),
            ("CONCAT", "Concatenation", "Concatenate values if values vary within a group"),
            ("VARIES", "Varies", "Show a custom value if values vary within a group"),
            ("SUM", "Sum", "Sums the total value of rows in a group"),
            ("AVERAGE", "Average", "Averages the total value of rows in a group"),
            ("MIN", "Min", "Gets the minimum value of rows in a group"),
            ("MAX", "Max", "Gets the maximum value of rows in a group"),
        ]
    )
    varies_value: StringProperty(default="Varies", name="Varies Value")
    summary: EnumProperty(
        items=[
            ("NONE", "None", "Don't provide a summary row"),
            ("SUM", "Sum", "Sums the total value of all rows"),
            ("AVERAGE", "Average", "Averages the total value of all rows"),
            ("MIN", "Min", "Gets the minimum value of all rows"),
            ("MAX", "Max", "Gets the maximum value of all rows"),
        ]
    )
    formatting: StringProperty(default="{{value}}", name="Formatting")

    if TYPE_CHECKING:
        name: str
        header: str
        sort: Literal["NONE", "ASC", "DESC"]
        group: Literal["NONE", "GROUP", "CONCAT", "VARIES", "SUM", "AVERAGE", "MIN", "MAX"]
        varies_value: str
        summary: Literal["NONE", "SUM", "AVERAGE", "MIN", "MAX"]
        formatting: str


class CsvProperties(PropertyGroup):
    csv_ifc_file: StringProperty(default="", name="IFC File")
    ifc_selector: StringProperty(default="", name="IFC Selector")
    filter_groups: CollectionProperty(type=BIMFilterGroup, name="Filter Groups")
    csv_attributes: CollectionProperty(name="CSV Attributes", type=CsvAttribute)
    should_generate_svg: BoolProperty(default=False, name="Generate SVG")
    should_preserve_existing: BoolProperty(default=False, name="Preserve Existing")
    include_global_id: BoolProperty(default=True, name="Include GlobalId")
    null_value: StringProperty(default="N/A", name="Null Value")
    empty_value: StringProperty(default="-", name="Empty String Value")
    true_value: StringProperty(default="YES", name="True Value")
    false_value: StringProperty(default="NO", name="False Value")
    concat_value: StringProperty(default=", ", name="Concat Value")
    csv_delimiter: EnumProperty(
        items=[
            (";", ";", ""),
            (",", ",", ""),
            (".", ".", ""),
            ("CUSTOM", "Custom", ""),
        ],
        name="IFC CSV Delimiter",
        default=",",
    )
    format: EnumProperty(
        items=[
            ("csv", "csv", ""),
            ("xlsx", "xlsx", ""),
            ("ods", "ods", ""),
            ("web", "web", ""),
        ],
        name="Output format",
        default="web",
    )
    csv_custom_delimiter: StringProperty(default="", name="Custom Delimiter")
    should_show_settings: BoolProperty(default=False, name="Show Settings")
    should_show_sort: BoolProperty(default=False, name="Show Sorting")
    should_show_group: BoolProperty(default=False, name="Show Grouping")
    should_show_summary: BoolProperty(default=False, name="Show Summary")
    should_show_formatting: BoolProperty(default=False, name="Show Formatting")
    should_load_from_memory: BoolProperty(
        default=False,
        name="Load from Memory",
        description="Use IFC file currently loaded in Bonsai",
    )

    if TYPE_CHECKING:
        csv_ifc_file: str
        ifc_selector: str
        filter_groups: bpy.types.bpy_prop_collection_idprop[BIMFilterGroup]
        csv_attributes: bpy.types.bpy_prop_collection_idprop[CsvAttribute]
        should_generate_svg: bool
        should_preserve_existing: bool
        include_global_id: bool
        null_value: str
        empty_value: str
        true_value: str
        false_value: str
        concat_value: str
        csv_delimiter: Literal["NONE", "ASC", "DESC"]
        format: Literal["csv", "xlsx", "ods", "web"]
        csv_custom_delimiter: str
        should_show_settings: bool
        should_show_sort: bool
        should_show_group: bool
        should_show_summary: bool
        should_show_formatting: bool
        should_load_from_memory: bool
