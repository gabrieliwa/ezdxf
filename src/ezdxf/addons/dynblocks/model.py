#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Object model of an AutoCAD dynamic block definition.

AutoCAD stores the dynamic behaviour of a BLOCK as undocumented objects in the
OBJECTS section, reachable from the BLOCK_RECORD's extension dictionary:

    BLOCK_RECORD
      xdata AcDbDynamicBlockGUID / AcDbDynamicBlockTrueName
      extension dict
        ACAD_ENHANCEDBLOCK -> ACAD_EVALUATION_GRAPH
            360 -> BLOCKLINEARPARAMETER / BLOCKSTRETCHACTION / BLOCKSCALEACTION / BLOCKVISIBILITYPARAMETER
                   BLOCKLOOKUPPARAMETER / BLOCKLOOKUPACTION / BLOCK*GRIP / ...
        AcDbDynamicBlockRoundTripPurgePreventer -> ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION

`ezdxf` loads all of these as :class:`DXFTagStorage` (raw tags). This module
parses the subset needed to *evaluate* a block instance: linear parameters with
their stretch and scale actions (chained ones included), flip parameters with
their flip actions, the base point parameter, and visibility parameters with
their lookup tables.
Everything else is preserved as raw tags but not interpreted.

The group codes were reverse engineered from AutoCAD 2018 output and cross
checked with ACadSharp (MIT) and LibreDWG's dwg2.spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Optional, Union

from ezdxf.lldxf import const
from ezdxf.lldxf.tags import Tags
from ezdxf.math import Vec3, is_point_in_polygon_2d

if TYPE_CHECKING:
    from ezdxf.document import Drawing
    from ezdxf.entities import BlockRecord, DXFEntity
    from ezdxf.layouts import BlockLayout

__all__ = [
    "DynamicBlockDefinition",
    "LinearParameter",
    "StretchAction",
    "ScaleAction",
    "FlipParameter",
    "FlipAction",
    "BasePointParameter",
    "VisibilityParameter",
    "VisibilityState",
    "LookupParameter",
    "LookupAction",
    "DynamicBlockError",
    "raw_tags",
    "is_dynamic_block",
    "dynamic_blocks",
]

APPID_GUID = "AcDbDynamicBlockGUID"
APPID_TRUE_NAME = "AcDbDynamicBlockTrueName"
APPID_REP_BTAG = "AcDbBlockRepBTag"
APPID_REP_ETAG = "AcDbBlockRepETag"
DYNAMIC_APPIDS = (APPID_GUID, APPID_TRUE_NAME, APPID_REP_BTAG, APPID_REP_ETAG)

KEY_ENHANCED_BLOCK = "ACAD_ENHANCEDBLOCK"
KEY_PURGE_PREVENTER = "AcDbDynamicBlockRoundTripPurgePreventer"

EVAL_GRAPH = "ACAD_EVALUATION_GRAPH"
LINEAR_PARAMETER = "BLOCKLINEARPARAMETER"
STRETCH_ACTION = "BLOCKSTRETCHACTION"
SCALE_ACTION = "BLOCKSCALEACTION"
FLIP_PARAMETER = "BLOCKFLIPPARAMETER"
FLIP_ACTION = "BLOCKFLIPACTION"
BASE_POINT_PARAMETER = "BLOCKBASEPOINTPARAMETER"
# Nodes that only matter while a draughtsman inserts or grips the block in
# AutoCAD: they never change the geometry of a reference.
INERT_NODES = ("BLOCKALIGNMENTPARAMETER", "BLOCKGRIPLOCATIONCOMPONENT")
VISIBILITY_PARAMETER = "BLOCKVISIBILITYPARAMETER"
LOOKUP_PARAMETER = "BLOCKLOOKUPPARAMETER"
LOOKUP_ACTION = "BLOCKLOOKUPACTION"


class DynamicBlockError(const.DXFError):
    pass


def raw_tags(entity: DXFEntity) -> Tags:
    """Returns all subclass tags of a raw (:class:`DXFTagStorage`) object as a
    flat :class:`Tags` list (the base class tags included).
    """
    xtags = getattr(entity, "xtags", None)
    if xtags is None:
        raise DynamicBlockError(f"{entity.dxftype()} is not a raw tag storage object")
    tags = Tags()
    for subclass in xtags.subclasses:
        tags.extend(subclass)
    return tags


