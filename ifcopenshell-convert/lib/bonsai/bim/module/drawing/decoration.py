# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Maxim Vasilyev <qwiglydee@gmail.com>
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

import gpu
import bpy
import blf
import math
import bmesh
import shapely
import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.unit
import bonsai.tool as tool
import bonsai.bim.module.drawing.helper as helper
from pathlib import Path
from math import pi, sin, cos, acos, atan, degrees, radians
from bpy.types import SpaceView3D
from mathutils import Vector, Matrix
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from bonsai.bim.module.drawing.data import DecoratorData, DrawingsData
from bonsai.bim.module.drawing.shaders import add_verts_sequence, add_offsets
from bonsai.bim.module.drawing.helper import format_distance
from timeit import default_timer as timer
from functools import lru_cache
from typing import Optional, Iterator, Type, Union

UNSPECIAL_ELEMENT_COLOR = (0.2, 0.2, 0.2, 1)  # GREY


class profile_consequential:
    start_time = None
    lines = []

    @classmethod
    def __init__(cls, test_name):
        cls.log()
        cls.start_time = timer()
        cls.test_name = test_name

    @classmethod
    def log(cls, args=[]):
        if cls.start_time is not None:
            args = "" if not args else "\t" + "\t".join(args)
            cls.lines.append(f"{cls.test_name}\t{timer() - cls.start_time:.10f}{args}")

    @classmethod
    def stop(cls):
        cls.log()
        cls.start_time = None
        lines = "\n".join(cls.lines)
        print(lines)
        import pyperclip

        pyperclip.copy(lines)
        cls.lines = []


def worldspace_to_winspace(verts: list[Vector], context: bpy.types.Context) -> list[Vector]:
    """Convert world space verts to window space"""
    region = context.region
    region3d = context.region_data
    clipspace_verts = [region3d.perspective_matrix @ Vector(v) for v in verts]
    winspace_vector = Vector([region.width / 2, region.height / 2, 1])
    winspace_vector_offset = Vector([region.width / 2, region.height / 2, 0])
    winspace_verts = [v * winspace_vector + winspace_vector_offset for v in clipspace_verts]
    return winspace_verts


def winspace_to_worldspace(verts: list[Vector], context: bpy.types.Context) -> list[Vector]:
    """Convert winspace verts to world space"""
    region = context.region
    region3d = context.region_data
    # clipspace_vector = 1 / winspace_vector
    clipspace_vector = Vector([1 / (region.width / 2), 1 / (region.height / 2), 1])
    winspace_vector_offset = Vector([region.width / 2, region.height / 2, 0])
    clipspace_verts = [(v - winspace_vector_offset) * clipspace_vector for v in verts]
    worldspace_verts = [region3d.perspective_matrix.inverted() @ v for v in clipspace_verts]
    return worldspace_verts


def get_arrow_head(edge_dir, size, rot_matrix_cw, rot_matrix_ccw):
    head = []
    head.append(edge_dir * size)
    head.append((rot_matrix_cw @ head[0].xy).to_3d())
    head.append((rot_matrix_ccw @ head[0].xy).to_3d())
    return head


def get_triangle_head(edge_dir, side, length, width):
    head = [
        edge_dir * length * (-0.5),
        side * width,
        edge_dir * length * (0.5),
    ]
    return head


def get_callout_head(edge_dir, edge_side, callout_size, callout_gap):
    head = [
        # callout handle
        edge_dir * -callout_size + edge_side * callout_gap,
        # callout triangle
        edge_dir * callout_gap * 2 + edge_side * callout_gap,
        edge_dir * callout_gap,
        edge_side * callout_gap,
    ]
    return head


def get_circle_head(size: float, segments: int = 20, rotation: float = 0.0) -> list[Vector]:
    """`rotation` value in radians"""
    angle_d = 2 * pi / segments
    head = []
    for i in range(segments):
        angle = angle_d * i + rotation
        head.append(Vector([cos(angle), sin(angle), 0]) * size)
    return head


def get_circle_head_asterisk(size: float, segments: int = 6, rotation: float = 0.0) -> Iterator[tuple[Vector, Vector]]:
    circle_head = get_circle_head(size, segments, rotation)
    middle = segments // 2
    return zip(circle_head[:middle], circle_head[middle:])


def get_angle_circle(circle_start, circle_angle, counterclockwise, segments=20):
    angle_d = circle_angle / segments
    head = []
    circle_start = circle_start.xy

    for i in range(segments + 1):
        angle = angle_d * i
        if counterclockwise:
            rot_matrix_ccw = Matrix.Rotation(angle, 2)
            head.append((rot_matrix_ccw @ circle_start).to_3d())
        else:
            rot_matrix_cw = Matrix.Rotation(-angle, 2)
            head.append((rot_matrix_cw @ circle_start).to_3d())
    return head


