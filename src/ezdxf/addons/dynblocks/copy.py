#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Copy a dynamic block definition, with its whole evaluation graph, into
another DXF document.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ezdxf.addons.importer import Importer
from ezdxf.entities import DXFClass, DXFEntity, Dictionary
from ezdxf.entities.xdict import ExtensionDict
from ezdxf.lldxf import const

from .model import (
    APPID_TRUE_NAME,
    DYNAMIC_APPIDS,
    DynamicBlockDefinition,
    DynamicBlockError,
    is_dynamic_block,
)
from .rawclone import HandleMap, clone_objects, collect_owned_subgraph, remap_tags

if TYPE_CHECKING:
    from ezdxf.document import Drawing
    from ezdxf.entities import FieldList

__all__ = [
    "copy_dynamic_block",
    "ensure_class",
    "ensure_appids",
    "register_fields",
    "DYNAMIC_CLASSES",
]

# CLASSES section entries of the dynamic block object family, as written by
# AutoCAD 2018: name -> (cpp class name, app name, proxy flags, was a proxy, is an entity)
_OBJECTDBX = "ObjectDBX Classes"
DYNAMIC_CLASSES: dict[str, tuple[str, str, int, int, int]] = {
    "ACAD_EVALUATION_GRAPH": ("AcDbEvalGraph", _OBJECTDBX, 1153, 0, 0),
    "ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION": ("AcDbDynamicBlockPurgePreventer", _OBJECTDBX, 1153, 0, 0),
    "ACDB_BLOCKREPRESENTATION_DATA": ("AcDbBlockRepresentationData", _OBJECTDBX, 1153, 0, 0),
    "BLOCKGRIPLOCATIONCOMPONENT": ("AcDbBlockGripExpr", _OBJECTDBX, 1153, 0, 0),
    "BLOCKLINEARPARAMETER": ("AcDbBlockLinearParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKLINEARGRIP": ("AcDbBlockLinearGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKSTRETCHACTION": ("AcDbBlockStretchAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKVISIBILITYPARAMETER": ("AcDbBlockVisibilityParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKVISIBILITYGRIP": ("AcDbBlockVisibilityGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKLOOKUPPARAMETER": ("AcDbBlockLookupParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKLOOKUPGRIP": ("AcDbBlockLookupGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKLOOKUPACTION": ("AcDbBlockLookupAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKALIGNMENTPARAMETER": ("AcDbBlockAlignmentParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKALIGNMENTGRIP": ("AcDbBlockAlignmentGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKBASEPOINTPARAMETER": ("AcDbBlockBasepointParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKFLIPPARAMETER": ("AcDbBlockFlipParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKFLIPGRIP": ("AcDbBlockFlipGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKFLIPACTION": ("AcDbBlockFlipAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKMOVEACTION": ("AcDbBlockMoveAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKPOINTPARAMETER": ("AcDbBlockPointParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKPOLARPARAMETER": ("AcDbBlockPolarParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKPOLARGRIP": ("AcDbBlockPolarGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKPOLARSTRETCHACTION": ("AcDbBlockPolarStretchAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKROTATIONPARAMETER": ("AcDbBlockRotationParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKROTATIONGRIP": ("AcDbBlockRotationGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKROTATEACTION": ("AcDbBlockRotateAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKSCALEACTION": ("AcDbBlockScaleAction", _OBJECTDBX, 1153, 0, 0),
    "BLOCKXYPARAMETER": ("AcDbBlockXYParameter", _OBJECTDBX, 1153, 0, 0),
    "BLOCKXYGRIP": ("AcDbBlockXYGrip", _OBJECTDBX, 1153, 0, 0),
    "BLOCKARRAYACTION": ("AcDbBlockArrayAction", _OBJECTDBX, 1153, 0, 0),
    "FIELD": ("AcDbField", _OBJECTDBX, 1152, 0, 0),
    "FIELDLIST": ("AcDbFieldList", _OBJECTDBX, 1152, 0, 0),
}


def ensure_class(target: Drawing, name: str, source: Optional[Drawing] = None) -> None:
    """Register the CLASSES entry `name` in `target`, copied from `source`
    when available, otherwise from :data:`DYNAMIC_CLASSES`.
    """
    try:
        target.classes.get(name)
        return
    except const.DXFKeyError:
        pass
    attribs: Optional[dict] = None
    if source is not None:
        try:
            src = source.classes.get(name)
            attribs = {
                k: src.dxf.get(k)
                for k in ("name", "cpp_class_name", "app_name", "flags", "was_a_proxy", "is_an_entity")
            }
        except const.DXFKeyError:
            pass
    if attribs is None:
        if name not in DYNAMIC_CLASSES:
            return
        cpp, app, flags, proxy, entity = DYNAMIC_CLASSES[name]
        attribs = {
            "name": name,
            "cpp_class_name": cpp,
            "app_name": app,
            "flags": flags,
            "was_a_proxy": proxy,
            "is_an_entity": entity,
        }
    target.classes.register(DXFClass.new(doc=target, dxfattribs=attribs))


def ensure_appids(target: Drawing, appids=DYNAMIC_APPIDS) -> None:
    for appid in appids:
        if not target.appids.has_entry(appid):
            target.appids.add(appid)


def field_list(doc: Drawing) -> FieldList:
    """The ACAD_FIELDLIST object of `doc`, created if missing."""
    rootdict = doc.rootdict
    fl = rootdict.get("ACAD_FIELDLIST")
    if fl is None or fl.dxftype() != "FIELDLIST":
        ensure_class(doc, "FIELDLIST")
        fl = doc.objects.new_entity("FIELDLIST", {"owner": rootdict.dxf.handle})
        rootdict["ACAD_FIELDLIST"] = fl
    return fl  # type: ignore


def register_fields(doc: Drawing, objects) -> None:
    """Add every FIELD object of `objects` to the document's field list."""
    handles = [o.dxf.handle for o in objects if o.dxftype() == "FIELD"]
    if not handles:
        return
    ensure_class(doc, "FIELD")
    fl = field_list(doc)
    known = set(fl.handles)
    fl.handles.extend(h for h in handles if h not in known)


def _copy_xdata(source: DXFEntity, target: DXFEntity, handle_map: HandleMap, doc: Drawing) -> None:
    if source.xdata is None:
        return
    for appid in list(source.xdata.data.keys()):
        tags = source.get_xdata(appid)
        if not doc.appids.has_entry(appid):
            doc.appids.add(appid)
        target.set_xdata(appid, remap_tags(tags, handle_map))


def _copy_extension_dict(
    source: DXFEntity, target: DXFEntity, handle_map: HandleMap, src_doc: Drawing, dst_doc: Drawing
) -> list[DXFEntity]:
    """Raw-clone the extension dictionary subgraph of `source` onto `target`."""
    if not source.has_extension_dict:
        return []
    root: Dictionary = source.get_extension_dict().dictionary
    objects = collect_owned_subgraph(src_doc, root)
    clones = clone_objects(objects, dst_doc, handle_map, owner=target.dxf.handle)
    if target.has_extension_dict:
        target.get_extension_dict().destroy()
    target.extension_dict = ExtensionDict(clones[0])  # type: ignore
    for obj in clones:
        ensure_class(dst_doc, obj.dxftype(), src_doc)
    register_fields(dst_doc, clones)
    return clones


def copy_dynamic_block(
    source: Drawing,
    block_name: str,
    target: Drawing,
    importer: Optional[Importer] = None,
) -> DynamicBlockDefinition:
    """Copy the dynamic block definition `block_name` from `source` into
    `target`: the BLOCK with its entities (through :class:`Importer`, so the
    layers, linetypes and text styles come along), the per-entity dynamic
    xdata, the attribute FIELD objects and the whole evaluation graph.

    If `target` already holds a dynamic block of that name it is reused.

    Args:
        source: document holding the definition
        block_name: name of the dynamic block
        target: target document
        importer: an :class:`Importer` shared by the caller; when omitted a
            private one is created and finalized

    Returns:
        the definition parsed from `target`
    """
    if block_name in target.blocks:
        record = target.blocks[block_name].block_record
        if is_dynamic_block(record):
            return DynamicBlockDefinition.load(target, block_name)
        raise DynamicBlockError(
            f"target already has a static block named {block_name!r}"
        )
    DynamicBlockDefinition.load(source, block_name)  # validates the source
    own_importer = importer is None
    if importer is None:
        importer = Importer(source, target)
    new_name = importer.import_block(block_name, rename=False)
    if own_importer:
        importer.finalize()

    src_block = source.blocks[block_name]
    dst_block = target.blocks[new_name]
    src_entities = list(src_block)
    dst_entities = list(dst_block)
    if len(src_entities) != len(dst_entities):
        raise DynamicBlockError(
            f"{block_name}: {len(src_entities) - len(dst_entities)} entities "
            "could not be imported, the evaluation graph would be inconsistent"
        )
    handle_map = HandleMap()
    for s, d in zip(src_entities, dst_entities):
        handle_map[s.dxf.handle] = d.dxf.handle
    for s, d in zip(src_entities, dst_entities):
        _copy_xdata(s, d, handle_map, target)
        _copy_extension_dict(s, d, handle_map, source, target)

    src_record = src_block.block_record
    dst_record = dst_block.block_record
    handle_map[src_record.dxf.handle] = dst_record.dxf.handle
    for attr in ("units", "explode", "scale"):
        if src_record.dxf.hasattr(attr):
            dst_record.dxf.set(attr, src_record.dxf.get(attr))
    ensure_appids(target)
    _copy_xdata(src_record, dst_record, handle_map, target)
    dst_record.set_xdata(APPID_TRUE_NAME, [(1000, new_name)])
    _copy_extension_dict(src_record, dst_record, handle_map, source, target)
    return DynamicBlockDefinition.load(target, new_name)