def _subclass(entity: DXFEntity, name: str) -> Tags:
    try:
        return entity.xtags.get_subclass(name)  # type: ignore
    except (const.DXFKeyError, AttributeError):
        raise DynamicBlockError(
            f"{entity.dxftype()} #{entity.dxf.handle}: missing subclass {name}"
        )


def _node_id(entity: DXFEntity) -> int:
    """The evaluation graph node id (AcDbEvalExpr, group code 90)."""
    return int(_subclass(entity, "AcDbEvalExpr").get_first_value(90))


def _element_name(entity: DXFEntity) -> str:
    return str(_subclass(entity, "AcDbBlockElement").get_first_value(300, ""))


def _parameter_flags(entity: DXFEntity) -> tuple[bool, bool]:
    """(show properties, chain actions) of a parameter (AcDbBlockParameter
    280 / 281). A hidden parameter is not offered in the Properties palette;
    a chained one fires its own actions when another action moves it."""
    tags = _subclass(entity, "AcDbBlockParameter")
    return int(tags.get_first_value(280, 1)) == 1, int(tags.get_first_value(281, 0)) == 1


def _grip_node(entity: DXFEntity) -> int:
    """Node id of the parameter's first grip (AcDbBlock2PtParameter, first 91
    after the 170 count), 0 if it has none."""
    found = False
    for code, value in _subclass(entity, "AcDbBlock2PtParameter"):
        if code == 170:
            found = True
        elif code == 91 and found:
            return int(value)
    return 0


def is_dynamic_block(block_record: BlockRecord) -> bool:
    """Returns ``True`` if the BLOCK_RECORD is a dynamic block definition.

    The GUID xdata alone is not enough: AutoCAD also tags static blocks edited
    in the block editor with it. A dynamic block definition owns the
    ACAD_ENHANCEDBLOCK evaluation graph in its extension dictionary.
    """
    if not block_record.has_xdata(APPID_GUID):
        return False
    if not block_record.has_extension_dict:
        return False
    graph = block_record.get_extension_dict().get(KEY_ENHANCED_BLOCK)
    return graph is not None and graph.dxftype() == EVAL_GRAPH


@dataclass
class LinearParameter:
    """A linear parameter (BLOCKLINEARPARAMETER): a distance between a base
    point and an end point, shown in the Properties palette under `label`.
    """

    node_id: int
    handle: str
    name: str  # element name, e.g. "Lineare"
    label: str  # property label, e.g. "Larghezza Tavolo"
    description: str
    base: Vec3
    end: Vec3
    show_properties: bool = True  # 280: offered in the Properties palette
    chain_actions: bool = False  # 281: fires its actions when another action moves it

    @property
    def default_distance(self) -> float:
        return (self.end - self.base).magnitude

    @property
    def direction(self) -> Vec3:
        return (self.end - self.base).normalize()

    def end_point_for(self, distance: float) -> Vec3:
        return self.base + self.direction * float(distance)

    @classmethod
    def load(cls, entity: DXFEntity) -> LinearParameter:
        two_pt = _subclass(entity, "AcDbBlock2PtParameter")
        linear = _subclass(entity, "AcDbBlockLinearParameter")
        show, chain = _parameter_flags(entity)
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            label=str(linear.get_first_value(305, "")),
            description=str(linear.get_first_value(306, "")),
            base=Vec3(two_pt.get_first_value(1010)),
            end=Vec3(two_pt.get_first_value(1011)),
            show_properties=show,
            chain_actions=chain,
        )


def _selection(entity: DXFEntity) -> list[str]:
    """The handles an action moves as a whole (AcDbBlockAction, 330 tags):
    entities, and the parameters and grips that sit inside the action's frame."""
    return [str(value) for code, value in _subclass(entity, "AcDbBlockAction") if code == 330]