class BaseDecorator:
    # base name of objects to decorate
    objecttype = "NOTDEFINED"

    def __init__(self):
        self.font_id = 0  # 0 is the default font

        # POLYLINE_UNIFORM_COLOR is good for smoothed lines since `bgl.enable(GL_LINE_SMOOTH)` is deprecated
        self.line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        self.base_shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    def get_camera_width_mm(self):
        # Horrific prototype code to ensure bgl draws at drawing scales
        # https://blender.stackexchange.com/questions/16493/is-there-a-way-to-fit-the-viewport-to-the-current-field-of-view
        def is_landscape(render):
            return render.resolution_x > render.resolution_y

        camera = bpy.context.scene.camera
        render = bpy.context.scene.render
        if is_landscape(render):
            camera_width_model = camera.data.ortho_scale
        else:
            camera_width_model = camera.data.ortho_scale / render.resolution_y * render.resolution_x

        scale = tool.Drawing.get_scale_ratio(tool.Drawing.get_diagram_scale(camera)["Scale"])
        camera_width_mm = scale * camera_width_model
        return camera_width_mm

    def camera_zoom_to_factor(self, zoom):
        return math.pow(((zoom / 50) + math.sqrt(2)) / 2, 2)

    def get_splines(self, obj):
        """Iterates through splines
        Args:
          obj: Blender object with Curve data

        Yields:
          verts: points of each spline, world coords
        """
        for spline in obj.data.splines:
            spline_points = spline.bezier_points if spline.bezier_points else spline.points
            if len(spline_points) < 2:
                continue
            yield [obj.matrix_world @ p.co for p in spline_points]

    def get_path_geom(self, obj, topo=True):
        """Parses path geometry into line segments

        Args:
          obj: Blender object with data of type Curve
          topo: bool; if types of vertices are needed

        Returns:
          vertices: 3-tuples of coords
          indices: 2-tuples of each segment verices' indices
          topology: types of vertices
            0: internal
            1: beginning
            2: ending
        """
        vertices = []
        indices = []
        topology = []

        idx = 0
        for spline in obj.data.splines:
            spline_points = spline.bezier_points if spline.bezier_points else spline.points
            if len(spline_points) < 2:
                continue
            points = [obj.matrix_world @ p.co for p in spline_points]
            cnt = len(points)
            vertices.extend(p[:3] for p in points)
            if topo:
                topology.append(1)
                topology.extend([0] * max(0, cnt - 2))
                topology.append(2)
            indices.extend((idx + i, idx + i + 1) for i in range(cnt - 1))
            idx += cnt

        return vertices, indices, topology

    def get_mesh_geom(self, obj, check_mode=True):
        """Parses mesh geometry into line segments

        Args:
          obj: Blender object with data of type Mesh

        Returns:
          vertices: 3-tuples of coords
          indices: 2-tuples of each segment verices' indices
        """
        if check_mode and obj.data.is_editmode:
            return self.get_editmesh_geom(obj)

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        vertices = [obj.matrix_world @ v.co for v in bm.verts]
        # In object mode, it's nicer to not show "internal edges". Most will be dissolved anyway.
        indices = [[v.index for v in e.verts] for e in bm.edges if len(e.link_faces) != 2]
        bm.free()

        return vertices, indices

    def get_editmesh_geom(self, obj):
        """Parses editmode mesh geometry into line segments"""
        bm = bmesh.from_edit_mesh(obj.data)
        indices = [[v.index for v in edge.verts] for edge in bm.edges]
        vertices = [obj.matrix_world @ v.co for v in bm.verts]
        return vertices, indices

    def decorate(self, context: bpy.types.Context, obj: bpy.types.Object):
        """perform actual drawing stuff"""
        raise NotImplementedError()

    def draw_arrow(self, context, obj):
        # gather geometry data and convert to winspace
        verts, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        arrow_size = viewportDrawingScale * 16
        angle = radians(15)
        rot_matrix_cw = Matrix.Rotation(-angle, 2)
        rot_matrix_ccw = Matrix.Rotation(angle, 2)

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }
        last_vert = len(winspace_verts) - 1

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)

            # arrow head on last vert
            if edge[1] == last_vert:
                edge_dir = (v1 - v0).normalized()
                arrow_head = get_arrow_head(edge_dir, arrow_size, rot_matrix_cw, rot_matrix_ccw)
                add_verts_sequence([v1, v1 - arrow_head[1], v1 - arrow_head[2]], start_i, **out_kwargs, closed=True)
                start_i += 3
                gap = edge_dir * arrow_size
                # stem with gaps for arrows
                add_verts_sequence([v0, v1 - gap], start_i, **out_kwargs)
            else:
                # stem with gaps for arrows
                add_verts_sequence([v0, v1], start_i, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)

    def draw_batch(
        self,
        shader_type: str,
        content_pos: list[Vector],
        color: tuple[float, float, float, float],
        indices: Optional[list[tuple[int, int]]] = None,
    ) -> None:
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self.line_shader if shader_type == "LINES" else self.base_shader
        batch = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    def draw_lines(
        self,
        context: bpy.types.Context,
        obj: Optional[bpy.types.Object],
        vertices: list[Vector],
        indices: list[tuple[int, int]],
        color: Optional[tuple[float, float, float, float]] = None,
        line_width: float = 1.0,
    ) -> None:
        # TODO: `obj` is actually never used, need to remove it
        """`verts` should be in winspace with `(0,0,0)` in the screen left bottom corner, not in the center"""
        region = context.region
        if not color:
            color = tool.Blender.get_addon_preferences().decorations_colour

        self.line_shader.bind()
        # POLYLINE_UNIFORM_COLOR specific uniforms
        self.line_shader.uniform_float("viewportSize", (region.width, region.height))
        self.line_shader.uniform_float("lineWidth", line_width)
        gpu.state.blend_set("ALPHA")
        self.draw_batch("LINES", vertices, color, indices)

    def get_viewport_drawing_scale(self, context: bpy.types.Context) -> float:
        # Horrific prototype code
        factor = self.camera_zoom_to_factor(context.space_data.region_3d.view_camera_zoom)
        camera_width_px = factor * context.region.width
        mm_to_px = camera_width_px / self.get_camera_width_mm()
        # 0.00025 is a magic constant number I visually discovered to get the right number.
        # It probably should be dynamically calculated using system.dpi or something.
        viewport_drawing_scale = 0.00025 * mm_to_px
        return viewport_drawing_scale

    def draw_label(
        self,
        context,
        text,
        pos,
        text_dir,
        gap=4,
        center=True,
        vcenter=False,
        font_size_mm=2.5,
        line_no=0,
        box_alignment=None,
        multiline=False,
        multiline_to_bottom=True,
    ):
        """Draw text label

        Args:
          pos: bottom-center
          multiline: \n characters will be interpreted as line breaks

        aligned and centered at segment middle

        NOTE: `blf.draw` seems to ignore the \n character, so we have to split the text ourselves
        and use the `line_no` argument of `draw_label`

        """

        if multiline:
            line_no = line_no
            lines = text.split("\\n")
            lines = lines if multiline_to_bottom else lines[::-1]
            for line in lines:
                self.draw_label(
                    context,
                    line,
                    pos,
                    text_dir,
                    gap=gap,
                    center=center,
                    vcenter=vcenter,
                    font_size_mm=font_size_mm,
                    line_no=line_no,
                    box_alignment=box_alignment,
                )
                line_no += 1 if multiline_to_bottom else -1
            return

        font_id = self.font_id

        color = tool.Blender.get_addon_preferences().decorations_colour

        ang = -Vector((1, 0)).angle_signed(text_dir)
        cos = math.cos(ang)
        sin = math.sin(ang)
        rotation_matrix = Matrix.Rotation(-ang, 2)

        # Horrific prototype code
        factor = self.camera_zoom_to_factor(context.space_data.region_3d.view_camera_zoom)
        camera_width_px = factor * context.region.width
        mm_to_px = camera_width_px / self.get_camera_width_mm()
        # magic_font_scale's default of (0.004118616) is a magic constant number I visually discovered to get the right number.
        # In particular it works only for the OpenGOST font and produces a 2.5mm font size.
        # It probably should be dynamically calculated using system.dpi or something.
        # font_size = 16 <-- this is a good default
        # TODO: need to synchronize it better with svg

        props = tool.Drawing.get_document_props()
        magic_font_scale = props.magic_font_scale
        font_size_px = int(magic_font_scale * mm_to_px) * font_size_mm / 2.5
        pos = pos - line_no * font_size_px * rotation_matrix[1]

        blf.size(font_id, font_size_px)

        if box_alignment or center or vcenter:
            w, h = blf.dimensions(font_id, text)

        if box_alignment:
            box_alignment_offset = Vector((0, 0))
            if "bottom" in box_alignment:
                pass
            elif "top" in box_alignment:
                box_alignment_offset += Vector((0, h))
            else:
                # middle / center
                box_alignment_offset += Vector((0, h / 2))

            if "left" in box_alignment:
                pass
            elif "right" in box_alignment:
                box_alignment_offset += Vector((w, 0))
            else:
                # middle / center
                box_alignment_offset += Vector((w / 2, 0))

            pos -= rotation_matrix.transposed() @ box_alignment_offset
        else:
            # horizontal centering
            if center:
                pos -= Vector((cos, sin)) * w * 0.5

            # vertical centering
            if vcenter:
                pos -= Vector((-sin, cos)) * h * 0.5

            # side-shifting
            if gap:
                pos += Vector((-sin, cos)) * gap

        blf.enable(font_id, blf.ROTATION)
        blf.position(font_id, pos.x, pos.y, 0) if hasattr(pos, "x") else blf.position(font_id, 0, 0, 0)

        blf.rotation(font_id, ang)
        blf.color(font_id, *color)
        blf.enable(font_id, blf.SHADOW)
        blf.shadow(font_id, 6, 0, 0, 0, 1)
        blf.draw(font_id, text)
        blf.disable(font_id, blf.ROTATION)
        blf.disable(font_id, blf.SHADOW)

    def draw_dimension_text(self, context, get_text, description, dimension_data, **draw_label_kwargs):
        prefix = dimension_data["text_prefix"]
        suffix = dimension_data["text_suffix"]
        show_description_only = dimension_data["show_description_only"]

        line_number_start = 0
        if not show_description_only:
            text = get_text()
            full_prefix = ((description + "\\n") if description else "") + prefix
            text = full_prefix + text + suffix
            line_number_start -= full_prefix.count("\\n")
        else:
            if not description:
                return
            text = description

        self.draw_label(context, text=text, line_no=line_number_start, multiline=True, **draw_label_kwargs)

    @lru_cache(maxsize=None)
    def format_value(self, context, value, custom_unit=None):
        drawing_pset_data = DrawingsData.data["active_drawing_pset_data"]
        precision = drawing_pset_data.get("MetricPrecision", None)
        if not precision:
            precision = drawing_pset_data.get("ImperialPrecision", None)

        decimal_places = drawing_pset_data.get("DecimalPlaces", None)
        return format_distance(
            value,
            precision=precision,
            decimal_places=decimal_places,
            suppress_zero_inches=True,
            custom_unit=custom_unit,
        )

    def draw_asterisk(self, context: bpy.types.Context, pos: Vector, rotation: float = 0.0, scale: float = 1.0) -> None:
        """`pos` is a world space position\n
        `rotation` value in radians"""
        # gather geometry data and convert to winspace
        verts = [pos]
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        circle_size = viewportDrawingScale * 4 * scale

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        v0 = winspace_verts[0]
        asterisk_head = get_circle_head_asterisk(circle_size, rotation=rotation)
        start_i = 0
        for segment in asterisk_head:
            start_i = add_verts_sequence(add_offsets(v0, segment), start_i, **out_kwargs)
        self.draw_lines(context, None, output_verts, output_edges)

    def get_annotation_direction(self, context: bpy.types.Context, obj: bpy.types.Object) -> Vector:
        camera = context.scene.camera

        def get_basis_vector(matrix, i=0):
            """returns basis vector for i in world space, unaffected by object scale"""
            return matrix.inverted()[i].to_3d().normalized()

        text_dir_world_x_axis = get_basis_vector(obj.matrix_world)
        camera_matrix = tool.Drawing.get_camera_matrix(camera)
        text_dir = (camera_matrix.inverted().to_quaternion() @ text_dir_world_x_axis).to_2d().normalized()
        return text_dir

    def draw_symbol(self, context: bpy.types.Context, obj: bpy.types.Object, annotation_dir: Vector) -> None:
        if obj.type not in ("MESH", "EMPTY"):
            return

        if obj.type == "MESH":
            mesh: bpy.types.Mesh = obj.data

            if len(mesh.vertices) == 0:
                return

            if mesh.edges:
                MiscDecorator.decorate(self, context, obj)
                return

            symbol = DecoratorData.get_symbol(obj)
            if not symbol:
                return

            for vert in mesh.vertices:
                self.draw_asterisk(context, obj.matrix_world @ vert.co)

            return

        # EMPTY objects
        symbol = DecoratorData.get_symbol(obj)
        if not symbol:
            return

        rotation = -Vector((1, 0)).angle_signed(annotation_dir)
        # NOTE: for now we assume that scale is uniform
        self.draw_asterisk(context, obj.location, rotation, obj.scale.x)

    def draw_text(
        self,
        context: bpy.types.Context,
        obj: bpy.types.Object,
        text_world_position: Optional[Vector] = None,
        reverse_lines_order=False,
    ) -> None:
        """if `text_world_position` is not provided, the object's location will be used"""

        if not text_world_position:
            text_world_position = obj.location

        region = context.region
        region3d = context.region_data
        text_dir = self.get_annotation_direction(context, obj)

        if not (pos := location_3d_to_region_2d(region, region3d, text_world_position)):
            return
        props = tool.Drawing.get_text_props(obj)
        text_data = DecoratorData.get_ifc_text_data(obj)
        if props.is_editing:
            text_data = text_data | props.get_text_edited_data()
        literals_data = text_data["Literals"]
        symbol = text_data["Symbol"]
        newline_at = text_data["Newline_At"]
        text_scale = 1.0

        # draw asterisk symbol to indicate that there is some symbol that's not shown in viewport
        if symbol:
            self.draw_symbol(context, obj, text_dir)
            text_scale = obj.scale.x

        line_i = 0
        font_size_mm = text_data["FontSize"] * text_scale
        for literal_data in literals_data:
            box_alignment = literal_data["BoxAlignment"]
            text = literal_data["CurrentValue"]

            if newline_at != 0:
                text = helper.add_newline_between_words(text, newline_at)

            multiple_lines = text.split("\n")

            for line in multiple_lines:
                self.draw_label(
                    context,
                    line,
                    pos,
                    text_dir,
                    gap=0,
                    center=False,
                    vcenter=False,
                    font_size_mm=font_size_mm,
                    line_no=line_i,
                    box_alignment=box_alignment,
                )
                line_i += 1 if not reverse_lines_order else -1


