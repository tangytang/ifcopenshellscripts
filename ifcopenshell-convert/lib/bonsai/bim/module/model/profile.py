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
import copy
import bmesh
import mathutils.geometry
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry
import ifcopenshell.util.type
import ifcopenshell.util.unit
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import bonsai.tool as tool
import bonsai.core.type
import bonsai.core.geometry
import bonsai.core.material
import bonsai.core.root
from bonsai.bim.ifc import IfcStore
from math import pi, degrees, atan2
from mathutils import Vector, Matrix
from bonsai.bim.module.model.wall import DumbWallRecalculator
from bonsai.bim.module.model.decorator import ProfileDecorator, PolylineDecorator, ProductDecorator
from bonsai.bim.module.model.polyline import PolylineOperator
from typing import Union, Any, Optional


class DumbProfileGenerator:
    def __init__(self, relating_type):
        self.relating_type = relating_type
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())

    def generate(self, insertion_type="CURSOR"):
        self.insertion_type = insertion_type
        self.file = tool.Ifc.get()
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        material = ifcopenshell.util.element.get_material(self.relating_type)
        if material and material.is_a("IfcMaterialProfileSet"):
            self.profile_set = material
        else:
            return

        self.body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")
        self.axis_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Axis", "GRAPH_VIEW")
        props = tool.Model.get_model_props()

        self.container = None
        self.container_obj = None
        if container := tool.Root.get_default_container():
            self.container = container
            self.container_obj = tool.Ifc.get_object(container)

        self.depth = props.extrusion_depth
        self.rotation = 0
        self.location = Vector((0, 0, 0))
        self.cardinal_point = int(props.cardinal_point)
        if self.insertion_type == "POLYLINE":
            return self.derive_from_polyline()
        elif self.insertion_type == "CURSOR":
            return self.derive_from_cursor()

    def derive_from_polyline(self) -> tuple[list[Union[dict[str, Any], None]], bool]:
        polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline
        polyline_points = polyline_data[0].polyline_points if polyline_data else []
        is_polyline_closed = False
        if len(polyline_points) > 3:
            first_vec = Vector((polyline_points[0].x, polyline_points[0].y, polyline_points[0].z))
            last_vec = Vector((polyline_points[-1].x, polyline_points[-1].y, polyline_points[-1].z))
            if first_vec == last_vec:
                is_polyline_closed = True

        profiles = []
        for i in range(len(polyline_points) - 1):
            vec1 = Vector((polyline_points[i].x, polyline_points[i].y, polyline_points[i].z))
            vec2 = Vector((polyline_points[i + 1].x, polyline_points[i + 1].y, polyline_points[i + 1].z))
            coords = (vec1, vec2)
            profiles.append(self.create_profile_from_2_points(coords))
        return profiles, is_polyline_closed

    def derive_from_cursor(self):
        self.location = bpy.context.scene.cursor.location
        return self.create_profile()

    def create_profile(self):
        ifc_classes = ifcopenshell.util.type.get_applicable_entities(self.relating_type.is_a(), self.file.schema)
        # Standard cases are deprecated, so let's cull them
        ifc_class = [c for c in ifc_classes if "StandardCase" not in c][0]

        mesh = bpy.data.meshes.new("Dummy")
        obj = bpy.data.objects.new(tool.Model.generate_occurrence_name(self.relating_type, ifc_class), mesh)

        matrix_world = Matrix()
        if self.relating_type.is_a() not in ("IfcColumnType", "IfcPileType"):
            if self.insertion_type not in {"POLYLINE"}:
                matrix_world = Matrix.Rotation(pi / 2, 4, "Z") @ Matrix.Rotation(pi / 2, 4, "X") @ matrix_world
                matrix_world = Matrix.Rotation(self.rotation, 4, "Z") @ matrix_world
            else:
                rotation_matrix = self.direction.to_track_quat("Z", "Y")
                matrix_world = rotation_matrix.to_matrix().to_4x4() @ matrix_world

        matrix_world.translation = self.location
        if self.insertion_type not in {"POLYLINE"} and self.container_obj:
            matrix_world.translation.z = self.container_obj.location.z
        element = bonsai.core.root.assign_class(
            tool.Ifc,
            tool.Collector,
            tool.Root,
            obj=obj,
            ifc_class=ifc_class,
            should_add_representation=False,
        )
        ifcopenshell.api.run("type.assign_type", self.file, related_objects=[element], relating_type=self.relating_type)

        material = ifcopenshell.util.element.get_material(element)
        material.CardinalPoint = self.cardinal_point

        obj.matrix_world = matrix_world
        bpy.context.view_layer.update()
        bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)

        if self.axis_context:
            representation = ifcopenshell.api.run(
                "geometry.add_axis_representation",
                tool.Ifc.get(),
                context=self.axis_context,
                axis=[(0.0, 0.0, 0.0), (0.0, 0.0, self.depth)],
            )
            ifcopenshell.api.run(
                "geometry.assign_representation", tool.Ifc.get(), product=element, representation=representation
            )

        representation = ifcopenshell.api.run(
            "geometry.add_profile_representation",
            tool.Ifc.get(),
            context=self.body_context,
            profile=self.profile_set.CompositeProfile or self.profile_set.MaterialProfiles[0].Profile,
            cardinal_point=self.cardinal_point,
            depth=self.depth,
        )
        ifcopenshell.api.run(
            "geometry.assign_representation", tool.Ifc.get(), product=element, representation=representation
        )
        bonsai.core.geometry.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=representation,
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )

        pset = ifcopenshell.api.run("pset.add_pset", self.file, product=element, name="EPset_Parametric")
        ifcopenshell.api.run("pset.edit_pset", self.file, pset=pset, properties={"Engine": "Bonsai.DumbProfile"})

        tool.Blender.select_object(obj)

        return obj

    def create_profile_from_2_points(self, coords, should_round=False) -> Union[dict[str, Any], None]:
        self.direction = coords[1] - coords[0]
        length = self.direction.length
        if round(length, 4) < 0.1:
            return
        data = {"coords": coords}

        self.depth = length
        self.rotation = atan2(self.direction[1], self.direction[0])
        if should_round:
            # Round to nearest 50mm (yes, metric for now)
            self.length = 0.05 * round(length / 0.05)
            # Round to nearest 5 degrees
            nearest_degree = (pi / 180) * 5
            self.rotation = nearest_degree * round(self.rotation / nearest_degree)
        self.location = coords[0]
        data["obj"] = self.create_profile()
        return data


