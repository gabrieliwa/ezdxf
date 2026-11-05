#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Evaluation ("virtual REGEN") of dynamic block parameters on plain entities.

AutoCAD never re-evaluates a dynamic block on load: every block reference
points to an anonymous ``*U`` block whose geometry is already the result of the
parameter values. These functions produce that geometry from a copy of the
definition's entities.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Mapping, Optional

from ezdxf.math import Vec3

from .model import (
    DynamicBlockDefinition,
    DynamicBlockError,
    FlipParameter,
    LinearParameter,
    VisibilityParameter,
)

if TYPE_CHECKING:
    from ezdxf.entities import DXFEntity

__all__ = [
    "move_vertices",
    "scale_entity",
    "mirror_entity",
    "mirror_point",
    "Evaluation",
    "linear_deltas",
    "evaluate",
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


def mirror_point(point: Vec3, a: Vec3, b: Vec3) -> Vec3:
    """`point` reflected about the line through `a` and `b` (2D, z kept)."""
    d = Vec3(b.x - a.x, b.y - a.y, 0).normalize()
    v = Vec3(point.x - a.x, point.y - a.y, 0)
    m = d * (2 * v.dot(d)) - v
    return Vec3(a.x + m.x, a.y + m.y, Vec3(point).z)


def _mirror_vector(vector: Vec3, a: Vec3, b: Vec3) -> Vec3:
    d = Vec3(b.x - a.x, b.y - a.y, 0).normalize()
    v = Vec3(vector.x, vector.y, 0)
    m = d * (2 * v.dot(d)) - v
    return Vec3(m.x, m.y, Vec3(vector).z)


def mirror_entity(
    entity: DXFEntity, a: Vec3, b: Vec3, warnings: Optional[list[str]] = None
) -> None:
    """Mirror `entity` about the line through `a` and `b` (block coordinates),
    the way AutoCAD's flip action does. Arcs and polylines stay counter
    clockwise in the XY plane (their extrusion is not turned over); texts keep
    reading left to right (AutoCAD's MIRRTEXT 0): only their position moves.
    """
    dxftype = entity.dxftype()
    dxf = entity.dxf
    theta = math.degrees(math.atan2(b.y - a.y, b.x - a.x))

    def at(point) -> Vec3:
        return mirror_point(Vec3(point), a, b)

    if dxftype == "LWPOLYLINE":
        points = list(entity.get_points())  # type: ignore  # (x, y, start_w, end_w, bulge)
        mirrored = []
        for x, y, sw, ew, bulge in points:
            v = at((x, y))
            mirrored.append((v.x, v.y, sw, ew, -bulge))
        entity.set_points(mirrored)  # type: ignore
    elif dxftype == "POLYLINE":
        for vertex in entity.vertices:  # type: ignore
            vertex.dxf.location = at(vertex.dxf.location)
            if vertex.dxf.hasattr("bulge"):
                vertex.dxf.bulge = -vertex.dxf.bulge
    elif dxftype == "LINE":
        dxf.start = at(dxf.start)
        dxf.end = at(dxf.end)
    elif dxftype == "ARC":
        dxf.center = at(dxf.center)
        start, end = dxf.start_angle, dxf.end_angle
        dxf.start_angle = (2 * theta - end) % 360
        dxf.end_angle = (2 * theta - start) % 360
    elif dxftype == "CIRCLE":
        dxf.center = at(dxf.center)
    elif dxftype == "ELLIPSE":
        dxf.center = at(dxf.center)
        dxf.major_axis = _mirror_vector(Vec3(dxf.major_axis), a, b)
        start, end = dxf.start_param, dxf.end_param
        dxf.start_param = (-end) % math.tau
        dxf.end_param = (-start) % math.tau
    elif dxftype == "POINT":
        dxf.location = at(dxf.location)
    elif dxftype in ("TEXT", "ATTDEF", "ATTRIB", "MTEXT"):
        dxf.insert = at(dxf.insert)
        if dxf.hasattr("align_point"):
            dxf.align_point = at(dxf.align_point)
    elif dxftype == "INSERT":
        dxf.insert = at(dxf.insert)
        dxf.rotation = (2 * theta - dxf.get("rotation", 0.0)) % 360
        dxf.yscale = -dxf.get("yscale", 1.0)
    elif dxftype in ("SOLID", "TRACE", "3DFACE"):
        for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
            if dxf.hasattr(name):
                setattr(dxf, name, at(getattr(dxf, name)))
    else:
        msg = f"flip of {dxftype} #{entity.dxf.handle} not supported, entity not mirrored"
        if warnings is not None:
            warnings.append(msg)
        else:
            raise DynamicBlockError(msg)


def linear_deltas(
    defn: DynamicBlockDefinition, distances: Iterable[tuple[LinearParameter, float]]
) -> dict[int, float]:
    """The change of distance of the linear parameters, by node id: those set
    in `distances`, plus the chained ones other actions move (the small leaf
    of a double door: width minus big leaf). A chained parameter that is also
    set keeps its own change on top."""
    own = {p.node_id: float(d) - p.default_distance for p, d in distances}
    out = dict(own)
    chained = [(p, defn.chain_of(p)) for p in defn.linear_parameters if p.chain_actions]
    chained = [(p, chain) for p, chain in chained if chain]
    # a chain may feed another chain: settle in as many passes as there are links
    for _ in range(len(chained) + 1):
        changed = False
        for p, chain in chained:
            total = own.get(p.node_id, 0.0) + sum(
                rate * out.get(q.node_id, 0.0) for q, rate in chain
            )
            if p.node_id not in out or abs(total - out[p.node_id]) > 1e-12:
                out[p.node_id] = total
                changed = True
        if not changed:
            break
    return {n: d for n, d in out.items() if n in own or abs(d) > 1e-12}


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


def _carried(
    defn: DynamicBlockDefinition, handle: str, point: Vec3, deltas: Mapping[int, Vec3]
) -> Vec3:
    """How far the stretches carry `point` of the selected object `handle`."""
    total = Vec3()
    for other, action in defn.stretch_movers(handle, point):
        delta = deltas.get(other.node_id)
        if delta is not None:
            total += action.displacement(delta)
    return total


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
    the base point by ``distance / definition distance``. A chained parameter
    the others move (chain actions on) fires its own actions with the change
    they gave it.

    All parameters are set together — stretches first, then scales — so that
    a scale action whose base point another parameter moves (a leaf hinged on
    the jamb the width pushes) scales about the moved point, whatever the
    order of `distances`.

    Returns the displacement vector of each changed parameter's grip, by node id.
    """
    by_node = {p.node_id: p for p in defn.linear_parameters}
    scalar = linear_deltas(defn, list(distances))
    deltas: dict[int, Vec3] = {
        node: by_node[node].direction * change for node, change in scalar.items()
    }
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
    for node, delta in deltas.items():
        if delta.is_null:
            continue
        for action in defn.stretch_actions_of(by_node[node]):
            displacement = action.displacement(delta)
            for handle, indices in action.targets:
                entity = copy_of(handle, "stretch target")
                if entity is not None:
                    move_vertices(entity, indices, displacement, warnings)
    for node, change in scalar.items():
        parameter = by_node[node]
        if parameter.default_distance == 0:
            continue
        factor = (parameter.default_distance + change) / parameter.default_distance
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


@dataclass
class Evaluation:
    """What evaluating a reference did, for the records AutoCAD keeps on it:
    the change of the linear parameters (vectors, by node id), the flip
    states, the flips applied in order, how many flips mirrored each selected
    object (by handle), and the shift that brought the base point parameter
    back onto the insertion point."""

    deltas: dict[int, Vec3] = field(default_factory=dict)
    flips: dict[int, int] = field(default_factory=dict)  # node id -> 0 / 1
    mirrored: dict[str, int] = field(default_factory=dict)  # handle -> number of flips
    shift: Vec3 = field(default_factory=Vec3)
    # the flips applied, in order: (line start, line end, selection)
    applied: list[tuple[Vec3, Vec3, list[str]]] = field(default_factory=list)

    def place(
        self, defn: DynamicBlockDefinition, handle: str, point: Vec3, own: Vec3 = Vec3()
    ) -> Vec3:
        """Where `point` of the selected object `handle` ends up: moved by
        `own` (a parameter's own grip), carried by the stretches, mirrored by
        the flips that select it, shifted with the whole geometry."""
        p = Vec3(point) + own + _carried(defn, handle, Vec3(point), self.deltas)
        for a, b, selection in self.applied:
            if handle in selection:
                p = mirror_point(p, a, b)
        return p + self.shift


def evaluate(
    defn: DynamicBlockDefinition,
    entities: Mapping[str, DXFEntity],
    distances: Iterable[tuple[LinearParameter, float]] = (),
    flips: Iterable[tuple[FlipParameter, int]] = (),
    warnings: Optional[list[str]] = None,
) -> Evaluation:
    """Evaluate a reference on the entity copies `entities` the way AutoCAD
    does: the linear parameters (chained ones included, see
    :func:`apply_linear_values`), then the flips — each flipped parameter
    mirrors its selection about its line, where the stretches carried it — and
    last the base point: when an action moved it (a door that grows on both
    sides), the whole geometry shifts back so it stays on the insertion point.
    """
    ev = Evaluation()
    ev.deltas = apply_linear_values(defn, entities, distances, warnings)
    ev.flips = {p.node_id: int(state) for p, state in flips}
    lines: dict[int, tuple[Vec3, Vec3]] = {}
    for p in defn.flip_parameters:
        lines[p.node_id] = (
            p.base + _carried(defn, p.handle, p.base, ev.deltas),
            p.end + _carried(defn, p.handle, p.end, ev.deltas),
        )
    by_handle = {p.handle: p for p in defn.flip_parameters}
    bp = defn.base_point
    bp_point = None
    if bp is not None:
        bp_point = bp.point + _carried(defn, bp.handle, bp.point, ev.deltas)
    for p in defn.flip_parameters:
        if not ev.flips.get(p.node_id):
            continue
        a, b = lines[p.node_id]
        if (b - a).is_null:
            continue
        for action in defn.flip_actions_of(p):
            ev.applied.append((a, b, list(action.selection)))
            for handle in action.selection:
                ev.mirrored[handle] = ev.mirrored.get(handle, 0) + 1
                other = by_handle.get(handle)
                if other is not None:
                    if other is not p:
                        oa, ob = lines[other.node_id]
                        lines[other.node_id] = (mirror_point(oa, a, b), mirror_point(ob, a, b))
                    continue
                if bp is not None and handle == bp.handle:
                    bp_point = mirror_point(bp_point, a, b)
                    continue
                entity = entities.get(handle)
                if entity is not None:
                    mirror_entity(entity, a, b, warnings)
    if bp_point is not None:
        ev.shift = Vec3(bp.point.x - bp_point.x, bp.point.y - bp_point.y, 0)
        if not ev.shift.is_null:
            for entity in entities.values():
                try:
                    entity.translate(ev.shift.x, ev.shift.y, 0)  # type: ignore
                except (AttributeError, NotImplementedError):
                    msg = f"{defn.name}: {entity.dxftype()} #{entity.dxf.handle} not shifted"
                    if warnings is None:
                        raise DynamicBlockError(msg)
                    warnings.append(msg)
    return ev


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