class DimensionDecorator(BaseDecorator):
    """Decorator for dimension objects
    - each edge of a segment with arrow
    - puts metric text next to each segment
    """

    objecttype = "DIMENSION"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts_original, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        winspace_verts = worldspace_to_winspace(verts_original, context)
        viewportDrawingScale = self.get_viewport_drawing_scale(context)

        # setup geometry parameters
        dimension_style = DecoratorData.get_dimension_data(obj)["dimension_style"]
        if dimension_style == "oblique":
            size = viewportDrawingScale * 10  # OLBIQUE_SYMBOL_SIZE
            angle = radians(45)
        else:
            size = viewportDrawingScale * 16  # ARROW_SIZE
            angle = radians(15)
        rot_matrix_cw = Matrix.Rotation(-angle, 2)
        rot_matrix_ccw = Matrix.Rotation(angle, 2)

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            edge_dir = (v1 - v0).normalized()
            start_i = len(output_verts)

            if dimension_style == "oblique":
                ortho = Vector((-edge_dir.y, edge_dir.x)).to_3d()
                # oblique dimension head
                head = []
                head.append(-edge_dir * size * 0.66)
                head.append(Vector([0] * 3))
                head.append(ortho * size)
                head.append(-head[-1])
                head.append(Vector([0] * 3))
                head.append(((rot_matrix_ccw @ edge_dir.xy) * size * 0.47).to_3d())
                head.append(-head[-1])

                n_segments = len(head) - 1
                # start edge arrow
                add_verts_sequence([v0 + v for v in head], start_i, **out_kwargs)
                # end edge arrow
                add_verts_sequence([v1 + v for v in head], start_i + n_segments + 1, **out_kwargs)
                # stem
                add_verts_sequence([v0, v1], start_i + n_segments * 2 + 2, **out_kwargs)

            else:
                # arrow dimension head
                head = get_arrow_head(edge_dir, size, rot_matrix_cw, rot_matrix_ccw)
                # start edge arrow
                add_verts_sequence([v0, v0 + head[1], v0 + head[2]], start_i, **out_kwargs, closed=True)
                # end edge arrow
                add_verts_sequence([v1, v1 - head[1], v1 - head[2]], start_i + 3, **out_kwargs, closed=True)
                # stem with gaps for arrows
                add_verts_sequence([v0 + head[0], v1 - head[0]], start_i + 6, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)
        self.draw_labels(context, obj, verts_original, edges_original)

    def draw_labels(self, context, obj, vertices, indices):
        region = context.region
        region3d = context.region_data

        element = tool.Ifc.get_entity(obj)
        description = element.Description
        dimension_data = DecoratorData.get_dimension_data(obj)
        show_description_only = dimension_data["show_description_only"]
        text_prefix = dimension_data["text_prefix"]
        text_suffix = dimension_data["text_suffix"]
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        text_offset_value = viewportDrawingScale * 3

        for i0, i1 in indices:
            v0 = Vector(vertices[i0])
            v1 = Vector(vertices[i1])
            p0 = location_3d_to_region_2d(region, region3d, v0)
            p1 = location_3d_to_region_2d(region, region3d, v1)
            if not p0 or not p1:
                return
            text_dir = p1 - p0
            if text_dir.length < 1:
                continue
            perpendicular = Vector((-text_dir.y, text_dir.x)).normalized()
            text_offset = perpendicular * text_offset_value

            common_label_attrs = {
                "context": context,
                "multiline": True,
                "text_dir": text_dir,
            }
            base_pos = p0 + text_dir * 0.5

            if not show_description_only:
                length = (v1 - v0).length
                text = self.format_value(context, length, custom_unit=dimension_data["custom_unit"])
                if isinstance(self, DiameterDecorator):
                    text = "D" + text
                text = text_prefix + text + text_suffix
            else:
                if not description:
                    continue
                text = description

            self.draw_label(
                text=text,
                pos=base_pos + text_offset,
                box_alignment="bottom-middle",
                multiline_to_bottom=False,
                **common_label_attrs,
            )

            if not show_description_only and description:
                self.draw_label(
                    text=description, pos=base_pos - text_offset, box_alignment="top-middle", **common_label_attrs
                )