@dataclass
class StretchAction:
    """A stretch action (BLOCKSTRETCHACTION) bound to a linear parameter: the
    listed vertices of the listed entities move by the parameter's displacement,
    scaled by `distance_multiplier` and turned by `angle_offset` (the two
    "Overrides" of the action's Properties palette: a line kept in the middle of
    a wall moves at 0.5, a leaf grows at right angles to its parameter).
    """

    node_id: int
    handle: str
    name: str
    parameter_node: int  # node id of the driving parameter (group code 92)
    frame: list[Vec3]  # stretch frame polygon (1011 tags)
    # (entity handle, vertex indices) pairs - the vertices that follow the grip
    targets: list[tuple[str, list[int]]]
    # everything the action moves as a whole: entities, and the parameters /
    # grips inside its frame (their base and end points follow the displacement)
    selection: list[str] = field(default_factory=list)
    distance_multiplier: float = 1.0  # group code 140
    angle_offset: float = 0.0  # group code 141, radians

    def contains(self, point: Vec3) -> bool:
        """``True`` if `point` lies in the stretch frame (a rectangle given by
        two opposite corners, or a polygon)."""
        if len(self.frame) < 2:
            return False
        if len(self.frame) == 2:
            a, b = self.frame
            return (
                min(a.x, b.x) - 1e-9 <= point.x <= max(a.x, b.x) + 1e-9
                and min(a.y, b.y) - 1e-9 <= point.y <= max(a.y, b.y) + 1e-9
            )
        return is_point_in_polygon_2d(point, self.frame) >= 0

    def moves(self, handle: str, point: Vec3) -> bool:
        """``True`` if the action carries `point` of the selected object
        `handle` along — AutoCAD moves the points of a selected parameter or
        grip that sit inside the frame, as it does for an entity's vertices."""
        return handle in self.selection and self.contains(point)

    def displacement(self, parameter_delta: Vec3) -> Vec3:
        """The vector the targets move by, for a parameter that changed by
        `parameter_delta` (its direction times the change of distance)."""
        delta = parameter_delta * self.distance_multiplier
        if self.angle_offset:
            delta = delta.rotate(self.angle_offset)
        return delta

    @classmethod
    def load(cls, entity: DXFEntity) -> StretchAction:
        stretch = _subclass(entity, "AcDbBlockStretchAction")
        frame: list[Vec3] = []
        targets: list[tuple[str, list[int]]] = []
        current: Optional[list[int]] = None
        for code, value in stretch:
            if code == 1011:
                frame.append(Vec3(value))
            elif code == 331:
                current = []
                targets.append((str(value), current))
            elif code == 94 and current is not None:
                current.append(int(value))
            elif code == 75:  # end of the target list
                current = None
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            parameter_node=int(stretch.get_first_value(92)),
            frame=frame,
            targets=targets,
            selection=_selection(entity),
            distance_multiplier=float(stretch.get_first_value(140, 1.0)),
            angle_offset=float(stretch.get_first_value(141, 0.0)),
        )


@dataclass
class ScaleAction:
    """A scale action (BLOCKSCALEACTION) bound to a linear parameter: the
    selected entities are scaled about the base point by the ratio between the
    parameter's distance and the distance it was defined with (a door leaf and
    its swing arc that grow with the width).

    The base point is either a fixed point of the block (`base_point`) or
    "dependent": the driving parameter's base point plus `offset` — and that
    base point itself moves when another action stretches the parameter.
    """

    node_id: int
    handle: str
    name: str
    parameter_node: int  # node id of the driving parameter (group code 92)
    selection: list[str]  # entity handles scaled (330 tags)
    base_point: Vec3  # 1011
    base_dependent: bool  # 280: 1 = the parameter's base point, 0 = `base_point`
    offset: Vec3  # 1012, added to the dependent base point

    def base_for(self, parameter: LinearParameter, parameter_shift: Vec3 = Vec3()) -> Vec3:
        """The point the selection scales about, for `parameter` whose base
        point another action moved by `parameter_shift`."""
        if self.base_dependent:
            return parameter.base + parameter_shift + self.offset
        return self.base_point

    @classmethod
    def load(cls, entity: DXFEntity) -> ScaleAction:
        with_base = _subclass(entity, "AcDbBlockActionWithBasePt")
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            parameter_node=int(with_base.get_first_value(92)),
            selection=_selection(entity),
            base_point=Vec3(with_base.get_first_value(1011, (0, 0, 0))),
            base_dependent=int(with_base.get_first_value(280, 0)) == 1,
            offset=Vec3(with_base.get_first_value(1012, (0, 0, 0))),
        )