class DumbProfileRegenerator:
    def regenerate_from_profile_def(self, profile):
        self.file = tool.Ifc.get()
        objs = []
        if not profile:
            return

        element_types = set()
        for element in self.get_elements_using_profile(profile):
            obj = tool.Ifc.get_object(element)
            if obj:
                objs.append(obj)
                if element.is_a("IfcElementType"):
                    element_types.add(element)

        DumbProfileRecalculator().recalculate(objs)

        # update related thumbnails
        for element in self.get_element_types_using_profile(profile):
            tool.Model.update_thumbnail_for_element(element, refresh=True)

    def regenerate_from_profile(self, usecase_path, ifc_file, settings):
        self.file = ifc_file
        objs = []
        profile = settings["profile"].Profile
        if not profile:
            return
        for element in self.get_elements_using_profile(profile):
            obj = tool.Ifc.get_object(element)
            if obj:
                objs.append(obj)
        DumbProfileRecalculator().recalculate(objs)

    def get_elements_using_profile(self, profile):
        results = []
        profile_sets = [
            mp.ToMaterialProfileSet[0] for mp in self.file.get_inverse(profile) if mp.is_a("IfcMaterialProfile")
        ]
        for profile_set in profile_sets:
            for inverse in self.file.get_inverse(profile_set):
                if not inverse.is_a("IfcMaterialProfileSetUsage"):
                    continue
                if self.file.schema == "IFC2X3":
                    for rel in self.file.get_inverse(inverse):
                        if not rel.is_a("IfcRelAssociatesMaterial"):
                            continue
                        results.extend(rel.RelatedObjects)
                else:
                    for rel in inverse.AssociatedTo:
                        results.extend(rel.RelatedObjects)
        return results

    def get_element_types_using_profile(self, profile):
        results = []
        profile_sets = [
            mp.ToMaterialProfileSet[0] for mp in self.file.get_inverse(profile) if mp.is_a("IfcMaterialProfile")
        ]
        for profile_set in profile_sets:
            for inverse in self.file.get_inverse(profile_set):
                if not inverse.is_a("IfcRelAssociatesMaterial"):
                    continue
                results.extend(inverse.RelatedObjects)
        return results

    def regenerate_from_type(self, usecase_path, ifc_file, settings):
        relating_type = settings["relating_type"]

        new_material = ifcopenshell.util.element.get_material(relating_type)
        if not new_material or not new_material.is_a("IfcMaterialProfileSet"):
            return

        for related_object in settings["related_objects"]:
            self._regenerate_from_type(related_object)

    def _regenerate_from_type(self, related_object: ifcopenshell.entity_instance) -> None:
        obj = tool.Ifc.get_object(related_object)
        if not obj or not tool.Geometry.get_active_representation(obj):
            return
        DumbProfileRecalculator().recalculate([obj])


class ExtendProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.extend_profile"
    bl_label = "Extend Profile"
    bl_options = {"REGISTER", "UNDO"}
    join_type: bpy.props.StringProperty()

    def _execute(self, context):
        selected_objs = context.selected_objects
        joiner = DumbProfileJoiner()
        if not self.join_type:
            for obj in selected_objs:
                joiner.unjoin(obj)
            return {"FINISHED"}
        if not context.active_object:
            return {"FINISHED"}
        for obj in selected_objs:
            tool.Geometry.clear_scale(obj)

        if len(selected_objs) == 1:
            joiner.join_E(context.active_object, context.scene.cursor.location)
            return {"FINISHED"}

        if len(selected_objs) == 2:
            if self.join_type == "L":
                joiner.join_L([o for o in selected_objs if o != context.active_object][0], context.active_object)
            elif self.join_type == "V":
                joiner.join_V([o for o in selected_objs if o != context.active_object][0], context.active_object)
        if len(selected_objs) < 2:
            return {"FINISHED"}
        if self.join_type == "T":
            for obj in selected_objs:
                if obj == context.active_object:
                    continue
                joiner.join_T(obj, context.active_object)

        return {"FINISHED"}