class AngleDecorator(BaseDecorator):
    """Decorator for angle objects
    - each edge of a segment with arrow
    - every non-last edge has angle circle
    - every circle is labeled with angle in degrees
    """

    objecttype = "ANGLE"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        out_kwargs_edges = {
            "output_verts": [],
            "output_edges": [],
        }
        out_kwargs_arcs = {
            "output_verts": [],
            "output_edges": [],
        }
        last_vert = len(winspace_verts) - 1

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i_edges = len(out_kwargs_edges["output_verts"])
            start_i_arcs = len(out_kwargs_arcs["output_verts"])

            # draw edge
            add_verts_sequence([v0, v1], start_i_edges, **out_kwargs_edges)

            # draw angle only on interal verts
            if edge[1] != last_vert:
                v2 = winspace_verts[edge[1] + 1]
                edge0 = v0 - v1  # note that order is reversed
                edge1 = v2 - v1
                edge_dir0 = edge0.normalized()
                edge_dir1 = edge1.normalized()
                edge0_length = edge0.length
                edge1_length = edge1.length

                angle_circle_size = min(edge0_length, edge1_length)
                circle_start = edge_dir0 * angle_circle_size
                circle_end = edge_dir1 * angle_circle_size

                try:
                    cos_a = edge0.dot(edge1) / (edge0_length * edge1_length)
                except ZeroDivisionError:
                    continue
                circle_angle = acos(cos_a)
                counter_clockwise = tool.Cad.is_counter_clockwise_order(v2, v1, v0)
                angle_circle = get_angle_circle(circle_start, circle_angle, counter_clockwise)
                add_verts_sequence([v1 + v for v in angle_circle], start_i_arcs, **out_kwargs_arcs)

        arcs_color = None
        edges_color = UNSPECIAL_ELEMENT_COLOR
        if context.active_object == obj and obj.data.is_editmode:
            arcs_color = tool.Blender.get_addon_preferences().decorator_color_special
            edges_color = None

        self.draw_lines(
            context,
            obj,
            vertices=out_kwargs_arcs["output_verts"],
            indices=out_kwargs_arcs["output_edges"],
            color=arcs_color,
        )
        self.draw_lines(
            context,
            obj,
            vertices=out_kwargs_edges["output_verts"],
            indices=out_kwargs_edges["output_edges"],
            color=edges_color,
        )
        self.draw_labels(context, obj, verts, edges_original)

    def draw_labels(self, context, obj, vertices, indices):
        # TODO: merge with code from .decorate
        region = context.region
        region3d = context.region_data

        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        ANGLE_LABEL_OFFSET = 20 * viewportDrawingScale
        last_segment_i = len(indices) - 1

        for edge_i, edge_vertices in enumerate(indices):
            if edge_i == last_segment_i:
                continue

            # draw angle label
            i0, i1 = edge_vertices
            v0 = Vector(vertices[i0])
            v1 = Vector(vertices[i1])
            v2 = Vector(vertices[i1 + 1])

            # calculate angle value
            edge0_ws = v0 - v1
            edge1_ws = v2 - v1
            try:
                cos_a = edge0_ws.dot(edge1_ws) / (edge0_ws.length * edge1_ws.length)
                angle_rad = acos(cos_a)
                angle = angle_rad / pi * 180
            except ZeroDivisionError:
                angle_rad, angle = 0, 0

            # calculate angle position
            p0, p1, p2 = [location_3d_to_region_2d(region, region3d, p) for p in vertices[i0 : i1 + 2]]
            if not p0 or not p1 or not p2:
                continue
            edge0 = p0 - p1
            edge1 = p2 - p1
            radius = min(edge0.length_squared, edge1.length_squared) ** 0.5
            # TODO: can be helpful for svg positioning
            base_edge = edge0 if tool.Cad.is_counter_clockwise_order(p0, p1, p2) else edge1
            text_offset = (Matrix.Rotation(-angle_rad / 2, 2) @ base_edge).normalized() * (radius + ANGLE_LABEL_OFFSET)
            label_position = p1 + text_offset
            drawing_pset_data = DrawingsData.data["active_drawing_pset_data"]
            decimal_places = drawing_pset_data.get("AngleDecimalPlaces", None)
            text = f"{round(angle, decimal_places)}°"
            label_dir = Vector((1, 0))
            self.draw_label(context, text, label_position, label_dir, box_alignment="center")


class DiameterDecorator(DimensionDecorator):
    objecttype = "DIAMETER"


class LeaderDecorator(BaseDecorator):
    """Decorating text with arrows
    - head point with arrow
    """

    objecttype = "TEXT_LEADER"

    def get_spline_end(self, obj):
        spline = obj.data.splines[0]
        spline_points = spline.bezier_points if spline.bezier_points else spline.points
        if not spline_points:
            return Vector((0, 0, 0))
        return (obj.matrix_world @ spline_points[0].co).to_3d()

    def decorate(self, context, obj):
        self.draw_arrow(context, obj)
        self.draw_text(context, obj, self.get_spline_end(obj))


class RadiusDecorator(BaseDecorator):
    """Decorating text with arrows
    - head point with arrow
    """

    objecttype = "RADIUS"

    def get_spline_points(self, obj):
        spline = obj.data.splines[0]
        spline_points = spline.bezier_points if spline.bezier_points else spline.points
        if not spline_points:
            spline_points = [Vector([0, 0, 0])] * 2
        elif len(spline_points) == 1:
            spline_points = [spline_points[0]] * 2

        return [(obj.matrix_world @ p.co) for p in spline_points[:2]]

    def decorate(self, context, obj):
        self.draw_arrow(context, obj)
        self.draw_labels(context, obj)

    def draw_labels(self, context, obj):
        region = context.region
        region3d = context.region_data
        spline_points = self.get_spline_points(obj)

        p0, p1 = [location_3d_to_region_2d(region, region3d, p) for p in self.get_spline_points(obj)]
        if not p0 or not p1:
            return
        element = tool.Ifc.get_entity(obj)
        description = element.Description
        dimension_data = DecoratorData.get_dimension_data(obj)
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        text_offset = 20 * viewportDrawingScale

        pos = p0 - (p1 - p0).normalized() * text_offset

        def get_text():
            length = (spline_points[-1] - spline_points[-2]).length
            return "R" + self.format_value(context, length, custom_unit=dimension_data["custom_unit"])

        self.draw_dimension_text(
            context, get_text, description, dimension_data, pos=pos, text_dir=Vector((1, 0)), box_alignment="center"
        )


class FallDecorator(BaseDecorator):
    """Decorating text with arrows
    - head point with arrow
    """

    objecttype = "FALL"

    def decorate(self, context, obj):
        self.draw_arrow(context, obj)
        self.draw_labels(context, obj)

    def draw_labels(self, context, obj):
        region = context.region
        region3d = context.region_data
        dir = Vector((1, 0))
        if not (pos := location_3d_to_region_2d(region, region3d, self.get_spline_end(obj))):
            return

        spline = obj.data.splines[0]
        spline_points = spline.bezier_points if spline.bezier_points else spline.points

        # generate label text
        # same function as in svgwriter.py
        def get_label_text():
            element = tool.Ifc.get_entity(obj)
            assert element
            B, A = [v.co.xyz for v in spline_points[:2]]
            rise = abs(A.z - B.z)
            O = A.copy()
            O.z = B.z
            run = (B - O).length
            if run != 0:
                angle_tg = rise / run
                angle = round(degrees(atan(angle_tg)))
            else:
                angle = 90

            # ues SLOPE_ANGLE as default
            object_type = ifcopenshell.util.element.get_predefined_type(element)
            if object_type in ("FALL", "SLOPE_ANGLE"):
                return f"{angle}°"
            elif object_type == "SLOPE_FRACTION":
                if angle == 90:
                    return "-"
                return f"{self.format_value(context, rise)} / {self.format_value(context, run)}"
            elif object_type == "SLOPE_PERCENT":
                if angle == 90:
                    return "-"
                return f"{round(angle_tg * 100)} %"

        if spline_points:
            text = get_label_text()
            self.draw_label(context, text, pos, dir, gap=0, center=False, vcenter=False)

    def get_spline_end(self, obj):
        spline = obj.data.splines[0]
        spline_points = spline.bezier_points if spline.bezier_points else spline.points
        if not spline_points:
            return Vector((0, 0, 0))
        return obj.matrix_world @ spline_points[0].co


