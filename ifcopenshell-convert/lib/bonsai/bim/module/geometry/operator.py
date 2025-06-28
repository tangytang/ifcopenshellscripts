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

import re
import bpy
import bmesh
import numpy as np
import numpy.typing as npt
import ifcopenshell
import ifcopenshell.api.layer
import ifcopenshell.api.style
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder
import ifcopenshell.util.unit
import ifcopenshell.api
import ifcopenshell.api.boundary
import ifcopenshell.api.grid
import bonsai.core.geometry
import bonsai.core.geometry as core
import bonsai.core.aggregate
import bonsai.core.nest
import bonsai.core.style
import bonsai.core.root
import bonsai.core.drawing
import bonsai.tool as tool
import bonsai.bim.handler
from mathutils import Vector, Matrix, Quaternion
from time import time
from bonsai.bim.ifc import IfcStore
from ifcopenshell.util.shape_builder import ShapeBuilder
from typing import Any, Union, Literal, get_args, TYPE_CHECKING, assert_never
from bonsai.bim.module.model.decorator import ProfileDecorator


class EditObjectPlacement(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_object_placement"
    bl_label = "Edit Object Placement"
    bl_description = (
        "Write selected objects placements to IFC.\n"
        "A star in the operator name indicates that active object placement in IFC is not yet synced with Blender"
    )
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()

    def _execute(self, context):
        objs = [bpy.data.objects.get(self.obj)] if self.obj else context.selected_objects
        for obj in objs:
            core.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)


