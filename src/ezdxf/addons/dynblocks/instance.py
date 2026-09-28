#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Create block references of dynamic blocks with evaluated parameters.

The result mirrors what AutoCAD writes for a dynamic block reference:

- an anonymous ``*U`` block holding the evaluated geometry, whose BLOCK_RECORD
  carries the ``AcDbBlockRepBTag`` xdata pointing at the definition;
- an INSERT of that block with an extension dictionary
  ``AcDbBlockRepresentation`` -> ``AcDbRepData`` (definition handle) and
  ``AppDataCache/ACAD_ENHANCEDBLOCKDATA`` -> one XRECORD per parameter with the
  current value;
- ATTRIB entities, optionally with the FIELD objects that bind an attribute to
  a linear parameter (``Parameter(n).UpdatedDistance``).
"""
from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Mapping, Optional, Union

from ezdxf.entities.copy import CopySettings, CopyStrategy
from ezdxf.entities.xdict import ExtensionDict
from ezdxf.entities import Dictionary
from ezdxf.lldxf import const
from ezdxf.lldxf.tags import Tags
from ezdxf.lldxf.types import DXFTag, dxftag
from ezdxf.math import Vec3

from .bake import apply_linear, apply_visibility, current_visibility_state
from .copy import ensure_appids, ensure_class, register_fields
from .model import (
    APPID_REP_BTAG,
    APPID_REP_ETAG,
    LINEAR_PARAMETER,
    LOOKUP_PARAMETER,
    STRETCH_ACTION,
    VISIBILITY_PARAMETER,
    DynamicBlockDefinition,
    DynamicBlockError,
    LinearParameter,
)
from .rawclone import HandleMap, clone_objects, collect_owned_subgraph, load_raw_object

if TYPE_CHECKING:
    from ezdxf.entities import Attrib, DXFEntity, DXFGraphic, Insert
    from ezdxf.layouts import BaseLayout

__all__ = ["insert_dynamic_block", "DynamicBlockValues", "guess_field_bindings", "field_checksum"]

# (1071, 1071) class magic numbers found in the parameter XRECORDs of AutoCAD 2018
XRECORD_MAGIC: dict[str, tuple[int, int]] = {
    LINEAR_PARAMETER: (18597260, 25303744),
    STRETCH_ACTION: (6895636, 9291323),
    VISIBILITY_PARAMETER: (135625452, 184556386),
    LOOKUP_PARAMETER: (18605567, 25317787),
}

_COPY_NO_XDICT = CopyStrategy(CopySettings(copy_extension_dict=False, copy_xdata=True))


def field_checksum(text: str) -> float:
    """AutoCAD's ACFD_FIELDTEXT_CHECKSUM: the position weighted sum of the
    character codes of the evaluated text.
    """
    return float(sum((i + 1) * ord(c) for i, c in enumerate(text)))


def _ascii_upper(text: str) -> str:
    """Upper case without accents: "Profondità" -> "PROFONDITA"."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).upper().replace("'", "")


def guess_field_bindings(defn: DynamicBlockDefinition) -> dict[str, str]:
    """Guess which ATTDEF is bound to which linear parameter: the definition
    stores the binding as a FIELD without a usable object reference, so the
    attribute tag is matched against the words of the parameter label
    (``LARGHEZZA`` vs ``Larghezza Tavolo``, accents ignored).
    Returns ``{tag: parameter label}``.
    """
    bindings: dict[str, str] = {}
    labels = [p.label for p in defn.linear_parameters if p.label]
    for attdef in defn.block.attdefs():
        tag = _ascii_upper(attdef.dxf.tag)
        for label in labels:
            words = _ascii_upper(label).split()
            if any(w == tag or tag.startswith(w) or w.startswith(tag) for w in words):
                bindings[attdef.dxf.tag] = label
                break
    return bindings


