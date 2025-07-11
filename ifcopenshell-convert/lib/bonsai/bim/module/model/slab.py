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
import json
import bmesh
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.geometry
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.unit
import ifcopenshell.util.type
import bonsai.core.type
import bonsai.core.geometry
import bonsai.core.root
import bonsai.tool as tool
from bonsai.bim.ifc import IfcStore
from math import cos, pi
from mathutils import Vector, Matrix
from bonsai.bim.module.model.decorator import ProfileDecorator, PolylineDecorator, ProductDecorator
from bonsai.bim.module.model.polyline import PolylineOperator
from bonsai.bim.module.model.wall import DumbWallRecalculator
from typing import Optional


class DumbSlabGenerator:
    def __init__(self, relating_type: ifcopenshell.entity_instance):
        self.relating_type = relating_type

    def generate(self, insertion_type="CURSOR"):
        self.file = tool.Ifc.get()
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        thicknesses = []
        for rel in self.relating_type.HasAssociations:
            if rel.is_a("IfcRelAssociatesMaterial"):
                material = rel.RelatingMaterial
                if material.is_a("IfcMaterialLayerSet"):
                    thicknesses = [l.LayerThickness for l in material.MaterialLayers]
                    break
        if not sum(thicknesses):
            return

        self.body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")
        self.footprint_context = ifcopenshell.util.representation.get_context(
            tool.Ifc.get(), "Plan", "FootPrint", "SKETCH_VIEW"
        )

        props = tool.Model.get_model_props()

        self.polyline = None
        self.container = None
        self.container_obj = None
        if container := tool.Root.get_default_container():
            self.container = container
            self.container_obj = tool.Ifc.get_object(container)

        self.depth = sum(thicknesses) * self.unit_scale
        self.width = 3
        self.length = 3
        self.rotation = 0
        self.location = Vector((0, 0, 0))
        self.x_angle = 0 if tool.Cad.is_x(props.x_angle, 0, tolerance=0.001) else props.x_angle

        if insertion_type == "POLYLINE":
            return self.derive_from_polyline()
        elif insertion_type == "WALLS":
            return self.derive_from_walls()
        elif insertion_type == "CURSOR":
            return self.derive_from_cursor()

    def derive_from_polyline(self):
        polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline
        polyline_points = polyline_data[0].polyline_points if polyline_data else []
        self.location = Vector((polyline_points[0].x, polyline_points[0].y, self.container_obj.location.z))
        self.polyline = [tuple(Vector((p.x, p.y, 0.0)) - self.location) for p in polyline_points]

        if len(self.polyline) <= 2:
            return

        # Always assume a closed polyline
        if self.polyline[0] != self.polyline[-1]:
            self.polyline.append(self.polyline[0])

        return self.create_slab()

    def derive_from_cursor(self):
        self.location = bpy.context.scene.cursor.location
        return self.create_slab()

    def derive_from_walls(self):
        walls = tool.Model.get_connected_walls(bpy.context.selected_objects)
        polyline_points = []
        poly = tool.Model.get_polygons_from_wall_axis(walls)
        polyline_points = [tuple([v for v in c]) for c in poly.exterior.coords]

        self.location = Vector((polyline_points[0][0], polyline_points[0][1], self.container_obj.location.z))
        self.polyline = [tuple(Vector((p[0], p[1], 0.0)) - self.location) for p in polyline_points]

        if len(self.polyline) <= 2:
            return

        # Always assume a closed polyline
        if self.polyline[0] != self.polyline[-1]:
            self.polyline.append(self.polyline[0])

        return self.create_slab()

    def create_slab(self):
        ifc_classes = ifcopenshell.util.type.get_applicable_entities(self.relating_type.is_a(), self.file.schema)
        # Standard cases are deprecated, so let's cull them
        ifc_class = [c for c in ifc_classes if "StandardCase" not in c][0]

        mesh = bpy.data.meshes.new("Dummy")
        obj = bpy.data.objects.new(tool.Model.generate_occurrence_name(self.relating_type, ifc_class), mesh)

        matrix_world = Matrix()
        matrix_world.translation = self.location
        if self.container_obj:
            matrix_world.translation.z = self.container_obj.location.z
        else:
            matrix_world.translation.z
        obj.matrix_world = matrix_world
        bpy.context.view_layer.update()

        element = bonsai.core.root.assign_class(
            tool.Ifc,
            tool.Collector,
            tool.Root,
            obj=obj,
            ifc_class=ifc_class,
            should_add_representation=False,
        )
        ifcopenshell.api.run("type.assign_type", self.file, related_objects=[element], relating_type=self.relating_type)

        bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
        representation = ifcopenshell.api.run(
            "geometry.add_slab_representation",
            tool.Ifc.get(),
            context=self.body_context,
            depth=self.depth,
            x_angle=self.x_angle,
            polyline=self.polyline,
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
        obj.matrix_world = obj.matrix_world @ Matrix.Rotation(self.x_angle, 4, "X")

        if self.footprint_context:
            extrusion = tool.Model.get_extrusion(representation)
            if extrusion.SweptArea.is_a("IfcArbitraryClosedProfileDef"):
                curves = [extrusion.SweptArea.OuterCurve]
                if extrusion.SweptArea.is_a("IfcArbitraryProfileDefWithVoids"):
                    curves.extend(extrusion.SweptArea.InnerCurves)
                representation = ifcopenshell.api.run(
                    "geometry.add_footprint_representation",
                    tool.Ifc.get(),
                    context=self.footprint_context,
                    curves=curves,
                )
                ifcopenshell.api.run(
                    "geometry.assign_representation", tool.Ifc.get(), product=element, representation=representation
                )

        pset = ifcopenshell.api.run("pset.add_pset", self.file, product=element, name="EPset_Parametric")
        ifcopenshell.api.run("pset.edit_pset", self.file, pset=pset, properties={"Engine": "Bonsai.DumbLayer3"})
        material = ifcopenshell.util.element.get_material(element)
        material.LayerSetDirection = "AXIS3"
        tool.Blender.select_object(obj)
        return obj


class DumbSlabPlaner:
    def regenerate_from_layer_set_usage(self, usecase_path, ifc_file, settings):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        obj = bpy.context.active_object
        element = tool.Ifc.get_entity(obj)

        # Called from material.add_layer or material.remove_layer
        material = ifcopenshell.util.element.get_material(element)
        material_set_usage = tool.Ifc.get().by_id(material.id())
        if not getattr(material_set_usage, "ForLayerSet", False):
            return
        layer_set = material_set_usage.ForLayerSet

        total_thickness = sum([l.LayerThickness for l in layer_set.MaterialLayers])
        if not total_thickness:
            return

        self.change_thickness(element, total_thickness)

    def regenerate_from_layer(self, layer: ifcopenshell.entity_instance) -> None:
        for layer_set in layer.ToMaterialLayerSet:
            self.regenerate_from_layer_set(layer_set)

    def regenerate_from_layer_set(self, layer_set: ifcopenshell.entity_instance) -> None:
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        total_thickness = sum([l.LayerThickness for l in layer_set.MaterialLayers])
        if not total_thickness:
            return

        for inverse in tool.Ifc.get().get_inverse(layer_set):
            if not inverse.is_a("IfcMaterialLayerSetUsage") or inverse.LayerSetDirection != "AXIS3":
                continue
            if tool.Ifc.get().schema == "IFC2X3":
                for rel in tool.Ifc.get().get_inverse(inverse):
                    if not rel.is_a("IfcRelAssociatesMaterial"):
                        continue
                    for element in rel.RelatedObjects:
                        self.change_thickness(element, total_thickness)
            else:
                for rel in inverse.AssociatedTo:
                    for element in rel.RelatedObjects:
                        self.change_thickness(element, total_thickness)

    def regenerate_from_type(self, usecase_path, ifc_file, settings):
        relating_type = settings["relating_type"]

        new_material = ifcopenshell.util.element.get_material(relating_type)
        if not new_material or not new_material.is_a("IfcMaterialLayerSet"):
            return

        parametric = ifcopenshell.util.element.get_psets(settings["relating_type"]).get("EPset_Parametric")
        layer_set_direction = None
        if parametric:
            layer_set_direction = parametric.get("LayerSetDirection", layer_set_direction)
        new_thickness = sum([l.LayerThickness for l in new_material.MaterialLayers])

        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        for related_object in settings["related_objects"]:
            self._regenerate_from_type(related_object, layer_set_direction, new_thickness)

    def _regenerate_from_type(
        self, related_object: ifcopenshell.entity_instance, layer_set_direction: Optional[str], new_thickness: float
    ) -> None:
        obj = tool.Ifc.get_object(related_object)
        if not obj or not tool.Geometry.get_active_representation(obj):
            return

        material = ifcopenshell.util.element.get_material(related_object)
        if not material or not material.is_a("IfcMaterialLayerSetUsage"):
            return
        if layer_set_direction:
            material.LayerSetDirection = layer_set_direction
        if material.LayerSetDirection == "AXIS3":
            self.change_thickness(related_object, new_thickness)

    def regenerate_from_occurence(self, element, material_set_usage):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        layer_set = material_set_usage.ForLayerSet
        total_thickness = sum([l.LayerThickness for l in layer_set.MaterialLayers])
        if not total_thickness:
            return
        self.change_thickness(element, total_thickness)

    def change_thickness(self, element: ifcopenshell.entity_instance, thickness: float) -> None:
        if tool.Model.get_usage_type(element) != "LAYER3":
            return
        layer_params = tool.Model.get_material_layer_parameters(element)
        ifc_file = tool.Ifc.get()
        body_context = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")
        obj = tool.Ifc.get_object(element)
        if not obj:
            return

        if thickness == 0:
            return

        representation = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        if representation:
            extrusion = tool.Model.get_extrusion(representation)
            if extrusion:
                # TODO Right now we don't have a reliable way to calculate the existing x_angle only based solely on the extrusion direction.
                # For instances, a 30 degrees angled extrusion with positive direction has the same extrusion direction as a
                # -150 degrees angled extrusion with negative direction. The difference lies in the object's rotation.
                # This means that things can get messy if the user changes the object x angle somehow. We have to figure out an alternative approach.
                existing_x_angle = obj.rotation_euler.x
                existing_x_angle = 0 if tool.Cad.is_x(existing_x_angle, 0, tolerance=0.001) else existing_x_angle
                existing_x_angle = 0 if tool.Cad.is_x(existing_x_angle, pi, tolerance=0.001) else existing_x_angle
                existing_x_angle = 0 if tool.Cad.is_x(existing_x_angle, 2 * pi, tolerance=0.001) else existing_x_angle
                direction_ratios = Vector(extrusion.ExtrudedDirection.DirectionRatios)
                offset_direction = direction_ratios.copy()
                perpendicular_depth = thickness * abs(1 / cos(existing_x_angle))
                perpendicular_offset = layer_params["offset"] * abs(1 / cos(existing_x_angle)) / self.unit_scale

                # Check angle and z direction to determine whether the extrusion direction is positive or negative
                if (abs(existing_x_angle) < (pi / 2) and direction_ratios.z > 0) or (
                    abs(existing_x_angle) > (pi / 2) and direction_ratios.z < 0
                ):
                    # The extrusion direction is positive. If the layer_parameter is set to negative,
                    # then the we change the extrusion direction.
                    if layer_params["direction_sense"] == "NEGATIVE":
                        direction_ratios *= -1
                elif (abs(existing_x_angle) > (pi / 2) and direction_ratios.z > 0) or (
                    abs(existing_x_angle) < (pi / 2) and direction_ratios.z < 0
                ):
                    # The extrusion direction is negative. If the layer_parameter is set to positive,
                    # then the we change the extrusion direction. And the offset direction should remain positive
                    # for either direction sense, so we change it.
                    offset_direction *= -1
                    if layer_params["direction_sense"] == "POSITIVE":
                        direction_ratios *= -1

                extrusion.ExtrudedDirection.DirectionRatios = tuple(direction_ratios)
                extrusion.Depth = perpendicular_depth

                ifc_position = extrusion.Position
                if perpendicular_offset == 0.0:
                    # Clean up possible previous offset.
                    tool.Model.reset_extrusion_position(extrusion)
                else:
                    position = offset_direction * perpendicular_offset
                    if ifc_position:
                        ifc_position.Location.Coordinates = position
                    else:
                        tool.Model.add_extrusion_position(extrusion, position)

            else:
                props = tool.Model.get_model_props()
                x_angle = 0 if tool.Cad.is_x(props.x_angle, 0, tolerance=0.001) else props.x_angle
                new_rep = ifcopenshell.api.geometry.add_slab_representation(
                    tool.Ifc.get(),
                    context=body_context,
                    depth=thickness * self.unit_scale,
                    x_angle=x_angle,
                )
                for inverse in tool.Ifc.get().get_inverse(representation):
                    ifcopenshell.util.element.replace_attribute(inverse, representation, new_rep)
                bonsai.core.geometry.switch_representation(
                    tool.Ifc,
                    tool.Geometry,
                    obj=obj,
                    representation=new_rep,
                    should_reload=True,
                    is_global=True,
                    should_sync_changes_first=False,
                )
                bonsai.core.geometry.remove_representation(
                    tool.Ifc, tool.Geometry, obj=obj, representation=representation
                )
                return
        else:
            props = tool.Model.get_model_props()
            x_angle = 0 if tool.Cad.is_x(props.x_angle, 0, tolerance=0.001) else props.x_angle
            representation = ifcopenshell.api.geometry.add_slab_representation(
                tool.Ifc.get(),
                context=body_context,
                depth=thickness * self.unit_scale,
                x_angle=x_angle,
            )
            ifcopenshell.api.geometry.assign_representation(
                tool.Ifc.get(), product=element, representation=representation
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


class EnableEditingSketchExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_sketch_extrusion_profile"
    bl_label = "Enable Editing Sketch Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        pset = ifcopenshell.util.element.get_psets(element).get("EPset_Parametric", None)
        if pset and pset["Engine"] == "CADSketcher" and pset["Entities"]:
            context.scene["sketcher"].update(json.loads(pset["Entities"]))
            bpy.context.view_layer.update()
            bpy.ops.wm.tool_set_by_id(name="sketcher.slvs_select")
            bpy.ops.view3d.slvs_set_all_constraints_visibility(visibility="SHOW")
            return {"FINISHED"}

        body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        extrusion = tool.Model.get_extrusion(body)
        profile = extrusion.SweptArea
        if extrusion.Position:
            position = Matrix(ifcopenshell.util.placement.get_axis2placement(extrusion.Position).tolist())
            position.matrix_world.translation *= self.unit_scale
        else:
            position = Matrix()

        z_values = [v[2] for v in obj.bound_box]

        element = tool.Ifc.get_entity(obj)
        entities = context.scene.sketcher.entities
        entities.ensure_origin_elements(context)

        origin = entities.add_point_3d(obj.matrix_world @ Vector((0, 0, max(z_values))))
        origin.fixed = True
        origin.origin = True
        normal = entities.add_normal_3d(obj.matrix_world.to_quaternion())
        normal.fixed = True
        normal.origin = True

        wp = entities.add_workplane(origin, normal)
        sketch = entities.add_sketch(wp)

        self.vertices = []
        self.edges = []
        self.arcs = []
        self.circles = []

        profile = extrusion.SweptArea
        if profile.is_a("IfcArbitraryClosedProfileDef"):
            self.process_curve(obj, position, profile.OuterCurve)
            if profile.is_a("IfcArbitraryProfileDefWithVoids"):
                for inner_curve in profile.InnerCurves:
                    self.process_curve(obj, position, inner_curve)

        points = []

        for vertex in self.vertices:
            points.append(context.scene.sketcher.entities.add_point_2d(vertex.to_2d(), sketch))
        for edge in self.edges:
            context.scene.sketcher.entities.add_line_2d(points[edge[0]], points[edge[1]], sketch)

        bpy.ops.view3d.slvs_set_active_sketch(index=sketch.slvs_index)
        return {"FINISHED"}

    def process_curve(self, obj, position, curve):
        offset = len(self.vertices)

        if curve.is_a("IfcPolyline"):
            total_points = len(curve.Points)
            last_index = len(curve.Points) - 1
            for i, point in enumerate(curve.Points):
                if i == last_index:
                    continue
                global_point = position @ Vector(self.convert_unit_to_si(point.Coordinates)).to_3d()
                self.vertices.append(global_point)
            self.edges.extend([(i, i + 1) for i in range(offset, len(self.vertices))])
            self.edges[-1] = (len(self.vertices) - 1, offset)  # Close the loop
        elif curve.is_a("IfcIndexedPolyCurve"):
            is_arc = False
            if curve.Segments:
                for segment in curve.Segments:
                    if len(segment[0]) == 3:  # IfcArcIndex
                        is_arc = True
                        local_point = self.convert_unit_to_si(curve.Points.CoordList[segment[0][0] - 1])
                        global_point = position @ Vector(local_point).to_3d()
                        self.vertices.append(global_point)
                        local_point = self.convert_unit_to_si(curve.Points.CoordList[segment[0][1] - 1])
                        global_point = position @ Vector(local_point).to_3d()
                        self.vertices.append(global_point)
                        self.arcs.append([len(self.vertices) - 2, len(self.vertices) - 1])
                    else:
                        local_point = self.convert_unit_to_si(curve.Points.CoordList[segment[0][0] - 1])
                        global_point = position @ Vector(local_point).to_3d()
                        self.vertices.append(global_point)
                        if is_arc:
                            self.arcs[-1].append(len(self.vertices) - 1)
                            is_arc = False
            else:
                for local_point in curve.Points.CoordList:
                    global_point = position @ Vector(self.convert_unit_to_si(local_point)).to_3d()
                    self.vertices.append(global_point)
                # Curves without segments are self closing
                del self.vertices[-1]

            self.edges.extend([(i, i + 1) for i in range(offset, len(self.vertices))])
            self.edges[-1] = (len(self.vertices) - 1, offset)  # Close the loop
        elif curve.is_a("IfcCircle"):
            center = self.convert_unit_to_si(
                Matrix(ifcopenshell.util.placement.get_axis2placement(curve.Position).tolist()).translation
            )
            radius = self.convert_unit_to_si(curve.Radius)
            self.vertices.extend(
                [
                    position @ Vector((center[0], center[1] - curve.Radius, 0.0)),
                    position @ Vector((center[0], center[1] + curve.Radius, 0.0)),
                ]
            )
            self.circles.append([offset, offset + 1])
            self.edges.append((offset, offset + 1))

    def convert_unit_to_si(self, value):
        if isinstance(value, (tuple, list)):
            return [v * self.unit_scale for v in value]
        return value * self.unit_scale


class EditSketchExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_sketch_extrusion_profile"
    bl_label = "Edit Sketch Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        representation = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        extrusion = tool.Model.get_extrusion(representation)
        if extrusion.Position:
            position = Matrix(ifcopenshell.util.placement.get_axis2placement(extrusion.Position).tolist())
        else:
            position = Matrix()
        position_i = position.inverted()

        cad_sketcher = __import__("CAD_Sketcher-main")
        sketch = context.scene.sketcher.active_sketch
        converter = cad_sketcher.convertors.BezierConverter(context.scene, sketch)
        converter.run()

        profile = tool.Ifc.get().createIfcArbitraryClosedProfileDef("AREA")
        for path in converter.paths:
            points = []
            lines = path[0]
            last_index = len(lines) - 1
            for i, line in enumerate(lines):
                local_point = (position_i @ Vector(line.p1.co).to_3d()).to_2d()
                points.append(tool.Ifc.get().createIfcCartesianPoint(local_point))
            points.append(points[0])
            curve = tool.Ifc.get().createIfcPolyline(points)
        profile.OuterCurve = curve

        old_profile = extrusion.SweptArea
        extrusion.SweptArea = profile
        ifcopenshell.util.element.remove_deep2(tool.Ifc.get(), old_profile)

        pset = ifcopenshell.util.element.get_psets(element).get("EPset_Parametric", None)
        if pset:
            pset = tool.Ifc.get().by_id(pset["id"])
        else:
            pset = ifcopenshell.api.run("pset.add_pset", tool.Ifc.get(), product=element, name="EPset_Parametric")
        ifcopenshell.api.run(
            "pset.edit_pset",
            tool.Ifc.get(),
            pset=pset,
            properties={"Engine": "CADSketcher", "Entities": json.dumps(context.scene["sketcher"].to_dict())},
        )

        bpy.ops.view3d.slvs_set_active_sketch(index=-1)
        workplane = sketch.wp
        p1 = workplane.p1
        p1.origin = False
        nm = workplane.nm
        nm.origin = False
        bpy.ops.view3d.slvs_delete_entity(index=sketch["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=workplane["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=p1["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=nm["slvs_index"])

        bonsai.core.geometry.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=representation,
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )
        return {"FINISHED"}


class DisableEditingSketchExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_sketch_extrusion_profile"
    bl_label = "Disable Editing Sketch Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        sketch = context.scene.sketcher.active_sketch
        bpy.ops.view3d.slvs_set_active_sketch(index=-1)
        workplane = sketch.wp
        p1 = workplane.p1
        p1.origin = False
        nm = workplane.nm
        nm.origin = False
        bpy.ops.view3d.slvs_delete_entity(index=sketch["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=workplane["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=p1["slvs_index"])
        bpy.ops.view3d.slvs_delete_entity(index=nm["slvs_index"])
        return {"FINISHED"}


def disable_editing_extrusion_profile(context):
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


class DisableEditingExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.disable_editing_extrusion_profile"
    bl_label = "Disable Editing Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        return disable_editing_extrusion_profile(context)


class EnableEditingExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.enable_editing_extrusion_profile"
    bl_label = "Enable Editing Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        body = ifcopenshell.util.representation.resolve_representation(body)
        extrusion = tool.Model.get_extrusion(body)
        existing_x_angle = tool.Model.get_existing_x_angle(extrusion)
        layer_params = tool.Model.get_material_layer_parameters(element)

        if extrusion.Position:
            position = Matrix(ifcopenshell.util.placement.get_axis2placement(extrusion.Position).tolist())
            position.translation *= self.unit_scale

            # Restore the position to before it was changed by the offset and x_angle
            rot_matrix = Matrix.Rotation(existing_x_angle, 4, "X")
            perpendicular_offset = layer_params["offset"] * abs(1 / cos(existing_x_angle))
            offset_vector = Vector((0.0, 0.0, -perpendicular_offset))
            rot_offset = offset_vector @ rot_matrix
            tranlation_matrix = Matrix.Translation(rot_offset)
            position = position @ tranlation_matrix

            # Restore Object rotation to zero
            local_rot_mat = obj.rotation_euler.to_matrix()
            rot_mat = Matrix.Rotation(-existing_x_angle, 4, "X")
            new_rot_mat = local_rot_mat.to_4x4() @ rot_mat
            new_rot_euler = new_rot_mat.to_euler()
            obj.rotation_euler = new_rot_euler

        else:
            position = Matrix()

        tool.Model.import_profile(extrusion.SweptArea, obj=obj, position=position, x_angle=existing_x_angle)

        bpy.ops.object.mode_set(mode="EDIT")
        ProfileDecorator.install(context, exit_edit_mode_callback=lambda: disable_editing_extrusion_profile(context))
        if not bpy.app.background:
            tool.Blender.set_viewport_tool("bim.cad_tool")
        return {"FINISHED"}


class EditExtrusionProfile(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.edit_extrusion_profile"
    bl_label = "Edit Extrusion Profile"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        ProfileDecorator.uninstall()
        bpy.ops.object.mode_set(mode="OBJECT")

        obj = context.active_object
        element = tool.Ifc.get_entity(obj)

        body = ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")
        body = ifcopenshell.util.representation.resolve_representation(body)
        extrusion = tool.Model.get_extrusion(body)
        existing_x_angle = tool.Model.get_existing_x_angle(extrusion)
        layer_params = tool.Model.get_material_layer_parameters(element)
        if extrusion.Position:
            position = Matrix(ifcopenshell.util.placement.get_axis2placement(extrusion.Position).tolist())
            position.translation *= self.unit_scale

            # Restore the position to after it was changed by the offset and x_angle
            rot_matrix = Matrix.Rotation(existing_x_angle, 4, "X")
            perpendicular_offset = layer_params["offset"] * abs(1 / cos(existing_x_angle))
            offset_vector = Vector((0.0, 0.0, -perpendicular_offset))
            rot_offset = offset_vector @ rot_matrix
            tranlation_matrix = Matrix.Translation(rot_offset)
            position = position @ tranlation_matrix

            # Restore Object rotation to x_angle
            local_rot_mat = obj.rotation_euler.to_matrix()
            rot_mat = Matrix.Rotation(existing_x_angle, 4, "X")
            new_rot_mat = local_rot_mat.to_4x4() @ rot_mat
            new_rot_euler = new_rot_mat.to_euler()
            obj.rotation_euler = new_rot_euler

        else:
            position = Matrix()

        profile = tool.Model.export_profile(obj, position=position, x_angle=existing_x_angle)

        if not profile:

            def msg(self, context):
                self.layout.label(text="INVALID PROFILE")

            bpy.context.window_manager.popup_menu(msg, title="Error", icon="ERROR")
            ProfileDecorator.install(
                context, exit_edit_mode_callback=lambda: disable_editing_extrusion_profile(context)
            )
            bpy.ops.object.mode_set(mode="EDIT")
            return

        old_profile = extrusion.SweptArea
        for inverse in tool.Ifc.get().get_inverse(old_profile):
            ifcopenshell.util.element.replace_attribute(inverse, old_profile, profile)
        ifcopenshell.util.element.remove_deep2(tool.Ifc.get(), old_profile)

        bonsai.core.geometry.switch_representation(
            tool.Ifc,
            tool.Geometry,
            obj=obj,
            representation=body,
            should_reload=True,
            is_global=True,
            should_sync_changes_first=False,
        )

        # Only certain classes should have a footprint
        if element.is_a() not in ("IfcSlab", "IfcRamp"):
            return

        footprint_context = ifcopenshell.util.representation.get_context(
            tool.Ifc.get(), "Plan", "FootPrint", "SKETCH_VIEW"
        )
        if not footprint_context:
            return

        curves = [profile.OuterCurve]
        if profile.is_a("IfcArbitraryProfileDefWithVoids"):
            curves.extend(profile.InnerCurves)
        new_footprint = ifcopenshell.api.run(
            "geometry.add_footprint_representation", tool.Ifc.get(), context=footprint_context, curves=curves
        )
        old_footprint = ifcopenshell.util.representation.get_representation(element, "Plan", "FootPrint", "SKETCH_VIEW")
        if old_footprint:
            for inverse in tool.Ifc.get().get_inverse(old_footprint):
                ifcopenshell.util.element.replace_attribute(inverse, old_footprint, new_footprint)
            bonsai.core.geometry.remove_representation(tool.Ifc, tool.Geometry, obj=obj, representation=old_footprint)
        else:
            ifcopenshell.api.run(
                "geometry.assign_representation", tool.Ifc.get(), product=element, representation=new_footprint
            )


class ResetVertex(bpy.types.Operator):
    bl_idname = "bim.reset_vertex"
    bl_label = "Reset Vertex"
    bl_description = "Reset selected vertices group assignments (e.g. remove curve/circle)."
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj) and obj.type == "MESH"

    def cancel_message(self, msg):
        self.report({"WARNING"}, msg)
        return {"CANCELLED"}

    def execute(self, context):
        obj = context.active_object
        bpy.ops.object.mode_set(mode="OBJECT")
        selected_vertices = [v.index for v in obj.data.vertices if v.select]
        for group in obj.vertex_groups:
            group.remove(selected_vertices)
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class SetArcIndex(bpy.types.Operator):
    bl_idname = "bim.set_arc_index"
    bl_label = "Set Arc Index"
    bl_description = (
        "Add an IfcArcIndex based 3 point arc for the selected vertices, add a vertex group to mark the created arc."
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj) and obj.type == "MESH"

    def cancel_message(self, msg):
        self.report({"ERROR"}, msg)
        return {"CANCELLED"}

    def execute(self, context):
        # NOTE: undo won't remove new verex group
        # because of jumping between modes
        obj = context.active_object

        bm = tool.Blender.get_bmesh_for_mesh(obj.data)
        selected_vertices = [v.index for v in bm.verts if v.select]
        if len(selected_vertices) != 3:
            return self.cancel_message("Select 3 vertices.")

        bpy.ops.object.mode_set(mode="OBJECT")
        group = obj.vertex_groups.new(name="IFCARCINDEX")
        group.add(selected_vertices, 1, "REPLACE")
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class AddSlabFromWall(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.draw_slab_from_wall"
    bl_label = "Draw Slab From Wall"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.space_data.type == "VIEW_3D"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.relating_type = None
        props = tool.Model.get_model_props()
        relating_type_id = props.relating_type_id
        if relating_type_id:
            self.relating_type = tool.Ifc.get().by_id(int(relating_type_id))

    def _execute(self, context):
        if not self.relating_type:
            return {"FINISHED"}
        walls = tool.Model.get_connected_walls(bpy.context.selected_objects)
        if not walls:
            self.report(
                {"WARNING"},
                "Please select a closed loop of walls, or deselect the walls to add a slab using the polyline tool.",
            )
            return {"FINISHED"}

        DumbSlabGenerator(self.relating_type).generate("WALLS")
        return {"FINISHED"}


class DrawPolylineSlab(bpy.types.Operator, PolylineOperator, tool.Ifc.Operator):
    bl_idname = "bim.draw_polyline_slab"
    bl_label = "Draw Polyline Slab"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.space_data.type == "VIEW_3D"

    def __init__(self, *args, **kwargs):
        bpy.types.Operator.__init__(self, *args, **kwargs)
        PolylineOperator.__init__(self)
        self.relating_type = None
        props = tool.Model.get_model_props()
        relating_type_id = props.relating_type_id
        if relating_type_id:
            self.relating_type = tool.Ifc.get().by_id(int(relating_type_id))

    def create_slab_from_polyline(self, context):
        if not self.relating_type:
            return {"FINISHED"}

        slab = DumbSlabGenerator(self.relating_type).generate("POLYLINE")
        if not slab:
            return

        model_props = tool.Model.get_model_props()
        direction_sense = model_props.direction_sense
        offset = model_props.offset
        model = tool.Ifc.get()
        element = tool.Ifc.get_entity(slab)
        material = ifcopenshell.util.element.get_material(element)
        material_set_usage = model.by_id(material.id())
        if not getattr(material_set_usage, "ForLayerSet", False):
            return
        attributes = {"OffsetFromReferenceLine": offset, "DirectionSense": direction_sense}
        ifcopenshell.api.run(
            "material.edit_layer_usage",
            model,
            **{"usage": material_set_usage, "attributes": attributes},
        )
        DumbSlabPlaner().regenerate_from_occurence(element, material_set_usage)

    def modal(self, context, event):
        return IfcStore.execute_ifc_operator(self, context, event, method="MODAL")

    def _modal(self, context, event):
        if not self.relating_type:
            self.report({"WARNING"}, "You need to select a slab type.")
            PolylineDecorator.uninstall()
            tool.Blender.update_viewport()
            return {"FINISHED"}

        PolylineDecorator.update(event, self.tool_state, self.input_ui, self.snapping_points[0])
        tool.Blender.update_viewport()

        self.handle_lock_axis(context, event)

        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            self.handle_mouse_move(context, event)
            return {"PASS_THROUGH"}

        props = tool.Model.get_model_props()
        if event.value == "RELEASE" and event.type == "F":
            direction_sense = props.direction_sense
            props.direction_sense = "NEGATIVE" if direction_sense == "POSITIVE" else "POSITIVE"

        if event.value == "RELEASE" and event.type == "O":
            items = ["TOP", "CENTER", "BOTTOM"]
            index = items.index(props.offset_type_horizontal)
            size = len(items)
            props.offset_type_horizontal = items[((index + 1) % size)]
            self.set_offset(context, self.relating_type)

        custom_instructions = {"Choose Axis": {"icons": True, "keys": ["EVENT_X", "EVENT_Y"]}}

        slab_config = [
            f"Direction: {props.direction_sense}",
            f"Offset Type: {props.offset_type_horizontal}",
            f"Offset Value: {tool.Polyline.format_input_ui_units(props.offset * self.unit_scale)}",
        ]

        self.handle_instructions(context, custom_instructions, slab_config)

        self.handle_mouse_move(context, event, should_round=True)

        self.choose_axis(event)

        self.handle_snap_selection(context, event)

        if (
            not self.tool_state.is_input_on
            and event.value == "RELEASE"
            and event.type in {"RET", "NUMPAD_ENTER", "RIGHTMOUSE"}
        ):
            self.create_slab_from_polyline(context)
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
        self.tool_state.use_default_container = True
        self.tool_state.plane_method = "XY"
        self.set_offset(context, self.relating_type)
        return {"RUNNING_MODAL"}


class RecalculateSlab(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.recalculate_slab"
    bl_label = "Recalculate Slab"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def _execute(self, context):
        walls = []
        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if element.is_a("IfcSlab"):
                for rel in element.ConnectedTo:
                    if rel.is_a() == "IfcRelConnectsElements" and rel.RelatedElement.is_a("IfcWall"):
                        walls.append(tool.Ifc.get_object(rel.RelatedElement))

        DumbWallRecalculator().recalculate(walls)
        return {"FINISHED"}