class StairDecorator(BaseDecorator):
    """Decorating stairs
    - head point with arrow
    - tail point with circle
    - middle points w/out decorations
    """

    objecttype = "STAIR_ARROW"

    def decorate(self, context, obj):
        # very similar to the draw_arrow function

        # gather geometry data and convert to winspace
        verts, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        arrow_size = viewportDrawingScale * 24
        circle_size = viewportDrawingScale * 6
        angle = radians(60)
        rot_matrix_cw = Matrix.Rotation(-angle, 2)
        rot_matrix_ccw = Matrix.Rotation(angle, 2)

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }
        last_vert = len(winspace_verts) - 1

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)

            # circle head on first vert
            if edge[0] == 0:
                circle_head = get_circle_head(circle_size)
                start_i = add_verts_sequence([v + v0 for v in circle_head], start_i, **out_kwargs, closed=True)

            # arrow head on last vert
            if edge[1] == last_vert:
                edge_dir = (v1 - v0).normalized()
                arrow_head = get_arrow_head(edge_dir, arrow_size, rot_matrix_cw, rot_matrix_ccw)
                start_i = add_verts_sequence([v1 - arrow_head[1], v1, v1 - arrow_head[2]], start_i, **out_kwargs)

            # stem with gaps for arrows
            add_verts_sequence([v0, v1], start_i, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)


class MiscDecorator(BaseDecorator):
    objecttype = "MISC"

    def decorate(self, context: bpy.types.Context, obj: bpy.types.Object):
        if obj.data.is_editmode:
            verts, idxs = self.get_editmesh_geom(obj)
        else:
            verts, idxs = self.get_mesh_geom(obj)
        winspace_verts = worldspace_to_winspace(verts, context)
        self.draw_lines(context, obj, winspace_verts, idxs)


class RevisionCloudDecorator(BaseDecorator):
    objecttype = "REVISION_CLOUD"

    def decorate(self, context, obj):
        if obj.data.is_editmode:
            verts, idxs = self.get_editmesh_geom(obj)
        else:
            verts, idxs = self.get_mesh_geom(obj)
        winspace_verts = worldspace_to_winspace(verts, context)
        # TODO: draw revision clouds inside viewport?
        self.draw_lines(context, obj, winspace_verts, idxs, color=(1, 0, 0, 1), line_width=2)


# TODO: custom frag shader to support dashed lines?
class HiddenDecorator(BaseDecorator):
    objecttype = "HIDDEN_LINE"

    def decorate(self, context, obj):
        if obj.data.is_editmode:
            verts, idxs = self.get_editmesh_geom(obj)
        else:
            verts, idxs = self.get_mesh_geom(obj)
        winspace_verts = worldspace_to_winspace(verts, context)
        color = [i for i in tool.Blender.get_addon_preferences().decorations_colour]
        color[3] = 0.5
        self.draw_lines(context, obj, winspace_verts, idxs, color)


class PlanLevelDecorator(BaseDecorator):
    objecttype = "PLAN_LEVEL"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        cross_size = viewportDrawingScale * 16
        circle_size = viewportDrawingScale * 8

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }
        last_vert = len(winspace_verts) - 1

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)
            gap = Vector((0, 0, 0))

            # arrow head on last vert
            if edge[1] == last_vert:
                circle_head = get_circle_head(circle_size)
                start_i = add_verts_sequence(add_offsets(v1, circle_head), start_i, **out_kwargs, closed=True)

                edge_dir = (v1 - v0).normalized()
                side = (edge_dir.yx * Vector((1, -1))).to_3d()
                start_i = add_verts_sequence(
                    add_offsets(v1, [side * cross_size, side * -cross_size]), start_i, **out_kwargs
                )

                gap = edge_dir * cross_size

            # stem with gaps for arrows
            add_verts_sequence([v0, v1 + gap], start_i, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)
        self.draw_labels(context, obj, self.get_splines(obj))

    def draw_labels(self, context, obj, splines):
        region = context.region
        region3d = context.region_data

        element = tool.Ifc.get_entity(obj)
        description = element.Description
        dimension_data = DecoratorData.get_dimension_data(obj)

        for verts in splines:
            p0, p1 = [location_3d_to_region_2d(region, region3d, v) for v in verts[:2]]
            if not p0 or not p1:
                continue
            text_dir = p1 - p0
            if text_dir.length < 1:
                continue

            if text_dir.x > 0:
                box_alignment = "bottom-left"
            else:
                box_alignment = "bottom-right"
                text_dir *= -1

            def get_text():
                z = verts[0].z
                rl = self.format_value(context, z)
                text = "{}{}".format("" if z < 0 else "+", rl)
                return text

            self.draw_dimension_text(
                context,
                get_text,
                description,
                dimension_data,
                box_alignment=box_alignment,
                pos=p0,
                text_dir=text_dir,
                gap=8,
                center=False,
            )


class SectionLevelDecorator(BaseDecorator):
    objecttype = "SECTION_LEVEL"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts, edges_original, _ = self.get_path_geom(obj, topo=False)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        callout_gap = viewportDrawingScale * 8
        callout_size = viewportDrawingScale * 64

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)
            edge_dir = (v1 - v0).normalized()
            gap = edge_dir * callout_gap

            # callout head on first vert
            if edge[0] == 0:
                side = (edge_dir.yx * Vector((1, -1))).to_3d()
                text_position = v0 + gap + side * callout_gap
                text_dir = -edge_dir
                callout_head = get_callout_head(edge_dir, side, callout_size, callout_gap)
                start_i = add_verts_sequence([v + v0 for v in callout_head], start_i, **out_kwargs, closed=False)

            # stem with gaps for arrows
            add_verts_sequence([v0 + gap, v1 - gap], start_i, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)
        self.draw_labels(context, obj, self.get_splines(obj), text_position.to_2d(), text_dir.to_2d())

    def draw_labels(self, context, obj, splines, text_position, text_dir):
        element = tool.Ifc.get_entity(obj)
        storey = tool.Drawing.get_annotation_element(element)
        tag = storey.Name if storey else element.Description

        dimension_data = DecoratorData.get_dimension_data(obj)

        for verts in splines:

            def get_text():
                z = verts[0].z
                rl = self.format_value(context, z)
                text = "RL {}{}".format("" if z < 0 else "+", rl)
                return text

            self.draw_dimension_text(
                context,
                get_text,
                tag,
                dimension_data,
                box_alignment="bottom-left",
                pos=text_position,
                text_dir=text_dir,
            )
            break  # support only 1 label


class BreakDecorator(BaseDecorator):
    """Decorator for breakline objects
    - first edge of a mesh with zigzag thingy in the middle

    Uses first two vertices in verts list.
    """

    objecttype = "BREAKLINE"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        if obj.data.is_editmode:
            verts, edges_original = self.get_editmesh_geom(obj)
        else:
            verts, edges_original = self.get_mesh_geom(obj)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        break_length = viewportDrawingScale * 32
        break_width = viewportDrawingScale * 16

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            middle = (v0 + v1) * 0.5
            edge_dir = (v1 - v0).normalized()
            start_i = len(output_verts)

            quart = edge_dir * break_length * 0.25
            side = (edge_dir.yx * Vector((1, -1)) * break_width).to_3d()

            # draw edge with zigzag
            add_verts_sequence(
                [v0, middle, middle + quart - side, middle + quart * 3 + side, middle + quart * 4, v1],
                start_i,
                **out_kwargs,
            )

        self.draw_lines(context, obj, output_verts, output_edges)


