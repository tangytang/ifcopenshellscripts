# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2024 Bruno Perdigão <contact@brunopo.com>
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
import math
import ifcopenshell
import ifcopenshell.util.unit
import bonsai.core.tool
import bonsai.tool as tool
from bonsai.bim.module.drawing.helper import format_distance
from dataclasses import dataclass, field
from lark import Lark, Transformer
from math import degrees, radians, sin, cos, tan
from mathutils import Vector, Matrix
from typing import Optional, Union, Literal, List


class Polyline(bonsai.core.tool.Polyline):
    @dataclass
    class PolylineUI:
        _D: str = ""
        _A: str = ""  # Relative to previous polyline points
        _WORLD_ANGLE: str = ""  # Relative to World Origin. Only used for specific operation. Not used on the UI.
        _X: str = ""
        _Y: str = ""
        _Z: str = ""
        _AREA: str = "0"
        input_options: List[str] = field(default_factory=list)

        def set_value(self, attribute_name, value):
            value = str(value)
            setattr(self, f"_{attribute_name}", value)

        def get_text_value(self, attribute_name):
            value = getattr(self, f"_{attribute_name}")
            return value

        def get_number_value(self, attribute_name):
            value = getattr(self, f"_{attribute_name}")
            if value:
                return float(value)
            else:
                return value

        def get_formatted_value(self, attribute_name):
            value = self.get_number_value(attribute_name)
            context = bpy.context
            if value is None:
                return None
            if attribute_name == "A":
                value = float(self.get_text_value(attribute_name))
                return f"{value:.2f}°"
            if attribute_name == "AREA":
                return Polyline.format_input_ui_units(value, True)
            else:
                return Polyline.format_input_ui_units(value)

    @dataclass
    class ToolState:
        use_default_container: bool = None
        snap_angle: float = None
        is_input_on: bool = None
        lock_axis: bool = False
        # angle_axis_start: Vector
        # angle_axis_end: Vector
        axis_method: Literal["X", "Y", "Z", None] = None
        plane_method: Literal["XY", "XZ", "YZ", None] = None
        plane_origin: Vector = Vector((0.0, 0.0, 0.0))
        instructions: str = """TAB: Cycle Input
        M: Modify Snap Point
        C: Close
        Backspace: Remove
        X Y: Axis
        L: Lock axis
    """
        snap_info: str = None
        mode: Literal["Mouse", "Select", "Edit", None] = None
        input_type: "Polyline.InputType" = None

    @classmethod
    def create_input_ui(cls, input_options: List[str] = []) -> PolylineUI:
        return cls.PolylineUI(input_options=input_options)

    @classmethod
    def create_tool_state(cls) -> ToolState:
        return cls.ToolState()

    @classmethod
    def calculate_distance_and_angle(
        cls, context: bpy.types.Context, input_ui: PolylineUI, tool_state: ToolState, should_round: bool = False
    ) -> None:

        polyline_data = context.scene.BIMPolylineProperties.insertion_polyline
        if len(polyline_data) > 0:
            polyline_data = polyline_data[0]
            polyline_points = polyline_data.polyline_points
            if len(polyline_points) > 0:
                last_point_data = polyline_points[len(polyline_points) - 1]
            else:
                last_point_data = None
        else:
            polyline_points = []
            last_point_data = None

        if tool.Ifc.get():
            default_container_elevation = tool.Root.get_default_container_elevation()
        else:
            default_container_elevation = 0

        mouse_point = context.scene.BIMPolylineProperties.snap_mouse_point[0]

        if last_point_data:
            last_point = Vector((last_point_data.x, last_point_data.y, last_point_data.z))
        else:
            last_point = Vector((0, 0, 0))

        if tool_state.is_input_on:
            if tool_state.use_default_container:
                mouse_vector = Vector(
                    (input_ui.get_number_value("X"), input_ui.get_number_value("Y"), default_container_elevation)
                )
            else:
                mouse_vector = Vector(
                    (input_ui.get_number_value("X"), input_ui.get_number_value("Y"), input_ui.get_number_value("Z"))
                )
        else:
            if tool_state.use_default_container:
                mouse_vector = Vector((mouse_point.x, mouse_point.y, default_container_elevation))
            else:
                mouse_vector = Vector((mouse_point.x, mouse_point.y, mouse_point.z))

        second_to_last_point = None
        if len(polyline_points) > 1:
            second_to_last_point_data = polyline_points[len(polyline_points) - 2]
            second_to_last_point = Vector(
                (second_to_last_point_data.x, second_to_last_point_data.y, second_to_last_point_data.z)
            )
        else:
            # Creates a fake "second to last" point away from the first point but in the same x axis
            # this allows to calculate the angle relative to x axis when there is only one point
            second_to_last_point = Vector((last_point.x + 1000000000, last_point.y, last_point.z))
            if tool_state.plane_method == "YZ":
                second_to_last_point = Vector((last_point.x, last_point.y + 1000000000, last_point.z))
            second_to_last_point = tool.Polyline.use_transform_orientations(second_to_last_point)

        world_second_to_last_point = Vector((last_point.x + 1000000000, last_point.y, last_point.z))
        if tool_state.plane_method == "YZ":
            world_second_to_last_point = Vector((last_point.x, last_point.y + 1000000000, last_point.z))
        world_second_to_last_point = tool.Polyline.use_transform_orientations(world_second_to_last_point)

        distance = (mouse_vector - last_point).length
        if distance < 0:
            return
        if distance > 0:
            angle = tool.Cad.angle_3_vectors(
                second_to_last_point, last_point, mouse_vector, new_angle=None, degrees=True
            )

            # Round angle to the nearest 0.05
            angle = round(angle / 0.05) * 0.05

            orientation_angle = tool.Cad.angle_3_vectors(
                world_second_to_last_point, last_point, mouse_vector, new_angle=None, degrees=True
            )

            # Round angle to the nearest 0.05
            orientation_angle = round(orientation_angle / 0.05) * 0.05

        if distance == 0:
            angle = 0
            orientation_angle = 0
        if input_ui:
            if should_round:
                angle = 5 * round(angle / 5)
                factor = tool.Snap.get_increment_snap_value(context)
                distance = factor * round(distance / factor)
            input_ui.set_value("X", mouse_vector.x)
            input_ui.set_value("Y", mouse_vector.y)
            if input_ui.get_number_value("Z") is not None:
                input_ui.set_value("Z", mouse_vector.z)

            input_ui.set_value("D", distance)
            input_ui.set_value("A", angle)
            input_ui.set_value("WORLD_ANGLE", orientation_angle)
            return

        return

    @classmethod
    def calculate_area(cls, context: bpy.types.Context, input_ui: PolylineUI) -> Union[PolylineUI, None]:
        try:
            polyline_data = context.scene.BIMPolylineProperties.insertion_polyline[0]
            polyline_points = polyline_data.polyline_points
        except:
            return input_ui

        if len(polyline_points) < 3:
            return input_ui

        points = []
        for data in polyline_points:
            points.append(Vector((data.x, data.y, data.z)))

        if points[0] == points[-1]:
            points = points[1:]

        # TODO move this to CAD
        # Calculate the normal vector of the plane formed by the first three vertices
        v1, v2, v3 = points[:3]
        normal = (v2 - v1).cross(v3 - v1).normalized()

        # Check if all points are coplanar
        is_coplanar = True
        tolerance = 1e-6  # Adjust this value as needed
        for v in points:
            if abs((v - v1).dot(normal)) > tolerance:
                is_coplanar = False

        if is_coplanar:
            area = 0
            for i in range(len(points)):
                j = (i + 1) % len(points)
                area += points[i].cross(points[j]).dot(normal)

            area = abs(area) / 2
        else:
            area = 0

        if input_ui.get_text_value("AREA") is not None:
            input_ui.set_value("AREA", area)

        area = input_ui.get_number_value("AREA")
        if area:
            area = tool.Polyline.format_input_ui_units(area, is_area=True)
            polyline_data.area = area
        return

    @classmethod
    def calculate_x_y_and_z(cls, context: bpy.types.Context, input_ui: PolylineUI, tool_state: ToolState) -> None:
        polyline_data = context.scene.BIMPolylineProperties.insertion_polyline
        if len(polyline_data) > 0:
            polyline_data = context.scene.BIMPolylineProperties.insertion_polyline[0]
            polyline_points = polyline_data.polyline_points
            if len(polyline_points) > 0:
                last_point_data = polyline_points[len(polyline_points) - 1]
                last_point = Vector((last_point_data.x, last_point_data.y, last_point_data.z))
            else:
                last_point = Vector((0, 0, 0))
        else:
            polyline_points = []
            last_point = Vector((0, 0, 0))

        if tool.Ifc.get():
            default_container_elevation = tool.Root.get_default_container_elevation()
        else:
            default_container_elevation = 0

        snap_prop = context.scene.BIMPolylineProperties.snap_mouse_point[0]
        snap_vector = Vector((snap_prop.x, snap_prop.y, snap_prop.z))

        if tool_state.is_input_on:
            if tool_state.use_default_container:
                mouse_vector = Vector(
                    (input_ui.get_number_value("X"), input_ui.get_number_value("Y"), default_container_elevation)
                )
            else:
                mouse_vector = Vector(
                    (input_ui.get_number_value("X"), input_ui.get_number_value("Y"), input_ui.get_number_value("Z"))
                )
        else:
            if tool_state.use_default_container:
                snap_vector = Vector((snap_prop.x, snap_prop.y, default_container_elevation))
            else:
                snap_vector = Vector((snap_prop.x, snap_prop.y, snap_prop.z))

        if len(polyline_points) > 1:
            second_to_last_point_data = polyline_points[len(polyline_points) - 2]
            second_to_last_point = Vector(
                (second_to_last_point_data.x, second_to_last_point_data.y, second_to_last_point_data.z)
            )
        else:
            # Creates a fake "second to last" point away from the first point but in the same x axis
            # this allows to calculate the angle relative to x axis when there is only one point
            second_to_last_point = Vector((last_point.x + 1000000000, last_point.y, last_point.z))
            if tool_state.plane_method == "YZ":
                second_to_last_point = Vector((last_point.x, last_point.y + 1000000000, last_point.z))
            second_to_last_point = tool.Polyline.use_transform_orientations(second_to_last_point)

        distance = input_ui.get_number_value("D")

        if distance < 0 or distance > 0:
            angle = radians(input_ui.get_number_value("A"))

            rot_vector = tool.Cad.angle_3_vectors(second_to_last_point, last_point, snap_vector, angle, degrees=True)

            # When the angle in 180 degrees it might create a rotation vector that is equal to
            # when the angle is 0 degrees, leading the insertion point to the opposite direction
            # This prevents the issue by ensuring the negative x direction
            if round(angle, 4) == round(math.pi, 4):
                rot_vector.x = -1.0

            coords = rot_vector * distance + last_point

            x = coords[0]
            y = coords[1]
            z = coords[2]
            if input_ui:
                input_ui.set_value("X", x)
                input_ui.set_value("Y", y)
                if input_ui.get_number_value("Z") is not None:
                    input_ui.set_value("Z", z)

                return

        input_ui.set_value("X", last_point.x)
        input_ui.set_value("Y", last_point.y)
        if input_ui.get_number_value("Z") is not None:
            input_ui.set_value("Z", last_point.z)

        return

    @classmethod
    def validate_input(cls, input_number: str, input_type: str) -> tuple[bool, str]:
        """
        :return: Tuple with a boolean indicating if the input is valid
            and the final string output.
            Distance units converted to meters, angles input/output is in degrees.
        """

        grammar_imperial = """
        start: (FORMULA dim expr) | dim
        dim: imperial

        FORMULA: "="

        imperial: (inches?) | (feet? "-"? inches?)
        feet: NUMBER? "-"? fraction? "'"?
        inches: NUMBER? "-"? fraction? "\\""
        fraction: NUMBER "/" NUMBER

        expr: (ADD | SUB) dim | (MUL | DIV) NUMBER

        NUMBER: /-?\\d+(?:\\.\\d+)?/
        ADD: "+"
        SUB: "-"
        MUL: "*"
        DIV: "/"

        %ignore " "
        """

        grammar_metric = """
        start: FORMULA? dim expr?
        dim: metric

        FORMULA: "="

        metric: NUMBER "mm"? "cm"? "dm"? "m"? "°"?

        expr: (ADD | SUB | MUL | DIV) dim

        NUMBER: /-?\\d+(?:\\.\\d+)?/
        ADD: "+"
        SUB: "-"
        MUL: "*"
        DIV: "/"

        %ignore " "
        """

        class InputTransform(Transformer):
            def NUMBER(self, n):
                return float(n)

            def fraction(self, numbers):
                return numbers[0] / numbers[1]

            def inches(self, args):
                if len(args) > 1:
                    result = args[0] + args[1]
                else:
                    result = args[0]
                return result / 12

            def feet(self, args):
                return args[0]

            def imperial(self, args):
                if len(args) > 1:
                    if args[0] <= 0:
                        result = args[0] - args[1]
                    else:
                        result = args[0] + args[1]
                else:
                    result = args[0] or 0.0
                return result

            def metric(self, args):
                return args[0]

            def dim(self, args):
                return args[0]

            def expr(self, args):
                op = args[0]
                value = float(args[1])
                if op == "+":
                    return lambda x: x + value
                elif op == "-":
                    return lambda x: x - value
                elif op == "*":
                    return lambda x: x * value
                elif op == "/":
                    return lambda x: x / value

            def FORMULA(self, args):
                return args[0]

            def start(self, args):
                i = 0
                if args[0] == "=":
                    i += 1
                else:
                    if len(args) > 1:
                        raise ValueError("Invalid input.")
                dimension = args[i]
                if len(args) > i + 1:
                    expression = args[i + 1]
                    return expression(dimension) * unit_scale
                else:
                    return dimension * unit_scale

        try:
            if tool.Ifc.get():
                unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
            else:
                unit_scale = tool.Blender.get_unit_scale()
            if bpy.context.scene.unit_settings.system == "IMPERIAL":
                parser = Lark(grammar_imperial)
            else:
                parser = Lark(grammar_metric)

            if input_type == "A":
                parser = Lark(grammar_metric)
                unit_scale = 1

            parse_tree = parser.parse(input_number)

            transformer = InputTransform()
            result = transformer.transform(parse_tree)
            result = round(result, 4)
            return True, str(result)
        except:
            return False, "0"

    @classmethod
    def format_input_ui_units(cls, value: float, is_area: bool = False) -> str:
        if tool.Ifc.get():
            unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        else:
            unit_scale = tool.Blender.get_unit_scale()
        if bpy.context.scene.unit_settings.system == "IMPERIAL":
            dprops = tool.Drawing.get_document_props()
            precision = dprops.imperial_precision
            if is_area:
                unit_scale = 1
        else:
            precision = None

        return format_distance(
            value / unit_scale,
            precision=precision,
            hide_units=False,
            isArea=is_area,
            suppress_zero_inches=True,
            in_unit_length=True,
        )

    @classmethod
    def insert_polyline_point(cls, input_ui: PolylineUI, tool_state: Optional[ToolState] = None) -> Union[str, None]:
        x = input_ui.get_number_value("X")
        y = input_ui.get_number_value("Y")
        if input_ui.get_number_value("Z") is not None:
            z = input_ui.get_number_value("Z")
        else:
            z = 0
        d = input_ui.get_formatted_value("D")
        a = input_ui.get_formatted_value("A")

        snap_vertex = bpy.context.scene.BIMPolylineProperties.snap_mouse_point[0]
        if tool_state and tool_state.use_default_container:
            z = tool.Root.get_default_container_elevation()

        # Lock one dimension when in plane method
        if tool_state.plane_origin:
            if tool_state.plane_method == "XY":
                z = tool_state.plane_origin.z
            elif tool_state.plane_method == "XZ":
                y = tool_state.plane_origin.y
            elif tool_state.plane_method == "YZ":
                x = tool_state.plane_origin.x

        if x is None and y is None:
            x = snap_vertex.x
            y = snap_vertex.y
            z = snap_vertex.z

        polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline
        if not polyline_data:
            polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline.add()
        else:
            polyline_data = polyline_data[0]
        polyline_points = polyline_data.polyline_points
        if polyline_points:
            # Avoids creating two points at the same location
            for point in polyline_points[1:]:  # The first can be repeated to form a wall loop
                if (x, y, z) == (point.x, point.y, point.z):
                    return "Cannot create two points at the same location"
            # Avoids creating overlapping edges
            if len(polyline_points) > 1:
                v1 = Vector((x, y, z))
                v2 = Vector((polyline_points[-1].x, polyline_points[-1].y, polyline_points[-1].z))
                v3 = Vector((polyline_points[-2].x, polyline_points[-2].y, polyline_points[-2].z))
                angle = tool.Cad.angle_3_vectors(v1, v2, v3, new_angle=None, degrees=True)
                if tool.Cad.is_x(angle, 0):
                    return

        polyline_point = polyline_points.add()
        polyline_point.x = x
        polyline_point.y = y
        polyline_point.z = z

        polyline_point.dim = d
        polyline_point.angle = a
        polyline_point.position = Vector((x, y, z))

        # Add total length
        total_length = 0
        for i, point in enumerate(polyline_points):
            if i == 0:
                continue
            dim = float(tool.Polyline.validate_input(point.dim, "D")[1])
            total_length += dim
        total_length = tool.Polyline.format_input_ui_units(total_length)
        polyline_data.total_length = total_length

    @classmethod
    def clear_polyline(cls) -> None:
        bpy.context.scene.BIMPolylineProperties.insertion_polyline.clear()

    @classmethod
    def remove_last_polyline_point(cls) -> None:
        polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline
        polyline_points = polyline_data[0].polyline_points if polyline_data else []
        polyline_points.remove(len(polyline_points) - 1)

    @classmethod
    def move_polyline_to_measure(cls, context: bpy.types.Context, input_ui: PolylineUI) -> None:
        polyline_data = bpy.context.scene.BIMPolylineProperties.insertion_polyline
        polyline_points = polyline_data[0].polyline_points if polyline_data else []
        measurement_data = bpy.context.scene.BIMPolylineProperties.measurement_polyline.add()
        measurement_type = bpy.context.scene.MeasureToolSettings.measurement_type
        measurement_data.measurement_type = measurement_type
        if measurement_type == "AREA" and len(polyline_points) < 3:
            return
        for point in polyline_points:
            measurement_point = measurement_data.polyline_points.add()
            measurement_point.x = point.x
            measurement_point.y = point.y
            measurement_point.z = point.z
            measurement_point.dim = point.dim
            measurement_point.angle = point.angle
            measurement_point.position = point.position

        measurement_data.total_length = polyline_data[0].total_length
        measurement_data.area = polyline_data[0].area

    @classmethod
    def use_transform_orientations(cls, value: Union[Vector, Matrix]) -> Union[Vector, Matrix]:
        custom_orientation = bpy.context.scene.transform_orientation_slots[0].custom_orientation
        if custom_orientation:
            custom_matrix = custom_orientation.matrix
            if isinstance(value, Vector):
                result = custom_matrix @ value
            else:
                result = custom_matrix.inverted() @ value
            return result
        return value