@dataclass
class FlipParameter:
    """A flip parameter (BLOCKFLIPPARAMETER): a reflection line from `base` to
    `end` and two states, "not flipped" (the definition) and "flipped", named
    in the Properties palette by `state_names` (e.g. "Non ribaltato" /
    "Ribaltato" for a door's hinge side).
    """

    node_id: int
    handle: str
    name: str  # element name, e.g. "Flip1"
    label: str  # property label, e.g. "Lato cerniera"
    description: str
    base: Vec3
    end: Vec3
    state_names: tuple[str, str]  # 307 / 308
    grip_node: int = 0  # node id of its flip grip (0: none)
    show_properties: bool = True
    chain_actions: bool = False

    def state_index(self, value: Union[str, bool, int]) -> int:
        """0 (as defined) or 1 (flipped) for a state name, a bool or 0 / 1."""
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return 1 if value else 0
        text = str(value).strip()
        for index, name in enumerate(self.state_names):
            if text.lower() == name.lower():
                return index
        if text in ("0", "1"):
            return int(text)
        raise DynamicBlockError(
            f"unknown flip state {value!r} of {self.label!r}; available: {list(self.state_names)}"
        )

    @classmethod
    def load(cls, entity: DXFEntity) -> FlipParameter:
        two_pt = _subclass(entity, "AcDbBlock2PtParameter")
        flip = _subclass(entity, "AcDbBlockFlipParameter")
        show, chain = _parameter_flags(entity)
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            label=str(flip.get_first_value(305, "")),
            description=str(flip.get_first_value(306, "")),
            base=Vec3(two_pt.get_first_value(1010)),
            end=Vec3(two_pt.get_first_value(1011)),
            state_names=(
                str(flip.get_first_value(307, "0")),
                str(flip.get_first_value(308, "1")),
            ),
            grip_node=_grip_node(entity),
            show_properties=show,
            chain_actions=chain,
        )


@dataclass
class FlipAction:
    """A flip action (BLOCKFLIPACTION) bound to a flip parameter: in the
    flipped state its selection (entities, and the parameters and grips it
    carries) is mirrored about the parameter's line."""

    node_id: int
    handle: str
    name: str
    parameter_node: int  # node id of the flip parameter (group code 92)
    selection: list[str]

    @classmethod
    def load(cls, entity: DXFEntity) -> FlipAction:
        flip = _subclass(entity, "AcDbBlockFlipAction")
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            parameter_node=int(flip.get_first_value(92)),
            selection=_selection(entity),
        )


@dataclass
class BasePointParameter:
    """The base point parameter (BLOCKBASEPOINTPARAMETER): the point of the
    geometry that sits on the insertion point of a reference. When an action
    carries it along (a door whose width grows on both sides), AutoCAD shifts
    the whole geometry back so the point stays on the insertion point."""

    node_id: int
    handle: str
    point: Vec3

    @classmethod
    def load(cls, entity: DXFEntity) -> BasePointParameter:
        base = _subclass(entity, "AcDbBlockBasepointParameter")
        one_pt = _subclass(entity, "AcDbBlock1PtParameter")
        # 1011 is the point AutoCAD keeps on the insertion point (its records
        # write it back as (0, 0) for every reference); 1010 only places the
        # parameter's label in the block editor.
        point = base.get_first_value(1011, None)
        if point is None:
            point = one_pt.get_first_value(1010)
        return cls(node_id=_node_id(entity), handle=entity.dxf.handle, point=Vec3(point))


@dataclass
class VisibilityState:
    name: str
    visible: list[str]  # entity handles visible in this state (332 tags)


@dataclass
class VisibilityParameter:
    """A visibility parameter (BLOCKVISIBILITYPARAMETER): named states, each
    listing the entities that are visible when the state is active.
    """

    node_id: int
    handle: str
    name: str
    label: str  # property label, e.g. "Tipologia"
    description: str
    position: Vec3
    entities: list[str]  # all handles governed by the parameter (331 tags)
    states: list[VisibilityState]

    def state(self, name: str) -> VisibilityState:
        for state in self.states:
            if state.name == name:
                return state
        raise DynamicBlockError(
            f"unknown visibility state {name!r}; available: {self.state_names}"
        )

    @property
    def state_names(self) -> list[str]:
        return [s.name for s in self.states]

    @classmethod
    def load(cls, entity: DXFEntity) -> VisibilityParameter:
        one_pt = _subclass(entity, "AcDbBlock1PtParameter")
        vis = _subclass(entity, "AcDbBlockVisibilityParameter")
        entities: list[str] = []
        states: list[VisibilityState] = []
        for code, value in vis:
            if code == 331:
                entities.append(str(value))
            elif code == 303:
                states.append(VisibilityState(str(value), []))
            elif code == 332 and states:
                states[-1].visible.append(str(value))
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            label=str(vis.get_first_value(301, "")),
            description=str(vis.get_first_value(302, "")),
            position=Vec3(one_pt.get_first_value(1010)),
            entities=entities,
            states=states,
        )


