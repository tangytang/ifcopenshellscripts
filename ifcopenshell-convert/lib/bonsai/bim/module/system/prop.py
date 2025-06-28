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
import bonsai.bim.handler
import bonsai.tool as tool
from bonsai.bim.module.system.data import SystemData
import bonsai.bim.module.system.decorator as decorator
from bonsai.bim.prop import StrProperty, Attribute
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
from typing import TYPE_CHECKING, Union


def get_system_class(self: "BIMSystemProperties", context: bpy.types.Context) -> list[tuple[str, str, str]]:
    if not SystemData.is_loaded:
        SystemData.load()
    return SystemData.data["system_class"]


def update_system_name(self: "System", context: bpy.types.Context) -> None:
    system = tool.Ifc.get().by_id(self.ifc_definition_id)
    if system.Name == self.name:
        return
    system.Name = self.name
    bonsai.bim.handler.refresh_ui_data()


class System(PropertyGroup):
    name: StringProperty(name="Name", update=update_system_name)
    ifc_class: StringProperty(name="IFC Class")
    ifc_definition_id: IntProperty(name="IFC Definition ID")

    if TYPE_CHECKING:
        ifc_class: str
        ifc_definition_id: int


def update_zone_name(self: "Zone", context: bpy.types.Context) -> None:
    zone = tool.Ifc.get().by_id(self.ifc_definition_id)
    if zone.Name == self.name:
        return
    zone.Name = self.name
    bonsai.bim.handler.refresh_ui_data()


class Zone(PropertyGroup):
    name: StringProperty(name="Name")
    ifc_definition_id: IntProperty(name="IFC Definition ID")

    if TYPE_CHECKING:
        ifc_definition_id: int


def toggle_decorations(self: "BIMSystemProperties", context: bpy.types.Context) -> None:
    toggle = self.should_draw_decorations
    if toggle:
        decorator.SystemDecorator.install(context)
    else:
        decorator.SystemDecorator.uninstall()


class BIMSystemProperties(PropertyGroup):
    system_attributes: CollectionProperty(name="System Attributes", type=Attribute)
    is_editing: BoolProperty(name="Is Editing", default=False)
    is_adding: BoolProperty(name="Is Adding", default=False)
    systems: CollectionProperty(name="Systems", type=System)
    active_system_index: IntProperty(name="Active System Index")
    active_system_id: IntProperty(name="Active System Id")
    edited_system_id: IntProperty(name="Edited System Id")
    system_class: EnumProperty(items=get_system_class, name="Class")
    should_draw_decorations: BoolProperty(
        name="Should Draw Decorations", description="Toggle system decorations", update=toggle_decorations
    )

    if TYPE_CHECKING:
        system_attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        is_editing: bool
        is_adding: bool
        systems: bpy.types.bpy_prop_collection_idprop[System]
        active_system_index: int
        active_system_id: int
        edited_system_id: int
        system_class: str
        should_draw_decorations: bool

    @property
    def active_system_ui_item(self) -> Union[System, None]:
        return tool.Blender.get_active_uilist_element(self.systems, self.active_system_index)


class BIMZoneProperties(PropertyGroup):
    attributes: CollectionProperty(name="Attributes", type=Attribute)
    is_loaded: BoolProperty(name="Is Loaded", default=False)
    is_editing: IntProperty(name="Is Editing", default=0)
    zones: CollectionProperty(name="Zones", type=Zone)
    active_zone_index: IntProperty(name="Active Zone Index")

    if TYPE_CHECKING:
        attributes: bpy.types.bpy_prop_collection_idprop[Attribute]
        is_loaded: bool
        is_editing: int
        zones: bpy.types.bpy_prop_collection_idprop[Zone]
        active_zone_index: int