class OverrideMeshSeparate(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.override_mesh_separate"
    bl_label = "IFC Mesh Separate"
    bl_options = {"REGISTER", "UNDO"}
    type: bpy.props.StringProperty()

    def _execute(self, context):
        obj = context.active_object
        if tool.Geometry.is_representation_item(obj):
            self.separate_item(context, obj)
        elif element := tool.Ifc.get_entity(obj):
            self.separate_element(element)

    def separate_item(self, context, obj):
        item = tool.Geometry.get_active_representation(obj)
        assert item
        if tool.Geometry.is_meshlike_item(item):
            previous_selected_objects = context.selected_objects
            bpy.ops.mesh.separate(type=self.type)
            for obj in context.selected_objects:
                if obj in previous_selected_objects:
                    continue
                self.add_meshlike_item(obj)
            tool.Geometry.reload_representation(tool.Geometry.get_geometry_props().representation_obj)
        else:
            self.report({"INFO"}, f"Separating an {item.is_a()} is not supported")

    def add_meshlike_item(self, obj: bpy.types.Object) -> None:
        props = tool.Geometry.get_geometry_props()
        obj.show_in_front = True
        tool.Geometry.lock_object(obj)

        builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        rep_obj = props.representation_obj
        assert rep_obj
        assert isinstance(obj.data, bpy.types.Mesh)
        verts = tool.Blender.get_verts_coordinates(obj.data.vertices)
        verts = verts.astype("d")
        if (coordinate_offset := tool.Geometry.get_cartesian_point_offset(rep_obj)) is not None:
            verts += coordinate_offset
        verts /= unit_scale
        faces = [p.vertices[:] for p in obj.data.polygons]

        representation = tool.Geometry.get_active_representation(rep_obj)
        assert representation
        representation = ifcopenshell.util.representation.resolve_representation(representation)

        representation_type = representation.RepresentationType
        if representation_type in ("Brep", "AdvancedBrep"):
            item = builder.faceted_brep(verts, faces)
        elif representation_type == "Tessellation":
            item = builder.mesh(verts, faces)
        else:
            assert False, f"Unexpected representation type: '{representation_type}'."

        representation.Items = list(representation.Items) + [item]
        obj.name = obj.data.name = f"Item/{item.is_a()}/{item.id()}"
        tool.Ifc.link(item, obj)
        props.add_item_object(obj, item)

    def separate_element(self, element):
        # You cannot separate meshes if the representation is mapped.
        relating_type = tool.Root.get_element_type(element)
        if relating_type and tool.Root.does_type_have_representations(relating_type):
            # We toggle edit mode to ensure that once representations are
            # unmapped, our Blender mesh only has a single user.
            tool.Blender.toggle_edit_mode(context)
            bpy.ops.bim.unassign_type(related_object=obj.name)
            tool.Blender.toggle_edit_mode(context)

        bpy.ops.mesh.separate(type=self.type)
        bpy.ops.object.mode_set(mode="OBJECT", toggle=False)
        new_objs = [obj]
        for new_obj in context.selected_objects:
            if new_obj == obj:
                continue
            # This is not very efficient, it needlessly copies the representations first.
            bonsai.core.root.copy_class(tool.Ifc, tool.Collector, tool.Geometry, tool.Root, obj=new_obj)
            new_objs.append(new_obj)
        for new_obj in new_objs:
            bpy.ops.bim.update_representation(obj=new_obj.name)


class OverrideOriginSet(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.override_origin_set"
    blender_op = bpy.ops.object.origin_set.get_rna_type()
    bl_label = "IFC Origin Set"
    bl_description = (
        blender_op.description + ".\nAlso makes sure changes are in sync with IFC (operator works only on IFC objects)"
    )
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()
    blender_type_prop = blender_op.properties["type"]
    origin_type: bpy.props.EnumProperty(
        name=blender_type_prop.name,
        default=blender_type_prop.default,
        items=[(i.identifier, i.name, i.description) for i in blender_type_prop.enum_items],
    )

    def _execute(self, context):
        objs = [bpy.data.objects.get(self.obj)] if self.obj else context.selected_objects
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            if tool.Ifc.is_moved(obj):
                core.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
            representation = tool.Geometry.get_active_representation(obj)
            if not representation:
                continue
            representation = ifcopenshell.util.representation.resolve_representation(representation)
            if not tool.Geometry.is_meshlike(representation):
                continue
            bpy.ops.object.origin_set(type=self.origin_type)
            bpy.ops.bim.update_representation(obj=obj.name)


class AddRepresentation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_representation"
    bl_label = "Add Representation"
    bl_options = {"REGISTER", "UNDO"}
    representation_conversion_method: bpy.props.EnumProperty(
        items=[
            ("OUTLINE", "Trace Outline", "Traces outline by local XY axes, for Profile - by local XZ axes."),
            (
                "BOX",
                "Bounding Box",
                "Creates a bounding box representation.\n"
                "For Plan context - 2D bounding box by local XY axes,\n"
                "for Profile - 2D bounding box by local XZ axes.\n"
                "For other contexts - bounding box is 3d.",
            ),
            (
                "OBJECT",
                "From Object",
                (
                    "Copies geometry from another object.\n"
                    "Final version of the geometry will be used (e.g. with all modifiers, shape keys applied)"
                ),
            ),
            ("PROJECT", "Full Representation", "Reuses the current representation"),
            (
                "CUBE",
                "Cube",
                (
                    "Add cube representation.\n"
                    "Useful for adding a representation to an object without any representations"
                ),
            ),
        ],
        name="Representation Conversion Method",
    )

    def _execute(self, context):
        obj = context.active_object
        assert obj
        props = tool.Geometry.get_geometry_props()
        oprops = tool.Geometry.get_object_geometry_props(obj)
        ifc_context = int(oprops.contexts or "0") or None
        if not ifc_context:
            return
        ifc_context = tool.Ifc.get().by_id(ifc_context)

        original_data = obj.data

        conversion_method = self.representation_conversion_method
        if not original_data and conversion_method not in ("OBJECT", "CUBE"):
            self.report(
                {"ERROR"},
                (f"No mesh data found for the active object. Mesh data required for '{conversion_method}' method."),
            )
            return {"FINISH"}

        new_rep_data: bpy.types.Mesh | None = None
        if conversion_method == "OUTLINE":
            if ifc_context.ContextType == "Plan":
                new_rep_data = tool.Geometry.generate_outline_mesh(obj, axis="+Z")
            elif ifc_context.ContextIdentifier == "Profile":
                new_rep_data = tool.Geometry.generate_outline_mesh(obj, axis="-Y")
            else:
                new_rep_data = tool.Geometry.generate_outline_mesh(obj, axis="+Z")
            tool.Geometry.change_object_data(obj, new_rep_data, is_global=True)
        elif conversion_method == "BOX":
            if ifc_context.ContextType == "Plan":
                new_rep_data = tool.Geometry.generate_2d_box_mesh(obj, axis="Z")
            elif ifc_context.ContextIdentifier == "Profile":
                new_rep_data = tool.Geometry.generate_2d_box_mesh(obj, axis="Y")
            else:
                new_rep_data = tool.Geometry.generate_3d_box_mesh(obj)
            tool.Geometry.change_object_data(obj, new_rep_data, is_global=True)
        elif conversion_method in ("OBJECT", "CUBE"):
            if conversion_method == "OBJECT":
                if not (source_obj := props.representation_from_object):
                    self.report({"ERROR"}, "No object is selected to copy a representation from.")
                    return {"FINISHED"}

                depsgraph = context.evaluated_depsgraph_get()
                eval_obj = source_obj.evaluated_get(depsgraph)
                new_rep_data = bpy.data.meshes.new_from_object(eval_obj)
            else:  # CUBE
                new_rep_data = bpy.data.meshes.new("Cube")
                bm = tool.Blender.get_bmesh_for_mesh(new_rep_data)
                bmesh.ops.create_cube(bm, size=1)
                tool.Blender.apply_bmesh(new_rep_data, bm)

            if original_data:
                tool.Geometry.change_object_data(obj, new_rep_data, is_global=True)
            else:
                obj = tool.Geometry.recreate_object_with_data(obj, new_rep_data, is_global=True)

        try:
            core.add_representation(
                tool.Ifc,
                tool.Geometry,
                tool.Style,
                tool.Surveyor,
                obj=obj,
                context=ifc_context,
                ifc_representation_class=None,
                profile_set_usage=None,
            )
            # Object might be recreated, need to set it as active again.
            if context.active_object != obj:
                tool.Blender.set_active_object(obj)
            if new_rep_data:
                bpy.data.meshes.remove(new_rep_data)
        except core.IncompatibleRepresentationError:
            if obj.data != original_data:
                tool.Geometry.change_object_data(obj, original_data, is_global=True)
                if new_rep_data:
                    bpy.data.meshes.remove(new_rep_data)
            self.report({"ERROR"}, "No compatible representation for the context could be created.")
            return {"CANCELLED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        props = tool.Geometry.get_geometry_props()
        row = self.layout.row()
        row.prop(self, "representation_conversion_method", text="")
        if self.representation_conversion_method == "OBJECT":
            row = self.layout.row()
            row.prop(props, "representation_from_object", text="")


class SelectConnection(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.select_connection"
    bl_label = "Select Connection"
    bl_options = {"REGISTER", "UNDO"}
    connection: bpy.props.IntProperty()

    def _execute(self, context):
        core.select_connection(tool.Geometry, connection=tool.Ifc.get().by_id(self.connection))


class RemoveConnection(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_connection"
    bl_label = "Remove Connection"
    bl_options = {"REGISTER", "UNDO"}
    connection: bpy.props.IntProperty()

    def _execute(self, context):
        core.remove_connection(tool.Geometry, connection=tool.Ifc.get().by_id(self.connection))


class SwitchRepresentation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.switch_representation"
    bl_label = "Switch Representation"
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()
    ifc_definition_id: bpy.props.IntProperty()
    should_reload: bpy.props.BoolProperty()
    disable_opening_subtractions: bpy.props.BoolProperty()
    should_switch_all_meshes: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        if (obj := context.active_object) and obj.mode == "OBJECT":
            return True
        cls.poll_message_set("Only available in OBJECT mode - Press TAB in the viewport")
        return False

    def _execute(self, context):
        provided_representation = tool.Ifc.get().by_id(self.ifc_definition_id)
        ifc_context = provided_representation.ContextOfItems
        for obj in tool.Blender.get_selected_objects():
            if not (element := tool.Ifc.get_entity(obj)) or obj.mode != "OBJECT":
                continue

            # Find representation to switch to.
            if (active_representation := tool.Geometry.get_active_representation(obj)) is None:
                # No active representation => probably has no representations.
                continue
            elif obj == context.active_object:
                # Prioritize provided representation.
                representation = provided_representation
            elif active_representation.ContextOfItems == ifc_context:
                # Prioritize already active representation if context matches.
                representation = active_representation
            else:
                representation = ifcopenshell.util.representation.get_representation(element, ifc_context)
                if not representation:
                    continue

            core.switch_representation(
                tool.Ifc,
                tool.Geometry,
                obj=obj,
                representation=representation,
                should_reload=self.should_reload,
                is_global=self.should_switch_all_meshes,
                should_sync_changes_first=True,
            )


class RemoveRepresentation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_representation"
    bl_label = "Remove Representation"
    bl_options = {"REGISTER", "UNDO"}
    representation_id: bpy.props.IntProperty()

    def _execute(self, context):
        core.remove_representation(
            tool.Ifc,
            tool.Geometry,
            obj=context.active_object,
            representation=tool.Ifc.get().by_id(self.representation_id),
        )


class PurgeUnusedRepresentations(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.purge_unused_representations"
    bl_label = "Purge Unused Representations"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        purged_representations = core.purge_unused_representations(tool.Ifc, tool.Geometry)
        self.report({"INFO"}, f"{purged_representations} representations were purged.")


class UpdateRepresentation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.update_representation"
    bl_label = "Update Representation"
    bl_description = (
        "Write selected objects representations to IFC.\n"
        "Converting to tesellation will also unassign material layer/profile sets.\n"
        "A star in the operator name indicates that active object representation in IFC is not yet synced with Blender.\n"
        "ALT+CLICK to apply openings to the mesh"
    )
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()
    ifc_representation_class: bpy.props.StringProperty()
    apply_openings: bpy.props.BoolProperty(
        name="Apply Openings",
        description=(
            "Whether to apply openings to the mesh.\n"
            "If False, operator will skip updating representation that has openings"
        ),
        default=False,
        options={"SKIP_SAVE"},
    )

    from_ui = False

    def invoke(self, context, event):
        if event.type == "LEFTMOUSE" and event.alt:
            self.apply_openings = True

        self.from_ui = True
        return self.execute(context)

    def _execute(self, context):
        if context.view_layer.objects.active and context.view_layer.objects.active.mode != "OBJECT":
            # Ensure mode is object to prevent invalid mesh data causing CTD
            bpy.ops.object.mode_set(mode="OBJECT", toggle=False)

        obj_name: str = self.obj
        objs = [bpy.data.objects[obj_name]] if obj_name else context.selected_objects
        self.file = tool.Ifc.get()

        for obj in objs:
            # TODO: write unit tests to see how this bulk operation handles
            # contradictory ifc_representation_class values and when
            # ifc_representation_class is IfcTextLiteral
            data = obj.data
            if not tool.Geometry.is_data_supported_for_adding_representation(data):
                continue
            self.update_obj_mesh_representation(context, obj)
            tool.Ifc.finish_edit(obj)
        tool.Geometry.reload_representation(objs)
        return {"FINISHED"}

    def update_obj_mesh_representation(self, context: bpy.types.Context, obj: bpy.types.Object) -> None:
        data = obj.data
        assert tool.Geometry.is_data_supported_for_adding_representation(data)
        mprops = tool.Geometry.get_mesh_props(data)

        product = tool.Ifc.get_entity(obj)
        assert product
        material = ifcopenshell.util.element.get_material(product, should_skip_usage=True)

        # NOTE: Currently iterator doesn't detect whether opening is actually affected the representation
        # or it's just present on the element. In theory, we can also allow editing representations
        # if we know that representation wasn't affected by existing openings.
        has_openings = tool.Geometry.has_openings(product) and tool.Geometry.get_mesh_props(data).has_openings_applied
        if has_openings and not self.apply_openings:
            # Meshlike things with openings can only be updated without openings applied.
            if self.from_ui:
                self.report({"ERROR"}, f"Object '{obj.name}' has openings - representation cannot be updated.")
            return

        if not product.is_a("IfcGridAxis"):
            tool.Geometry.clear_cache(product)

        if product.is_a("IfcGridAxis"):
            # Grid geometry does not follow the "representation" paradigm and needs to be treated specially
            tool.Model.create_axis_curve(obj, product)
            return
        elif product.is_a("IfcRelSpaceBoundary"):
            # TODO refactor
            settings = tool.Boundary.get_assign_connection_geometry_settings(obj)
            ifcopenshell.api.boundary.assign_connection_geometry(tool.Ifc.get(), **settings)
            return

        if tool.Ifc.is_moved(obj) or tool.Geometry.is_scaled(obj):
            core.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)

        old_representation = tool.Geometry.get_active_representation(obj)
        assert old_representation
        if material and material.is_a() in ["IfcMaterialProfileSet", "IfcMaterialLayerSet"]:
            if self.ifc_representation_class == "IfcTessellatedFaceSet":
                # We are explicitly casting to a tessellation, so remove all parametric materials.
                element_type = ifcopenshell.util.element.get_type(product)
                if element_type:  # Some invalid IFCs use material sets without a type.
                    ifcopenshell.api.run("material.unassign_material", tool.Ifc.get(), products=[element_type])
                    tool.Material.ensure_material_unassigned([element_type])
                ifcopenshell.api.run("material.unassign_material", tool.Ifc.get(), products=[product])
                tool.Material.ensure_material_unassigned([product])
            else:
                # These objects are parametrically based on an axis and should not be modified as a mesh
                if extrusion := tool.Model.get_extrusion(old_representation):
                    self.report(
                        {"INFO"},
                        f"Representation for '{obj.name}' wasn't updated because "
                        "it has material set and extrusion that probably was defined parametrically.",
                    )
                    return

        context_of_items = old_representation.ContextOfItems

        # TODO: remove this code a bit later
        # added this as a fallback for easier transition some annotation types to 3d
        # if they were create before as 2d
        element = tool.Ifc.get_entity(obj)
        if tool.Drawing.is_annotation_object_type(element, ("FALL", "SECTION_LEVEL", "PLAN_LEVEL")):
            context_of_items = tool.Drawing.get_annotation_context("MODEL_VIEW")

        representation_data = {
            "context": context_of_items,
            "blender_object": obj,
            "geometry": obj.data,
            "coordinate_offset": tool.Geometry.get_cartesian_point_offset(obj),
            "total_items": max(1, len(obj.material_slots)),
            "should_force_faceted_brep": tool.Geometry.should_force_faceted_brep(),
            "should_force_triangulation": tool.Geometry.should_force_triangulation(),
            "should_generate_uvs": tool.Geometry.should_generate_uvs(obj),
            "ifc_representation_class": self.ifc_representation_class,
        }

        if not self.ifc_representation_class:
            representation_data["ifc_representation_class"] = tool.Geometry.get_ifc_representation_class(
                product, old_representation
            )
            representation_data["profile_set_usage"] = tool.Geometry.get_profile_set_usage(product)
            representation_data["text_literal"] = tool.Geometry.get_text_literal(old_representation)

        # TODO: replace with core.add_representation?
        new_representation = ifcopenshell.api.run("geometry.add_representation", self.file, **representation_data)
        if new_representation is None:
            self.report({"ERROR"}, "Error creating representation for Blender object.")
            return {"CANCELLED"}

        if tool.Geometry.is_body_representation(new_representation):
            [
                tool.Geometry.run_style_add_style(obj=mat)
                for mat in tool.Geometry.get_object_materials_without_styles(obj)
            ]
        props = tool.Geometry.get_geometry_props()
        ifcopenshell.api.style.assign_representation_styles(
            self.file,
            shape_representation=new_representation,
            styles=tool.Geometry.get_styles(obj, only_assigned_to_faces=True),
            should_use_presentation_style_assignment=props.should_use_presentation_style_assignment,
        )
        tool.Geometry.record_object_materials(obj)

        # TODO: move this into a replace_representation usecase or something
        for inverse in self.file.get_inverse(old_representation):
            ifcopenshell.util.element.replace_attribute(inverse, old_representation, new_representation)

        # As openings are already 'baked' to the geometry, we mark their representation as 'Reference'
        # as they're not part of the object representation anymore.
        if has_openings and self.apply_openings:
            ifc_context = new_representation.ContextOfItems
            for opening_rel in tool.Geometry.get_openings(product):
                opening = opening_rel.RelatedOpeningElement
                representation = ifcopenshell.util.representation.get_representation(opening, ifc_context)
                if not representation:
                    continue
                representation.RepresentationIdentifier = "Reference"

        tool.Ifc.link(new_representation, data)
        data.name = tool.Loader.get_mesh_name(new_representation)

        # TODO: In simple scenarios, a type has a ShapeRepresentation of ID
        # 123. This is then mapped through mapped representations by
        # occurrences, with no cartesian transformation. In this case, the mesh
        # data is 100% shared and therefore all have the same mesh name
        # referencing ID 123. (i.e. the local origins are shared). However, in
        # complex scenarios, occurrences may have their own cartesian
        # transformation (via MappingTarget). This will mean that occurrences
        # will not share the same mesh data and will instead reference a
        # different ShapeRepresentation ID. In this scenario, we have to
        # propagate the obj.data back to the type itself and all sibling
        # occurrences and accommodate their individual cartesian
        # transformations.

        core.remove_representation(tool.Ifc, tool.Geometry, obj=obj, representation=old_representation)
        if mprops.ifc_parameters:
            core.get_representation_ifc_parameters(tool.Geometry, obj=obj)


class UpdateParametricRepresentation(bpy.types.Operator):
    bl_idname = "bim.update_parametric_representation"
    bl_label = "Update Parametric Representation"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return (obj := context.active_object) and obj.mode == "OBJECT" and tool.Geometry.has_mesh_properties(obj.data)

    def execute(self, context):
        self.file = tool.Ifc.get()
        obj = context.active_object
        assert obj and tool.Geometry.has_mesh_properties(obj.data)
        props = tool.Geometry.get_mesh_props(obj.data)
        parameter = props.ifc_parameters[self.index]
        self.file.by_id(parameter.step_id)[parameter.index] = parameter.value
        show_representation_parameters = bool(props.ifc_parameters)
        core.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=tool.Ifc.get().by_id(props.ifc_definition_id),
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )
        if show_representation_parameters:
            core.get_representation_ifc_parameters(tool.Geometry, obj=obj)
        return {"FINISHED"}


class GetRepresentationIfcParameters(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.get_representation_ifc_parameters"
    bl_label = "Get Representation IFC Parameters"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        assert obj and tool.Geometry.has_mesh_properties((data := obj.data))
        core.get_representation_ifc_parameters(tool.Geometry, obj=obj)
        parameters = tool.Geometry.get_mesh_props(data).ifc_parameters
        self.report({"INFO"}, f"{len(parameters)} parameters found.")


class CopyRepresentation(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.copy_representation"
    bl_label = "Copy Representation"
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()

    def _execute(self, context):
        if not context.active_object:
            return
        bm = bmesh.new()
        bm.from_mesh(context.active_object.data)
        geometric_context = tool.Root.get_representation_context(
            tool.Root.get_object_representation(context.active_object)
        )
        for obj in context.selected_objects:
            if obj == context.active_object:
                continue
            if obj.data:
                element = tool.Ifc.get_entity(obj)
                if not element:
                    continue
                bm.to_mesh(obj.data)
                old_rep = tool.Geometry.get_representation_by_context(element, geometric_context)
                if old_rep:
                    ifcopenshell.api.run(
                        "geometry.unassign_representation", tool.Ifc.get(), product=element, representation=old_rep
                    )
                    ifcopenshell.api.run("geometry.remove_representation", tool.Ifc.get(), representation=old_rep)
                core.add_representation(
                    tool.Ifc,
                    tool.Geometry,
                    tool.Style,
                    tool.Surveyor,
                    obj=obj,
                    context=geometric_context,
                    ifc_representation_class=None,
                    profile_set_usage=None,
                )


def lock_error_message(name: str) -> str:
    return f"'{name}' is locked. Unlock it via the Spatial panel in the Project Overview tab."


class OverrideDelete(bpy.types.Operator):
    bl_idname = "bim.override_object_delete"
    bl_label = "IFC Delete"
    bl_options = {"REGISTER", "UNDO"}
    use_global: bpy.props.BoolProperty(default=False)
    confirm: bpy.props.BoolProperty(default=True)
    is_batch: bpy.props.BoolProperty(name="Is Batch", default=False)

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        # Deep magick from the dawn of time
        if tool.Ifc.get():
            return IfcStore.execute_ifc_operator(self, context)
        for obj in context.selected_objects:
            bpy.data.objects.remove(obj)
        # Required otherwise gizmos are still visible
        context.view_layer.objects.active = None
        return {"FINISHED"}

    def invoke(self, context, event):
        if tool.Ifc.get():
            total_elements = len(tool.Ifc.get().wrapped_data.entity_names())
            total_polygons = sum([len(o.data.polygons) for o in context.selected_objects if o.type == "MESH"])
            # These numbers are a bit arbitrary, but basically batching is only
            # really necessary on large models and large geometry removals.
            self.is_batch = total_elements > 500000 and total_polygons > 2000
            if self.is_batch:
                return context.window_manager.invoke_props_dialog(self)
            elif self.confirm:
                return context.window_manager.invoke_confirm(self, event)
        elif self.confirm:
            return context.window_manager.invoke_confirm(self, event)
        self.confirm = True
        return self.execute(context)

    def draw(self, context):
        row = self.layout.row()
        row.prop(self, "is_batch", text="Enable Faster Deletion")
        if self.is_batch:
            row = self.layout.row()
            row.label(text="Warning: Faster deletion will use more memory.", icon="ERROR")
        row = self.layout.row()
        row.label(text="See system console for deletion progress.")

    def _execute(self, context: bpy.types.Context):
        start_time = time()

        if self.is_batch:
            ifcopenshell.util.element.batch_remove_deep2(tool.Ifc.get())

        self.process_arrays(context)
        clear_active_object = True
        objects_to_remove = context.selected_objects
        for i, obj in enumerate(objects_to_remove, 1):
            # Log time.
            time_since_start = time() - start_time
            is_valid_data_block = tool.Blender.is_valid_data_block(obj)
            if time_since_start > 10:
                obj_name = f" ({obj.name})" if is_valid_data_block else ""
                print(
                    f"Removing object {i}/{len(objects_to_remove)}{obj_name}. "
                    f"Time since start: {time_since_start:.2f} seconds."
                )

            if not is_valid_data_block:
                continue
            element = tool.Ifc.get_entity(obj)
            if element:
                if tool.Geometry.is_locked(element):
                    if obj == context.view_layer.objects.active:
                        clear_active_object = False
                    self.report({"ERROR"}, lock_error_message(obj.name))
                    continue
                if ifcopenshell.util.element.get_pset(element, "BBIM_Array"):
                    self.report({"INFO"}, "Elements that are part of an array cannot be deleted.")
                    return {"FINISHED"}
                tool.Geometry.delete_ifc_object(obj)
            elif tool.Geometry.is_representation_item(obj):
                tool.Geometry.delete_ifc_item(obj)
            else:
                bpy.data.objects.remove(obj)

        for opening in tool.Model.get_model_props().openings:
            if opening.obj is not None and not tool.Ifc.get_entity(opening.obj):
                bpy.data.objects.remove(opening.obj)
        tool.Model.purge_scene_openings()

        if self.is_batch:
            old_file = tool.Ifc.get()
            old_file.end_transaction()
            new_file = ifcopenshell.util.element.unbatch_remove_deep2(tool.Ifc.get())
            new_file.begin_transaction()
            tool.Ifc.set(new_file)
            self.transaction_data = {"old_file": old_file, "new_file": new_file}
            IfcStore.add_transaction_operation(self)

        if clear_active_object:
            # Required otherwise gizmos are still visible
            context.view_layer.objects.active = None

        operator_time = time() - start_time
        if operator_time > 10:
            self.report({"INFO"}, "IFC Delete was finished in {:.2f} seconds".format(operator_time))

        return {"FINISHED"}

    def rollback(self, data):
        tool.Ifc.set(data["old_file"])
        data["old_file"].undo()

    def commit(self, data):
        data["old_file"].redo()
        tool.Ifc.set(data["new_file"])

    def process_arrays(self, context: bpy.types.Context) -> None:
        selected_objects = set(context.selected_objects)
        array_parents = set()
        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            pset = ifcopenshell.util.element.get_pset(element, "BBIM_Array")
            if not pset:
                continue
            array_parents.add(tool.Ifc.get().by_guid(pset["Parent"]))

        for array_parent in array_parents:
            array_parent_obj = tool.Ifc.get_object(array_parent)
            data = [(i, data) for i, data in enumerate(tool.Blender.Modifier.Array.get_modifiers_data(array_parent))]
            # NOTE: there is a way to remove arrays more precisely but it's more complex
            for i, modifier_data in reversed(data):
                children = set(tool.Blender.Modifier.Array.get_children_objects(modifier_data))
                if children.issubset(selected_objects):
                    with context.temp_override(active_object=array_parent_obj):
                        bpy.ops.bim.remove_array(item=i)
                else:
                    break  # allows to remove only n last layers of an array


class OverrideOutlinerDelete(bpy.types.Operator):
    bl_idname = "bim.override_outliner_delete"
    bl_label = "IFC Delete"
    bl_options = {"REGISTER", "UNDO"}
    hierarchy: bpy.props.BoolProperty(default=False)
    is_batch: bpy.props.BoolProperty(name="Is Batch", default=False)

    @classmethod
    def poll(cls, context):
        return len(getattr(context, "selected_ids", [])) > 0

    def execute(self, context):
        # In this override, we don't check self.hierarchy. This effectively
        # makes Delete and Delete Hierarchy identical. This is on purpose, since
        # non-hierarchical deletion may imply a whole bunch of potentially
        # unintended IFC spatial modifications. To make life less confusing for
        # the user, Delete means Delete. End of story.
        # Deep magick from the dawn of time
        if tool.Ifc.get():
            return IfcStore.execute_ifc_operator(self, context)
        # https://blender.stackexchange.com/questions/203729/python-get-selected-objects-in-outliner
        objects_to_delete = set()
        collections_to_delete = set()
        for item in context.selected_ids:
            if item.bl_rna.identifier == "Collection":
                collection = bpy.data.collections.get(item.name)
                collection_data = self.get_collection_objects_and_children(collection)
                objects_to_delete |= collection_data["objects"]
                collections_to_delete |= collection_data["children"]
                collections_to_delete.add(collection)
            elif item.bl_rna.identifier == "Object":
                objects_to_delete.add(bpy.data.objects.get(item.name))
        for obj in objects_to_delete:
            bpy.data.objects.remove(obj)
        for collection in collections_to_delete:
            bpy.data.collections.remove(collection)
        return {"FINISHED"}

    def invoke(self, context, event):
        if tool.Ifc.get():
            total_elements = len(tool.Ifc.get().wrapped_data.entity_names())
            total_polygons = sum([len(o.data.polygons) for o in context.selected_objects if o.type == "MESH"])
            # These numbers are a bit arbitrary, but basically batching is only
            # really necessary on large models and large geometry removals.
            self.is_batch = total_elements > 500000 and total_polygons > 2000
            if self.is_batch:
                return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        row = self.layout.row()
        row.prop(self, "is_batch", text="Enable Faster Deletion")
        if self.is_batch:
            row = self.layout.row()
            row.label(text="Warning: Faster deletion will use more memory.", icon="ERROR")

    def _execute(self, context):
        if self.is_batch:
            ifcopenshell.util.element.batch_remove_deep2(tool.Ifc.get())
        objects_to_delete = set()
        collections_to_delete = set()
        for item in context.selected_ids:
            if item.bl_rna.identifier == "Collection":
                collection = bpy.data.collections.get(item.name)
                collection_data = self.get_collection_objects_and_children(collection)
                objects_to_delete |= collection_data["objects"]
                collections_to_delete |= collection_data["children"]
                collections_to_delete.add(collection)
            elif item.bl_rna.identifier == "Object":
                objects_to_delete.add(bpy.data.objects.get(item.name))
        for obj in objects_to_delete:
            if element := tool.Ifc.get_entity(obj):
                if tool.Geometry.is_locked(element):
                    self.report({"ERROR"}, lock_error_message(obj.name))
                    if collection := tool.Blender.get_object_bim_props(obj).collection:
                        collections_to_delete.discard(collection)
                    continue
                tool.Geometry.delete_ifc_object(obj)
            else:
                bpy.data.objects.remove(obj)
        for collection in collections_to_delete:
            # Removing an aggregate object would also remove it's collection
            # making the collection data-block invalid.
            if not tool.Blender.is_valid_data_block(collection):
                continue
            bpy.data.collections.remove(collection)
        if self.is_batch:
            old_file = tool.Ifc.get()
            old_file.end_transaction()
            new_file = ifcopenshell.util.element.unbatch_remove_deep2(tool.Ifc.get())
            new_file.begin_transaction()
            tool.Ifc.set(new_file)
            self.transaction_data = {"old_file": old_file, "new_file": new_file}
            IfcStore.add_transaction_operation(self)
        return {"FINISHED"}

    def get_collection_objects_and_children(self, collection: bpy.types.Collection) -> dict[str, Any]:
        objects = set()
        children = set()
        queue = [collection]
        while queue:
            collection = queue.pop()
            for obj in collection.objects:
                objects.add(obj)
            queue.extend(collection.children)
            children = children.union(collection.children)
        return {"objects": objects, "children": children}

    def rollback(self, data):
        tool.Ifc.set(data["old_file"])
        data["old_file"].undo()

    def commit(self, data):
        data["old_file"].redo()
        tool.Ifc.set(data["new_file"])


class OverrideDuplicateMoveMacro(bpy.types.Macro):
    bl_idname = "bim.override_object_duplicate_move_macro"
    bl_label = "IFC Duplicate Objects"
    bl_options = {"REGISTER", "UNDO"}


class OverrideDuplicateMove(bpy.types.Operator):
    bl_idname = "bim.override_object_duplicate_move"
    bl_label = "IFC Duplicate Objects"
    bl_options = {"REGISTER", "UNDO"}
    is_interactive: bpy.props.BoolProperty(name="Is Interactive", default=True)

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        return OverrideDuplicateMove.execute_duplicate_operator(self, context, linked=False)

    def _execute(self, context):
        return OverrideDuplicateMove.execute_ifc_duplicate_operator(self, context)

    @staticmethod
    def execute_duplicate_operator(self, context, linked=False):
        # Deep magick from the dawn of time
        if tool.Ifc.get():
            IfcStore.execute_ifc_operator(self, context)
            if self.new_active_obj:
                context.view_layer.objects.active = self.new_active_obj
            return {"FINISHED"}

        new_active_obj = None
        for obj in context.selected_objects:
            new_obj = obj.copy()
            if obj.data and not linked:
                new_obj.data = obj.data.copy()
            if obj == context.active_object:
                new_active_obj = new_obj
            for collection in obj.users_collection:
                collection.objects.link(new_obj)
            obj.select_set(False)
            new_obj.select_set(True)
        if new_active_obj:
            context.view_layer.objects.active = new_active_obj
        return {"FINISHED"}

    @staticmethod
    def execute_ifc_duplicate_operator(self, context, linked=False):
        objects_to_duplicate = set(context.selected_objects)

        # handle arrays
        arrays_to_duplicate, array_children = OverrideDuplicateMove.process_arrays(self, context)
        objects_to_duplicate -= array_children
        for child in array_children:
            child.select_set(False)

        self.new_active_obj = None
        # Track decompositions so they can be recreated after the operation
        decomposition_relationships = tool.Root.get_decomposition_relationships(objects_to_duplicate)
        connection_relationships = tool.Root.get_connection_relationships(objects_to_duplicate)
        old_to_new = {}

        for obj in objects_to_duplicate:
            element = tool.Ifc.get_entity(obj)
            if element:
                if element.is_a("IfcAnnotation") and element.ObjectType == "DRAWING":
                    self.report(
                        {"INFO"}, "Did not duplicate. Duplicate drawings through the Drawings and Documents UI."
                    )
                    obj.select_set(False)
                    continue  # For now, don't copy drawings until we stabilise a bit more. It's tricky.
                elif tool.Geometry.is_locked(element):
                    tool.Blender.deselect_object(obj, ensure_active_object=True)
                    self.report({"ERROR"}, lock_error_message(obj.name))
                    continue
            elif tool.Geometry.is_representation_item(obj):
                OverrideDuplicateMove.duplicate_item(obj)
                continue

            tracked_opening_type = tool.Model.get_tracked_opening_type(obj)
            is_tracked_opening = bool(tracked_opening_type)
            keep_data_linked = linked and not element and not is_tracked_opening

            # Prior to duplicating, sync the object placement to make decomposition recreation more stable.
            if tool.Ifc.is_moved(obj):
                bonsai.core.geometry.edit_object_placement(
                    tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj, apply_scale=False
                )

            new_obj = obj.copy()
            temp_data = None

            # Currently for optimization we do not apply pending changes (scale or changed .data)
            # to the original and duplicated objects.
            # Keep new object edited if original is.
            if tool.Ifc.is_edited(obj, ignore_scale=True):
                tool.Ifc.edit(new_obj)

            if obj.data and not keep_data_linked:
                # assure root.copy_class won't replace the previous mesh globally
                temp_data = obj.data.copy()
                new_obj.data = temp_data

                # Unlink from previous boolean element
                # and keep object tracked for decorations.
                if is_tracked_opening:
                    mprops = tool.Geometry.get_mesh_props(new_obj.data)
                    mprops.ifc_boolean_id = 0
                    tool.Root.add_tracked_opening(new_obj, tracked_opening_type)

            if obj == context.active_object:
                self.new_active_obj = new_obj
            for collection in obj.users_collection:
                collection.objects.link(new_obj)
            obj.select_set(False)
            new_obj.select_set(True)

            if not element:
                continue

            # clear object's collection so it will be able to have it's own
            tool.Blender.get_object_bim_props(new_obj).collection = None
            # copy the actual class
            new = bonsai.core.root.copy_class(tool.Ifc, tool.Collector, tool.Geometry, tool.Root, obj=new_obj)

            # clean up the orphaned mesh with ifc id of the original object to avoid confusion
            # IfcGridAxis keeps the same mesh data (it's pointing to ifc id 0, so it's not a problem)
            if new and temp_data and not new.is_a("IfcGridAxis"):
                if new.is_a("IfcRelSpaceBoundary"):
                    surface = new.ConnectionGeometry.SurfaceOnRelatingElement
                    temp_data.name = f"0/{surface.id()}"
                    tool.Ifc.link(surface, temp_data)
                else:
                    tool.Blender.remove_data_block(temp_data)

            if new:
                # TODO: handle array data for other cases of duplication
                array_data = arrays_to_duplicate.get(obj, None)
                tool.Model.handle_array_on_copied_element(new, array_data)
                if array_data:
                    for child in tool.Blender.Modifier.Array.get_all_children_objects(new):
                        child.select_set(True)

                # TODO: add new array children to recreate their decomposition too
                old_to_new[element] = [new]
                if new.is_a("IfcRelSpaceBoundary"):
                    tool.Boundary.decorate_boundary(new_obj)

        # Recreate aggregate relationship
        for old in old_to_new.keys():
            if old.is_a("IfcElementAssembly"):
                tool.Root.recreate_aggregate(old_to_new)

        # Remove connections with old objects and recreates paths
        OverrideDuplicateMove.remove_old_connections(old_to_new)
        tool.Root.recreate_connections(connection_relationships, old_to_new)

        # Recreate decompositions
        tool.Root.recreate_decompositions(decomposition_relationships, old_to_new)
        OverrideDuplicateMove.remove_linked_aggregate_data(old_to_new)
        bonsai.bim.handler.refresh_ui_data()
        tool.Root.reload_grid_decorator()
        return old_to_new

    @staticmethod
    def duplicate_item(obj: bpy.types.Object) -> None:
        props = tool.Geometry.get_geometry_props()
        item = tool.Geometry.get_active_representation(obj)
        assert item
        new_item = ifcopenshell.util.element.copy_deep(tool.Ifc.get(), item)
        new_obj = obj.copy()
        assert tool.Geometry.has_mesh_properties(obj.data)
        temp_data = obj.data.copy()
        new_obj.data = temp_data
        tool.Ifc.link(new_item, temp_data)
        new_obj.name = obj.data.name = f"Item/{new_item.is_a()}/{new_item.id()}"
        props.add_item_object(new_obj, new_item)

        for collection in obj.users_collection:
            collection.objects.link(new_obj)

        representation = tool.Geometry.get_active_representation(props.representation_obj)
        representation = ifcopenshell.util.representation.resolve_representation(representation)
        representation.Items = list(representation.Items) + [new_item]

        tool.Geometry.reload_representation(props.representation_obj)

        obj.select_set(False)
        tool.Root.reload_item_decorator()

    @staticmethod
    def process_arrays(
        self, context: bpy.types.Context
    ) -> tuple[dict[bpy.types.Object, Any], set[ifcopenshell.entity_instance]]:
        """ "Process arrays for currently selected objects.

        :return: A tuple of two elements:\n
            - dictionary of objects and their array data. Those objects are safe to duplicate and regenerate arrays using the data.\n
            - set of array children objects. Those objects can be ignored during duplication, they will be recreated automatically
            when arrays are regenerated for objects from the dictionary.
        """
        selected_objects = set(context.selected_objects)
        array_parents = set()
        arrays_to_create: dict[bpy.types.Object, Any] = dict()
        array_children: set[ifcopenshell.entity_instance] = set()  # will be ignored during the duplication

        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            pset = ifcopenshell.util.element.get_pset(element, "BBIM_Array")
            if not pset:
                continue
            array_parents.add(tool.Ifc.get().by_guid(pset["Parent"]))

        for array_parent in array_parents:
            array_parent_obj = tool.Ifc.get_object(array_parent)
            if array_parent_obj not in selected_objects:
                continue

            array_data = []
            for modifier_data in tool.Blender.Modifier.Array.get_modifiers_data(array_parent):
                children = set(tool.Blender.Modifier.Array.get_children_objects(modifier_data))
                if children.issubset(selected_objects):
                    modifier_data["children"] = []
                    array_data.append(modifier_data)
                    array_children.update(children)
                else:
                    break  # allows to duplicate only n first layers of an array

            if array_data:
                arrays_to_create[array_parent_obj] = array_data

        return arrays_to_create, array_children

    def remove_old_connections(old_to_new):
        single_obj = False
        if len(old_to_new) == 1:
            single_obj = True

        for new in old_to_new.values():
            if not hasattr(new[0], "ConnectedTo"):
                continue
            for connection in new[0].ConnectedTo:
                entity = connection.RelatedElement
                if entity in old_to_new.keys() or single_obj:
                    core.remove_connection(tool.Geometry, connection=connection)
            for connection in new[0].ConnectedFrom:
                entity = connection.RelatingElement
                if entity in old_to_new.keys() or single_obj:
                    core.remove_connection(tool.Geometry, connection=connection)

    def remove_linked_aggregate_data(old_to_new):
        for old, new in old_to_new.items():
            pset = ifcopenshell.util.element.get_pset(new[0], "BBIM_Linked_Aggregate")
            if pset:
                pset = tool.Ifc.get().by_id(pset["id"])
                ifcopenshell.api.run("pset.remove_pset", tool.Ifc.get(), product=new[0], pset=pset)

            if new[0].is_a("IfcElementAssembly"):
                linked_aggregate_group = [
                    r.RelatingGroup
                    for r in getattr(new[0], "HasAssignments", []) or []
                    if r.is_a("IfcRelAssignsToGroup")
                    if "BBIM_Linked_Aggregate" in r.RelatingGroup.Name
                ]
                if linked_aggregate_group:
                    tool.Ifc.run("group.unassign_group", group=linked_aggregate_group[0], products=[new[0]])


class OverrideDuplicateMoveLinkedMacro(bpy.types.Macro):
    bl_idname = "bim.override_object_duplicate_move_linked_macro"
    bl_label = "IFC Duplicate Linked"
    bl_options = {"REGISTER", "UNDO"}


class OverrideDuplicateMoveLinked(bpy.types.Operator):
    bl_idname = "bim.override_object_duplicate_move_linked"
    bl_label = "IFC Duplicate Linked"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        return OverrideDuplicateMove.execute_duplicate_operator(self, context, linked=True)

    def _execute(self, context):
        return OverrideDuplicateMove.execute_ifc_duplicate_operator(self, context, linked=True)


class DuplicateMoveLinkedAggregateMacro(bpy.types.Macro):
    bl_description = "Create and move a new linked aggregate"
    bl_idname = "bim.object_duplicate_move_linked_aggregate_macro"
    bl_label = "IFC Duplicate and Move Linked Aggregate"
    bl_options = {"REGISTER", "UNDO"}


class DuplicateMoveLinkedAggregate(bpy.types.Operator):
    bl_idname = "bim.object_duplicate_move_linked_aggregate"
    bl_label = "IFC Duplicate and Move Linked Aggregate"
    bl_options = {"REGISTER", "UNDO"}
    is_interactive: bpy.props.BoolProperty(name="Is Interactive", default=True)

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        return OverrideDuplicateMove.execute_duplicate_operator(self, context, linked=False)

    def _execute(self, context):
        return DuplicateMoveLinkedAggregate.execute_ifc_duplicate_linked_aggregate_operator(self, context)

    @staticmethod
    def execute_ifc_duplicate_linked_aggregate_operator(self, context, location_from_3d_cursor=False):
        self.new_active_obj = None
        self.group_name = "BBIM_Linked_Aggregate"
        self.pset_name = "BBIM_Linked_Aggregate"
        old_to_new = {}

        def select_objects_and_add_data(element):
            add_linked_aggregate_group(element)
            obj = tool.Ifc.get_object(element)
            obj.select_set(True)
            parts = ifcopenshell.util.element.get_parts(element)
            if parts:
                index = get_max_index(parts)
                add_linked_aggregate_pset(element, index)
                index += 1
                for part in parts:
                    if part.is_a("IfcElementAssembly"):
                        select_objects_and_add_data(part)
                    else:
                        index = add_linked_aggregate_pset(part, index)
                        # index += 1

                    obj = tool.Ifc.get_object(part)
                    obj.select_set(True)

        def add_linked_aggregate_pset(part, index):
            pset = ifcopenshell.util.element.get_pset(part, self.pset_name)

            if not pset:
                pset = ifcopenshell.api.run("pset.add_pset", tool.Ifc.get(), product=part, name=self.pset_name)

                ifcopenshell.api.run(
                    "pset.edit_pset",
                    tool.Ifc.get(),
                    pset=pset,
                    properties={"Index": index},
                )

                index += 1
            else:
                pass

            return index

        def add_linked_aggregate_group(element):
            linked_aggregate_group = None
            product_groups_name = [
                r.RelatingGroup.Name
                for r in getattr(element, "HasAssignments", []) or []
                if r.is_a("IfcRelAssignsToGroup")
            ]
            if self.group_name in product_groups_name:
                return

            linked_aggregate_group = ifcopenshell.api.run("group.add_group", tool.Ifc.get(), name=self.group_name)
            ifcopenshell.api.run("group.assign_group", tool.Ifc.get(), products=[element], group=linked_aggregate_group)

        def custom_incremental_naming_for_element_assembly(old_to_new):
            for new in old_to_new.values():
                if new[0].is_a("IfcElementAssembly"):
                    group_elements = [
                        r.RelatedObjects
                        for r in getattr(new[0], "HasAssignments", []) or []
                        if r.is_a("IfcRelAssignsToGroup")
                        if "BBIM_Linked_Aggregate" in r.RelatingGroup.Name
                    ][0]

                    number = len(group_elements) - 1
                    number = f"{number:02d}"
                    new_obj = tool.Ifc.get_object(new[0])
                    pattern1 = r"_\d"
                    if re.findall(pattern1, new_obj.name):
                        split_name = new_obj.name.split("_")
                        new_obj.name = split_name[0] + "_" + number
                        continue
                    pattern2 = r"\.\d{3}"
                    if re.findall(pattern2, new_obj.name):
                        split_name = new_obj.name.split(".")
                        new_obj.name = split_name[0] + "_" + number

        def get_max_index(parts):
            psets = [ifcopenshell.util.element.get_pset(p, "BBIM_Linked_Aggregate") for p in parts]
            index = [i["Index"] for i in psets if i]
            if len(index) > 0:
                index = max(index)
                return index
            else:
                return 0

        def copy_linked_aggregate_data(old_to_new):
            for old, new in old_to_new.items():
                pset = ifcopenshell.util.element.get_pset(old, "BBIM_Linked_Aggregate")
                if pset:
                    new_pset = ifcopenshell.api.run(
                        "pset.add_pset", tool.Ifc.get(), product=new[0], name=self.pset_name
                    )

                    ifcopenshell.api.run(
                        "pset.edit_pset",
                        tool.Ifc.get(),
                        pset=new_pset,
                        properties={"Index": pset["Index"]},
                    )

                if new[0].is_a("IfcElementAssembly"):
                    linked_aggregate_group = [
                        r.RelatingGroup
                        for r in getattr(old, "HasAssignments", []) or []
                        if r.is_a("IfcRelAssignsToGroup")
                        if "BBIM_Linked_Aggregate" in r.RelatingGroup.Name
                    ]
                    tool.Ifc.run("group.assign_group", group=linked_aggregate_group[0], products=new)

        def get_location_from_3d_cursor(old_to_new, aggregate):
            base_obj = tool.Ifc.get_object(aggregate)
            base_obj_location = base_obj.location.copy()

            for new in old_to_new.values():
                new_obj = tool.Ifc.get_object(new[0])
                location_diff = new_obj.location - base_obj_location
                new_obj.location = context.scene.cursor.location + location_diff

        if len(context.selected_objects) != 1:
            return {"FINISHED"}

        selected_obj = context.selected_objects[0]
        selected_element = tool.Ifc.get_entity(selected_obj)

        if selected_element.is_a("IfcElementAssembly"):
            pass
        elif selected_element.Decomposes:
            if selected_element.Decomposes[0].RelatingObject.is_a("IfcElementAssembly"):
                selected_element = selected_element.Decomposes[0].RelatingObject
                selected_obj = tool.Ifc.get_object(selected_element)
        else:
            self.report({"INFO"}, "Object is not part of a IfcElementAssembly.")
            return {"FINISHED"}

        select_objects_and_add_data(selected_element)

        old_to_new = OverrideDuplicateMove.execute_ifc_duplicate_operator(self, context, linked=True)

        tool.Root.recreate_aggregate(old_to_new)

        copy_linked_aggregate_data(old_to_new)

        custom_incremental_naming_for_element_assembly(old_to_new)

        if location_from_3d_cursor:
            get_location_from_3d_cursor(old_to_new, selected_element)

        bonsai.bim.handler.refresh_ui_data()

        return old_to_new


class DuplicateLinkedAggregateTo3dCursor(bpy.types.Operator):
    bl_idname = "bim.duplicate_linked_aggregate_to_3d_cursor"
    bl_label = "IFC Duplicate Linked Aggregate to 3d Cursor"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        return OverrideDuplicateMove.execute_duplicate_operator(self, context, linked=False)

    def _execute(self, context):
        return DuplicateMoveLinkedAggregate.execute_ifc_duplicate_linked_aggregate_operator(
            self, context, location_from_3d_cursor=True
        )


class RefreshLinkedAggregate(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.refresh_linked_aggregate"
    bl_label = "IFC Refresh Linked Aggregate"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def _execute(self, context):
        self.new_active_obj = None
        self.group_name = "BBIM_Linked_Aggregate"
        self.pset_name = "BBIM_Linked_Aggregate"
        refresh_start_time = time()
        old_to_new = {}
        original_names: dict[int, dict[int, str]] = {}

        def delete_objects(element: ifcopenshell.entity_instance) -> None:
            """Remove IfcElementAssembly and it's parts."""
            parts = ifcopenshell.util.element.get_parts(element)
            if parts:
                for part in parts:
                    if part.is_a("IfcElementAssembly"):
                        delete_objects(part)
                        continue

                    tool.Geometry.delete_ifc_object(tool.Ifc.get_object(part))

            tool.Geometry.delete_ifc_object(tool.Ifc.get_object(element))

        def get_original_names(element: ifcopenshell.entity_instance) -> dict[int, dict[int, str]]:
            group = [
                r.RelatingGroup
                for r in getattr(element, "HasAssignments", []) or []
                if r.is_a("IfcRelAssignsToGroup")
                if self.group_name in r.RelatingGroup.Name
            ][0].id()
            original_names[group] = {}

            pset = ifcopenshell.util.element.get_pset(element, self.pset_name)
            index = pset["Index"]
            original_names[group][index] = tool.Ifc.get_object(element).name

            parts = ifcopenshell.util.element.get_parts(element)
            if parts:
                for part in parts:
                    if part.is_a("IfcElementAssembly"):
                        # TODO: missing assignment.
                        original_names | get_original_names(part)
                    else:
                        try:
                            pset = ifcopenshell.util.element.get_pset(part, self.pset_name)
                        except:
                            continue
                        if pset:
                            index = pset["Index"]
                            original_names[group][index] = tool.Ifc.get_object(part).name

            return original_names

        def set_original_name(obj: bpy.types.Object, original_names: dict[int, dict[int, str]]) -> None:
            element = tool.Ifc.get_entity(obj)
            aggregate = ifcopenshell.util.element.get_aggregate(element)
            if ifcopenshell.util.element.get_parts(
                element
            ):  # if element has parts it means it is the base of and aggregate or sub-aggregate
                aggregate = element

            group = [
                r.RelatingGroup
                for r in getattr(aggregate, "HasAssignments", []) or []
                if r.is_a("IfcRelAssignsToGroup")
                if self.group_name in r.RelatingGroup.Name
            ]
            if not group:
                return

            group = group[0].id()

            pset = ifcopenshell.util.element.get_pset(element, self.pset_name)
            index = pset["Index"]

            try:
                obj.name = original_names[group][index]
            except:
                return

        def get_element_assembly(element: ifcopenshell.entity_instance) -> Union[ifcopenshell.entity_instance, None]:
            if element.is_a("IfcElementAssembly"):
                return element
            elif element.Decomposes:
                if element.Decomposes[0].RelatingObject.is_a("IfcElementAssembly"):
                    element = element.Decomposes[0].RelatingObject
                    return element
            else:
                return None

        def handle_selection(
            selected_objs: list[bpy.types.Object],
        ) -> tuple[list[int], list[ifcopenshell.entity_instance]]:
            selected_elements = [tool.Ifc.get_entity(selected_obj) for selected_obj in selected_objs]
            if None in selected_elements:
                self.report({"INFO"}, "Object has no Ifc Metadata.")
                return None, None

            selected_parents = []
            for selected_element in selected_elements:
                selected_element = get_element_assembly(selected_element)
                if not selected_element:
                    self.report({"INFO"}, "Object is not part of a IfcElementAssembly.")
                    return None, None
                selected_parents.append(selected_element)

            selected_parents = list(set(selected_parents))
            linked_aggregate_groups = []
            for selected_element in selected_parents:
                product_linked_agg_group = [
                    r.RelatingGroup
                    for r in getattr(selected_element, "HasAssignments", []) or []
                    if r.is_a("IfcRelAssignsToGroup")
                    if self.group_name in r.RelatingGroup.Name
                ]
                try:
                    linked_aggregate_groups.append(product_linked_agg_group[0].id())
                except:
                    self.report({"INFO"}, "Object is not part of a Linked Aggregate.")
                    return None, None

            return list(set(linked_aggregate_groups)), selected_parents

        def get_original_matrix(
            element: ifcopenshell.entity_instance, base_instance: ifcopenshell.entity_instance
        ) -> tuple[Matrix, tuple[Vector, Quaternion, Vector]]:
            selected_obj = tool.Ifc.get_object(base_instance)
            selected_matrix = selected_obj.matrix_world
            object_duplicate = tool.Ifc.get_object(element)
            duplicate_matrix = object_duplicate.matrix_world.decompose()

            return selected_matrix, duplicate_matrix

        def set_new_matrix(
            selected_matrix: Matrix, duplicate_matrix: tuple[Vector, Quaternion, Vector], old_to_new: dict
        ) -> None:
            for old, new in old_to_new.items():
                new_obj = tool.Ifc.get_object(new[0])
                new_base_matrix = Matrix.LocRotScale(*duplicate_matrix)
                matrix_diff = Matrix.inverted(selected_matrix) @ new_obj.matrix_world
                new_obj_matrix = new_base_matrix @ matrix_diff
                new_obj.matrix_world = new_obj_matrix

        active_element = tool.Ifc.get_entity(context.active_object)
        if not active_element:
            self.report({"INFO"}, "Object has no Ifc metadata.")
            return {"FINISHED"}

        active_element = get_element_assembly(active_element)
        selected_objs = context.selected_objects
        linked_aggregate_groups, selected_parents = handle_selection(selected_objs)
        if not linked_aggregate_groups or not selected_parents:
            return {"FINISHED"}

        if len(linked_aggregate_groups) > 1:
            if len(selected_parents) != len(linked_aggregate_groups):
                self.report(
                    {"INFO"},
                    "Select only one object from each Linked Aggregate or multiple objects from the same Linked Aggregate.",
                )
                return {"FINISHED"}

        for group in linked_aggregate_groups:
            elements = tool.Drawing.get_group_elements(tool.Ifc.get().by_id(group))
            if len(linked_aggregate_groups) > 1:
                base_instance = [e for e in elements if e in selected_parents][0]
                instances_to_refresh = elements

            elif (len(linked_aggregate_groups) == 1) and (len(selected_parents) > 1):
                base_instance = active_element
                instances_to_refresh = [element for element in elements if element in selected_parents]

            else:
                base_instance = active_element
                instances_to_refresh = elements

            for element in instances_to_refresh:
                if element.GlobalId == base_instance.GlobalId:
                    continue

                element_aggregate = ifcopenshell.util.element.get_aggregate(element)

                selected_matrix, duplicate_matrix = get_original_matrix(element, base_instance)

                original_names = get_original_names(element)

                delete_objects(element)

                for obj in context.selected_objects:
                    obj.select_set(False)

                tool.Ifc.get_object(base_instance).select_set(True)

                old_to_new = DuplicateMoveLinkedAggregate.execute_ifc_duplicate_linked_aggregate_operator(self, context)

                set_new_matrix(selected_matrix, duplicate_matrix, old_to_new)

                for old, new in old_to_new.items():
                    if element_aggregate and new[0].is_a("IfcElementAssembly"):
                        new_aggregate = ifcopenshell.util.element.get_aggregate(new[0])

                        if not new_aggregate:
                            bonsai.core.aggregate.assign_object(
                                tool.Ifc,
                                tool.Aggregate,
                                tool.Collector,
                                relating_obj=tool.Ifc.get_object(element_aggregate),
                                related_obj=tool.Ifc.get_object(new[0]),
                            )

                for old, new in old_to_new.items():
                    new_obj = tool.Ifc.get_object(new[0])
                    set_original_name(new_obj, original_names)

        bonsai.bim.handler.refresh_ui_data()

        operator_time = time() - refresh_start_time
        if operator_time > 10:
            self.report({"INFO"}, "Refresh Aggregate was finished in {:.2f} seconds".format(operator_time))
        return {"FINISHED"}


class OverrideJoin(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.override_object_join"
    bl_label = "IFC Join"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.types.Object
    target_element: ifcopenshell.entity_instance

    def invoke(self, context, event):
        if not tool.Ifc.get():
            return bpy.ops.object.join()
        return self.execute(context)

    def _execute(self, context):
        if not tool.Ifc.get():
            return bpy.ops.object.join()

        if not context.active_object:
            return

        self.target = context.active_object
        if tool.Geometry.is_representation_item(self.target):
            return self.join_item()
        elif target_element := tool.Ifc.get_entity(self.target):
            self.target_element = target_element
            return self.join_ifc_obj()
        return self.join_blender_obj()

    def join_item(self) -> None:
        props = tool.Geometry.get_geometry_props()
        ifc_file = tool.Ifc.get()
        item = tool.Geometry.get_active_representation(self.target)
        assert item
        if tool.Geometry.is_meshlike_item(item):
            tool.Geometry.dissolve_triangulated_edges(self.target)
            item_objs = [i.obj for i in props.item_objs if i.obj]
            joined_objs = []
            for selected_obj in bpy.context.selected_objects:
                if selected_obj in item_objs:
                    if selected_obj != self.target:
                        tool.Geometry.dissolve_triangulated_edges(selected_obj)
                        joined_objs.append(selected_obj)
                else:
                    selected_obj.select_set(False)
            bpy.ops.object.join()
            tool.Geometry.edit_meshlike_item(self.target)

            for joined_obj in joined_objs:
                tool.Geometry.delete_ifc_item(joined_obj)

            items_data = [dict(i) for i in props.item_objs if i.obj]
            props.item_objs.clear()
            for item_data in items_data:
                props.add_item_object(item_data["obj"], ifc_file.by_id(item_data["ifc_definition_id"]))

            tool.Geometry.reload_representation(props.representation_obj)
            bpy.context.view_layer.update()
            tool.Root.reload_item_decorator()

    def join_ifc_obj(self) -> None:
        ifc_file = tool.Ifc.get()
        builder = ShapeBuilder(ifc_file)
        si_conversion = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        representation = tool.Geometry.get_active_representation(self.target)
        assert representation
        representation_type = representation.RepresentationType
        if representation_type in ("Tessellation", "Brep"):
            for obj in bpy.context.selected_objects:
                if obj == self.target:
                    continue
                element = tool.Ifc.get_entity(obj)
                if element:
                    ifcopenshell.api.run("root.remove_product", ifc_file, product=element)
            bpy.ops.object.join()
            bpy.ops.bim.update_representation(obj=self.target.name, ifc_representation_class="")
        else:
            # Could support it but need to make sure all objects are on the same plane.
            if representation_type == "Curve2D":
                self.report({"ERROR"}, "Joining representation of type Curve2D is not supported.")
                return

            target_placement = np.array(self.target.matrix_world)
            target_placement[:, 3][:3] /= si_conversion

            def apply_placement(
                local_pos: npt.NDArray[np.float64], obj_placement: ifcopenshell.util.placement.MatrixType
            ) -> npt.NDArray[np.float64]:
                position = obj_placement @ local_pos
                position = np.linalg.inv(target_placement) @ position
                return position

            items = list(representation.Items)
            for obj in bpy.context.selected_objects:
                if obj == self.target:
                    continue
                element = tool.Ifc.get_entity(obj)

                # Non IFC elements cannot be joined since we cannot guarantee SweptSolid compliance
                if not element:
                    obj.select_set(False)
                    continue

                # Only objects of the same representation type can be joined
                obj_rep = tool.Geometry.get_active_representation(obj)
                assert obj_rep
                if obj_rep.RepresentationType != representation_type:
                    obj.select_set(False)
                    self.report(
                        {"ERROR"},
                        f"IFC join failed - object '{obj.name}' has a different representation type "
                        f"({obj_rep.RepresentationType})\nthan target '{self.target.name}' ({representation_type}).",
                    )
                    return

                placement = np.array(obj.matrix_world)
                placement[:, 3][:3] /= si_conversion

                rep_items = list(obj_rep.Items)
                curve_set_items = {}
                curve_set_mapping = {}

                supported_item_types = ("IfcSweptAreaSolid", "IfcIndexedPolyCurve", "IfcGeometricCurveSet")

                def validate_item(item: ifcopenshell.entity_instance) -> bool:
                    return any(item.is_a(ifc_class) for ifc_class in supported_item_types)

                error_msg = "Unsupported representation item type for joining: {}."
                for item in rep_items:
                    item_class = item.is_a()
                    if not validate_item(item):
                        self.report({"ERROR"}, error_msg.format(item_class))
                        return
                    if item_class == "IfcGeometricCurveSet":
                        sub_items = item.Elements
                        for sub_item in item.Elements:
                            if not validate_item(sub_item):
                                self.report({"ERROR"}, error_msg.format(sub_item.is_a()))
                                return
                        rep_items.extend(sub_items)
                        for sub_item in sub_items:
                            curve_set_items[sub_item] = item

                processed_point_lists = {}
                for item in rep_items:
                    item_class = item.is_a()

                    if item_class == "IfcGeometricCurveSet":
                        copied_item = ifc_file.create_entity("IfcGeometricCurveSet", Elements=())
                    elif item_class == "IfcIndexedPolyCurve":
                        # Process points lists separately as they tend to be reused.
                        copied_item = ifcopenshell.util.element.copy_deep(
                            ifc_file, item, exclude=("IfcCartesianPointList",)
                        )
                        new_points = processed_point_lists.get((points := item.Points))
                        if new_points is None:
                            new_points = ifcopenshell.util.element.copy_deep(ifc_file, points)
                            dim = item.Dim
                            append_coord = (1.0,) if dim == 3 else (0.0, 1.0)
                            coords = points.CoordList
                            points.CoordList = [
                                apply_placement(np.append(c, append_coord), placement).tolist()[:3] for c in coords
                            ]
                            processed_point_lists[points] = new_points
                        item.Points = new_points
                    else:
                        copied_item = ifcopenshell.util.element.copy_deep(ifc_file, item)

                    for style in item.StyledByItem:
                        copied_style = ifcopenshell.util.element.copy(ifc_file, style)
                        copied_style.Item = copied_item

                    if item.is_a("IfcSweptAreaSolid"):
                        if copied_item.Position:
                            position = ifcopenshell.util.placement.get_axis2placement(copied_item.Position)
                        else:
                            position = np.eye(4, dtype=float)
                        position = apply_placement(position, placement)
                        copied_item.Position = builder.create_axis2_placement_3d_from_matrix(position)
                    elif item.is_a("IfcIndexedPolyCurve"):
                        curve_set = curve_set_items.get(item)
                        if curve_set:
                            new_curve_set = curve_set_mapping[curve_set]
                            new_curve_set.Elements = new_curve_set.Elements + (copied_item,)
                            continue  # Item is added to curve set items instead of representation items.

                    elif item_class == "IfcGeometricCurveSet":
                        curve_set_mapping[item] = copied_item
                    else:
                        assert False, f"Unexpected item type: {item.is_a()}. This is a bug."

                    items.append(copied_item)
                ifcopenshell.api.run("root.remove_product", ifc_file, product=element)
            representation.Items = items
            bpy.ops.object.join()
            core.switch_representation(
                tool.Ifc,
                tool.Geometry,
                obj=self.target,
                representation=representation,
                should_reload=True,
                is_global=True,
                should_sync_changes_first=False,
                apply_openings=True,
            )

    def join_blender_obj(self) -> None:
        for obj in bpy.context.selected_objects:
            if obj == self.target:
                continue
            # TODO Properly handle element types, grid axes, and representation items
            element = tool.Ifc.get_entity(obj)
            if element:
                ifcopenshell.api.run("root.remove_product", tool.Ifc.get(), product=element)
        bpy.ops.object.join()


class OverridePasteBuffer(bpy.types.Operator):
    bl_idname = "bim.override_paste_buffer"
    bl_label = "IFC Paste BIM Objects"
    bl_description = (
        "Paste objects from the internal clipboard.\n\n"
        "Note that pasted objects will be unliked from IFC, so some IFC data will be lost (psets, materials, etc).\n"
        "To paste object and keep IFC data use 'IFC Duplicate Objects'.\n\n"
        "If working in Bonsai, to prevent issues this operator must be always used instead of the default Blender 'Paste Objects'."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        previously_selected_objects = set(context.selected_objects)
        bpy.ops.view3d.pastebuffer()

        # If object selection did not change, then there were no objects to paste.
        # Then exist early to prevent unlinking previously selected objects.
        pasted_objects = context.selected_objects
        if previously_selected_objects == set(pasted_objects):
            self.report({"INFO"}, "No objects to paste.")
            return {"FINISHED"}

        for obj in pasted_objects:
            # Pasted objects may come from another Blender session, or even
            # from the same session where the original object has since
            # been deleted. As the source element may not exist, paste will
            # always unlink the element. If you want to duplicate an
            # element, use the duplicate commands.
            tool.Root.unlink_object(obj)
        self.report({"INFO"}, f"{len(pasted_objects)} object(s) pasted and unlinked from IFC.")
        return {"FINISHED"}


class OverrideEscape(bpy.types.Operator):
    bl_idname = "bim.override_escape"
    bl_label = "Override Escape"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Geometry.get_geometry_props()
        if props.mode == "ITEM":
            tool.Geometry.disable_item_mode()
        elif props.mode == "EDIT":
            bpy.ops.bim.override_mode_set_object("INVOKE_DEFAULT", should_save=False)
            tool.Geometry.disable_item_mode()
        elif tool.Model.get_model_props().openings:
            bpy.ops.bim.hide_all_openings()
        elif tool.Aggregate.get_aggregate_props().in_aggregate_mode:
            bpy.ops.bim.disable_aggregate_mode()
        elif active_object := context.active_object:
            if tool.Blender.Modifier.try_canceling_editing_modifier_parameters_or_path(active_object):
                pass
        return {"FINISHED"}


class OverrideModeSetEdit(bpy.types.Operator, tool.Ifc.Operator):
    bl_description = "Switch from Object to Item to Edit mode"
    bl_idname = "bim.override_mode_set_edit"
    bl_label = "IFC Mode Set Edit"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="INVOKE")

    def _invoke(self, context, event):
        if not tool.Ifc.get():
            return tool.Blender.toggle_edit_mode(context)
        return self.execute(context)

    def _execute(self, context):
        selected_objs = context.selected_objects  # Purposely exclude active object
        aprops = tool.Aggregate.get_aggregate_props()
        nprops = tool.Nest.get_nest_props()

        if self.has_aggregates(selected_objs):
            if not aprops.in_aggregate_mode:
                bonsai.core.aggregate.enable_aggregate_mode(tool.Aggregate, context.active_object)
                return {"FINISHED"}

        if self.has_nests(selected_objs):
            if not nprops.in_nest_mode:
                bonsai.core.nest.enable_nest_mode(tool.Nest, context.active_object)
                return {"FINISHED"}

        if len(selected_objs) == 1 and context.active_object == selected_objs[0]:
            self.handle_single_object(context, context.active_object)
        elif len(selected_objs) == 0:
            if aprops.in_aggregate_mode:
                bonsai.core.aggregate.disable_aggregate_mode(tool.Aggregate)
                return {"FINISHED"}
            if nprops.in_nest_mode:
                bonsai.core.nest.disable_nest_mode(tool.Nest)
                return {"FINISHED"}
            tool.Geometry.disable_item_mode()
        elif len(selected_objs) > 1:
            self.handle_multiple_selected_objects(context)

    def handle_single_object(self, context: bpy.types.Context, obj: bpy.types.Object) -> None:
        element = tool.Ifc.get_entity(obj)
        props = tool.Geometry.get_geometry_props()
        pprops = tool.Project.get_project_props()
        aprops = tool.Aggregate.get_aggregate_props()
        if obj == props.representation_obj:
            self.report({"ERROR"}, f"Element '{obj.name}' is in item mode and cannot be edited directly")
        elif obj in [o.obj for o in aprops.not_editing_objects]:
            obj.select_set(False)
            self.report(
                {"ERROR"}, f"Element '{obj.name}' does not belong to this aggregate and cannot be edited directly"
            )
        elif obj in pprops.clipping_planes_objs:
            self.report({"ERROR"}, "Clipping planes cannot be edited")
        elif element:
            if not obj.data:
                self.report({"INFO"}, "No geometry to edit")
            elif tool.Geometry.is_locked(element):
                self.report({"ERROR"}, lock_error_message(obj.name))
            elif obj.data and tool.Geometry.is_profile_based(obj.data):
                bpy.ops.bim.hotkey(hotkey="S_E")
            elif element.is_a("IfcRelSpaceBoundary"):
                bpy.ops.bim.enable_editing_boundary_geometry()
            elif element.is_a("IfcGridAxis"):
                self.enable_edit_mode(context)
            elif tool.Blender.Modifier.try_applying_edit_mode(obj, element):
                pass
            else:
                bpy.ops.bim.import_representation_items()
        elif tool.Geometry.is_representation_item(obj):
            self.enable_editing_representation_item(context, obj)
        else:  # A regular Blender object
            self.enable_edit_mode(context)

    def handle_multiple_selected_objects(self, context: bpy.types.Context) -> Union[None, set[str]]:
        obj = context.active_object
        if obj and (tool.Ifc.get_entity(obj) or tool.Geometry.is_representation_item(obj)):
            tool.Blender.select_and_activate_single_object(context, obj)
            self.handle_single_object(context, obj)
        else:
            blender_objs = []
            ifc_objs = []
            for selected_obj in tool.Blender.get_selected_objects():
                if tool.Ifc.get_entity(selected_obj) or tool.Geometry.is_representation_item(selected_obj):
                    ifc_objs.append(selected_obj)
                else:
                    blender_objs.append(selected_obj)

                if blender_objs:
                    for obj in ifc_objs:
                        obj.select_set(False)
                    context.view_layer.objects.active = blender_objs[0]
                    blender_objs[0].select_set(True)
                    return tool.Blender.toggle_edit_mode(context)
                elif ifc_objs:
                    obj = ifc_objs[0]
                    tool.Blender.select_and_activate_single_object(context, obj)
                    self.handle_single_object(context, obj)

    def enable_editing_representation_item(self, context: bpy.types.Context, obj: bpy.types.Object) -> None:
        item = tool.Geometry.get_active_representation(obj)
        assert item
        element = tool.Ifc.get_entity(tool.Geometry.get_geometry_props().representation_obj)
        if tool.Geometry.is_meshlike_item(item):
            tool.Geometry.dissolve_triangulated_edges(obj)
            tool.Blender.select_and_activate_single_object(context, obj)
            assert isinstance(mesh := obj.data, bpy.types.Mesh)
            props = tool.Geometry.get_mesh_props(mesh)
            props.mesh_checksum = tool.Geometry.get_mesh_checksum(mesh)
            self.enable_edit_mode(context)
        elif (
            item.is_a("IfcSweptAreaSolid")
            and (usage := tool.Model.get_usage_type(element))
            and usage in ("LAYER1", "LAYER2")
        ):
            self.report({"INFO"}, f"Parametric {usage} elements cannot be edited directly")
        elif item.is_a("IfcSweptAreaSolid"):
            tool.Geometry.sync_item_positions()
            res = tool.Model.import_profile((profile := item.SweptArea), obj=obj)
            if res is None:
                self.report(
                    {"INFO"},
                    f"Couldn't import profile, editing it directly is not yet supported. Failing profile: {profile}.",
                )
                return
            tool.Ifc.link(item, obj.data)
            self.enable_edit_mode(context)
            ProfileDecorator.install(context)
            if not bpy.app.background:
                tool.Blender.set_viewport_tool("bim.cad_tool")
        elif item.is_a("IfcAnnotationFillArea"):
            tool.Model.import_annotation_fill_area(item, obj=obj)
            tool.Ifc.link(item, obj.data)
            self.enable_edit_mode(context)
            ProfileDecorator.install(context)
            if not bpy.app.background:
                tool.Blender.set_viewport_tool("bim.cad_tool")
        elif tool.Geometry.is_curvelike_item(item):
            tool.Model.import_curve(item, obj=obj)
            tool.Ifc.link(item, obj.data)
            self.enable_edit_mode(context)
            ProfileDecorator.install(context)
            if not bpy.app.background:
                tool.Blender.set_viewport_tool("bim.cad_tool")
        else:
            self.report({"INFO"}, f"Editing {item.is_a()} geometry is not supported")

    def enable_edit_mode(self, context: bpy.types.Context) -> Union[None, set[str]]:
        if tool.Blender.toggle_edit_mode(context) == {"CANCELLED"}:
            return {"CANCELLED"}
        props = tool.Geometry.get_geometry_props()
        props.is_changing_mode = True
        if props.mode != "EDIT":
            props.mode = "EDIT"
        props.is_changing_mode = False

    def has_aggregates(self, objs):
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            aggregate = ifcopenshell.util.element.get_aggregate(element)
            parts = ifcopenshell.util.element.get_parts(element)
            props = tool.Aggregate.get_aggregate_props()
            if (aggregate or parts) and not props.in_aggregate_mode:
                return True
            else:
                return False

    def has_nests(self, objs):
        for obj in objs:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            nest = ifcopenshell.util.element.get_nest(element)
            components = ifcopenshell.util.element.get_components(element)
            props = tool.Nest.get_nest_props()
            if (nest or components) and not props.in_nest_mode:
                return True
            else:
                return False


class OverrideModeSetObject(bpy.types.Operator, tool.Ifc.Operator):
    bl_description = "Switch from Edit to Item or Object mode"
    bl_idname = "bim.override_mode_set_object"
    bl_label = "IFC Mode Set Object"
    bl_options = {"REGISTER", "UNDO"}
    should_save: bpy.props.BoolProperty(name="Should Save", default=True)

    def invoke(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="INVOKE")

    def _invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        if not tool.Ifc.get():
            return tool.Blender.toggle_edit_mode(context)

        if not context.active_object:
            return {"FINISHED"}
        self.is_valid = True

        tool.Blender.toggle_edit_mode(context)

        props = tool.Geometry.get_geometry_props()
        props.is_changing_mode = True
        if props.representation_obj:
            if props.mode != "ITEM":
                props.mode = "ITEM"
        else:
            if props.mode != "OBJECT":
                props.mode = "OBJECT"
        props.is_changing_mode = False

        if context.active_object and self.should_save:
            element = tool.Ifc.get_entity(context.active_object)
            if element and element.is_a("IfcRelSpaceBoundary"):
                return bpy.ops.bim.edit_boundary_geometry()
            elif tool.Geometry.is_representation_item(context.active_object):
                self.edit_representation_item(context.active_object)
                tool.Root.reload_item_decorator()
                # So you can keep hitting tab to cycle out of edit mode
                context.active_object.select_set(False)
                bpy.context.view_layer.objects.active = None
                return {"FINISHED"}

        objs = context.selected_objects or [context.active_object]

        self.edited_objs = []
        self.unchanged_objs_with_openings = []

        for obj in objs:
            if not obj:
                continue

            element = tool.Ifc.get_entity(obj)
            if not element:
                continue

            if tool.Profile.is_editing_profile():
                props = tool.Profile.get_profile_props()
                profile_id = props.active_profile_id
                if profile_id:
                    profile = tool.Ifc.get().by_id(profile_id)
                    if tool.Ifc.get_object(profile):  # We are editing an arbitrary profile
                        bpy.ops.bim.edit_arbitrary_profile()
                elif tool.Blender.Modifier.is_railing(element):
                    bpy.ops.bim.finish_editing_railing_path()
                elif tool.Blender.Modifier.is_roof(element):
                    bpy.ops.bim.finish_editing_roof_path()
                elif tool.Model.get_usage_type(element) == "PROFILE":
                    bpy.ops.bim.edit_extrusion_axis()
                # if in the process of editing arbitrary profile
                elif props.active_arbitrary_profile_id:
                    bpy.ops.bim.edit_arbitrary_profile()
                else:
                    bpy.ops.bim.edit_extrusion_profile()
                return self.execute(context)
            elif representation := tool.Geometry.get_active_representation(obj):
                if not tool.Geometry.is_geometric_data(obj.data):
                    self.is_valid = False
                    self.should_save = False
                assert tool.Geometry.has_mesh_properties(obj.data)
                mesh_props = tool.Geometry.get_mesh_props(obj.data)
                if tool.Geometry.is_meshlike(
                    representation
                ) and mesh_props.mesh_checksum != tool.Geometry.get_mesh_checksum(obj.data):
                    self.edited_objs.append(obj)
                elif getattr(element, "HasOpenings", None):
                    self.unchanged_objs_with_openings.append(obj)
                else:
                    tool.Ifc.finish_edit(obj)
            elif element.is_a("IfcGridAxis"):
                if not tool.Geometry.is_geometric_data(obj.data):
                    self.is_valid = False
                    self.should_save = False
                assert tool.Geometry.has_mesh_properties(obj.data)
                mesh_props = tool.Geometry.get_mesh_props(obj.data)
                if mesh_props.mesh_checksum != tool.Geometry.get_mesh_checksum(obj.data):
                    self.edited_objs.append(obj)
                else:
                    tool.Ifc.finish_edit(obj)

        return self.execute(context)

    def _execute(self, context):
        if not context.active_object:
            return {"FINISHED"}
        for obj in self.edited_objs:
            if self.should_save:
                bpy.ops.bim.update_representation(obj=obj.name, ifc_representation_class="")
                if getattr(tool.Ifc.get_entity(obj), "HasOpenings", False):
                    tool.Geometry.reload_representation(obj)
            else:
                tool.Geometry.reload_representation(obj)
            tool.Ifc.finish_edit(obj)

        for obj in self.unchanged_objs_with_openings:
            tool.Geometry.reload_representation(obj)
            tool.Ifc.finish_edit(obj)
        return {"FINISHED"}

    def edit_representation_item(self, obj: bpy.types.Object) -> None:
        props = tool.Geometry.get_geometry_props()
        item = tool.Geometry.get_active_representation(obj)
        assert item
        if tool.Geometry.is_meshlike_item(item):
            if tool.Geometry.is_geometric_data(obj.data) and obj.data.polygons:
                tool.Geometry.edit_meshlike_item(obj)
            else:
                tool.Geometry.import_item(obj)
        elif item.is_a("IfcSweptAreaSolid"):
            ProfileDecorator.uninstall()
            if not (profile := tool.Model.export_profile(obj)):

                def msg(self, context):
                    self.layout.label(text="INVALID PROFILE")

                bpy.context.window_manager.popup_menu(msg, title="Error", icon="ERROR")
                ProfileDecorator.install(bpy.context)
                self.enable_edit_mode(bpy.context)
                return

            old_profile = item.SweptArea
            profile.ProfileName = old_profile.ProfileName
            for inverse in tool.Ifc.get().get_inverse(old_profile):
                ifcopenshell.util.element.replace_attribute(inverse, old_profile, profile)
            tool.Profile.replace_profile_in_profiles_ui(old_profile.id(), profile.id())
            ifcopenshell.util.element.remove_deep2(tool.Ifc.get(), old_profile)

            tool.Geometry.reload_representation(props.representation_obj)
            tool.Geometry.import_item(obj)
            tool.Geometry.import_item_attributes(obj)

            element = tool.Ifc.get_entity(props.representation_obj)
            # Only certain classes should have a footprint
            if element.is_a() in ("IfcSlab", "IfcRamp"):
                footprint_context = ifcopenshell.util.representation.get_context(
                    tool.Ifc.get(), "Plan", "FootPrint", "SKETCH_VIEW"
                )
                if footprint_context:
                    if profile.is_a("IfcCompositeProfileDef"):
                        profiles = profile.Profiles
                    else:
                        profiles = [profile]
                    curves = []
                    for profile in profiles:
                        curves.append(profile.OuterCurve)
                        if profile.is_a("IfcArbitraryProfileDefWithVoids"):
                            curves.extend(profile.InnerCurves)
                    new_footprint = ifcopenshell.api.run(
                        "geometry.add_footprint_representation",
                        tool.Ifc.get(),
                        context=footprint_context,
                        curves=curves,
                    )
                    old_footprint = ifcopenshell.util.representation.get_representation(
                        element, "Plan", "FootPrint", "SKETCH_VIEW"
                    )
                    if old_footprint:
                        for inverse in tool.Ifc.get().get_inverse(old_footprint):
                            ifcopenshell.util.element.replace_attribute(inverse, old_footprint, new_footprint)
                        bonsai.core.geometry.remove_representation(
                            tool.Ifc, tool.Geometry, obj=obj, representation=old_footprint
                        )
                    else:
                        ifcopenshell.api.run(
                            "geometry.assign_representation",
                            tool.Ifc.get(),
                            product=element,
                            representation=new_footprint,
                        )
        elif item.is_a("IfcAnnotationFillArea"):
            ProfileDecorator.uninstall()
            if not (profile := tool.Model.export_annotation_fill_area(obj)):

                def msg(self, context):
                    self.layout.label(text="INVALID PROFILE")

                bpy.context.window_manager.popup_menu(msg, title="Error", icon="ERROR")
                ProfileDecorator.install(bpy.context)
                self.enable_edit_mode(bpy.context)
                return

            for inverse in tool.Ifc.get().get_inverse(item):
                ifcopenshell.util.element.replace_attribute(inverse, item, profile)
            ifcopenshell.util.element.remove_deep2(tool.Ifc.get(), item)
            tool.Ifc.link(profile, obj.data)

            tool.Geometry.reload_representation(props.representation_obj)
            tool.Geometry.import_item(obj)
            tool.Geometry.import_item_attributes(obj)
        elif tool.Geometry.is_curvelike_item(item):
            ProfileDecorator.uninstall()
            new = tool.Model.export_curves(obj)

            if not new:

                def msg(self, context):
                    self.layout.label(text="INVALID PROFILE")

                bpy.context.window_manager.popup_menu(msg, title="Error", icon="ERROR")
                ProfileDecorator.install(bpy.context)
                self.enable_edit_mode(bpy.context)
                return

            additional_curves = []
            if len(new) > 1:
                additional_curves = new[1:]
            new = new[0]

            for inverse in tool.Ifc.get().get_inverse(item):
                ifcopenshell.util.element.replace_attribute(inverse, item, new)
            ifcopenshell.util.element.remove_deep2(tool.Ifc.get(), item)

            tool.Ifc.link(new, obj.data)
            tool.Geometry.import_item(obj)

            for item in additional_curves:
                representation = tool.Geometry.get_active_representation(props.representation_obj)
                representation = ifcopenshell.util.representation.resolve_representation(representation)
                representation.Items = list(representation.Items) + [item]

                name = f"Item/{item.is_a()}/{item.id()}"
                mesh = bpy.data.meshes.new(name)
                new_obj = bpy.data.objects.new(name, mesh)
                tool.Ifc.link(item, new_obj.data)
                bpy.context.collection.objects.link(new_obj)
                props.add_item_object(new_obj, item)
                new_obj.matrix_world = obj.matrix_world
                tool.Geometry.import_item(new_obj)

            if additional_curves:
                tool.Root.reload_item_decorator()
            tool.Geometry.reload_representation(props.representation_obj)

    def enable_edit_mode(self, context):
        if tool.Blender.toggle_edit_mode(context) == {"CANCELLED"}:
            return {"CANCELLED"}
        props = tool.Geometry.get_geometry_props()
        props.is_changing_mode = True
        if props.mode != "EDIT":
            props.mode = "EDIT"
        props.is_changing_mode = False


class FlipObject(bpy.types.Operator):
    bl_idname = "bim.flip_object"
    bl_label = "Flip Object"
    bl_description = "Flip Element about its local axes, keep the position"
    bl_options = {"REGISTER", "UNDO"}

    flip_local_axes: bpy.props.EnumProperty(
        name="Flip Local Axes", items=(("XY", "XY", ""), ("YZ", "YZ", ""), ("XZ", "XZ", "")), default="XY"
    )

    def execute(self, context):
        for obj in context.selected_objects:
            tool.Geometry.flip_object(obj, self.flip_local_axes)
        return {"FINISHED"}


class EnableEditingRepresentationItems(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_representation_items"
    bl_label = "Enable Editing Representation Items"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()

        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing = True

        props.items.clear()

        def add_tag(item, tag: str) -> None:
            if item.tags:
                item.tags += ","
            item.tags += tag

        if tool.Geometry.has_mesh_properties((data := obj.data)):
            representation = tool.Geometry.get_data_representation(data)
            assert representation

            # Shape aspects must be considered from the PartOfProductDefinitionShape level
            element = tool.Ifc.get_entity(obj)
            assert element
            product_reps = []
            if element.is_a("IfcProduct"):
                product_reps = [element.Representation]
                if element_type := ifcopenshell.util.element.get_type(element):
                    product_reps.extend(element_type.RepresentationMaps or [])
            elif element.is_a("IfcTypeProduct"):
                product_reps = element.RepresentationMaps
            item_aspect = {}
            for product_rep in product_reps:
                for aspect in product_rep.HasShapeAspects:
                    for aspect_rep in aspect.ShapeRepresentations:
                        if aspect_rep.ContextOfItems != representation.ContextOfItems:
                            continue
                        for item in aspect_rep.Items:
                            item_aspect[item] = aspect

            # IfcShapeRepresentation or IfcTopologyRepresentation.
            if not representation.is_a("IfcShapeModel"):
                return
            queue = list(representation.Items)
            while queue:
                item = queue.pop()
                if item.is_a("IfcMappedItem"):
                    queue.extend(item.MappingSource.MappedRepresentation.Items)
                else:
                    new = props.items.add()
                    new.name = item.is_a()
                    new.ifc_definition_id = item.id()

                    styles = []
                    for inverse in tool.Ifc.get().get_inverse(item):
                        if inverse.is_a("IfcStyledItem"):
                            styles = inverse.Styles
                            if styles and styles[0].is_a("IfcPresentationStyleAssignment"):
                                styles = styles[0].Styles
                            for style in styles:
                                if style.is_a("IfcSurfaceStyle"):
                                    new.surface_style = style.Name or "Unnamed"
                                    new.surface_style_id = style.id()
                        elif inverse.is_a("IfcPresentationLayerAssignment"):
                            new.layer = inverse.Name or "Unnamed"
                            new.layer_id = inverse.id()
                        elif inverse.is_a("IfcIndexedTextureMap"):
                            add_tag(new, "UV")
                        elif inverse.is_a("IfcIndexedColourMap"):
                            add_tag(new, "Colour")

                    if aspect := item_aspect.get(item, None):
                        new.shape_aspect = aspect.Name
                        new.shape_aspect_id = aspect.id()

            # sort created items
            sorted_items = sorted(props.items[:], key=lambda i: (not i.shape_aspect, i.shape_aspect))
            for i, item in enumerate(sorted_items[:-1]):  # last item is sorted automatically
                props.items.move(props.items[:].index(item), i)


class DisableEditingRepresentationItems(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_representation_items"
    bl_label = "Disable Editing Representation Items"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        tool.Geometry.get_object_geometry_props(obj).is_editing = False


class RemoveRepresentationItem(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_representation_item"
    bl_label = "Remove Representation Item"
    bl_options = {"REGISTER", "UNDO"}
    representation_item_id: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        if tool.Geometry.get_geometry_props().representation_obj:
            return False  # Artificial restriction for now to prevent removing when in item mode
        if (
            not (obj := tool.Geometry.get_active_or_representation_obj())
            or len(tool.Geometry.get_object_geometry_props(obj).items) <= 1
        ):
            cls.poll_message_set(
                "Active object need to have more than 1 representation items to keep representation valid"
            )
            return False
        return True

    def _execute(self, context):
        assert (obj := tool.Geometry.get_active_or_representation_obj())
        assert (element := tool.Ifc.get_entity(obj))
        ifc_file = tool.Ifc.get()

        representation_item = ifc_file.by_id(self.representation_item_id)
        tool.Geometry.remove_representation_item(representation_item, element)
        tool.Geometry.reload_representation(obj)

        # reload items ui
        bpy.ops.bim.disable_editing_representation_items()
        bpy.ops.bim.enable_editing_representation_items()


class SelectRepresentationItem(bpy.types.Operator):
    bl_idname = "bim.select_representation_item"
    bl_label = "Select Representation Item"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = tool.Geometry.get_geometry_props()
        if not props.representation_obj:
            cls.poll_message_set("No object opened in item mode.")
            return False
        return True

    def execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        obj_props = tool.Geometry.get_object_geometry_props(obj)
        assert obj_props.active_item
        item = tool.Ifc.get().by_id(obj_props.active_item.ifc_definition_id)
        item_ids = self.get_nested_item_ids(item)

        props = tool.Geometry.get_geometry_props()
        for item_obj in props.item_objs:
            obj_ = item_obj.obj
            props = tool.Geometry.get_mesh_props(obj_.data)
            if props.ifc_definition_id in item_ids:
                tool.Blender.select_object(obj_)
        return {"FINISHED"}

    def get_nested_item_ids(self, item):
        results = set()
        if item.is_a("IfcBooleanResult"):
            results.update(self.get_nested_item_ids(item.FirstOperand))
            results.update(self.get_nested_item_ids(item.SecondOperand))
        elif item.is_a("IfcCsgSolid"):
            results.update(self.get_nested_item_ids(item.TreeRootExpression))
        else:
            results.add(item.id())
        return results


def poll_editing_representation_item_style(cls, context):
    if not (obj := tool.Geometry.get_active_or_representation_obj()):
        return False
    props = tool.Geometry.get_object_geometry_props(obj)
    if not props.is_editing:
        return False
    if not (item := props.active_item):
        return False
    shape_aspect = item.shape_aspect
    if shape_aspect == "":
        return True

    from bonsai.bim.module.material.data import ObjectMaterialData

    if not ObjectMaterialData.is_loaded:
        ObjectMaterialData.load()
    constituents = ObjectMaterialData.data["active_material_constituents"]
    if any(c for c in constituents if c == shape_aspect):
        cls.poll_message_set(
            "Style comes from item's shape aspect related to the material constituent "
            "with the same name and cannot be edited on representation item directly - "
            "you can change it from the material constituent"
        )
        return False
    return True


class EnableEditingRepresentationItemStyle(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_representation_item_style"
    bl_label = "Enable Editing Representation Item Style"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return poll_editing_representation_item_style(cls, context)

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_style = True
        ifc_file = tool.Ifc.get()

        # set dropdown to currently active style
        representation_item_id = props.active_item.ifc_definition_id
        representation_item = ifc_file.by_id(representation_item_id)
        style = tool.Style.get_representation_item_style(representation_item)
        if style:
            props.representation_item_style = str(style.id())


class EditRepresentationItemStyle(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_representation_item_style"
    bl_label = "Edit Representation Item Style"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_style = False
        ifc_file = tool.Ifc.get()

        surface_style = ifc_file.by_id(int(props.representation_item_style))
        representation_item_id = props.active_item.ifc_definition_id
        representation_item = ifc_file.by_id(representation_item_id)

        tool.Style.assign_style_to_representation_item(representation_item, surface_style)
        tool.Geometry.reload_representation(obj)
        # reload items ui
        bpy.ops.bim.disable_editing_representation_items()
        bpy.ops.bim.enable_editing_representation_items()


class DisableEditingRepresentationItemStyle(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_representation_item_style"
    bl_label = "Disable Editing Representation Item Style"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_style = False


class UnassignRepresentationItemStyle(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unassign_representation_item_style"
    bl_label = "Unassign Representation Item Style"
    bl_description = "Will remove the style from all selected objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return poll_editing_representation_item_style(cls, context)

    def _execute(self, context):
        active_obj = tool.Geometry.get_active_or_representation_obj()
        assert active_obj
        active_props = tool.Geometry.get_object_geometry_props(active_obj)
        active_props.is_editing_item_style = False

        # Get active representation item
        active_representation_item_id = active_props.active_item.ifc_definition_id
        active_representation_item = tool.Ifc.get_entity_by_id(active_representation_item_id)
        if not active_representation_item:
            self.report({"ERROR"}, f"Couldn't find representation item by id {active_representation_item_id}.")
            return {"CANCELLED"}

        # Retrieve styles applied to the active representation item
        active_styles = set()
        if hasattr(active_representation_item, "StyledByItem"):
            for styled_by_item in active_representation_item.StyledByItem:
                if hasattr(styled_by_item, "Styles"):
                    active_styles.update(styled_by_item.Styles)

        if not active_styles:
            self.report({"ERROR"}, "Couldn't find any styles associated with the active representation item.")
            return {"CANCELLED"}

        # Unassign matching styles from the active object itself
        for style in active_styles:
            tool.Style.assign_style_to_representation_item(active_representation_item, None)
            tool.Geometry.reload_representation(active_obj)
            break  # No need to check further if one matching style is found

        # Iterate over selected objects and unassign matching styles
        for obj in context.selected_objects:
            if obj == active_obj:
                continue  # Skip the active object itself

            # Get the IFC entity directly from the object
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue  # Skip if no IFC entity is found

            representation_item_id = element.Representation.Representations[0].Items[0].id()
            representation_item = tool.Ifc.get_entity_by_id(representation_item_id)

            if representation_item:
                # Retrieve styles for the current representation item
                styles = set()
                if hasattr(representation_item, "StyledByItem"):
                    for styled_by_item in representation_item.StyledByItem:
                        if hasattr(styled_by_item, "Styles"):
                            styles.update(styled_by_item.Styles)

                # Unassign matching styles
                for style in styles:
                    if style in active_styles:
                        tool.Style.assign_style_to_representation_item(representation_item, None)
                        tool.Geometry.reload_representation(obj)
                        break  # No need to check further if one matching style is found

        # Reload UI items
        bpy.ops.bim.disable_editing_representation_items()
        bpy.ops.bim.enable_editing_representation_items()


class EnableEditingRepresentationItemShapeAspect(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_representation_item_shape_aspect"
    bl_label = "Enable Editing Representation Item Shape Aspect"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_shape_aspect = True

        # set dropdown to currently active shape aspect
        shape_aspect_id = props.active_item.shape_aspect_id
        if shape_aspect_id != 0:
            props.representation_item_shape_aspect = str(shape_aspect_id)


class EditRepresentationItemShapeAspect(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_representation_item_shape_aspect"
    bl_label = "Edit Representation Item Shape Aspect"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        element = tool.Ifc.get_entity(obj)
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_shape_aspect = False
        ifc_file = tool.Ifc.get()

        assert props.active_item
        representation_item_id = props.active_item.ifc_definition_id
        representation_item = ifc_file.by_id(representation_item_id)

        if props.representation_item_shape_aspect == "NEW":
            active_representation = tool.Geometry.get_active_representation(obj)
            # find IfcProductRepresentationSelect based on current representation
            if hasattr(element, "Representation"):  # IfcProduct
                product_shape = element.Representation
            else:  # IfcTypeProduct
                for representation_map in element.RepresentationMaps:
                    if representation_map.MappedRepresentation == active_representation:
                        product_shape = representation_map
            previous_shape_aspect_id = props.active_item.shape_aspect_id
            # will be None if item didn't had a shape aspect
            previous_shape_aspect = tool.Ifc.get_entity_by_id(previous_shape_aspect_id)
            shape_aspect = tool.Geometry.create_shape_aspect(
                product_shape, active_representation, [representation_item], previous_shape_aspect
            )
        else:
            shape_aspect = ifc_file.by_id(int(props.representation_item_shape_aspect))
            tool.Geometry.add_representation_item_to_shape_aspect([representation_item], shape_aspect)

        # set attributes from UI
        shape_aspect_attrs = props.shape_aspect_attrs
        shape_aspect.Name = shape_aspect_attrs.name
        shape_aspect.Description = shape_aspect_attrs.description

        shape_aspect_representation = tool.Geometry.get_shape_aspect_representation_for_item(
            shape_aspect, representation_item
        )
        styles = tool.Geometry.get_shape_aspect_styles(element, shape_aspect, representation_item)
        # TODO this looks wrong to me. In theory styles can be > 1 (e.g. curve
        # styles) and then the usecase will assign the wrong style.
        tool.Ifc.run(
            "style.assign_representation_styles",
            shape_representation=shape_aspect_representation,
            styles=styles,
        )
        tool.Geometry.reload_representation(obj)

        # reload items ui
        bpy.ops.bim.disable_editing_representation_items()
        bpy.ops.bim.enable_editing_representation_items()


class DisableEditingRepresentationItemShapeAspect(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_representation_item_shape_aspect"
    bl_label = "Disable Editing Representation Item Shape Aspect"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        props.is_editing_item_shape_aspect = False


class RemoveRepresentationItemFromShapeAspect(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.remove_representation_item_from_shape_aspect"
    bl_label = "Remove Representation Item From Shape Aspect"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = tool.Geometry.get_active_or_representation_obj()
        assert obj
        element = tool.Ifc.get_entity(obj)
        props = tool.Geometry.get_object_geometry_props(obj)
        ifc_file = tool.Ifc.get()

        assert props.active_item
        representation_item_id = props.active_item.ifc_definition_id
        representation_item = ifc_file.by_id(representation_item_id)
        shape_aspect = ifc_file.by_id(props.active_item.shape_aspect_id)

        # unassign items before removing items as removing items
        # might remove shape aspect
        if representation_item.StyledByItem:
            styles = tool.Geometry.get_shape_aspect_styles(element, shape_aspect, representation_item)
            self.remove_styles_from_item(representation_item, styles)
            tool.Geometry.reload_representation(obj)

        tool.Geometry.remove_representation_items_from_shape_aspect([representation_item], shape_aspect)

        # reload items ui
        bpy.ops.bim.disable_editing_representation_items()
        bpy.ops.bim.enable_editing_representation_items()

    def remove_styles_from_item(self, representation_item, styles):
        ifc_file = tool.Ifc.get()
        styled_item = representation_item.StyledByItem[0]
        new_styles = [s for s in styled_item.Styles if s not in styles]
        styled_item.Styles = new_styles

        for style_ in new_styles:
            if style_.is_a("IfcPresentationStyleAssignment"):
                new_assignment_styles = [s for s in styled_item.Styles if s not in styles]
                if not new_assignment_styles:
                    ifc_file.remove(style_)
                else:
                    style_.Styles = new_assignment_styles

        if not styled_item.Styles:
            ifc_file.remove(styled_item)


class ImportRepresentationItems(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.import_representation_items"
    bl_label = "Import Representation Items"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        assert obj
        props = tool.Geometry.get_geometry_props()
        if previous_obj := props.representation_obj:
            previous_obj.hide_set(False)
        props.representation_obj = obj
        obj.hide_set(True)
        tool.Geometry.lock_object(obj)

        props.is_changing_mode = True
        if props.mode != "ITEM":
            props.mode = "ITEM"
        props.is_changing_mode = False

        tool.Loader.load_settings()

        data = obj.data
        assert data
        if "ios_item_ids" in data:  # Has faces.
            item_ids = data["ios_item_ids"]
        elif "ios_edges_item_ids" in data:  # Has edges.
            item_ids = data["ios_edges_item_ids"]
        elif "ios_verts_item_ids" in data:  # Has only verts.
            item_ids = data["ios_verts_item_ids"]
        else:
            assert False, "Unexpected mesh type."

        if not item_ids:
            # It is possible that the user has created a shape that
            # IfcOpenShell cannot render (i.e. boolean clipped everything), but
            # we still want to edit items. I'm not sure the best way to handle
            # this, but for now perhaps we can detect when there are no
            # item_ids at all.
            representation = tool.Ifc.get_entity(data)
            item_ids = [i["item"].id() for i in ifcopenshell.util.representation.resolve_items(representation)]

        queue = list(set(item_ids))
        processed_ids = set()
        boolean_ids = set()
        while queue:
            item_id = queue.pop()
            if item_id in processed_ids:
                continue  # In theory, an item can be used multiple times (e.g. in a boolean stack).
            item = tool.Ifc.get().by_id(item_id)
            if item.is_a("IfcBooleanResult"):
                queue.append(item.FirstOperand.id())
                queue.append(item.SecondOperand.id())
                boolean_ids.add(item.SecondOperand.id())
                continue
            item_mesh = bpy.data.meshes.new(f"Item/{item.is_a()}/{item_id}")
            tool.Ifc.link(item, item_mesh)

            item_obj = bpy.data.objects.new(f"Item/{item.is_a()}/{item_id}", item_mesh)
            item_obj.matrix_world = obj.matrix_world
            bpy.context.collection.objects.link(item_obj)

            props.add_item_object(item_obj, item)
            tool.Geometry.import_item(item_obj)
            tool.Geometry.import_item_attributes(item_obj)

            if not tool.Geometry.is_movable(item):
                tool.Geometry.lock_object(item_obj)

            item_obj.select_set(True)  # so you can quickly hit tab again, for edit mode
            context.view_layer.objects.active = item_obj
            processed_ids.add(item_id)

        tool.Root.reload_item_decorator()


class UpdateItemAttributes(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.update_item_attributes"
    bl_label = "Update Item Attributes"
    bl_description = "Update item attributes in IFC and reload mesh for representation item"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        tool.Geometry.sync_item_positions()
        tool.Geometry.update_item_attributes(obj)
        tool.Geometry.reload_representation(tool.Geometry.get_geometry_props().representation_obj)
        tool.Geometry.import_item(obj)
        tool.Root.reload_item_decorator()


class NameProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.name_profile"
    bl_label = "Name Profile"
    bl_description = "Add name to existing unnamed profile"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    extrusion_item_obj: bpy.props.StringProperty(name="Extrusion Item Object Name")
    profile_name: bpy.props.StringProperty(
        name="Profile Name",
        options={"SKIP_SAVE"},
    )

    if TYPE_CHECKING:
        extrusion_item_obj: str
        profile_name: str

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "profile_name")

    def _execute(self, context):
        if not self.profile_name:
            self.report({"INFO"}, "Profile name is not provided")
            return {"CANCELLED"}

        ifc_file = tool.Ifc.get()
        extrusion_item_obj = bpy.data.objects[self.extrusion_item_obj]
        mesh_props = tool.Geometry.get_mesh_props(extrusion_item_obj.data)
        extrusion = ifc_file.by_id(mesh_props.ifc_definition_id)
        assert extrusion.is_a("IfcSweptAreaSolid")
        profile = extrusion.SweptArea
        profile.ProfileName = self.profile_name
        bonsai.bim.handler.refresh_ui_data()  # Ensure enum is up to date.
        mesh_props.item_profile = str(profile.id())


class AddMeshlikeItem(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_meshlike_item"
    bl_label = "Add Meshlike Item"
    bl_options = {"REGISTER", "UNDO"}
    shape: bpy.props.StringProperty(name="Shape")

    def _execute(self, context):
        props = tool.Geometry.get_geometry_props()
        mesh = bpy.data.meshes.new("Tmp")
        obj = bpy.data.objects.new("Tmp", mesh)
        scene = bpy.context.scene
        scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bm = bmesh.new()
        matrix = props.representation_obj.matrix_world.copy()
        matrix.translation = context.scene.cursor.location
        matrix = props.representation_obj.matrix_world.inverted() @ matrix
        if self.shape == "CUBE":
            bmesh.ops.create_cube(bm, matrix=matrix, size=0.5)
        elif self.shape == "PLANE":
            bmesh.ops.create_grid(bm, matrix=matrix, size=0.5)
        elif self.shape == "CIRCLE":
            bmesh.ops.create_circle(bm, matrix=matrix, radius=0.25, segments=16, cap_ends=True)
        elif self.shape == "UVSPHERE":
            bmesh.ops.create_uvsphere(bm, matrix=matrix, radius=0.25, u_segments=16, v_segments=16)
        elif self.shape == "ICOSPHERE":
            bmesh.ops.create_icosphere(bm, matrix=matrix, radius=0.25, subdivisions=2)
        elif self.shape == "CYLINDER":
            # Cone is legitimate.
            bmesh.ops.create_cone(bm, matrix=matrix, radius1=0.25, radius2=0.25, depth=0.5, segments=16, cap_ends=True)

        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        obj.matrix_world = props.representation_obj.matrix_world
        obj.show_in_front = True
        tool.Geometry.lock_object(obj)

        builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        rep_obj = tool.Geometry.get_geometry_props().representation_obj
        assert rep_obj
        assert isinstance(obj.data, bpy.types.Mesh)
        verts = tool.Blender.get_verts_coordinates(obj.data.vertices)
        verts = verts.astype("d")
        if (coordinate_offset := tool.Geometry.get_cartesian_point_offset(rep_obj)) is not None:
            verts += coordinate_offset
        verts /= unit_scale
        faces = [p.vertices[:] for p in obj.data.polygons]

        representation = tool.Geometry.get_active_representation(props.representation_obj)
        representation = ifcopenshell.util.representation.resolve_representation(representation)

        if tool.Ifc.get().schema == "IFC2X3" or representation.RepresentationType in ("Brep", "AdvancedBrep"):
            item = builder.faceted_brep(verts, faces)
        else:
            item = builder.mesh(verts, faces)

        props.add_item_object(obj, item)
        representation.Items = list(representation.Items) + [item]
        tool.Geometry.reload_representation(props.representation_obj)
        obj.name = obj.data.name = f"Item/{item.is_a()}/{item.id()}"
        tool.Geometry.get_mesh_props(obj.data).ifc_definition_id = item.id()
        tool.Root.reload_item_decorator()


class AddSweptAreaSolidItem(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_swept_area_solid_item"
    bl_label = "Add Swept Area Solid Item"
    bl_options = {"REGISTER", "UNDO"}
    shape: bpy.props.StringProperty(name="Shape")

    def _execute(self, context):
        props = tool.Geometry.get_geometry_props()
        mesh = bpy.data.meshes.new("Tmp")
        obj = bpy.data.objects.new("Tmp", mesh)
        scene = bpy.context.scene
        scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        matrix = props.representation_obj.matrix_world.copy()
        matrix.translation = context.scene.cursor.location
        local_matrix = props.representation_obj.matrix_world.inverted() @ matrix

        obj.show_in_front = True
        obj.matrix_world = matrix
        tool.Geometry.record_object_position(obj)

        builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        if self.shape == "CUBE":
            curve = builder.rectangle(size=Vector((0.5, 0.5)) / unit_scale)
        elif self.shape == "CYLINDER":
            curve = builder.circle(radius=0.25 / unit_scale)
        item = builder.extrude(
            curve,
            magnitude=0.5 / unit_scale,
            position=local_matrix.translation,
            position_x_axis=local_matrix.col[0].to_3d(),
            position_z_axis=local_matrix.col[2].to_3d(),
        )

        representation = tool.Geometry.get_active_representation(props.representation_obj)
        representation = ifcopenshell.util.representation.resolve_representation(representation)

        props.add_item_object(obj, item)
        representation.Items = list(representation.Items) + [item]
        tool.Geometry.reload_representation(props.representation_obj)

        obj.name = obj.data.name = f"Item/{item.is_a()}/{item.id()}"
        tool.Geometry.get_mesh_props(obj.data).ifc_definition_id = item.id()
        tool.Geometry.import_item(obj)
        tool.Geometry.import_item_attributes(obj)
        tool.Root.reload_item_decorator()


class AddCurvelikeItem(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_curvelike_item"
    bl_label = "Add Curvelike Item"
    bl_options = {"REGISTER", "UNDO"}
    CurveShape = Literal["LINE", "CIRCLE", "ELLIPSE"]

    shape: bpy.props.EnumProperty(name="Shape", items=[(i, i, i) for i in get_args(CurveShape)])

    if TYPE_CHECKING:
        shape: CurveShape

    def _execute(self, context):
        props = tool.Geometry.get_geometry_props()

        representation = tool.Geometry.get_active_representation(props.representation_obj)
        is_2d = representation.ContextOfItems.ContextType == "Plan"
        representation = ifcopenshell.util.representation.resolve_representation(representation)

        mesh = bpy.data.meshes.new("Tmp")
        obj = bpy.data.objects.new("Tmp", mesh)
        scene = bpy.context.scene
        scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        tool.Geometry.lock_object(obj)

        matrix = props.representation_obj.matrix_world.copy()
        matrix.translation = context.scene.cursor.location
        local_matrix = props.representation_obj.matrix_world.inverted() @ matrix

        obj.show_in_front = True
        obj.matrix_world = matrix
        tool.Geometry.record_object_position(obj)

        ifc_file = tool.Ifc.get()
        builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)

        offset = local_matrix.translation.to_2d() / unit_scale
        if not is_2d:
            offset = offset.to_3d()

        if self.shape == "LINE":
            if is_2d:
                points = [Vector((0, 0)), Vector((0.5 / unit_scale, 0))]
            else:
                points = [Vector((0, 0, 0)), Vector((0.5 / unit_scale, 0, 0))]
            item = builder.polyline(points=points, position_offset=offset)
        elif self.shape == "CIRCLE":
            item = builder.circle(radius=0.25 / unit_scale, center=offset)
        elif self.shape == "ELLIPSE":
            item = ifc_file.create_entity(
                "IfcEllipse",
                Position=builder.create_axis2_placement_2d(),
                SemiAxis1=0.25 / unit_scale,
                SemiAxis2=0.125 / unit_scale,
            )
        else:
            assert_never(self.shape)

        props.add_item_object(obj, item)
        representation.Items = list(representation.Items) + [item]
        tool.Geometry.reload_representation(props.representation_obj)

        obj.name = obj.data.name = f"Item/{item.is_a()}/{item.id()}"
        tool.Geometry.get_mesh_props(obj.data).ifc_definition_id = item.id()
        tool.Geometry.import_item(obj)
        tool.Geometry.import_item_attributes(obj)


class AddHalfSpaceSolidItem(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_half_space_solid_item"
    bl_label = "Add Half Space Solid Item"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        if (
            not tool.Blender.get_selected_objects()
            or not (active_obj := tool.Blender.get_active_object())
            or not tool.Geometry.is_representation_item(active_obj)
        ):
            # In theory we can have half space solid as a top level item but let's not go there today.
            self.report({"ERROR"}, "Select an item to apply the half space solid to.")
            return {"CANCELLED"}

        props = tool.Geometry.get_geometry_props()
        mesh = bpy.data.meshes.new("Tmp")
        obj = bpy.data.objects.new("Tmp", mesh)
        scene = bpy.context.scene
        scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        matrix = props.representation_obj.matrix_world.copy()
        matrix.translation = context.scene.cursor.location
        local_matrix = props.representation_obj.matrix_world.inverted() @ matrix

        obj.show_in_front = True
        obj.matrix_world = matrix
        tool.Geometry.record_object_position(obj)

        unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
        location = np.array(local_matrix.translation) / unit_scale
        item = builder.half_space_solid(builder.plane(location=location))
        props.add_item_object(obj, item)

        representation = tool.Geometry.get_active_representation(props.representation_obj)
        representation = ifcopenshell.util.representation.resolve_representation(representation)

        representation.Items = list(representation.Items) + [item]
        tool.Geometry.reload_representation(props.representation_obj)

        obj.name = obj.data.name = f"Item/{item.is_a()}/{item.id()}"
        tool.Geometry.get_mesh_props(obj.data).ifc_definition_id = item.id()
        tool.Geometry.import_item(obj)

        # TODO refactor to core and not rely on selection
        tool.Blender.select_and_activate_single_object(context, active_obj)
        tool.Blender.select_object(obj)
        bpy.ops.bim.add_boolean()


class OverrideMoveMacro(bpy.types.Macro):
    bl_idname = "bim.override_move_macro"
    bl_label = "IFC Move Aggregate"
    bl_options = {"REGISTER", "UNDO"}


class OverrideMove(bpy.types.Operator):
    bl_idname = "bim.override_move"
    bl_label = "IFC Move"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        # Deep magick from the dawn of time
        if tool.Ifc.get():
            IfcStore.execute_ifc_operator(self, context)
            if self.new_active_obj:
                context.view_layer.objects.active = self.new_active_obj
            return {"FINISHED"}

        return {"FINISHED"}

    def _execute(self, context):
        # Get filling objects
        selection = []
        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            selection.append(obj)
            for rel in getattr(element, "HasOpenings", []) or []:
                opening = rel.RelatedOpeningElement
                for rel2 in getattr(opening, "HasFillings", []) or []:
                    filling = rel2.RelatedBuildingElement
                    selection.append(tool.Ifc.get_object(filling))
        for obj in selection:
            obj.select_set(True)
            self.new_active_obj = obj

        # Get aggregates
        props = tool.Aggregate.get_aggregate_props()
        not_editing_objs = [o.obj for o in props.not_editing_objects]
        aggregates_to_move = []
        for obj in context.selected_objects:
            self.new_active_obj = None
            if obj in not_editing_objs:
                obj.select_set(False)
                continue
            if obj == props.editing_aggregate:
                continue
            element = tool.Ifc.get_entity(obj)
            if not element or not element.is_a("IfcElement"):
                continue
            parts = ifcopenshell.util.element.get_parts(element)
            if parts:
                aggregates_to_move.append(tool.Ifc.get_object(element))
                continue
            if not parts and props.in_aggregate_mode:
                continue
            aggregate = ifcopenshell.util.element.get_aggregate(element)
            if aggregate:
                aggregates_to_move.append(tool.Ifc.get_object(aggregate))
                obj.select_set(False)
        aggregates_to_move = set(aggregates_to_move)

        if aggregates_to_move:
            for obj in aggregates_to_move:
                obj.select_set(True)
                for part in tool.Aggregate.get_parts_recursively(tool.Ifc.get_entity(obj)):
                    part_obj = tool.Ifc.get_object(part)
                    part_obj.select_set(True)
                self.new_active_obj = obj
            return {"FINISHED"}

        # Get nests
        props = tool.Nest.get_nest_props()
        not_editing_objs = [o.obj for o in props.not_editing_objects]
        nests_to_move = []
        for obj in context.selected_objects:
            self.new_active_obj = None
            if obj in not_editing_objs:
                obj.select_set(False)
                continue
            if obj == props.editing_nest:
                continue
            element = tool.Ifc.get_entity(obj)
            if not element or not element.is_a("IfcElement"):
                continue
            components = ifcopenshell.util.element.get_components(element)
            if components:
                nests_to_move.append(tool.Ifc.get_object(element))
                continue
            if not components and props.in_nest_mode:
                continue
            nest = ifcopenshell.util.element.get_nest(element)
            if nest:
                nests_to_move.append(tool.Ifc.get_object(nest))
                obj.select_set(False)
        nests_to_move = set(nests_to_move)

        if nests_to_move:
            for obj in nests_to_move:
                obj.select_set(True)
                for component in tool.Nest.get_components_recursively(tool.Ifc.get_entity(obj)):
                    component_obj = tool.Ifc.get_object(component)
                    component_obj.select_set(True)
                self.new_active_obj = obj
            return {"FINISHED"}

        return {"FINISHED"}


class EditRepresentationItemLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_representation_item_layer"
    bl_label = "Edit Representation Item Layer"
    bl_description = "Edit presentation layer for the active representation item."
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        obj = context.active_object
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        new_layer = ifc_file.by_id(int(props.representation_item_layer))

        item = ifc_file.by_id(int(props.active_item.ifc_definition_id))
        item_layer = next(iter(item.LayerAssignment), None)

        # We assume just 1 layer can be assigned to representation item.
        if item_layer:
            # Same layer is already assigned.
            if item_layer == new_layer:
                props.is_editing_item_layer = False
                return {"FINISHED"}
            ifcopenshell.api.layer.unassign_layer(ifc_file, [item], item_layer)

        ifcopenshell.api.layer.assign_layer(ifc_file, [item], new_layer)
        props.is_editing_item_layer = False
        bpy.ops.bim.enable_editing_representation_items()
        return {"FINISHED"}


class UnassignRepresentationItemLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unassign_representation_item_layer"
    bl_label = "Unassign Representation Item Layer"
    bl_description = "Unassign presentation layer from the active representation item."
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        obj = context.active_object
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        item = ifc_file.by_id(props.active_item.ifc_definition_id)
        layer = item.LayerAssignment[0]  # If there is no layer, then button is not visible in UI.
        ifcopenshell.api.layer.unassign_layer(ifc_file, [item], layer)
        bpy.ops.bim.enable_editing_representation_items()
        return {"FINISHED"}


class AssignRepresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_representation_layer"
    bl_label = "Assign Representation Layer"
    bl_description = "Assign presentation layer to the active representation."
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        obj = context.active_object
        assert obj
        props = tool.Geometry.get_object_geometry_props(obj)
        new_layer = ifc_file.by_id(int(props.representation_layer))

        representation = tool.Geometry.get_active_representation(obj)
        assert representation
        item_layers = representation.LayerAssignments

        # On practice enum doesn't display already assigned layers
        # but let's double check.
        if new_layer in item_layers:
            props.is_adding_representation_layer = False
            return {"FINISHED"}

        ifcopenshell.api.layer.assign_layer(ifc_file, [representation], new_layer)
        props.is_adding_representation_layer = False
        return {"FINISHED"}


class UnassignRepresentationLayer(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unassign_representation_layer"
    bl_label = "Unassign Representation Layer"
    bl_description = "Unassign presentation layer from the active representation."
    bl_options = {"REGISTER", "UNDO"}

    layer_id: bpy.props.IntProperty()

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        obj = context.active_object
        assert obj

        representation = tool.Geometry.get_active_representation(obj)
        assert representation

        layer = ifc_file.by_id(self.layer_id)
        ifcopenshell.api.layer.unassign_layer(ifc_file, [representation], layer)
        return {"FINISHED"}