@dataclass
class LookupParameter:
    """A lookup parameter (BLOCKLOOKUPPARAMETER): the "second drop down" of
    the Properties palette whose values are mapped by a lookup action.
    """

    node_id: int
    handle: str
    name: str
    label: str  # property label, e.g. "TIPO_SEDUTA"
    description: str
    position: Vec3

    @classmethod
    def load(cls, entity: DXFEntity) -> LookupParameter:
        one_pt = _subclass(entity, "AcDbBlock1PtParameter")
        lookup = _subclass(entity, "AcDbBlockLookUpParameter")
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            label=str(lookup.get_first_value(303, "")),
            description=str(lookup.get_first_value(304, "")),
            position=Vec3(one_pt.get_first_value(1010)),
        )


@dataclass
class LookupColumn:
    parameter_node: int  # 94
    property_name: str  # 304, e.g. "VisibilityState" or "lookupString"
    label: str  # 305
    is_output: bool  # 282: the lookup property column


@dataclass
class LookupAction:
    """A lookup action (BLOCKLOOKUPACTION): a table whose rows map the input
    parameter values (e.g. a visibility state) to the lookup string shown in
    the lookup parameter's drop down, and back (reverse lookup).
    """

    node_id: int
    handle: str
    name: str
    columns: list[LookupColumn]
    rows: list[list[str]]

    @property
    def output_column(self) -> int:
        for i, col in enumerate(self.columns):
            if col.is_output:
                return i
        return len(self.columns) - 1

    def input_columns(self) -> list[int]:
        return [i for i, col in enumerate(self.columns) if not col.is_output]

    def value_for_state(self, state: str) -> Optional[str]:
        """Lookup string for the given input value (e.g. visibility state)."""
        out = self.output_column
        for row in self.rows:
            if any(row[i] == state for i in self.input_columns()):
                return row[out]
        return None

    def state_for_value(self, value: str) -> Optional[str]:
        """Input value (e.g. visibility state) for the given lookup string."""
        out = self.output_column
        for row in self.rows:
            if row[out] == value:
                inputs = self.input_columns()
                return row[inputs[0]] if inputs else None
        return None

    @property
    def values(self) -> list[str]:
        out = self.output_column
        return [row[out] for row in self.rows]

    @classmethod
    def load(cls, entity: DXFEntity) -> LookupAction:
        lookup = _subclass(entity, "AcDbBlockLookupAction")
        nrows = int(lookup.get_first_value(92, 0))
        ncols = int(lookup.get_first_value(93, 0))
        cells = [str(v) for code, v in lookup if code == 302]
        rows = [cells[r * ncols : (r + 1) * ncols] for r in range(nrows)]
        # one column descriptor per 303 tag following the cell list:
        #   303 "" 94 <node> 95 .. 96 .. 282 <is output> 305 <label> 281 .. 304 <property>
        columns: list[dict] = []
        current: Optional[dict] = None
        for code, value in lookup:
            if code == 303:
                current = {"node": 0, "prop": "", "label": "", "out": False}
                columns.append(current)
            elif current is None:
                continue
            elif code == 94:
                current["node"] = int(value)
            elif code == 304:
                current["prop"] = str(value)
            elif code == 305:
                current["label"] = str(value)
            elif code == 282:
                current["out"] = bool(int(value))
        return cls(
            node_id=_node_id(entity),
            handle=entity.dxf.handle,
            name=_element_name(entity),
            columns=[
                LookupColumn(c["node"], c["prop"], c["label"], c["out"]) for c in columns
            ],
            rows=rows,
        )