class DumbProfileJoiner:
    def __init__(self):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        self.axis_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Axis", "GRAPH_VIEW")
        self.body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")

    def unjoin(self, profile1: bpy.types.Object) -> None:
        element1 = tool.Ifc.get_entity(profile1)
        if not element1:
            return

        ifcopenshell.api.run("geometry.disconnect_path", tool.Ifc.get(), element=element1, connection_type="ATSTART")
        ifcopenshell.api.run("geometry.disconnect_path", tool.Ifc.get(), element=element1, connection_type="ATEND")

        axis1 = self.get_profile_axis(profile1)
        axis = copy.deepcopy(axis1)
        body = copy.deepcopy(axis1)
        self.recreate_profile(element1, profile1, axis, body)

    def join_E(self, profile1: bpy.types.Object, target: Vector, connection: Optional[str] = None) -> None:
        """`connection` = `ATEND` / `ATSTART` to explicitly define the reference point for the join.

        For example if profile 1m long and `target` is at (0, 0, 0.1) and `connection` = `None`
        it will implicitly use `connection` = `ATSTART` resulting in profile object 0.9m long and moved to (0, 0, 0.1).

        But with `connection` = `ATEND` it will result in the profile object 0.1m long, locaiton unchanged.
        """
        element1 = tool.Ifc.get_entity(profile1)
        if not element1:
            return
        axis1 = self.get_profile_axis(profile1)
        intersect, connection_value = mathutils.geometry.intersect_point_line(target, *axis1)
        if connection is None:
            connection = "ATEND" if connection_value > 0.5 else "ATSTART"

        ifcopenshell.api.run("geometry.disconnect_path", tool.Ifc.get(), element=element1, connection_type=connection)

        axis = copy.deepcopy(axis1)
        body = copy.deepcopy(axis1)
        axis[1 if connection == "ATEND" else 0] = intersect
        body[1 if connection == "ATEND" else 0] = intersect
        self.recreate_profile(element1, profile1, axis, body)

    def set_depth(self, profile1: bpy.types.Object, si_length: float) -> None:
        element1 = tool.Ifc.get_entity(profile1)
        if not element1:
            return

        ifcopenshell.api.run("geometry.disconnect_path", tool.Ifc.get(), element=element1, connection_type="ATEND")

        axis1 = self.get_profile_axis(profile1)
        axis = copy.deepcopy(axis1)
        body = copy.deepcopy(axis1)
        end = profile1.matrix_world @ Vector((0, 0, si_length))
        axis[1] = end
        body[1] = end
        self.recreate_profile(element1, profile1, axis, body)

    def join_T(self, profile1: bpy.types.Object, profile2: bpy.types.Object) -> None:
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)
        intersect = tool.Cad.intersect_edges(axis1, axis2)
        if intersect:
            intersect, _ = intersect
        else:
            return
        connection = "ATEND" if tool.Cad.edge_percent(intersect, axis1) > 0.5 else "ATSTART"

        ifcopenshell.api.run(
            "geometry.connect_path",
            tool.Ifc.get(),
            related_element=element1,
            relating_element=element2,
            relating_connection="ATPATH",
            related_connection=connection,
            description="BUTT",
        )

        self.recreate_profile(element1, profile1, axis1, axis1)

    def join_V(self, profile1: bpy.types.Object, profile2: bpy.types.Object) -> None:
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)
        intersect = tool.Cad.intersect_edges(axis1, axis2)
        if intersect:
            intersect, _ = intersect
        else:
            return
        profile1_end = "ATEND" if tool.Cad.edge_percent(intersect, axis1) > 0.5 else "ATSTART"
        profile2_end = "ATEND" if tool.Cad.edge_percent(intersect, axis2) > 0.5 else "ATSTART"

        ifcopenshell.api.run(
            "geometry.connect_path",
            tool.Ifc.get(),
            relating_element=element1,
            related_element=element2,
            relating_connection=profile1_end,
            related_connection=profile2_end,
            description="MITRE",
        )

        self.recreate_profile(element1, profile1, axis1, axis1)
        self.recreate_profile(element2, profile2, axis2, axis2)

    def join_L(self, profile1: bpy.types.Object, profile2: bpy.types.Object) -> None:
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)
        intersect = tool.Cad.intersect_edges(axis1, axis2)
        if intersect:
            intersect, _ = intersect
        else:
            return
        profile1_end = "ATEND" if tool.Cad.edge_percent(intersect, axis1) > 0.5 else "ATSTART"
        profile2_end = "ATEND" if tool.Cad.edge_percent(intersect, axis2) > 0.5 else "ATSTART"

        ifcopenshell.api.run(
            "geometry.connect_path",
            tool.Ifc.get(),
            relating_element=element1,
            related_element=element2,
            relating_connection=profile1_end,
            related_connection=profile2_end,
            description="BUTT",
        )

        self.recreate_profile(element1, profile1, axis1, axis1)
        self.recreate_profile(element2, profile2, axis2, axis2)

    def recreate_profile(self, element: ifcopenshell.entity_instance, obj: bpy.types.Object, axis=None, body=None):
        if axis is None or body is None:
            axis = body = self.get_profile_axis(obj)
        self.axis = copy.deepcopy(axis)
        self.body = copy.deepcopy(body)
        material = ifcopenshell.util.element.get_material(element, should_skip_usage=False)
        usage = None
        if not material:
            return
        if "ProfileSet" not in material.is_a():
            return
        if material.is_a("IfcMaterialProfileSetUsage"):
            usage = material
            material = material.ForProfileSet
        self.profile = material.CompositeProfile or material.MaterialProfiles[0].Profile
        self.clippings = []

        for rel in element.ConnectedTo:
            connection = rel.RelatingConnectionType
            other = tool.Ifc.get_object(rel.RelatedElement)
            if connection not in ["ATPATH", "NOTDEFINED"]:
                self.join(
                    obj, other, connection, rel.RelatedConnectionType, is_relating=True, description=rel.Description
                )
        for rel in element.ConnectedFrom:
            connection = rel.RelatedConnectionType
            other = tool.Ifc.get_object(rel.RelatingElement)
            if connection not in ["ATPATH", "NOTDEFINED"]:
                self.join(
                    obj, other, connection, rel.RelatingConnectionType, is_relating=False, description=rel.Description
                )

        new_matrix = copy.deepcopy(obj.matrix_world)
        new_matrix.translation = self.body[0].copy()
        new_matrix.invert()

        for clipping in self.clippings:
            if clipping["operand_type"] == "IfcHalfSpaceSolid":
                clipping["matrix"] = new_matrix @ clipping["matrix"]

        self.clippings.extend(tool.Model.get_manual_booleans(element))

        depth = (self.body[1] - self.body[0]).length

        if self.axis_context:
            axis = [(new_matrix @ a) for a in self.axis]
            new_axis = ifcopenshell.api.run(
                "geometry.add_axis_representation", tool.Ifc.get(), context=self.axis_context, axis=axis
            )
            old_axis = ifcopenshell.util.representation.get_representation(element, "Model", "Axis", "GRAPH_VIEW")
            if old_axis:
                for inverse in tool.Ifc.get().get_inverse(old_axis):
                    ifcopenshell.util.element.replace_attribute(inverse, old_axis, new_axis)
                bonsai.core.geometry.remove_representation(tool.Ifc, tool.Geometry, obj=obj, representation=old_axis)
            else:
                ifcopenshell.api.run(
                    "geometry.assign_representation", tool.Ifc.get(), product=element, representation=new_axis
                )

        def get_placement_axes(
            body_representation: Union[ifcopenshell.entity_instance, None],
        ) -> Union[tuple[tuple[float, float, float], tuple[float, float, float]], tuple[None, None]]:
            if not body_representation:
                return None, None
            extrusion = tool.Model.get_extrusion(body_representation)
            if not extrusion:
                return None, None
            position = extrusion.Position
            if position.Axis:
                return (position.Axis.DirectionRatios, position.RefDirection.DirectionRatios)
            return ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

        old_body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        new_body = ifcopenshell.api.geometry.add_profile_representation(
            tool.Ifc.get(),
            context=self.body_context,
            profile=self.profile,
            depth=depth,
            cardinal_point=usage.CardinalPoint if usage else None,
            clippings=self.clippings,
            placement_zx_axes=get_placement_axes(old_body),
        )

        if old_body:
            for inverse in tool.Ifc.get().get_inverse(old_body):
                ifcopenshell.util.element.replace_attribute(inverse, old_body, new_body)
            assert isinstance(mesh := obj.data, bpy.types.Mesh)
            tool.Ifc.link(new_body, mesh)
            mesh.name = tool.Loader.get_mesh_name(new_body)
            bonsai.core.geometry.remove_representation(tool.Ifc, tool.Geometry, obj=obj, representation=old_body)
        else:
            ifcopenshell.api.run(
                "geometry.assign_representation", tool.Ifc.get(), product=element, representation=new_body
            )

        previous_matrix = obj.matrix_world.copy()
        previous_origin = obj.location.copy()
        obj.location[0], obj.location[1], obj.location[2] = self.body[0]
        bpy.context.view_layer.update()
        if tool.Ifc.is_moved(obj):
            # Openings should move with the host overall ...
            # ... except their position should stay the same along the local Z axis of the wall
            for opening in [r.RelatedOpeningElement for r in element.HasOpenings]:
                percent = tool.Cad.edge_percent(self.body[0], (previous_origin, (previous_matrix @ Vector((0, 0, 1)))))
                is_z_offset_increased = True if percent < 0 else False

                change_in_z = (self.body[0] - previous_origin).length / self.unit_scale
                coordinates = list(opening.ObjectPlacement.RelativePlacement.Location.Coordinates)
                if is_z_offset_increased:
                    coordinates[2] += change_in_z
                else:
                    coordinates[2] -= change_in_z
                opening.ObjectPlacement.RelativePlacement.Location.Coordinates = coordinates
            bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
        bonsai.core.geometry.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=new_body,
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )
        tool.Geometry.record_object_materials(obj)
        if element.is_a("IfcFlowSegment") or element.is_a("IfcFlowFitting"):
            # lazy import to avoid circular import errors
            from bonsai.bim.module.model.mep import MEPGenerator

            MEPGenerator().setup_ports(obj)

    def join(self, profile1, profile2, connection1, connection2, is_relating=True, description="BUTT"):
        element1 = tool.Ifc.get_entity(profile1)
        element2 = tool.Ifc.get_entity(profile2)
        axis1 = self.get_profile_axis(profile1)
        axis2 = self.get_profile_axis(profile2)

        angle = tool.Cad.angle_edges(axis1, axis2, signed=False, degrees=True)
        if tool.Cad.is_x(angle, (0, 180), tolerance=0.001):
            return False

        intersect, _ = tool.Cad.intersect_edges(axis1, axis2)

        proposed_axis = [self.axis[0], intersect] if connection1 == "ATEND" else [intersect, self.axis[1]]

        if tool.Cad.is_x(tool.Cad.angle_edges(self.axis, proposed_axis, degrees=True), 180):
            # The user has moved the element into an invalid position that cannot connect at the desired end
            return False

        self.axis[1 if connection1 == "ATEND" else 0] = intersect

        # Work out body extents

        if connection1 == "ATEND":
            axisl = (profile2.matrix_world.inverted() @ axis1[1]) - (profile2.matrix_world.inverted() @ axis1[0])
        elif connection1 == "ATSTART":
            axisl = (profile2.matrix_world.inverted() @ axis1[0]) - (profile2.matrix_world.inverted() @ axis1[1])
        xy_angle = degrees(Vector((1, 0)).angle_signed(axisl.normalized().to_2d()))
        if xy_angle >= -135 and xy_angle <= -45:
            closest_plane = "bottom"
            furthest_plane = "top"
        elif xy_angle >= 45 and xy_angle <= 135:
            closest_plane = "top"
            furthest_plane = "bottom"
        elif xy_angle >= -45 and xy_angle <= 45:
            closest_plane = "left"
            furthest_plane = "right"
        else:
            closest_plane = "right"
            furthest_plane = "left"

        is_orthogonal = tool.Cad.is_x(tool.Cad.angle_edges(axis1, axis2, degrees=True), 90, tolerance=0.001)

        if description == "MITRE":
            # Mitre joints are an unofficial convention

            # Check the closest plane from the perspective of the other element
            if connection2 == "ATEND":
                axisl = (profile1.matrix_world.inverted() @ axis2[1]) - (profile1.matrix_world.inverted() @ axis2[0])
            elif connection2 == "ATSTART":
                axisl = (profile1.matrix_world.inverted() @ axis2[0]) - (profile1.matrix_world.inverted() @ axis2[1])
            xy_angle2 = degrees(Vector((1, 0)).angle_signed(axisl.normalized().to_2d()))
            if xy_angle2 >= -135 and xy_angle2 <= -45:
                closest_plane2 = "bottom"
                furthest_plane2 = "top"
            elif xy_angle2 >= 45 and xy_angle2 <= 135:
                closest_plane2 = "top"
                furthest_plane2 = "bottom"
            elif xy_angle2 >= -45 and xy_angle2 <= 45:
                closest_plane2 = "left"
                furthest_plane2 = "right"
            else:
                closest_plane2 = "right"
                furthest_plane2 = "left"

            if connection1 == "ATEND":
                if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                    plane = self.get_profile_plane(profile2, furthest_plane)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    self.body[1] = intersect
                else:
                    plane = self.get_profile_plane(profile2, furthest_plane, z_inwards=False)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    max_dim = self.get_max_bound_box_dimension(profile1)
                    self.body[1] = intersect + profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))

                # Mitre clip
                if connection1 == connection2:
                    plane1 = self.get_profile_plane(profile1, furthest_plane2)
                    plane2 = self.get_profile_plane(profile2, furthest_plane)
                    clip1, direction1 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                    plane1 = self.get_profile_plane(profile1, closest_plane2)
                    plane2 = self.get_profile_plane(profile2, closest_plane)
                    clip2, direction2 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )
                else:
                    plane1 = self.get_profile_plane(profile1, furthest_plane2)
                    plane2 = self.get_profile_plane(profile2, furthest_plane)
                    clip1, direction1 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                    plane1 = self.get_profile_plane(profile1, closest_plane2)
                    plane2 = self.get_profile_plane(profile2, closest_plane)
                    clip2, direction2 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                y_axis = direction2
                x_axis = (clip2 - clip1).normalized().to_3d()
                z_axis = x_axis.cross(y_axis)
                self.clippings.append(
                    {
                        "type": "IfcBooleanClippingResult",
                        "operand_type": "IfcHalfSpaceSolid",
                        "matrix": self.create_matrix(clip1, x_axis, y_axis, z_axis),
                    }
                )
            elif connection1 == "ATSTART":
                if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                    plane = self.get_profile_plane(profile2, furthest_plane)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    self.body[0] = intersect
                else:
                    plane = self.get_profile_plane(profile2, furthest_plane, z_inwards=False)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    max_dim = self.get_max_bound_box_dimension(profile1)
                    self.body[0] = intersect - profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))

                if connection1 == connection2:
                    plane1 = self.get_profile_plane(profile1, furthest_plane2)
                    plane2 = self.get_profile_plane(profile2, furthest_plane)
                    clip1, direction1 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                    plane1 = self.get_profile_plane(profile1, closest_plane2)
                    plane2 = self.get_profile_plane(profile2, closest_plane)
                    clip2, direction2 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )
                else:
                    plane1 = self.get_profile_plane(profile1, furthest_plane2)
                    plane2 = self.get_profile_plane(profile2, furthest_plane)
                    clip1, direction1 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                    plane1 = self.get_profile_plane(profile1, closest_plane2)
                    plane2 = self.get_profile_plane(profile2, closest_plane)
                    clip2, direction2 = mathutils.geometry.intersect_plane_plane(
                        plane1.translation, plane1.col[2].to_3d(), plane2.translation, plane2.col[2].to_3d()
                    )

                y_axis = direction2
                x_axis = (clip2 - clip1).normalized().to_3d()
                z_axis = x_axis.cross(y_axis)
                self.clippings.append(
                    {
                        "type": "IfcBooleanClippingResult",
                        "operand_type": "IfcHalfSpaceSolid",
                        "matrix": self.create_matrix(clip1, x_axis, y_axis, z_axis),
                    }
                )
        else:
            # This is the standard L and T joints described by IFC
            if connection1 == "ATEND":
                if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                    plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    self.body[1] = intersect
                else:
                    plane = self.get_profile_plane(
                        profile2,
                        furthest_plane if is_relating else closest_plane,
                        z_inwards=False if is_relating else True,
                    )
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    max_dim = self.get_max_bound_box_dimension(profile1)
                    self.body[1] = intersect + profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))
                    self.clippings.append(
                        {
                            "type": "IfcBooleanClippingResult",
                            "operand_type": "IfcHalfSpaceSolid",
                            "matrix": plane,
                        }
                    )
            elif connection1 == "ATSTART":
                if tool.Cad.is_x(abs(xy_angle), (0, 90, 180), tolerance=0.001) and is_orthogonal:
                    plane = self.get_profile_plane(profile2, furthest_plane if is_relating else closest_plane)
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    self.body[0] = intersect
                else:
                    plane = self.get_profile_plane(
                        profile2,
                        furthest_plane if is_relating else closest_plane,
                        z_inwards=False if is_relating else True,
                    )
                    intersect = mathutils.geometry.intersect_line_plane(*axis1, plane.translation, plane.col[2].to_3d())
                    max_dim = self.get_max_bound_box_dimension(profile1)
                    self.body[0] = intersect - profile1.matrix_world.to_quaternion() @ Vector((0, 0, max_dim))
                    self.clippings.append(
                        {
                            "type": "IfcBooleanClippingResult",
                            "operand_type": "IfcHalfSpaceSolid",
                            "matrix": plane,
                        }
                    )

    def get_max_bound_box_dimension(self, obj):
        x = [v[0] for v in obj.bound_box]
        y = [v[1] for v in obj.bound_box]
        x_dim = max(x) - min(x)
        y_dim = max(y) - min(y)
        return x_dim if x_dim > y_dim else y_dim

    def get_profile_plane(self, obj, plane, z_inwards=True):
        # Get a matrix for one of the bounding planes of the profile to be used as cutting plane
        # Here's an example of looking at the end of an I-Beam profile with a +Z extrusion out of the screen
        #        top
        #      +-----+           Y
        #      |=====|           ^
        #      |  |  |           |
        # left |  |  | right     Z->X
        #      |  |  |
        #      |=====|
        #      +-----+
        #      bottom
        if plane == "top":
            max_y = max([v[1] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((0, max_y, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            if z_inwards:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
            else:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
        elif plane == "bottom":
            min_y = min([v[1] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((0, min_y, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            if z_inwards:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
            else:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
        elif plane == "right":
            max_x = max([v[0] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((max_x, 0, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            if z_inwards:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
            else:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
        elif plane == "left":
            min_x = min([v[0] for v in obj.bound_box])
            p = obj.matrix_world @ Vector((min_x, 0, 0))
            x_axis = obj.matrix_world.to_quaternion() @ Vector((0, 0, 1))
            if z_inwards:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((0, -1, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((1, 0, 0))
            else:
                y_axis = obj.matrix_world.to_quaternion() @ Vector((0, 1, 0))
                z_axis = obj.matrix_world.to_quaternion() @ Vector((-1, 0, 0))
        return self.create_matrix(p, x_axis, y_axis, z_axis)

    def create_matrix(self, p: Vector, x: Vector, y: Vector, z: Vector) -> Matrix:
        return Matrix([x, y, z, p]).to_4x4().transposed()

    def get_profile_axis(self, obj: bpy.types.Object) -> list[Vector]:
        z_values = [v[2] for v in obj.bound_box]
        return [
            (obj.matrix_world @ Vector((0.0, 0.0, min(z_values)))),
            (obj.matrix_world @ Vector((0.0, 0.0, max(z_values)))),
        ]


class RecalculateProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.recalculate_profile"
    bl_label = "Recalculate Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        DumbProfileRecalculator().recalculate(context.selected_objects)
        return {"FINISHED"}


class DumbProfileRecalculator:
    def recalculate(self, profiles):
        "`profiles` is a list of blender profile objects"
        queue = set()

        # also recalculate all connected elements
        for profile in profiles:
            element = tool.Ifc.get_entity(profile)
            queue.add((element, profile))
            connected_elements = []

            for rel in getattr(element, "ConnectedTo", []):
                connected_elements.append(rel.RelatedElement)
            for rel in getattr(element, "ConnectedFrom", []):
                connected_elements.append(rel.RelatingElement)

            for element in connected_elements:
                queue.add((element, tool.Ifc.get_object(element)))

        joiner = DumbProfileJoiner()
        for element, profile in queue:
            if profile:
                joiner.recreate_profile(element, profile)


class ChangeProfileDepth(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.change_profile_depth"
    bl_label = "Update"
    bl_description = "Update Profile Length"
    bl_options = {"REGISTER", "UNDO"}
    depth: bpy.props.FloatProperty(subtype="DISTANCE")

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        joiner = DumbProfileJoiner()
        for obj in context.selected_objects:
            joiner.set_depth(obj, self.depth)
        return {"FINISHED"}


class ChangeCardinalPoint(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.change_cardinal_point"
    bl_label = "Update"
    bl_description = "Update Cardinal Point for all selected objects."
    bl_options = {"REGISTER", "UNDO"}
    cardinal_point: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        objs = []
        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if not element:
                continue
            material = ifcopenshell.util.element.get_material(element, should_skip_usage=False)
            if not material:
                continue
            if material.is_a("IfcMaterialProfileSetUsage"):
                material.CardinalPoint = self.cardinal_point
                objs.append(obj)
        DumbProfileRecalculator().recalculate(objs)
        return {"FINISHED"}


class Rotate90(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.rotate_90"
    bl_label = "Rotate 90"
    bl_options = {"REGISTER", "UNDO"}
    axis: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        profile_objs = []
        layer2_objs = []
        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            usage = tool.Model.get_usage_type(element)
            if usage == "PROFILE":
                profile_objs.append(obj)
            elif usage == "LAYER2":
                layer2_objs.append(obj)
            if element.ConnectedTo or element.ConnectedFrom:
                ifcopenshell.api.run(
                    "geometry.disconnect_path", tool.Ifc.get(), element=element, connection_type="ATSTART"
                )
                ifcopenshell.api.run(
                    "geometry.disconnect_path", tool.Ifc.get(), element=element, connection_type="ATEND"
                )
                ifcopenshell.api.run(
                    "geometry.disconnect_path", tool.Ifc.get(), element=element, connection_type="ATPATH"
                )
            rotate_matrix = Matrix.Rotation(pi / 2, 4, self.axis)
            obj.matrix_world @= rotate_matrix
        bpy.context.view_layer.update()
        DumbProfileRecalculator().recalculate(profile_objs)
        DumbWallRecalculator().recalculate(layer2_objs)
        return {"FINISHED"}


class PatchNonParametricMepSegment(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.patch_non_parametric_mep_segment"
    bl_label = "Set MEP segment Material Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object

    def _execute(self, context):
        bonsai.core.material.patch_non_parametric_mep_segment(
            tool.Ifc, tool.Material, tool.Profile, obj=context.active_object
        )
        bpy.ops.bim.enable_editing_extrusion_axis()
        bpy.ops.bim.edit_extrusion_axis()


class EnableEditingExtrusionAxis(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_extrusion_axis"
    bl_label = "Enable Editing Extrusion Axis"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        axis = ifcopenshell.util.representation.get_representation(element, "Model", "Axis", "GRAPH_VIEW")
        if axis:
            position = obj.matrix_world.copy()
            tool.Model.import_axis(axis.Items[0], obj=obj)
        else:
            body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
            extrusion = tool.Model.get_extrusion(body)

            if extrusion.Position:
                position = Matrix(ifcopenshell.util.placement.get_axis2placement(extrusion.Position).tolist())
                position.translation *= self.unit_scale
            else:
                position = Matrix()

            direction = Vector(extrusion.ExtrudedDirection.DirectionRatios).normalized()
            tool.Model.import_axis((Vector((0, 0, 0)), direction * extrusion.Depth), obj=obj, position=position)

        bpy.ops.object.mode_set(mode="EDIT")
        ProfileDecorator.install(context, exit_edit_mode_callback=lambda: disable_editing_extrusion_axis(context))
        if not bpy.app.background:
            tool.Blender.set_viewport_tool("bim.cad_tool")
        return {"FINISHED"}


def disable_editing_extrusion_axis(context):
    ProfileDecorator.uninstall()
    bpy.ops.object.mode_set(mode="OBJECT")

    obj = context.active_object
    element = tool.Ifc.get_entity(obj)
    body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")

    bonsai.core.geometry.switch_representation(
        tool.Ifc,
        tool.Geometry,
        obj=obj,
        representation=body,
        should_reload=True,
        is_global=True,
        should_sync_changes_first=False,
    )
    return {"FINISHED"}


class DisableEditingExtrusionAxis(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_extrusion_axis"
    bl_label = "Disable Editing Extrusion Axis"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        return disable_editing_extrusion_axis(context)


class EditExtrusionAxis(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_extrusion_axis"
    bl_label = "Edit Extrusion Axis"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        ProfileDecorator.uninstall()
        bpy.ops.object.mode_set(mode="OBJECT")

        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        matrix = obj.matrix_world
        previous_z_axis = matrix.col[2].to_3d().normalized()

        start = matrix @ obj.data.vertices[0].co
        end = matrix @ obj.data.vertices[1].co
        depth = (end - start).length
        z_axis = (end - start).normalized()

        # if z-axis didn't changed we can just reuse the previous rotation
        if not tool.Cad.are_vectors_equal(previous_z_axis, z_axis):
            y_axis = Vector((0, 0, 1))
            # making sure z_axis != y_axis
            if z_axis == y_axis:
                y_axis = Vector((0, 1, 0))

            x_axis = y_axis.cross(z_axis).normalized()
            y_axis = z_axis.cross(x_axis).normalized()

            # update basises
            matrix.col[0].xyz = x_axis
            matrix.col[1].xyz = y_axis
            matrix.col[2].xyz = z_axis

        matrix.translation = start

        body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        bonsai.core.geometry.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=body,
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )

        bpy.context.view_layer.update()

        joiner = DumbProfileJoiner()
        joiner.set_depth(obj, depth)
        return {"FINISHED"}


class DrawPolylineProfile(bpy.types.Operator, PolylineOperator, tool.Ifc.Operator):
    bl_idname = "bim.draw_polyline_profile"
    bl_label = "Draw Polyline Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.space_data.type == "VIEW_3D"

    def __init__(self, *args, **kwargs):
        bpy.types.Operator.__init__(self, *args, **kwargs)
        PolylineOperator.__init__(self)
        self.input_options = ["D", "A", "X", "Y", "Z"]
        self.input_ui = tool.Polyline.create_input_ui(input_options=self.input_options)
        self.relating_type = None
        props = tool.Model.get_model_props()
        relating_type_id = props.relating_type_id
        if relating_type_id:
            self.relating_type = tool.Ifc.get().by_id(int(relating_type_id))

    def create_profiles_from_polyline(self, context: bpy.types.Context) -> Union[set[str], None]:
        if not self.relating_type:
            return {"FINISHED"}

        model_props = tool.Model.get_model_props()

        profiles, is_polyline_closed = DumbProfileGenerator(self.relating_type).generate("POLYLINE")
        if profiles:
            if is_polyline_closed:
                for profile1, profile2 in zip(profiles, profiles[1:] + [profiles[0]]):
                    DumbProfileJoiner().join_V(profile2["obj"], profile1["obj"])
            else:
                if len(profiles) == 1:
                    profile1 = profiles[0]
                    element1 = tool.Ifc.get_entity(profile1["obj"])
                    if element1.is_a("IfcFlowSegment") or element1.is_a("IfcFlowFitting"):
                        # lazy import to avoid circular import errors
                        from bonsai.bim.module.model.mep import MEPGenerator

                        MEPGenerator().setup_ports(profile1["obj"])
                else:
                    for profile1, profile2 in zip(profiles[:-1], profiles[1:]):
                        DumbProfileJoiner().join_V(profile2["obj"], profile1["obj"])

    def modal(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="MODAL")

    def _modal(self, context, event):
        if not self.relating_type:
            self.report({"WARNING"}, "You need to select a profile type.")
            PolylineDecorator.uninstall()
            tool.Blender.update_viewport()
            return {"FINISHED"}

        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()

        self.handle_lock_axis(context, event)  # Must come before "PASS_TRHOUGH"

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self.handle_mouse_move(context, event)
            return {"PASS_THROUGH"}

        self.handle_instructions(context)

        self.handle_mouse_move(context, event, should_round=True)

        self.choose_axis(event, z=True)

        self.choose_plane(event)

        self.handle_snap_selection(context, event)

        if (
            not self.tool_state.is_input_on
            and event.value == "RELEASE"
            and event.type in {"RET", "NUMPAD_ENTER", "RIGHTMOUSE"}
        ):
            self.create_profiles_from_polyline(context)
            context.workspace.status_text_set(text=None)
            ProductDecorator.uninstall()
            PolylineDecorator.uninstall()
            tool.Polyline.clear_polyline()
            tool.Blender.update_viewport()
            return {"FINISHED"}

        self.handle_keyboard_input(context, event)

        self.handle_inserting_polyline(context, event)

        self.get_product_preview_data(context, self.relating_type)

        cancel = self.handle_cancelation(context, event)
        if cancel is not None:
            ProductDecorator.uninstall()
            return cancel

        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="INVOKE")

    def _invoke(self, context, event):
        super().invoke(context, event)
        ProductDecorator.install(context)
        self.tool_state.use_default_container = False
        self.tool_state.plane_method = None
        return {"RUNNING_MODAL"}
