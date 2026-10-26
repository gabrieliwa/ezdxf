#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Evaluation ("virtual REGEN") of dynamic block parameters on plain entities.

AutoCAD never re-evaluates a dynamic block on load: every block reference
points to an anonymous ``*U`` block whose geometry is already the result of the
parameter values. These functions produce that geometry from a copy of the
definition's entities.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Mapping, Optional

from ezdxf.math import Vec3

from .model import (
    DynamicBlockDefinition,
    DynamicBlockError,
    LinearParameter,
    VisibilityParameter,
)

if TYPE_CHECKING:
    from ezdxf.entities import DXFEntity

__all__ = [
    "move_vertices",
    "scale_entity",
    "apply_linear",
    "apply_linear_values",
    "apply_visibility",
    "current_visibility_state",
]


def move_vertices(
    entity: DXFEntity, indices: Iterable[int], delta: Vec3, warnings: Optional[list[str]] = None
) -> None:
    """Move the vertices `indices` of `entity` by `delta` (block coordinates).

    The vertex numbering follows AutoCAD's stretch action: polyline vertices by
    index, LINE 0 = start / 1 = end, ARC and CIRCLE 0 = center, TEXT-like
    entities 0 = insertion point (the alignment point follows).
    """
    dxftype = entity.dxftype()
    indices = list(indices)
    dxf = entity.dxf
    if dxftype == "LWPOLYLINE":
        points = list(entity.get_points())  # type: ignore  # (x, y, start_w, end_w, bulge)
        for i in indices:
            if 0 <= i < len(points):
                x, y, *rest = points[i]
                points[i] = (x + delta.x, y + delta.y, *rest)
        entity.set_points(points)  # type: ignore
    elif dxftype == "POLYLINE":
        vertices = list(entity.vertices)  # type: ignore
        for i in indices:
            if 0 <= i < len(vertices):
                vertices[i].dxf.location = Vec3(vertices[i].dxf.location) + delta
    elif dxftype == "LINE":
        for i in indices:
            if i == 0:
                dxf.start = Vec3(dxf.start) + delta
            elif i == 1:
                dxf.end = Vec3(dxf.end) + delta
    elif dxftype in ("ARC", "CIRCLE", "ELLIPSE"):
        if 0 in indices:
            dxf.center = Vec3(dxf.center) + delta
    elif dxftype in ("TEXT", "ATTDEF", "ATTRIB", "MTEXT", "INSERT", "POINT"):
        if 0 in indices:
            dxf.insert = Vec3(dxf.insert) + delta
            if dxf.hasattr("align_point"):
                dxf.align_point = Vec3(dxf.align_point) + delta
    elif dxftype in ("SOLID", "TRACE", "3DFACE"):
        names = ("vtx0", "vtx1", "vtx2", "vtx3")
        for i in indices:
            if 0 <= i < 4 and dxf.hasattr(names[i]):
                setattr(dxf, names[i], Vec3(getattr(dxf, names[i])) + delta)
    else:
        msg = f"stretch of {dxftype} #{entity.dxf.handle} not supported, vertices not moved"
        if warnings is not None:
            warnings.append(msg)
        else:
            raise DynamicBlockError(msg)


def scale_entity(
    entity: DXFEntity, base: Vec3, factor: float, warnings: Optional[list[str]] = None
) -> None:
    """Scale `entity` uniformly about `base` by `factor` (block coordinates),
    the way AutoCAD's scale action does: every point moves to
    ``base + (point - base) * factor``, radii, text heights and widths follow.
    """
    if factor == 1.0:
        return
    dxftype = entity.dxftype()
    dxf = entity.dxf

    def at(point) -> Vec3:
        return base + (Vec3(point) - base) * factor

    if dxftype == "LWPOLYLINE":
        points = list(entity.get_points())  # type: ignore  # (x, y, start_w, end_w, bulge)
        scaled = []
        for x, y, sw, ew, bulge in points:
            v = at((x, y))
            scaled.append((v.x, v.y, sw * factor, ew * factor, bulge))  # bulge is an angle
        entity.set_points(scaled)  # type: ignore
    elif dxftype == "POLYLINE":
        for vertex in entity.vertices:  # type: ignore
            vertex.dxf.location = at(vertex.dxf.location)
    elif dxftype == "LINE":
        dxf.start = at(dxf.start)
        dxf.end = at(dxf.end)
    elif dxftype in ("ARC", "CIRCLE"):
        dxf.center = at(dxf.center)
        dxf.radius = dxf.radius * factor
    elif dxftype == "ELLIPSE":
        dxf.center = at(dxf.center)
        dxf.major_axis = Vec3(dxf.major_axis) * factor
    elif dxftype in ("TEXT", "ATTDEF", "ATTRIB"):
        dxf.insert = at(dxf.insert)
        if dxf.hasattr("align_point"):
            dxf.align_point = at(dxf.align_point)
        dxf.height = dxf.height * factor
    elif dxftype == "MTEXT":
        dxf.insert = at(dxf.insert)
        dxf.char_height = dxf.char_height * factor
        if dxf.hasattr("width"):
            dxf.width = dxf.width * factor
    elif dxftype == "INSERT":
        dxf.insert = at(dxf.insert)
        dxf.xscale = dxf.xscale * factor
        dxf.yscale = dxf.yscale * factor
        dxf.zscale = dxf.zscale * factor
    elif dxftype == "POINT":
        dxf.location = at(dxf.location)
    elif dxftype in ("SOLID", "TRACE", "3DFACE"):
        for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
            if dxf.hasattr(name):
                setattr(dxf, name, at(getattr(dxf, name)))
    else:
        msg = f"scale of {dxftype} #{entity.dxf.handle} not supported, entity not scaled"
        if warnings is not None:
            warnings.append(msg)
        else:
            raise DynamicBlockError(msg)