@dataclass
class DynamicBlockDefinition:
    """Parsed dynamic block definition, bound to the DXF document it lives in."""

    doc: Drawing
    block: BlockLayout
    graph: DXFEntity  # ACAD_EVALUATION_GRAPH (raw)
    nodes: dict[int, DXFEntity] = field(default_factory=dict)  # node id -> raw object
    linear_parameters: list[LinearParameter] = field(default_factory=list)
    stretch_actions: list[StretchAction] = field(default_factory=list)
    scale_actions: list[ScaleAction] = field(default_factory=list)
    flip_parameters: list[FlipParameter] = field(default_factory=list)
    flip_actions: list[FlipAction] = field(default_factory=list)
    base_point: Optional[BasePointParameter] = None
    visibility: Optional[VisibilityParameter] = None
    lookup_parameters: list[LookupParameter] = field(default_factory=list)
    lookup_actions: list[LookupAction] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)  # dxftypes of ignored nodes

    @property
    def name(self) -> str:
        return self.block.name

    @property
    def block_record(self) -> BlockRecord:
        return self.block.block_record

    @property
    def guid(self) -> str:
        try:
            return str(self.block_record.get_xdata(APPID_GUID).get_first_value(1000, ""))
        except const.DXFValueError:
            return ""

    @property
    def true_name(self) -> str:
        try:
            return str(
                self.block_record.get_xdata(APPID_TRUE_NAME).get_first_value(1000, "")
            )
        except const.DXFValueError:
            return self.name

    def linear_parameter(self, key: Union[str, int]) -> LinearParameter:
        """Find a linear parameter by node id, label or element name."""
        for p in self.linear_parameters:
            if key == p.node_id or key == p.label or key == p.name:
                return p
        raise DynamicBlockError(
            f"{self.name}: unknown linear parameter {key!r}; "
            f"available: {[p.label for p in self.linear_parameters]}"
        )

    def flip_parameter(self, key: Union[str, int]) -> FlipParameter:
        """Find a flip parameter by node id, label or element name."""
        for p in self.flip_parameters:
            if key == p.node_id or key == p.label or key == p.name:
                return p
        raise DynamicBlockError(
            f"{self.name}: unknown flip parameter {key!r}; "
            f"available: {[p.label for p in self.flip_parameters]}"
        )

    def flip_actions_of(self, parameter: FlipParameter) -> list[FlipAction]:
        return [a for a in self.flip_actions if a.parameter_node == parameter.node_id]

    def stretch_movers(self, handle: str, point: Vec3) -> list[tuple[LinearParameter, StretchAction]]:
        """The (parameter, stretch action) pairs that carry `point` of the
        selected object `handle` (a parameter, the base point) along."""
        found = []
        for action in self.stretch_actions:
            if not action.moves(handle, point):
                continue
            for other in self.linear_parameters:
                if other.node_id == action.parameter_node and other.handle != handle:
                    found.append((other, action))
        return found

    def chain_of(self, parameter: LinearParameter) -> list[tuple[LinearParameter, float]]:
        """For a chained parameter (chain actions on): the parameters whose
        stretches move its points, each with the change of `parameter`'s
        distance per unit change of theirs — the small leaf of a double door
        is the width minus the big leaf: [(width, 1), (big leaf, -1)].
        Empty for a parameter that does not chain."""
        if not parameter.chain_actions:
            return []
        rates: dict[int, float] = {}
        by_node: dict[int, LinearParameter] = {}
        direction = parameter.direction
        for which, point in ((1.0, parameter.end), (-1.0, parameter.base)):
            for other, action in self.stretch_movers(parameter.handle, point):
                moved = action.displacement(other.direction)
                rates[other.node_id] = rates.get(other.node_id, 0.0) + which * moved.dot(direction)
                by_node[other.node_id] = other
        return [(by_node[n], rate) for n, rate in rates.items() if abs(rate) > 1e-12]

    def lookup_parameter(self, key: Union[str, int]) -> LookupParameter:
        for p in self.lookup_parameters:
            if key == p.node_id or key == p.label or key == p.name:
                return p
        raise DynamicBlockError(
            f"{self.name}: unknown lookup parameter {key!r}; "
            f"available: {[p.label for p in self.lookup_parameters]}"
        )

    def stretch_actions_of(self, parameter: LinearParameter) -> list[StretchAction]:
        return [a for a in self.stretch_actions if a.parameter_node == parameter.node_id]

    def scale_actions_of(self, parameter: LinearParameter) -> list[ScaleAction]:
        return [a for a in self.scale_actions if a.parameter_node == parameter.node_id]

    def parameter_by_handle(self, handle: str) -> Optional[LinearParameter]:
        for p in self.linear_parameters:
            if p.handle == handle:
                return p
        return None

    def movers_of(self, parameter: LinearParameter) -> list[tuple[LinearParameter, StretchAction]]:
        """The (parameter, stretch action) pairs whose action carries `parameter`
        along — its base point sits in their frame — so a scale action that
        depends on that base point follows them too."""
        found = []
        for action in self.stretch_actions:
            if not action.moves(parameter.handle, parameter.base):
                continue
            for other in self.linear_parameters:
                if other.node_id == action.parameter_node and other is not parameter:
                    found.append((other, action))
        return found

    def lookup_action_of(self, parameter: LookupParameter) -> Optional[LookupAction]:
        for action in self.lookup_actions:
            for col in action.columns:
                if col.parameter_node == parameter.node_id:
                    return action
        return None

    @property
    def is_supported(self) -> bool:
        """``True`` if every graph node is either understood or irrelevant for
        the evaluation (grips, grip location components).
        """
        return not self.unsupported

    @classmethod
    def load(cls, doc: Drawing, block_name: str) -> DynamicBlockDefinition:
        """Parse the dynamic block definition `block_name` of `doc`.

        Raises:
            DynamicBlockError: not a dynamic block or broken structure
        """
        block = doc.blocks.get(block_name)
        if block is None:
            raise DynamicBlockError(f"block {block_name!r} not found")
        record = block.block_record
        if not is_dynamic_block(record):
            raise DynamicBlockError(f"block {block_name!r} is not a dynamic block")
        if not record.has_extension_dict:
            raise DynamicBlockError(
                f"block {block_name!r}: BLOCK_RECORD has no extension dictionary"
            )
        xdict = record.get_extension_dict()
        graph = xdict.get(KEY_ENHANCED_BLOCK)
        if graph is None or graph.dxftype() != EVAL_GRAPH:
            raise DynamicBlockError(
                f"block {block_name!r}: {KEY_ENHANCED_BLOCK} evaluation graph not found"
            )
        defn = cls(doc=doc, block=block, graph=graph)
        for node in graph_nodes(doc, graph):
            defn._add_node(node)
        return defn

    def _add_node(self, node: DXFEntity) -> None:
        dxftype = node.dxftype()
        try:
            self.nodes[_node_id(node)] = node
        except DynamicBlockError:
            self.unsupported.append(dxftype)
            return
        if dxftype == LINEAR_PARAMETER:
            self.linear_parameters.append(LinearParameter.load(node))
        elif dxftype == STRETCH_ACTION:
            self.stretch_actions.append(StretchAction.load(node))
        elif dxftype == SCALE_ACTION:
            self.scale_actions.append(ScaleAction.load(node))
        elif dxftype == VISIBILITY_PARAMETER:
            if self.visibility is not None:
                raise DynamicBlockError(f"{self.name}: more than one visibility parameter")
            self.visibility = VisibilityParameter.load(node)
        elif dxftype == LOOKUP_PARAMETER:
            self.lookup_parameters.append(LookupParameter.load(node))
        elif dxftype == LOOKUP_ACTION:
            self.lookup_actions.append(LookupAction.load(node))
        elif dxftype == FLIP_PARAMETER:
            self.flip_parameters.append(FlipParameter.load(node))
        elif dxftype == FLIP_ACTION:
            self.flip_actions.append(FlipAction.load(node))
        elif dxftype == BASE_POINT_PARAMETER:
            self.base_point = BasePointParameter.load(node)
        elif dxftype.endswith("GRIP") or dxftype in INERT_NODES:
            pass  # grips and alignment do not influence the evaluation
        else:
            self.unsupported.append(dxftype)


def graph_nodes(doc: Drawing, graph: DXFEntity) -> Iterator[DXFEntity]:
    """Yields the node objects of an ACAD_EVALUATION_GRAPH (360 tags)."""
    db = doc.entitydb
    for code, value in raw_tags(graph):
        if code == 360:
            node = db.get(str(value))
            if node is not None:
                yield node


def dynamic_blocks(doc: Drawing) -> Iterator[str]:
    """Yields the names of all dynamic block definitions in `doc` (anonymous
    representation blocks excluded).
    """
    for block in doc.blocks:
        if is_dynamic_block(block.block_record):
            yield block.name