class BattingDecorator(BaseDecorator):
    """Decorator for batting objects"""

    objecttype = "BATTING"

    def decorate(self, context, obj):
        def get_winspace_batting_thickness():
            # TODO: find the less ugly way to figure thickness
            thickness = DecoratorData.get_batting_thickness(obj)
            region = context.region
            region3d = context.region_data
            original_edge_length = (verts[1] - verts[0]).length
            clipspace_verts = [region3d.perspective_matrix @ v for v in verts[:2]]
            winspace_verts = [v * Vector([region.width / 2, region.height / 2, 1]) for v in clipspace_verts]
            win_space_edge_length = (winspace_verts[1] - winspace_verts[0]).xy.length
            k = win_space_edge_length / original_edge_length
            batting_thickness = k * thickness
            return batting_thickness

        # gather geometry data and convert to winspace
        if obj.data.is_editmode:
            verts, edges_original = self.get_editmesh_geom(obj)
        else:
            verts, edges_original = self.get_mesh_geom(obj)
        if not edges_original:
            return
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        batting_thickness = get_winspace_batting_thickness()

        output_verts = []
        output_edges = []

        # process edges
        for edge in edges_original[:1]:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            edge_dir = (v1 - v0).normalized()
            side = (edge_dir.yx * Vector((1, -1)) * batting_thickness * 0.5).to_3d()
            start_i = len(output_verts)

            # simplified rectangle + cross to indicate batting
            output_verts.extend(
                [
                    v0 + side,
                    v0 - side,
                    v1 - side,
                    v1 + side,
                ]
            )
            output_edges.extend(
                [
                    (start_i, start_i + 1),
                    (start_i + 1, start_i + 2),
                    (start_i + 2, start_i + 3),
                    (start_i + 3, start_i),
                    (start_i, start_i + 2),
                    (start_i + 1, start_i + 3),
                ]
            )

        self.draw_lines(context, obj, output_verts, output_edges)


# TODO: custom shader to support dashed lines
class GridDecorator(BaseDecorator):
    objecttype = "GRID"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts, edges_original = self.get_mesh_geom(obj)
        if not edges_original:
            return
        verts = verts[:2]
        edges_original = [(0, 1)]
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        circle_size = viewportDrawingScale * 16
        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)
            circle_head = get_circle_head(circle_size)
            edge_dir = (v1 - v0).normalized()
            gap = edge_dir * circle_size

            # circle head on first vert
            start_i = add_verts_sequence(add_offsets(v0, circle_head), start_i, **out_kwargs, closed=True)
            start_i = add_verts_sequence(add_offsets(v1, circle_head), start_i, **out_kwargs, closed=True)

            # stem with gaps for circles
            add_verts_sequence([v0 + gap, v1 - gap], start_i, **out_kwargs)

        color = [i for i in tool.Blender.get_addon_preferences().decorations_colour]
        color[3] = 0.5
        self.draw_lines(context, obj, output_verts, output_edges, color)
        self.draw_labels(context, obj, verts)

    def draw_labels(self, context, obj, vertices):
        region = context.region
        region3d = context.region_data
        v0 = Vector(vertices[0])
        v1 = Vector(vertices[1])
        p0 = location_3d_to_region_2d(region, region3d, v0)
        p1 = location_3d_to_region_2d(region, region3d, v1)
        dir = Vector((1, 0))
        text = obj.name.split("/")[1].split(".")[0]
        if p0:
            self.draw_label(context, text, p0, dir, vcenter=True, gap=0)
        if p1:
            self.draw_label(context, text, p1, dir, vcenter=True, gap=0)


class ElevationDecorator(BaseDecorator):
    objecttype = "ELEVATION"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        verts = [obj.matrix_world @ v for v in [Vector((0, 0, 0)), Vector((0, 0, -1))]]
        edges_original = [(0, 1)]
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        triangle_length = viewportDrawingScale * 22.63
        triangle_width = viewportDrawingScale * 11.31
        circle_size = viewportDrawingScale * 8

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)

            circle_head = get_circle_head(circle_size)
            start_i = add_verts_sequence(add_offsets(v0, circle_head), start_i, **out_kwargs, closed=True)

            edge_dir = (v1 - v0).normalized()
            side = (edge_dir.yx * Vector((1, -1))).to_3d()
            triangle_head = get_triangle_head(side, edge_dir, triangle_length, triangle_width)
            start_i = add_verts_sequence(add_offsets(v0, triangle_head), start_i, **out_kwargs, closed=True)

            # # stem with gaps for arrows
            # add_verts_sequence([v0, v1], start_i, **out_kwargs)

        self.draw_lines(context, obj, output_verts, output_edges)


class SectionDecorator(BaseDecorator):
    objecttype = "SECTION"

    def decorate(self, context, obj):
        # gather geometry data and convert to winspace
        if obj.data.is_editmode:
            verts, edges_original = self.get_editmesh_geom(obj)
        else:
            verts, edges_original = self.get_mesh_geom(obj)
        if not edges_original:
            return
        viewportDrawingScale = self.get_viewport_drawing_scale(context)
        winspace_verts = worldspace_to_winspace(verts, context)

        # setup geometry parameters
        triangle_length = viewportDrawingScale * 22.63
        triangle_width = viewportDrawingScale * 11.31
        circle_size = viewportDrawingScale * 8

        output_verts = []
        output_edges = []
        out_kwargs = {
            "output_verts": output_verts,
            "output_edges": output_edges,
        }

        display_data = DecoratorData.get_section_markers_display_data(obj)
        connect_markers = display_data["connect_markers"]
        display_start_symbol = display_data["start"]["add_symbol"]
        display_end_symbol = display_data["end"]["add_symbol"]
        display_start_circle = display_data["start"]["add_circle"]
        display_end_circle = display_data["end"]["add_circle"]

        # process edges
        for edge in edges_original:
            v0, v1 = winspace_verts[edge[0]], winspace_verts[edge[1]]
            start_i = len(output_verts)

            if display_start_circle or display_end_circle:
                circle_head = get_circle_head(circle_size)

            if display_start_symbol or display_end_symbol or connect_markers:
                edge_dir = (v1 - v0).normalized()
                side = (edge_dir.yx * Vector((1, -1))).to_3d()
                edge_dir_circle = edge_dir * circle_size

            if display_start_symbol or display_end_symbol:
                triangle_head = get_triangle_head(edge_dir, -side, triangle_length, triangle_width)
                divider_offset = []
                divider_offset.append(edge_dir_circle if connect_markers else edge_dir_circle * 3)
                divider_offset.append(edge_dir_circle)

            if display_start_circle:
                start_i = add_verts_sequence([v + v0 for v in circle_head], start_i, **out_kwargs, closed=True)
                # circle middle divider
                if not display_start_symbol:
                    start_i = add_verts_sequence(
                        [v0 + divider_offset[0], v0 - divider_offset[1]], start_i, **out_kwargs
                    )

            if display_start_symbol:
                start_i = add_verts_sequence([v + v0 for v in triangle_head], start_i, **out_kwargs, closed=True)

            if display_end_circle:
                start_i = add_verts_sequence([v + v1 for v in circle_head], start_i, **out_kwargs, closed=True)
                # circle middle divider
                if not display_end_symbol:
                    start_i = add_verts_sequence(
                        [v1 + divider_offset[1], v1 - divider_offset[0]], start_i, **out_kwargs
                    )

            if display_end_symbol:
                start_i = add_verts_sequence([v + v1 for v in triangle_head], start_i, **out_kwargs, closed=True)

            if connect_markers:
                gap = []
                gap.append(edge_dir_circle if display_start_symbol else Vector((0, 0, 0)))
                gap.append(edge_dir_circle if display_end_symbol else Vector((0, 0, 0)))
                add_verts_sequence([v0 + gap[0], v1 - gap[1]], start_i, **out_kwargs)

        # TODO: add dashed line to shader with frag shader
        self.draw_lines(context, obj, output_verts, output_edges)


