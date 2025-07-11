# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2021 Dion Moult <dion@thinkmoult.com>
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

from __future__ import annotations
import bpy
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.style
import ifcopenshell.util.representation
import ifcopenshell.util.element
import ifcopenshell.util.placement
import bonsai.core.tool
import bonsai.core.aggregate
import bonsai.core.geometry
import bonsai.tool as tool
from typing import Union, Optional, Any, Literal, TYPE_CHECKING
from bonsai.bim.module.spatial.decorator import GridDecorator
from bonsai.bim.module.geometry.decorator import ItemDecorator

if TYPE_CHECKING:
    from bonsai.bim.module.root.prop import BIMRootProperties


class Root(bonsai.core.tool.Root):
    @classmethod
    def get_root_props(cls) -> BIMRootProperties:
        return bpy.context.scene.BIMRootProperties

    @classmethod
    def add_tracked_opening(cls, obj: bpy.types.Object, opening_type: Literal["OPENING", "BOOLEAN"]) -> None:
        """Add tracked opening or boolean object."""
        props = tool.Model.get_model_props()
        new = props.openings.add()
        new.obj = obj
        new.name = opening_type

    @classmethod
    def assign_body_styles(cls, element: ifcopenshell.entity_instance, obj: bpy.types.Object) -> None:
        # Should this even be here? Should it be in the geometry tool?
        body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        if body:
            [
                tool.Geometry.run_style_add_style(obj=mat)
                for mat in tool.Geometry.get_object_materials_without_styles(obj)
            ]
            props = tool.Geometry.get_geometry_props()
            ifcopenshell.api.style.assign_representation_styles(
                tool.Ifc.get(),
                shape_representation=body,
                styles=tool.Geometry.get_styles(obj),
                should_use_presentation_style_assignment=props.should_use_presentation_style_assignment,
            )

    @classmethod
    def copy_representation(
        cls, source: ifcopenshell.entity_instance, dest: ifcopenshell.entity_instance
    ) -> dict[int, ifcopenshell.entity_instance]:
        def exclude_callback(attribute):
            return attribute.is_a("IfcProfileDef") and attribute.ProfileName

        copied_entities: dict[int, ifcopenshell.entity_instance] = {}

        if dest.is_a("IfcProduct"):
            if not source.Representation:
                return copied_entities
            dest.Representation = ifcopenshell.util.element.copy_deep(
                tool.Ifc.get(),
                source.Representation,
                exclude=["IfcGeometricRepresentationContext"],
                exclude_callback=exclude_callback,
                copied_entities=copied_entities,
            )
        elif dest.is_a("IfcTypeProduct"):
            if not source.RepresentationMaps:
                return copied_entities
            dest.RepresentationMaps = [
                ifcopenshell.util.element.copy_deep(
                    tool.Ifc.get(),
                    m,
                    exclude=["IfcGeometricRepresentationContext"],
                    exclude_callback=exclude_callback,
                    copied_entities=copied_entities,
                )
                for m in source.RepresentationMaps
            ]
        return copied_entities

    @classmethod
    def does_type_have_representations(cls, element: ifcopenshell.entity_instance) -> bool:
        return bool(element.RepresentationMaps)

    @classmethod
    def get_decomposition_relationships(
        cls, objs: list[bpy.types.Object]
    ) -> dict[ifcopenshell.entity_instance, dict[str, Any]]:
        relationships = {}
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            if hasattr(element, "FillsVoids") and element.FillsVoids:
                building = element.FillsVoids[0].RelatingOpeningElement.VoidsElements[0].RelatingBuildingElement
                relationships[element] = {"type": "fill", "element": building}
        return relationships

    @classmethod
    def get_default_container(cls) -> Optional[ifcopenshell.entity_instance]:
        props = tool.Spatial.get_spatial_props()
        if container := props.default_container:
            try:
                return tool.Ifc.get().by_id(container)
            except:
                props.default_container = 0
        return None

    @classmethod
    def get_default_container_elevation(cls) -> float:
        default_container = cls.get_default_container()
        if not default_container:
            return 0.0
        obj = tool.Ifc.get_object(default_container)
        assert isinstance(obj, bpy.types.Object)
        return obj.location.z

    @classmethod
    def get_connection_relationships(
        cls, objs: list[bpy.types.Object]
    ) -> dict[ifcopenshell.entity_instance, dict[str, Any]]:
        relationships = {}
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            if hasattr(element, "ConnectedTo") and element.ConnectedTo:
                paths = [
                    connection for connection in element.ConnectedTo if connection.is_a("IfcRelConnectsPathElements")
                ]
                for path in paths:
                    relationships[element] = {
                        "type": "path",
                        "related_connection_type": path.RelatedConnectionType,
                        "related_element": path.RelatedElement,
                        "related_priorities": path.RelatedPriorities,
                        "relating_connection_type": path.RelatingConnectionType,
                        "relating_element": path.RelatingElement,
                        "relating_priorities": path.RelatingPriorities,
                    }
        return relationships

    @classmethod
    def get_element_representation(
        cls, element: ifcopenshell.entity_instance, context: ifcopenshell.entity_instance
    ) -> Union[ifcopenshell.entity_instance, None]:
        if context.is_a("IfcGeometricRepresentationSubContext"):
            return ifcopenshell.util.representation.get_representation(
                element,
                context=context.ContextType,
                subcontext=context.ContextIdentifier,
                target_view=context.TargetView,
            )
        return ifcopenshell.util.representation.get_representation(element, context=context.ContextType)

    @classmethod
    def get_element_type(cls, element: ifcopenshell.entity_instance) -> Union[ifcopenshell.entity_instance, None]:
        return ifcopenshell.util.element.get_type(element)

    @classmethod
    def get_object_name(cls, obj: bpy.types.Object) -> None:
        if "." in obj.name and obj.name.split(".")[-1].isnumeric():
            return ".".join(obj.name.split(".")[:-1])
        return obj.name

    @classmethod
    def get_object_representation(cls, obj: bpy.types.Object) -> Union[ifcopenshell.entity_instance, None]:
        if obj.data and (mesh_props := tool.Geometry.get_mesh_props(obj.data)).ifc_definition_id:
            return tool.Ifc.get().by_id(mesh_props.ifc_definition_id)
        element = tool.Ifc.get_entity(obj)
        if element.is_a("IfcTypeProduct"):
            if element.RepresentationMaps:
                return element.RepresentationMaps[0].MappedRepresentation
        elif element.is_a("IfcProduct"):
            if element.Representation:
                return element.Representation.Representations[0]

    @classmethod
    def get_representation_context(cls, representation: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance:
        return representation.ContextOfItems

    @classmethod
    def is_containable(cls, element: ifcopenshell.entity_instance) -> bool:
        if (
            (element.is_a("IfcElement") and not element.is_a("IfcFeatureElement"))
            or element.is_a("IfcGrid")
            or element.is_a("IfcAnnotation")
        ):
            return True
        return False

    @classmethod
    def is_drawing_annotation(cls, element: ifcopenshell.entity_instance) -> bool:
        if not element.is_a("IfcAnnotation"):
            return False
        if element.ObjectType == "DRAWING":
            return True
        camera = bpy.context.scene.camera
        if not camera or not tool.Ifc.get_entity(camera):
            return False
        return True

    @classmethod
    def is_element_a(cls, element: ifcopenshell.entity_instance, ifc_class: str) -> bool:
        return element.is_a(ifc_class)

    @classmethod
    def is_spatial_element(cls, element: ifcopenshell.entity_instance) -> bool:
        if tool.Ifc.get().schema == "IFC2X3":
            return element.is_a("IfcSpatialStructureElement")
        return element.is_a("IfcSpatialElement")

    @classmethod
    def is_grid_axis(cls, element: ifcopenshell.entity_instance) -> bool:
        return element.is_a("IfcGridAxis")

    @classmethod
    def is_in_aggregate_mode(cls, element: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance:
        props = tool.Aggregate.get_aggregate_props()
        if props.editing_aggregate and props.in_aggregate_mode:
            return tool.Ifc.get_entity(props.editing_aggregate)

    @classmethod
    def is_in_nest_mode(cls, element: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance:
        props = tool.Nest.get_nest_props()
        if props.editing_nest and props.in_nest_mode:
            return tool.Ifc.get_entity(props.editing_nest)

    @classmethod
    def reload_grid_decorator(cls) -> None:
        axes = tool.Spatial.get_grid_props().grid_axes
        axes.clear()
        for axis in tool.Ifc.get().by_type("IfcGridAxis"):
            if obj := tool.Ifc.get_object(axis):
                new = axes.add()
                new.obj = obj
        GridDecorator.install(bpy.context)

    @classmethod
    def reload_item_decorator(cls) -> None:
        bpy.context.view_layer.update()
        ItemDecorator.install(bpy.context)

    @classmethod
    def link_object_data(cls, source_obj: bpy.types.Object, destination_obj: bpy.types.Object) -> None:
        destination_obj.data = source_obj.data

    @classmethod
    def recreate_decompositions(
        cls, relationships, old_to_new: dict[ifcopenshell.entity_instance, list[ifcopenshell.entity_instance]]
    ) -> None:
        for subelement, data in relationships.items():
            new_subelements = old_to_new.get(subelement)
            new_elements = old_to_new.get(data["element"])
            if not new_subelements or not new_elements:
                continue
            for i, new_subelement in enumerate(new_subelements):
                new_element = new_elements[i]
                if data["type"] == "fill":
                    element = new_element
                    filling = new_subelement
                    voided_obj = tool.Ifc.get_object(new_element)
                    filling_obj = tool.Ifc.get_object(new_subelement)

                    existing_opening_occurrence = subelement.FillsVoids[0].RelatingOpeningElement
                    opening = ifcopenshell.api.run(
                        "root.copy_class", tool.Ifc.get(), product=existing_opening_occurrence
                    )
                    ifcopenshell.api.run(
                        "geometry.edit_object_placement",
                        tool.Ifc.get(),
                        product=opening,
                        matrix=ifcopenshell.util.placement.get_local_placement(opening.ObjectPlacement),
                        is_si=False,
                    )

                    representation = ifcopenshell.util.representation.get_representation(
                        existing_opening_occurrence, "Model", "Body", "MODEL_VIEW"
                    )
                    representation = ifcopenshell.util.representation.resolve_representation(representation)
                    mapped_representation = ifcopenshell.api.run(
                        "geometry.map_representation", tool.Ifc.get(), representation=representation
                    )
                    ifcopenshell.api.run(
                        "geometry.assign_representation",
                        tool.Ifc.get(),
                        product=opening,
                        representation=mapped_representation,
                    )
                    ifcopenshell.api.run("feature.add_feature", tool.Ifc.get(), feature=opening, element=element)
                    ifcopenshell.api.run("feature.add_filling", tool.Ifc.get(), opening=opening, element=filling)

                    voided_objs = [voided_obj]
                    # Openings affect all subelements of an aggregate
                    for subelement in ifcopenshell.util.element.get_decomposition(element):
                        subobj = tool.Ifc.get_object(subelement)
                        if subobj:
                            voided_objs.append(subobj)

                    for voided_obj in voided_objs:
                        if data := voided_obj.data:
                            representation = tool.Ifc.get().by_id(tool.Geometry.get_mesh_props(data).ifc_definition_id)
                            bonsai.core.geometry.switch_representation(
                                tool.Ifc,
                                tool.Geometry,
                                obj=voided_obj,
                                representation=representation,
                                should_reload=True,
                                is_global=True,
                                should_sync_changes_first=False,
                            )

    @classmethod
    def recreate_connections(
        cls,
        relationship: dict[ifcopenshell.entity_instance, dict[str, Any]],
        old_to_new: dict[ifcopenshell.entity_instance, list[ifcopenshell.entity_instance]],
    ) -> None:
        for element, data in relationship.items():
            try:
                new_relating_element = old_to_new.get(data["relating_element"])[0]
                new_related_element = old_to_new.get(data["related_element"])[0]
            except:
                continue
            ifcopenshell.api.run(
                "geometry.connect_path",
                tool.Ifc.get(),
                relating_element=new_relating_element,
                related_element=new_related_element,
                relating_connection=data["relating_connection_type"],
                related_connection=data["related_connection_type"],
            )

    @classmethod
    def recreate_aggregate(
        cls, old_to_new: dict[ifcopenshell.entity_instance, list[ifcopenshell.entity_instance]]
    ) -> None:
        for old, new in old_to_new.items():
            old_aggregate = ifcopenshell.util.element.get_aggregate(old)
            if old_aggregate:
                try:
                    new_aggregate = old_to_new[old_aggregate]
                except:
                    bonsai.core.aggregate.unassign_object(
                        tool.Ifc,
                        tool.Aggregate,
                        tool.Collector,
                        relating_obj=tool.Ifc.get_object(old_aggregate),
                        related_obj=tool.Ifc.get_object(new[0]),
                    )
                    continue

                bonsai.core.aggregate.assign_object(
                    tool.Ifc,
                    tool.Aggregate,
                    tool.Collector,
                    relating_obj=tool.Ifc.get_object(new_aggregate[0]),
                    related_obj=tool.Ifc.get_object(new[0]),
                )

                # Make sure that the array children also get reassigned to the correct aggregate
                pset = ifcopenshell.util.element.get_pset(new[0], "BBIM_Array")
                if pset:
                    array_children = tool.Blender.Modifier.Array.get_all_children_objects(new[0])
                    for obj in array_children:
                        bonsai.core.aggregate.assign_object(
                            tool.Ifc,
                            tool.Aggregate,
                            tool.Collector,
                            relating_obj=tool.Ifc.get_object(new_aggregate[0]),
                            related_obj=tool.Ifc.get_object(tool.Ifc.get_entity(obj)),
                        )

        tool.Blender.select_and_activate_single_object(bpy.context, tool.Ifc.get_object(new_aggregate[0]))

    @classmethod
    def run_geometry_add_representation(
        cls,
        obj: bpy.types.Object,
        context: ifcopenshell.entity_instance,
        ifc_representation_class: Optional[str] = None,
        profile_set_usage: Optional[ifcopenshell.entity_instance] = None,
    ) -> ifcopenshell.entity_instance:
        return bonsai.core.geometry.add_representation(
            tool.Ifc,
            tool.Geometry,
            tool.Style,
            tool.Surveyor,
            obj=obj,
            context=context,
            ifc_representation_class=ifc_representation_class,
            profile_set_usage=profile_set_usage,
        )

    @classmethod
    def set_object_name(cls, obj: bpy.types.Object, element: ifcopenshell.entity_instance) -> None:
        name = tool.Loader.get_name(element)
        if obj.name != name:
            props = tool.Blender.get_object_bim_props(obj)
            # If it's not an IFC object, then it doesn't have a name callback.
            # So, we need to protect it writing to IFC.
            props.is_renaming = bool(tool.Ifc.get_entity(obj))
            obj.name = name  # The handler will trigger, and reset is_renaming to False

    @classmethod
    def set_material_name(cls, material: bpy.types.Material, name: str) -> None:
        """Rename material without triggerring name callback and unnecessary writing to IFC."""
        if material.name == name:
            return
        msprops = tool.Style.get_material_style_props(material)
        msprops.is_renaming = bool(tool.Ifc.get_entity(material))
        material.name = name  # The handler will trigger, and reset is_renaming to False.

    @classmethod
    def unlink_object(cls, obj: bpy.types.Object) -> None:
        """Purge all IFC data associated with a Blender object.

        This method is safe to run on objects from other Blender sessions
        as it won't try to find related IFC elements from the IFC data
        to unlink them.
        """
        tool.Ifc.unlink(obj=obj)
        if tool.Geometry.has_mesh_properties((data := obj.data)):
            tool.Geometry.get_mesh_props(data).ifc_definition_id = 0
        for material_slot in obj.material_slots:
            if material := material_slot.material:
                tool.Ifc.unlink(obj=material)
        if "Ifc" in obj.name and "/" in obj.name:
            obj.name = obj.name.split("/", 1)[1]

    @classmethod
    def get_ifc_products(cls) -> tuple[str, ...]:
        version = tool.Ifc.get_schema()
        if version == "IFC2X3":
            products = (
                "IfcElementType",
                "IfcElement",
                "IfcFeatureElement",
                "IfcSpatialStructureElement",
                "IfcStructuralItem",
                "IfcAnnotation",
                "IfcRelSpaceBoundary",
            )
        else:
            products = (
                "IfcElementType",
                "IfcElement",
                "IfcFeatureElement",
                "IfcSpatialElement",
                "IfcSpatialElementType",
                "IfcStructuralItem",
                "IfcAnnotation",
                "IfcRelSpaceBoundary",
            )
        return products
