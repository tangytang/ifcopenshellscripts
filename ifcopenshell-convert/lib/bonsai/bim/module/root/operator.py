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
import bmesh
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.util.schema
import ifcopenshell.util.element
import ifcopenshell.util.shape_builder
import ifcopenshell.util.type
import ifcopenshell.util.unit
import bonsai.bim.handler
import bonsai.core.root as core
import bonsai.core.geometry
import bonsai.tool as tool
import bonsai.bim.module.root.prop as root_prop
from bonsai.bim.ifc import IfcStore
from bonsai.bim.helper import get_enum_items, prop_with_search
from mathutils import Vector
from typing import TYPE_CHECKING


class EnableReassignClass(bpy.types.Operator):
    bl_idname = "bim.enable_reassign_class"
    bl_label = "Enable Reassign IFC Class"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rprops = tool.Root.get_root_props()
        obj = context.active_object
        self.file = tool.Ifc.get()
        element = tool.Ifc.get_entity(obj)
        assert element
        ifc_class = element.is_a()
        props = tool.Blender.get_object_bim_props(obj)
        props.is_reassigning_class = True
        ifc_products = tool.Root.get_ifc_products()
        schema = tool.Ifc.schema()
        declaration = schema.declaration_by_name(ifc_class)
        for ifc_product in ifc_products:
            if ifcopenshell.util.schema.is_a(declaration, ifc_product):
                rprops.ifc_product = ifc_product
                break
        else:
            self.report({"ERROR"}, f"Couldn't find matching IFC product for the selected object: '{element}'.")
            props.is_reassigning_class = False
            return {"CANCELLED"}

        element = self.file.by_id(tool.Blender.get_ifc_definition_id(obj))
        rprops.ifc_class = element.is_a()
        rprops.relating_class_object = None
        if hasattr(element, "PredefinedType"):
            if element.PredefinedType:
                rprops.ifc_predefined_type = element.PredefinedType
            userdefined_type = ifcopenshell.util.element.get_predefined_type(element)
            rprops.ifc_userdefined_type = userdefined_type or ""
        return {"FINISHED"}


class DisableReassignClass(bpy.types.Operator):
    bl_idname = "bim.disable_reassign_class"
    bl_label = "Disable Reassign IFC Class"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = tool.Blender.get_object_bim_props(context.active_object)
        props.is_reassigning_class = False
        return {"FINISHED"}