class DynamicBlockValues:
    """Parameter values of one block reference.

    Args:
        linear: ``{parameter label | name | node id: distance}``
        visibility: name of the visibility state
        lookup: ``{lookup parameter label: value}``; when `visibility` is not
            given the state is derived from the lookup table
    """

    def __init__(
        self,
        linear: Optional[Mapping[Union[str, int], float]] = None,
        visibility: Optional[str] = None,
        lookup: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.linear = dict(linear or {})
        self.visibility = visibility
        self.lookup = dict(lookup or {})


def insert_dynamic_block(
    defn: DynamicBlockDefinition,
    layout: BaseLayout,
    insert: Vec3 | tuple,
    values: Optional[DynamicBlockValues] = None,
    *,
    dxfattribs: Optional[dict] = None,
    attribs: Optional[Mapping[str, str]] = None,
    field_bindings: Optional[Mapping[str, Union[str, int]]] = None,
    precision: int = 2,
    warnings: Optional[list[str]] = None,
) -> Insert:
    """Insert a reference of the dynamic block `defn` into `layout` with the
    parameter `values` already applied ("virtual REGEN").

    Args:
        defn: definition living in the same document as `layout`
        layout: target layout (model space, paper space or block)
        insert: insertion point
        values: parameter values, defaults keep the definition's state
        dxfattribs: INSERT attributes (rotation, xscale, layer, ...)
        attribs: ATTRIB texts by tag; missing tags get the ATTDEF default
        field_bindings: ``{attribute tag: linear parameter}``: the ATTRIB text
            becomes the parameter distance (`precision` decimals) and a FIELD is
            attached so AutoCAD keeps it in sync after grip edits
        precision: decimals of the field formatted distances
        warnings: collector for non fatal problems (unsupported stretch
            targets); without it those raise :class:`DynamicBlockError`
    """
    doc = layout.doc
    if doc is None or defn.doc is not doc:
        raise DynamicBlockError("definition and layout must belong to the same document")
    values = values or DynamicBlockValues()
    ensure_appids(doc)

    # 1. anonymous block with the evaluated geometry
    src_block = defn.block
    anon = doc.blocks.new_anonymous_block("U", base_point=src_block.block.dxf.base_point)
    anon.block.dxf.flags = src_block.block.dxf.flags | const.BLK_ANONYMOUS
    entities: dict[str, DXFGraphic] = {}
    handle_map = HandleMap()
    for entity in src_block:
        clone = entity.copy(copy_strategy=_COPY_NO_XDICT)
        anon.add_entity(clone)
        entities[entity.dxf.handle] = clone
        handle_map[entity.dxf.handle] = clone.dxf.handle
        _retag_entity(entity, clone)
    for entity in src_block:
        if entity.has_extension_dict:  # e.g. the FIELD of an ATTDEF
            _clone_extension_dict(entity, entities[entity.dxf.handle], handle_map)

    # 2. evaluate the parameters
    distances: dict[int, float] = {}
    for key, distance in values.linear.items():
        param = defn.linear_parameter(key)
        distances[param.node_id] = float(distance)
        apply_linear(defn, entities, param, distance, warnings)

    state: Optional[str] = values.visibility
    lookup_values: dict[int, str] = {}
    for label, value in values.lookup.items():
        lparam = defn.lookup_parameter(label)
        lookup_values[lparam.node_id] = str(value)
        if state is None:
            action = defn.lookup_action_of(lparam)
            if action is not None:
                state = action.state_for_value(str(value))
    vis = defn.visibility
    if vis is not None:
        if state is None:
            state = current_visibility_state(entities, vis) or vis.state_names[0]
        apply_visibility(entities, vis, state)
        for lparam in defn.lookup_parameters:
            if lparam.node_id not in lookup_values:
                action = defn.lookup_action_of(lparam)
                value = action.value_for_state(state) if action else None
                if value is not None:
                    lookup_values[lparam.node_id] = value

    # 3. link the anonymous block to the definition
    anon.block_record.set_xdata(
        APPID_REP_BTAG, [(1070, 1), (1005, defn.block_record.dxf.handle)]
    )

    # 4. the block reference and its attributes
    ref = layout.add_blockref(anon.name, insert, dxfattribs=dxfattribs)
    texts = dict(attribs or {})
    bindings: dict[str, LinearParameter] = {}
    for tag, key in (field_bindings or {}).items():
        param = defn.linear_parameter(key)
        bindings[tag] = param
        texts.setdefault(tag, f"{distances.get(param.node_id, param.default_distance):.{precision}f}")
    ref.add_auto_attribs(texts)
    attdefs = {a.dxf.tag: a for a in anon.attdefs()}
    for attrib in ref.attribs:
        attdef = attdefs.get(attrib.dxf.tag)
        if attdef is not None and attdef.has_xdata(APPID_REP_ETAG):
            attrib.set_xdata(APPID_REP_ETAG, attdef.get_xdata(APPID_REP_ETAG))

    # 5. representation data
    _add_representation_data(ref, defn, distances, state, lookup_values)
    if bindings:
        for attrib in ref.attribs:
            param = bindings.get(attrib.dxf.tag)
            if param is not None:
                _add_distance_field(ref, attrib, param, distances.get(param.node_id, param.default_distance), precision)
    return ref


def _clone_extension_dict(source: DXFEntity, clone: DXFEntity, handle_map: HandleMap) -> None:
    doc = source.doc
    assert doc is not None
    root = source.get_extension_dict().dictionary
    objects = collect_owned_subgraph(doc, root)
    clones = clone_objects(objects, doc, handle_map, owner=clone.dxf.handle)
    clone.extension_dict = ExtensionDict(clones[0])  # type: ignore
    register_fields(doc, clones)


def _retag_entity(source: DXFEntity, clone: DXFEntity) -> None:
    """Point the AcDbBlockRepETag xdata of the copy at itself (AutoCAD
    convention for entities of ``*U`` blocks)."""
    if not clone.has_xdata(APPID_REP_ETAG):
        return
    tags = []
    for code, value in clone.get_xdata(APPID_REP_ETAG):
        if code == 1005 and value == source.dxf.handle:
            value = clone.dxf.handle
        tags.append((code, value))
    clone.set_xdata(APPID_REP_ETAG, tags)


def _hard(dictionary: Dictionary) -> Dictionary:
    """Make `dictionary` look like AutoCAD's hard owner dictionaries: 360
    entry codes and a reactor link to the owner."""
    dictionary._value_code = 360
    owner = dictionary.dxf.owner
    if owner and owner != "0" and dictionary.doc is not None:
        if dictionary.doc.entitydb.get(owner) is not None and dictionary.doc.entitydb.get(owner).dxftype() == "DICTIONARY":
            dictionary.set_reactors([owner])
    return dictionary


def _add_representation_data(
    ref: Insert,
    defn: DynamicBlockDefinition,
    distances: Mapping[int, float],
    state: Optional[str],
    lookup_values: Mapping[int, str],
) -> None:
    doc = ref.doc
    assert doc is not None
    ensure_class(doc, "ACDB_BLOCKREPRESENTATION_DATA")
    xdict = ref.new_extension_dict()
    _hard(xdict.dictionary)
    rep = _hard(xdict.add_dictionary("AcDbBlockRepresentation", hard_owned=True))
    rep_data = load_raw_object(
        [
            DXFTag(0, "ACDB_BLOCKREPRESENTATION_DATA"),
            DXFTag(5, doc.entitydb.next_handle()),
            DXFTag(330, rep.dxf.handle),
            DXFTag(100, "AcDbBlockRepresentationData"),
            DXFTag(70, 1),
            DXFTag(340, defn.block_record.dxf.handle),
        ],
        doc,
    )
    rep_data.set_reactors([rep.dxf.handle])
    rep["AcDbRepData"] = rep_data
    cache = _hard(rep.add_new_dict("AppDataCache", hard_owned=True))
    data = _hard(cache.add_new_dict("ACAD_ENHANCEDBLOCKDATA", hard_owned=True))

    records: list[tuple[int, list]] = []

    for param in defn.linear_parameters:
        magic = XRECORD_MAGIC[LINEAR_PARAMETER]
        end = param.end_point_for(distances.get(param.node_id, param.default_distance))
        records.append((
            param.node_id,
            [
                (1071, magic[0]), (1071, magic[1]), (70, 25), (70, 104),
                (10, param.base.xyz), (10, end.xyz), (10, (0.0, 0.0, -1.0)),
            ],
        ))
    for action in defn.stretch_actions:
        magic = XRECORD_MAGIC[STRETCH_ACTION]
        records.append((action.node_id, [(1071, magic[0]), (1071, magic[1]), (70, 25), (70, 104), (40, 0.0)]))
    vis = defn.visibility
    if vis is not None and state is not None:
        magic = XRECORD_MAGIC[VISIBILITY_PARAMETER]
        records.append((
            vis.node_id,
            [(1071, magic[0]), (1071, magic[1]), (70, 25), (70, 104), (10, vis.position.xyz), (1, state)],
        ))
    for lparam in defn.lookup_parameters:
        value = lookup_values.get(lparam.node_id)
        if value is None:
            continue
        magic = XRECORD_MAGIC[LOOKUP_PARAMETER]
        records.append((
            lparam.node_id,
            [(1071, magic[0]), (1071, magic[1]), (70, 25), (70, 104), (10, lparam.position.xyz), (1, value)],
        ))
    for node_id, tags in sorted(records):  # AutoCAD writes them by node id
        xr = data.add_xrecord(str(node_id))
        xr.set_reactors([data.dxf.handle])
        xr.tags = Tags(dxftag(code, value) for code, value in tags)


def _add_distance_field(
    ref: Insert, attrib: Attrib, param: LinearParameter, distance: float, precision: int
) -> None:
    """Attach the FIELD pair that binds `attrib` to ``Parameter(n).UpdatedDistance``."""
    doc = ref.doc
    assert doc is not None
    ensure_class(doc, "FIELD")
    text = f"{distance:.{precision}f}"
    fmt = f"%lu2%pr{precision}"
    xdict = attrib.new_extension_dict()
    _hard(xdict.dictionary)
    fdict = _hard(xdict.add_dictionary("ACAD_FIELD", hard_owned=True))
    db = doc.entitydb
    parent_handle = db.next_handle()
    child_handle = db.next_handle()
    end = (304, "ACVALUE_END")
    parent = load_raw_object(
        [
            DXFTag(0, "FIELD"), DXFTag(5, parent_handle), DXFTag(330, fdict.dxf.handle),
            DXFTag(100, "AcDbField"),
            DXFTag(1, "_text"), DXFTag(2, "%<\\_FldIdx 0>%"), DXFTag(90, 1), DXFTag(360, child_handle),
            DXFTag(97, 0), DXFTag(91, 63), DXFTag(92, 0), DXFTag(94, 9), DXFTag(95, 2), DXFTag(96, 0),
            DXFTag(300, ""), DXFTag(93, 1),
            DXFTag(6, "ACFD_FIELDTEXT_CHECKSUM"), DXFTag(93, 2), DXFTag(90, 2),
            DXFTag(140, field_checksum(text)), DXFTag(94, 0), DXFTag(300, ""), DXFTag(302, ""), DXFTag(*end),
            DXFTag(7, "ACFD_FIELD_VALUE"), DXFTag(93, 3), DXFTag(90, 0), DXFTag(94, 0),
            DXFTag(300, ""), DXFTag(302, ""), DXFTag(*end),
            DXFTag(301, ""), DXFTag(98, 0),
        ],
        doc,
    )
    child = load_raw_object(
        [
            DXFTag(0, "FIELD"), DXFTag(5, child_handle), DXFTag(330, parent_handle),
            DXFTag(100, "AcDbField"),
            DXFTag(1, "AcObjProp"),
            DXFTag(2, f'\\AcObjProp Object(%<\\_ObjIdx 0>%).Parameter({param.node_id}).UpdatedDistance \\f "{fmt}"'),
            DXFTag(90, 0), DXFTag(97, 1), DXFTag(331, ref.dxf.handle),
            DXFTag(91, 63), DXFTag(92, 0), DXFTag(94, 59), DXFTag(95, 2), DXFTag(96, 0),
            DXFTag(300, ""), DXFTag(93, 3),
            DXFTag(6, "ObjectPropertyId"), DXFTag(93, 2), DXFTag(90, 64), DXFTag(330, ref.dxf.handle),
            DXFTag(94, 0), DXFTag(300, ""), DXFTag(302, ""), DXFTag(*end),
            DXFTag(6, "ObjectPropertyName"), DXFTag(93, 2), DXFTag(90, 4), DXFTag(1, "UpdatedDistance"),
            DXFTag(94, 0), DXFTag(300, ""), DXFTag(302, ""), DXFTag(*end),
            DXFTag(6, "ObjectPropertyParameterId"), DXFTag(93, 2), DXFTag(90, 1), DXFTag(91, param.node_id),
            DXFTag(94, 0), DXFTag(300, ""), DXFTag(302, ""), DXFTag(*end),
            DXFTag(7, "ACFD_FIELD_VALUE"), DXFTag(93, 4), DXFTag(90, 2), DXFTag(140, float(distance)),
            DXFTag(94, 0), DXFTag(300, fmt), DXFTag(302, text), DXFTag(*end),
            DXFTag(301, text), DXFTag(98, 4),
        ],
        doc,
    )
    parent.set_reactors([fdict.dxf.handle])
    fdict["TEXT"] = parent
    register_fields(doc, [parent, child])