class TextDecorator(BaseDecorator):
    """Decorator for text objects
    - draws the text next to the origin
    """

    objecttype = "TEXT"

    def decorate(self, context, obj):
        self.draw_text(context, obj)


class SymbolDecorator(BaseDecorator):
    objecttype = "SYMBOL"

    def decorate(self, context: bpy.types.Context, obj: bpy.types.Object) -> None:
        annotation_dir = self.get_annotation_direction(context, obj)
        self.draw_symbol(context, obj, annotation_dir)


class CutDecorator:
    installed = None
    cache = {}

    @classmethod
    def install(cls, context):
        DecoratorData.cut_cache.clear()
        DecoratorData.slice_cache.clear()
        DecoratorData.fill_cache.clear()
        if cls.installed:
            cls.uninstall()
        handler = cls()
        cls.installed = SpaceView3D.draw_handler_add(handler, (context,), "WINDOW", "POST_VIEW")

    @classmethod
    def uninstall(cls):
        try:
            SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
        except ValueError:
            pass
        cls.installed = None

    def __call__(self, context):
        if not context.scene.camera:
            return

        self.addon_prefs = tool.Blender.get_addon_preferences()
        selected_elements_color = self.addon_prefs.decorator_color_selected
        self.fallback_colour = (0.3, 0.3, 0.3, 1)

        all_vertices = []
        all_edges = []
        selected_vertices = []
        selected_edges = []
        fills = {}
        all_vertex_i_offset = 0
        selected_vertex_i_offset = 0

        for obj in [o for o in bpy.context.visible_objects if o.type == "MESH"]:
            if not (element := tool.Ifc.get_entity(obj)):
                continue
            self.decorate(context, obj, element)

            verts, edges = DecoratorData.cut_cache[element.id()]
            if verts:
                if obj.select_get():
                    selected_vertices.extend(verts)
                    selected_edges.extend([[vi + selected_vertex_i_offset for vi in e] for e in edges])
                    selected_vertex_i_offset += len(verts)
                else:
                    all_vertices.extend(verts)
                    all_edges.extend([[vi + all_vertex_i_offset for vi in e] for e in edges])
                    all_vertex_i_offset += len(verts)

            verts, edges = DecoratorData.slice_cache.get(element.id(), (None, None))
            if verts:
                if obj.select_get():
                    selected_vertices.extend(verts)
                    selected_edges.extend([[vi + selected_vertex_i_offset for vi in e] for e in edges])
                    selected_vertex_i_offset += len(verts)
                else:
                    all_vertices.extend(verts)
                    all_edges.extend([[vi + all_vertex_i_offset for vi in e] for e in edges])
                    all_vertex_i_offset += len(verts)

            for colour, element_fills in DecoratorData.fill_cache[element.id()].items():
                fills.setdefault(colour, []).append(element_fills)

        gpu.state.point_size_set(1)
        gpu.state.blend_set("ALPHA")

        # POLYLINE_UNIFORM_COLOR is good for smoothed lines since `bgl.enable(GL_LINE_SMOOTH)` is deprecated
        self.line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        self.line_shader.bind()
        # POLYLINE_UNIFORM_COLOR specific uniforms
        self.line_shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        self.line_shader.uniform_float("lineWidth", 2.0)

        # general shader
        self.shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        self.shader.bind()

        black = (0, 0, 0, 1)

        for colour, element_fills in fills.items():
            for verts_tris in element_fills:
                for verts, tris in verts_tris:
                    self.draw_batch("TRIS", verts, colour, tris)

        gpu.state.point_size_set(2)
        self.line_shader.uniform_float("lineWidth", 3.0)

        if all_vertices:
            self.draw_batch("LINES", all_vertices, black, all_edges)
            self.draw_batch("POINTS", all_vertices, black)
        if selected_vertices:
            self.draw_batch("LINES", selected_vertices, selected_elements_color, selected_edges)
            self.draw_batch("POINTS", selected_vertices, selected_elements_color)

    def draw_batch(self, shader_type, content_pos, color, indices=None):
        if not tool.Blender.validate_shader_batch_data(content_pos, indices):
            return
        shader = self.line_shader if shader_type == "LINES" else self.shader
        batch = batch_for_shader(shader, shader_type, {"pos": content_pos}, indices=indices)
        shader.uniform_float("color", color)
        batch.draw(shader)

    def decorate(self, context, obj: bpy.types.Object, element: ifcopenshell.entity_instance) -> None:
        has_cut_cache = element.id() in DecoratorData.cut_cache
        has_fill_cache = element.id() in DecoratorData.fill_cache

        # Currently selected objects must be recalculated as they may be being moved / edited.
        # If the camera is selected, we also recalculate as the user may be moving the camera.

        if not has_cut_cache or obj.select_get() or context.scene.camera.select_get():
            self.recalculate_cut(context, obj, element)
        if not has_fill_cache or obj.select_get() or context.scene.camera.select_get():
            self.recalculate_fill(context, obj, element)

    def recalculate_cut(self, context, obj: bpy.types.Object, element: ifcopenshell.entity_instance) -> None:
        if not tool.Drawing.is_intersecting_camera(obj, context.scene.camera):
            DecoratorData.cut_cache[element.id()] = (False, False)
        else:
            verts, edges = tool.Drawing.bisect_mesh(obj, context.scene.camera)
            DecoratorData.cut_cache[element.id()] = (verts, edges)

    def recalculate_fill(self, context, obj: bpy.types.Object, element: ifcopenshell.entity_instance) -> None:
        element_id = element.id()

        if not tool.Drawing.is_intersecting_camera(obj, context.scene.camera):
            DecoratorData.fill_cache[element_id] = {}
            return

        DecoratorData.fill_cache[element_id] = {}

        mesh = obj.data
        bm_original = bmesh.new()
        bm_original.from_mesh(mesh)

        # Slice our mesh into a 2D drawing cut (2D is always easier)
        camera_matrix = obj.matrix_world.inverted() @ context.scene.camera.matrix_world
        global_offset = context.scene.camera.matrix_world.col[2].xyz * -context.scene.camera.data.clip_start
        plane_co = camera_matrix.translation
        plane_no = camera_matrix.col[2].xyz
        geom = bm_original.verts[:] + bm_original.edges[:] + bm_original.faces[:]
        bmesh.ops.bisect_plane(
            bm_original,
            geom=geom,
            dist=0.0001,
            plane_co=plane_co,
            plane_no=plane_no,
            clear_outer=True,
            clear_inner=True,
        )
        bmesh.ops.remove_doubles(bm_original, verts=bm_original.verts, dist=0.000001)
        bmesh.ops.triangle_fill(bm_original, use_dissolve=True, edges=bm_original.edges)

        if not (material := ifcopenshell.util.element.get_material(element)):
            verts, tris = self.get_bmesh_tris(obj, bm_original, context.scene.camera)
            DecoratorData.fill_cache[element_id].setdefault(self.fallback_colour, []).append((verts, tris))
            return

        if material.is_a() not in ("IfcMaterialLayerSet", "IfcMaterialLayerSetUsage"):
            # Constituents, lists, and item styles not supported yet
            material = ifcopenshell.util.element.get_materials(element)[0]
            verts, tris = self.get_bmesh_tris(obj, bm_original, context.scene.camera)
            colour = self.get_material_colour(material)
            DecoratorData.fill_cache[element_id].setdefault(colour, []).append((verts, tris))
            return

        self.unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())

        if material.is_a("IfcMaterialLayerSetUsage"):
            usage = material
            layer_set = material.ForLayerSet
            offset = usage.OffsetFromReferenceLine * self.unit_scale
            sense_factor = 1 if usage.DirectionSense == "POSITIVE" else -1
        elif material.is_a("IfcMaterialLayerSet"):
            usage = None
            layer_set = material
            offset = 0
            sense_factor = 1

        if len(layer_set.MaterialLayers) == 1:
            material = layer_set.MaterialLayers[0].Material
            verts, tris = self.get_bmesh_tris(obj, bm_original, context.scene.camera)
            colour = self.get_material_colour(material)
            DecoratorData.fill_cache[element_id].setdefault(colour, []).append((verts, tris))
            return

        bm = bm_original.copy()
        prev_co = None
        if not usage:
            sense_factor = 1  # Assume the extrusion vector points in the direction sense
            no = tool.Drawing.get_extrusion_vector(element).normalized()
            co = Vector((0.0, 0.0, offset))
        elif usage.LayerSetDirection == "AXIS2":
            co = Vector((0.0, offset, 0.0))
            no = tool.Drawing.get_extrusion_vector(element).normalized()
            no = no.cross(Vector([1.0, 0.0, 0.0]))
        elif usage.LayerSetDirection == "AXIS3":
            co = Vector((0.0, 0.0, offset))
            no = tool.Drawing.get_extrusion_vector(element).normalized()
            no = Vector([0.0, 0.0, 1.0])
        elif usage.LayerSetDirection == "AXIS1":
            co = Vector((0.0, 0.0, offset))
            no = tool.Drawing.get_extrusion_vector(element).normalized()
            no = Vector([1.0, 0.0, 0.0])
        no *= sense_factor
        last_i = len(layer_set.MaterialLayers) - 1

        vert_map = {}
        verts = []
        edges = []
        j = 0

        for i, layer in enumerate(layer_set.MaterialLayers):
            prev_co = co.copy()
            co += no * layer.LayerThickness * self.unit_scale
            if i != last_i:
                bisect = bmesh.ops.bisect_plane(
                    bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=0.0001, plane_co=co, plane_no=no
                )
                for geom in bisect["geom_cut"]:
                    if isinstance(geom, bmesh.types.BMVert):
                        verts.append(tuple((obj.matrix_world @ geom.co) + global_offset))
                        vert_map[geom.index] = j
                        j += 1
                    else:
                        # It seems as though edges always appear after verts
                        edges.append([vert_map[v.index] for v in geom.verts])

            colour = self.get_material_colour(layer.Material)
            bm_fill = bm_original.copy()
            if i != last_i:
                geom = bm_fill.verts[:] + bm_fill.edges[:] + bm_fill.faces[:]
                bmesh.ops.bisect_plane(bm_fill, geom=geom, dist=0.0001, plane_co=co, plane_no=no, clear_outer=True)
            if i != 0:
                geom = bm_fill.verts[:] + bm_fill.edges[:] + bm_fill.faces[:]
                bmesh.ops.bisect_plane(bm_fill, geom=geom, dist=0.0001, plane_co=prev_co, plane_no=no, clear_inner=True)

            DecoratorData.fill_cache[element_id].setdefault(colour, []).append(
                self.get_bmesh_tris(obj, bm_fill, context.scene.camera)
            )

        DecoratorData.slice_cache[element.id()] = (verts, edges)
        bm_original.free()

    def get_material_colour(self, material: ifcopenshell.entity_instance) -> tuple[float, float, float, float]:
        body = ifcopenshell.util.representation.get_context(tool.Ifc.get(), "Model", "Body", "MODEL_VIEW")
        style = ifcopenshell.util.representation.get_material_style(material, body)
        if not style:
            return self.fallback_colour
        if not (styles := [s for s in style.Styles if s.is_a("IfcSurfaceStyleShading")]):
            return self.fallback_colour
        colour = styles[0].SurfaceColour
        return (colour.Red, colour.Green, colour.Blue, 1)

    def get_bmesh_tris(self, obj, bm, camera):
        global_offset = camera.matrix_world.col[2].xyz * -camera.data.clip_start
        triangulate = bmesh.ops.triangulate(bm, faces=bm.faces)
        vert_map = {}
        verts = []
        tris = []
        i = 0
        for face in triangulate["faces"]:
            tri = []
            for vert in face.verts:
                if index := vert_map.get(vert.index, None):
                    tri.append(index)
                else:
                    verts.append(tuple((obj.matrix_world @ vert.co) + global_offset))
                    vert_map[vert.index] = i
                    tri.append(i)
                    i += 1
            tris.append(tri)
        return verts, tris


