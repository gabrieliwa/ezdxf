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

__all__ = ["move_vertices", "apply_linear", "apply_visibility", "current_visibility_state"]


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


def apply_linear(
    defn: DynamicBlockDefinition,
    entities: Mapping[str, DXFEntity],
    parameter: LinearParameter,
    distance: float,
    warnings: Optional[list[str]] = None,
) -> Vec3:
    """Set `parameter` to `distance` on the entity copies `entities`
    (definition entity handle -> copy) by running its stretch actions.

    Returns the displacement vector applied to the grip.
    """
    delta = parameter.direction * (float(distance) - parameter.default_distance)
    if delta.is_null:
        return delta
    for action in defn.stretch_actions_of(parameter):
        for handle, indices in action.targets:
            entity = entities.get(handle)
            if entity is None:
                msg = f"{defn.name}: stretch target #{handle} not found"
                if warnings is not None:
                    warnings.append(msg)
                    continue
                raise DynamicBlockError(msg)
            move_vertices(entity, indices, delta, warnings)
    return delta


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