class ReassignClass(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.reassign_class"
    bl_label = "Reassign IFC Class"
    bl_description = "Reassign IFC class for selected objects"
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty()

    def _execute(self, context):
        if self.obj:
            objects = [bpy.data.objects.get(self.obj)]
        else:
            objects = set(context.selected_objects + [context.active_object])
        self.file = tool.Ifc.get()
        root_props = tool.Root.get_root_props()
        ifc_product = root_props.ifc_product
        ifc_class = root_props.ifc_class
        type_ifc_class = next(iter(ifcopenshell.util.type.get_applicable_types(ifc_class, self.file.schema)), None)

        predefined_type = root_props.ifc_predefined_type
        if predefined_type == "USERDEFINED":
            predefined_type = root_props.ifc_userdefined_type

        # NOTE: root.reassign_class
        # automatically will reassign class for other occurrences of the type
        # so we need to run it only for the types or non-typed elements
        elements_to_reassign: dict[ifcopenshell.entity_instance, str] = dict()
        # need to update blender object name
        # for all elements that were changed in the process
        elements_to_update: set[ifcopenshell.entity_instance] = set()
        for obj in objects:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue

            same_ifc_product = element.is_a(ifc_product)

            if not same_ifc_product:
                if not (element.is_a("IfcElement") and ifc_product == "IfcElementType") and not (
                    element.is_a("IfcElementType") and ifc_product == "IfcElement"
                ):
                    self.report(
                        {"ERROR"}, f"Not supported class reassignment for object '{obj.name}' -> {ifc_product}."
                    )
                    return {"CANCELLED"}

            props = tool.Blender.get_object_bim_props(obj)
            props.is_reassigning_class = False
            if element.is_a("IfcTypeObject"):
                elements_to_reassign[element] = ifc_class
                elements_to_update.update(ifcopenshell.util.element.get_types(element))
                continue
            elif same_ifc_product and (element_type := ifcopenshell.util.element.get_type(element)):
                assert type_ifc_class
                elements_to_reassign[element_type] = type_ifc_class
                elements_to_update.update(ifcopenshell.util.element.get_types(element_type))
                continue

            # non-typed element
            elements_to_reassign[element] = ifc_class

        # store elements to objects to update later as elements will get invalid
        # after class reassignment
        elements_to_update = elements_to_update | set(elements_to_reassign)
        objects_to_update = set(o for e in elements_to_update if (o := tool.Ifc.get_object(e)))

        reassigned_elements: set[ifcopenshell.entity_instance] = set()
        for element, ifc_class_ in elements_to_reassign.items():
            element = ifcopenshell.api.root.reassign_class(
                self.file,
                product=element,
                ifc_class=ifc_class_,
                predefined_type=predefined_type,
                # Provide occurrence class in all cases as it won't really matter
                # for non-IfcTypeProducts.
                occurrence_class=ifc_class,
            )
            reassigned_elements.add(element)

        for obj in objects_to_update:
            tool.Root.set_object_name(obj, tool.Ifc.get_entity(obj))
            tool.Collector.assign(obj)
        return {"FINISHED"}


class AssignClass(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.assign_class"
    bl_label = "Assign IFC Class"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Assign the IFC Class to the selected non-ifc objects."
    obj: bpy.props.StringProperty()
    ifc_class: bpy.props.StringProperty()
    predefined_type: bpy.props.StringProperty()
    userdefined_type: bpy.props.StringProperty()
    context_id: bpy.props.IntProperty()

    # TODO: is never used?
    should_add_representation: bpy.props.BoolProperty(default=True)

    ifc_representation_class: bpy.props.StringProperty()

    if TYPE_CHECKING:
        obj: str
        ifc_class: str
        predefined_type: str
        userdefined_type: str
        context_id: int
        should_add_representation: bool
        ifc_representation_class: str

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        props = tool.Root.get_root_props()
        objects: list[bpy.types.Object] = []
        if self.obj:
            objects = [bpy.data.objects[self.obj]]
        else:
            objects = list(tool.Blender.get_selected_objects())

        if not objects:
            self.report({"INFO"}, "No objects selected.")
            return

        ifc_class = self.ifc_class or props.ifc_class
        predefined_type = self.userdefined_type if self.predefined_type == "USERDEFINED" else self.predefined_type
        ifc_context = self.context_id
        if not ifc_context and get_enum_items(props, "contexts", context):
            ifc_context = int(props.contexts or "0") or None
        if ifc_context:
            ifc_context = tool.Ifc.get().by_id(ifc_context)

        schema = ifcopenshell.schema_by_name(ifc_file.schema)
        declaration = schema.declaration_by_name(ifc_class)
        is_structural = ifcopenshell.util.schema.is_a(declaration, "IfcStructuralItem")

        # Manage selection as operator can be called not from UI but using `object` argument.
        current_selection = tool.Blender.get_objects_selection(context)
        tool.Blender.clear_objects_selection()

        for obj in objects:
            element = tool.Ifc.get_entity(obj)
            if element:
                continue

            if obj.mode != "OBJECT":
                self.report({"ERROR"}, "Object must be in OBJECT mode to assign class")
                continue

            # Clear any transform modifications.
            if not tool.Blender.apply_transform_as_local(obj):
                self.report(
                    {"ERROR"},
                    f"Object '{obj.name}' has parent/constraints with a shear transform that cannot be applied safely as a local transform.\n"
                    "Please apply parent/constraints manually and try again.",
                )
                continue

            if (
                self.should_add_representation
                and not is_structural
                and isinstance(obj.data, bpy.types.Mesh)
                and obj.data.polygons
            ):
                # Export mesh as tesselation.

                def ensure_single_user_mesh(mesh: bpy.types.Mesh) -> None:
                    if mesh.users == 1:
                        return
                    obj.select_set(True)
                    # temp_override is not supported.
                    bpy.ops.object.make_single_user(
                        object=True, obdata=True, material=False, animation=False, obdata_animation=False
                    )
                    obj.select_set(False)

                # Apply geometry.
                if obj.modifiers:
                    ensure_single_user_mesh(obj.data)
                    with context.temp_override(selected_editable_objects=[obj]):
                        bpy.ops.object.convert(target="MESH")

                # Apply scale.
                if obj.scale != (1, 1, 1):
                    ensure_single_user_mesh(obj.data)
                    is_negative = obj.matrix_world.is_negative
                    with context.temp_override(selected_editable_objects=[obj]):
                        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True, properties=False)
                    # object.transform_apply is losing normals.
                    if is_negative:
                        for polygon in obj.data.polygons:
                            polygon.flip()

                if tool.Geometry.mesh_has_loose_geometry(obj.data):
                    self.report(
                        {"WARNING"},
                        f"Mesh '{obj.data.name}' has loose geometry, loose geometry will be ignored to save mesh to IFC as a tessellation.",
                    )

                element = core.assign_class(
                    tool.Ifc,
                    tool.Collector,
                    tool.Root,
                    obj=obj,
                    ifc_class=ifc_class,
                    predefined_type=predefined_type,
                    should_add_representation=False,
                )
                representation = tool.Geometry.export_mesh_to_tessellation(obj, ifc_context)
                ifcopenshell.api.geometry.assign_representation(tool.Ifc.get(), element, representation)
                bonsai.core.geometry.switch_representation(
                    tool.Ifc,
                    tool.Geometry,
                    obj=obj,
                    representation=representation,
                    should_reload=True,
                    is_global=True,
                    should_sync_changes_first=False,
                )
            else:

                def is_representation_supported() -> bool:
                    # We don't support much topological representations
                    # and need to prevent assigning IfcShapeRepresentations to structural items.
                    if is_structural:
                        return False
                    data = obj.data
                    # Is empty mesh.
                    if isinstance(data, bpy.types.Mesh) and not data.vertices:
                        return False
                    # Is empty curve.
                    if isinstance(data, bpy.types.Curve) and not data.splines:
                        return False
                    return True

                element = core.assign_class(
                    tool.Ifc,
                    tool.Collector,
                    tool.Root,
                    obj=obj,
                    ifc_class=ifc_class,
                    predefined_type=predefined_type,
                    should_add_representation=self.should_add_representation and is_representation_supported(),
                    context=ifc_context,
                    ifc_representation_class=self.ifc_representation_class,
                )
                representation = tool.Geometry.get_active_representation(obj)
                if representation:
                    tool.Geometry.reload_representation(obj)
                elif obj.data is not None:
                    new_obj = tool.Geometry.recreate_object_with_data(obj, None)

        # TODO: reload representation might lead to the object being replaced by object of the other type.
        # We probably should track it somehow and keep the original selection.

        # Validation selection.
        new_selected_objects = list(filter(tool.Blender.is_valid_data_block, current_selection[2]))
        active_object = current_selection[1]
        if active_object and not tool.Blender.is_valid_data_block(active_object):
            active_object = None
        current_selection = (current_selection[0], active_object, new_selected_objects)

        tool.Blender.set_objects_selection(*current_selection)


class UnlinkObject(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.unlink_object"
    bl_label = "Unlink Object"
    bl_description = (
        "Unlink Blender object from it's linked IFC element.\n\n"
        "You can either remove element the blender object is linked to from IFC or keep it. "
        "Note that keeping the unlinked element in IFC might lead to unpredictable issues "
        "and should be used only by advanced users"
    )
    bl_options = {"REGISTER", "UNDO"}
    obj: bpy.props.StringProperty(name="Object Name")
    should_delete: bpy.props.BoolProperty(name="Delete IFC Element", default=True)
    skip_invoke: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def _execute(self, context):
        if self.obj:
            objects = [bpy.data.objects.get(self.obj)]
        else:
            objects = context.selected_objects

        objects: list[bpy.types.Object]
        for obj in objects:
            was_active_object = obj == context.active_object

            tool.Ifc.finish_edit(obj)

            element = tool.Ifc.get_entity(obj)
            if element and self.should_delete:
                object_name = obj.name

                # Copy object, so it won't be removed by `delete_ifc_object`
                obj_copy = obj.copy()
                if obj.data:
                    obj_copy.data = obj.data.copy()

                    # prevent unlinking materials that might be used elsewhere
                    replacements: dict[bpy.types.Material, bpy.types.Material] = dict()
                    for material_slot in obj_copy.material_slots:
                        material = material_slot.material
                        if material is None:
                            continue

                        if material in replacements:
                            material_replacement = replacements[material]

                        # no need to copy non-ifc materials as unlinking won't do anything to them
                        elif tool.Ifc.get_entity(material) is None:
                            replacements[material] = material
                            continue

                        else:
                            material_replacement = material.copy()
                            replacements[material] = material_replacement

                        material_slot.material = material_replacement

                tool.Geometry.delete_ifc_object(obj)

                obj = obj_copy
                obj.name = object_name
            elif element:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                tool.Ifc.unlink(element)

            tool.Root.unlink_object(obj)
            for collection in obj.users_collection:
                # Reset collection because its original collection may be removed too.
                collection.objects.unlink(obj)
            bpy.context.scene.collection.objects.link(obj)

            if was_active_object:
                tool.Blender.set_active_object(obj)
        return {"FINISHED"}

    def draw(self, context):
        row = self.layout.row()
        row.prop(self, "should_delete")

    def invoke(self, context, event):
        if self.skip_invoke:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)


class AddElement(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.add_element"
    bl_label = "Add Element"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Add an IFC physical product, construction type, and more"
    is_specific_tool: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    ifc_product: bpy.props.StringProperty(options={"SKIP_SAVE"})
    ifc_class: bpy.props.StringProperty(options={"SKIP_SAVE"})
    skip_dialog: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="INVOKE")

    def _invoke(self, context, event):
        props = tool.Root.get_root_props()
        # For convenience, preselect OBJs if applicable
        if props.ifc_product == "IfcFeatureElement":
            if (obj := tool.Blender.get_active_object(is_selected=True)) and obj.type == "MESH":
                props.featured_obj = obj
                props.representation_template = "EXTRUSION"
                props.representation_obj = None
        elif (obj := tool.Blender.get_active_object(is_selected=True)) and obj.type == "MESH":
            props.representation_template = "OBJ"
            props.representation_obj = obj
        # For convenience, preselect IFC class
        if self.ifc_product:
            props.ifc_product = self.ifc_product
        if self.ifc_class:
            props.ifc_class = self.ifc_class
        if self.skip_dialog:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def _execute(self, context):
        props = tool.Root.get_root_props()
        predefined_type = (
            props.ifc_userdefined_type if props.ifc_predefined_type == "USERDEFINED" else props.ifc_predefined_type
        )
        representation_template = props.representation_template
        ifc_file = tool.Ifc.get()

        if props.ifc_product == "IfcFeatureElement" and not props.featured_obj:
            return self.report({"WARNING"}, "A featured element must be nominated.")

        ifc_context = None
        if get_enum_items(props, "contexts", context):
            ifc_context = int(props.contexts or "0") or None
            if ifc_context:
                ifc_context = tool.Ifc.get().by_id(ifc_context)

        if representation_template in (
            "EMPTY",
            "LAYERSET_AXIS2",
            "LAYERSET_AXIS3",
            "PROFILESET",
        ) or representation_template.startswith("FLOW_SEGMENT_"):
            mesh = None
        elif representation_template == "OBJ" and not props.representation_obj:
            mesh = None
        else:
            mesh = bpy.data.meshes.new("Mesh")

        obj = bpy.data.objects.new(props.ifc_class[3:], mesh)
        obj.name = props.name or "Unnamed"
        obj.location = bpy.context.scene.cursor.location
        element = core.assign_class(
            tool.Ifc,
            tool.Collector,
            tool.Root,
            obj=obj,
            ifc_class=props.ifc_class,
            predefined_type=predefined_type,
            should_add_representation=False,
        )
        element.Description = props.description or None

        if representation_template == "EMTPY" or not ifc_context:
            pass
        elif representation_template == "OBJ" and props.representation_obj:
            obj.matrix_world = props.representation_obj.matrix_world.copy()
            obj.scale = (1, 1, 1)
            representation = tool.Geometry.export_mesh_to_tessellation(props.representation_obj, ifc_context)
            ifcopenshell.api.geometry.assign_representation(tool.Ifc.get(), element, representation)
            bonsai.core.geometry.switch_representation(
                tool.Ifc,
                tool.Geometry,
                obj=obj,
                representation=representation,
                should_reload=True,
                is_global=True,
                should_sync_changes_first=False,
            )
            if not tool.Ifc.get_entity(props.representation_obj):
                bpy.data.objects.remove(props.representation_obj)
        elif representation_template == "MESH":
            builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=0.5)
            verts = [v.co / unit_scale for v in bm.verts]
            faces = [[v.index for v in p.verts] for p in bm.faces]
            item = builder.mesh(verts, faces)
            bm.free()
            representation = builder.get_representation(ifc_context, [item])
            ifcopenshell.api.geometry.assign_representation(tool.Ifc.get(), element, representation)
            bonsai.core.geometry.switch_representation(
                tool.Ifc,
                tool.Geometry,
                obj=obj,
                representation=representation,
                should_reload=True,
                is_global=True,
                should_sync_changes_first=False,
            )
        elif representation_template == "EXTRUSION":
            builder = ifcopenshell.util.shape_builder.ShapeBuilder(tool.Ifc.get())
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            curve = builder.rectangle(size=Vector((0.5, 0.5)) / unit_scale)
            item = builder.extrude(curve, magnitude=0.5 / unit_scale)
            representation = builder.get_representation(ifc_context, [item])
            ifcopenshell.api.geometry.assign_representation(tool.Ifc.get(), element, representation)
            bonsai.core.geometry.switch_representation(
                tool.Ifc,
                tool.Geometry,
                obj=obj,
                representation=representation,
                should_reload=True,
                is_global=True,
                should_sync_changes_first=False,
            )
        elif representation_template in ("LAYERSET_AXIS2", "LAYERSET_AXIS3"):
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            materials = tool.Ifc.get().by_type("IfcMaterial")
            if materials:
                material = materials[0]  # Arbitrarily pick a material
            else:
                material = ifcopenshell.api.run("material.add_material", tool.Ifc.get(), name="Unknown")
            rel = ifcopenshell.api.run(
                "material.assign_material", tool.Ifc.get(), products=[element], type="IfcMaterialLayerSet"
            )
            layer_set = rel.RelatingMaterial
            layer = ifcopenshell.api.run("material.add_layer", tool.Ifc.get(), layer_set=layer_set, material=material)
            thickness = 0.1  # Arbitrary metric thickness for now
            layer.LayerThickness = thickness / unit_scale
            pset = ifcopenshell.api.run("pset.add_pset", tool.Ifc.get(), product=element, name="EPset_Parametric")
            if representation_template == "LAYERSET_AXIS2":
                axis = "AXIS2"
            elif representation_template == "LAYERSET_AXIS3":
                axis = "AXIS3"
            ifcopenshell.api.run("pset.edit_pset", tool.Ifc.get(), pset=pset, properties={"LayerSetDirection": axis})
        elif representation_template == "PROFILESET" or representation_template.startswith("FLOW_SEGMENT_"):
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            materials = tool.Ifc.get().by_type("IfcMaterial")
            if materials:
                material = materials[0]  # Arbitrarily pick a material
            else:
                material = ifcopenshell.api.run("material.add_material", tool.Ifc.get(), name="Unknown")
            if representation_template == "PROFILESET":
                profile_id = tool.Blender.get_enum_safe(props, "profile")
                if profile_id in ("-", None):
                    profile = next((p for p in ifc_file.by_type("IfcProfileDef") if p.ProfileName), None)
                    if profile is None:
                        size = 0.5 / unit_scale
                        profile = ifc_file.create_entity(
                            "IfcRectangleProfileDef",
                            ProfileName="New Profile",
                            ProfileType="AREA",
                            XDim=size,
                            YDim=size,
                        )
                else:
                    profile = ifc_file.by_id(int(profile_id))
            else:
                # NOTE: defaults dims are in meters / mm
                # for now default names are hardcoded to mm
                if representation_template == "FLOW_SEGMENT_RECTANGULAR":
                    default_x_dim = 0.4
                    default_y_dim = 0.2
                    profile_name = f"{props.ifc_class}-{default_x_dim*1000}x{default_y_dim*1000}"
                    profile = tool.Ifc.get().create_entity(
                        "IfcRectangleProfileDef",
                        ProfileName=profile_name,
                        ProfileType="AREA",
                        XDim=default_x_dim / unit_scale,
                        YDim=default_y_dim / unit_scale,
                    )
                elif representation_template == "FLOW_SEGMENT_CIRCULAR":
                    default_diameter = 0.1
                    profile_name = f"{props.ifc_class}-{default_diameter*1000}"
                    profile = tool.Ifc.get().create_entity(
                        "IfcCircleProfileDef",
                        ProfileName=profile_name,
                        ProfileType="AREA",
                        Radius=(default_diameter / 2) / unit_scale,
                    )
                elif representation_template == "FLOW_SEGMENT_CIRCULAR_HOLLOW":
                    default_diameter = 0.15
                    default_thickness = 0.005
                    profile_name = f"{props.ifc_class}-{default_diameter*1000}x{default_thickness*1000}"
                    profile = tool.Ifc.get().create_entity(
                        "IfcCircleHollowProfileDef",
                        ProfileName=profile_name,
                        ProfileType="AREA",
                        Radius=(default_diameter / 2) / unit_scale,
                        WallThickness=default_thickness,
                    )

            rel = ifcopenshell.api.run(
                "material.assign_material", tool.Ifc.get(), products=[element], type="IfcMaterialProfileSet"
            )
            profile_set = rel.RelatingMaterial
            material_profile = ifcopenshell.api.run(
                "material.add_profile", tool.Ifc.get(), profile_set=profile_set, material=material
            )
            ifcopenshell.api.run(
                "material.assign_profile", tool.Ifc.get(), material_profile=material_profile, profile=profile
            )
        elif representation_template == "WINDOW":
            with context.temp_override(active_object=obj, selected_objects=[]):
                bpy.ops.bim.add_window()
        elif representation_template == "DOOR":
            with context.temp_override(active_object=obj, selected_objects=[]):
                bpy.ops.bim.add_door()
        elif representation_template == "STAIR":
            with context.temp_override(active_object=obj, selected_objects=[]):
                bpy.ops.bim.add_stair()
        elif representation_template == "RAILING":
            with context.temp_override(active_object=obj, selected_objects=[]):
                bpy.ops.bim.add_railing()
        elif representation_template == "ROOF":
            with context.temp_override(active_object=obj, selected_objects=[]):
                bpy.ops.bim.add_roof()

        bpy.context.view_layer.update()  # Ensures obj.matrix_world is correct

        if props.ifc_product == "IfcFeatureElement":
            tool.Feature.add_feature(props.featured_obj, [obj])
            new = tool.Model.get_model_props().openings.add()
            new.obj = obj
            bpy.ops.bim.show_openings()
            tool.Model.purge_scene_openings()
            tool.Collector.assign(obj)

        bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
        tool.Blender.set_active_object(obj)

    def draw(self, context):
        props = tool.Root.get_root_props()
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        row = self.layout.row()
        row.prop(props, "name")
        row = self.layout.row()
        row.prop(props, "description")
        if not self.is_specific_tool:
            if not self.ifc_product:
                prop_with_search(self.layout, props, "ifc_product", text="Definition", should_click_ok=True)
            prop_with_search(self.layout, props, "ifc_class", should_click_ok=True)
        ifc_predefined_types = root_prop.get_ifc_predefined_types(props, context)
        if ifc_predefined_types:
            prop_with_search(self.layout, props, "ifc_predefined_type", should_click_ok=True)
            if props.ifc_predefined_type == "USERDEFINED":
                row = self.layout.row()
                row.prop(props, "ifc_userdefined_type")
        if props.ifc_product == "IfcFeatureElement":
            row = self.layout.row()
            row.prop(props, "featured_obj", text="Featured Object")
        prop_with_search(self.layout, props, "representation_template", text="Representation", should_click_ok=True)
        if props.representation_template == "OBJ":
            row = self.layout.row()
            row.prop(props, "representation_obj", text="Object")
        elif props.representation_template == "PROFILESET":
            row = self.layout.row()
            row.prop(props, "profile", text="Profile")
        if props.representation_template != "EMPTY":
            prop_with_search(self.layout, props, "contexts", should_click_ok=True)