class DecorationsHandler:
    decorators_classes: list[Type[BaseDecorator]] = [
        DimensionDecorator,
        AngleDecorator,
        GridDecorator,
        HiddenDecorator,
        LeaderDecorator,
        RadiusDecorator,
        DiameterDecorator,
        MiscDecorator,
        PlanLevelDecorator,
        SectionLevelDecorator,
        StairDecorator,
        RevisionCloudDecorator,
        BreakDecorator,
        SectionDecorator,
        ElevationDecorator,
        TextDecorator,
        SymbolDecorator,
        BattingDecorator,
        FallDecorator,
    ]

    installed = None

    @classmethod
    def install(cls, context):
        if cls.installed:
            cls.uninstall()
        handler = cls()
        # NOTE: we USE POST_PIXEL here so that we can use both POLYLINE_UNIFORM_COLOR
        # and drawing text in the same handler. BUT this means that we supply coordinates in WINSPACE
        cls.installed = SpaceView3D.draw_handler_add(handler, (context,), "WINDOW", "POST_PIXEL")

    @classmethod
    def uninstall(cls):
        try:
            SpaceView3D.draw_handler_remove(cls.installed, "WINDOW")
        except ValueError:
            pass
        cls.installed = None

    def __init__(self):
        self.decorators = {cls.objecttype: cls() for cls in self.decorators_classes}
        for object_type in ("SLOPE_ANGLE", "SLOPE_FRACTION", "SLOPE_PERCENT"):
            self.decorators[object_type] = self.decorators["FALL"]
        self.decorators["MULTI_SYMBOL"] = self.decorators["SYMBOL"]
        props = tool.Drawing.get_document_props()
        if drawing_font := props.drawing_font:
            drawing_font_path = tool.Blender.get_data_dir_path(Path("fonts") / drawing_font)
            if drawing_font_path.is_file():
                font_id = blf.load(drawing_font_path.__str__())
                for decorator in self.decorators.values():
                    decorator.font_id = font_id

    def get_objects_and_decorators(self, collection):
        # TODO: do it in data instead of the handler for performance?
        results = []
        viewport = bpy.context.space_data

        for obj in collection.all_objects:
            if not obj.visible_get(viewport=viewport):
                continue

            element = tool.Ifc.get_entity(obj)
            if not element:
                continue

            if not element.is_a("IfcAnnotation"):
                continue

            object_type: Union[str, None] = ifcopenshell.util.element.get_predefined_type(element)
            if object_type == "DRAWING":
                continue

            if dec := self.decorators.get(object_type, None):
                results.append((obj, dec))

            elif isinstance(obj.data, bpy.types.Mesh):
                if object_type == "LINEWORK" and "dashed" in str(
                    ifcopenshell.util.element.get_pset(element, "EPset_Annotation", "Classes")
                ).split(" "):
                    results.append((obj, self.decorators["HIDDEN_LINE"]))
                else:
                    results.append((obj, self.decorators["MISC"]))

        return results

    def __call__(self, context):
        collection, _ = helper.get_active_drawing(context.scene)
        if collection is None:
            return

        if not DrawingsData.is_loaded:
            DrawingsData.load()

        object_decorators = self.get_objects_and_decorators(collection)
        for obj, decorator in object_decorators:
            decorator.decorate(context, obj)