def _parameter_shifts(
    defn: DynamicBlockDefinition, deltas: Mapping[int, Vec3]
) -> dict[int, Vec3]:
    """How far each linear parameter's base point moves, when other parameters'
    stretch actions carry it along (the parameter sits in their frame)."""
    shifts: dict[int, Vec3] = {}
    for parameter in defn.linear_parameters:
        total = Vec3()
        for other, action in defn.movers_of(parameter):
            delta = deltas.get(other.node_id)
            if delta is not None:
                total += action.displacement(delta)
        if not total.is_null:
            shifts[parameter.node_id] = total
    return shifts


def apply_linear_values(
    defn: DynamicBlockDefinition,
    entities: Mapping[str, DXFEntity],
    distances: Iterable[tuple[LinearParameter, float]],
    warnings: Optional[list[str]] = None,
) -> dict[int, Vec3]:
    """Set every parameter in `distances` on the entity copies `entities`
    (definition entity handle -> copy), `distances` being (parameter, distance)
    pairs: the stretch actions move their
    vertices by the parameter's displacement (times the action's multiplier,
    turned by its angle offset), the scale actions scale their selection about
    the base point by ``distance / definition distance``.

    All parameters are set together — stretches first, then scales — so that
    a scale action whose base point another parameter moves (a leaf hinged on
    the jamb the width pushes) scales about the moved point, whatever the
    order of `distances`.

    Returns the displacement vector of each parameter's grip, by node id.
    """
    distances = list(distances)
    deltas: dict[int, Vec3] = {}
    for parameter, distance in distances:
        deltas[parameter.node_id] = parameter.direction * (
            float(distance) - parameter.default_distance
        )
    shifts = _parameter_shifts(defn, deltas)

    def copy_of(handle: str, what: str) -> Optional[DXFEntity]:
        entity = entities.get(handle)
        if entity is None:
            msg = f"{defn.name}: {what} #{handle} not found"
            if warnings is None:
                raise DynamicBlockError(msg)
            warnings.append(msg)
        return entity

    # Every stretch first, then every scale: a scale about a point another
    # parameter pushes (the big leaf hinged on the jamb the width moves) must
    # find both the point and its selection already moved, whatever the
    # order the values came in.
    for parameter, distance in distances:
        delta = deltas[parameter.node_id]
        if delta.is_null:
            continue
        for action in defn.stretch_actions_of(parameter):
            displacement = action.displacement(delta)
            for handle, indices in action.targets:
                entity = copy_of(handle, "stretch target")
                if entity is not None:
                    move_vertices(entity, indices, displacement, warnings)
    for parameter, distance in distances:
        if parameter.default_distance == 0:
            continue
        factor = float(distance) / parameter.default_distance
        if factor == 1.0:
            continue
        for action in defn.scale_actions_of(parameter):
            base = action.base_for(parameter, shifts.get(parameter.node_id, Vec3()))
            for handle in action.selection:
                entity = entities.get(handle)
                if entity is None:
                    continue  # a parameter or grip in the selection, not geometry
                scale_entity(entity, base, factor, warnings)
    return deltas


def apply_linear(
    defn: DynamicBlockDefinition,
    entities: Mapping[str, DXFEntity],
    parameter: LinearParameter,
    distance: float,
    warnings: Optional[list[str]] = None,
) -> Vec3:
    """Set the single `parameter` to `distance` — see :func:`apply_linear_values`,
    which is what an instance with several parameters should use.

    Returns the displacement vector applied to the grip.
    """
    return apply_linear_values(defn, entities, [(parameter, distance)], warnings)[parameter.node_id]


def apply_visibility(
    entities: Mapping[str, DXFEntity], parameter: VisibilityParameter, state_name: str
) -> None:
    """Activate the visibility state `state_name`: every governed entity outside
    the state gets the invisible flag, like AutoCAD does in ``*U`` blocks.
    """
    state = parameter.state(state_name)
    visible = set(state.visible)
    for handle in parameter.entities:
        entity = entities.get(handle)
        if entity is None:
            continue
        entity.dxf.invisible = 0 if handle in visible else 1


def current_visibility_state(
    entities: Mapping[str, DXFEntity], parameter: VisibilityParameter
) -> Optional[str]:
    """The state whose visible set matches the invisible flags of `entities`
    (the state the definition was saved with), ``None`` if none matches.
    """
    for state in parameter.states:
        visible = set(state.visible)
        ok = True
        for handle in parameter.entities:
            entity = entities.get(handle)
            if entity is None:
                continue
            if bool(entity.dxf.get("invisible", 0)) != (handle not in visible):
                ok = False
                break
        if ok:
            return state.name
    return None
