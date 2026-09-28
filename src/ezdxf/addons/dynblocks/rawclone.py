#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Raw cloning of DXF objects between documents.

`ezdxf` refuses to copy :class:`DXFTagStorage` objects (unknown object types
such as the dynamic block parameters, actions and FIELD objects). This module
clones any DXF object by exporting its tags, remapping every handle through a
translation table and loading the tags into the target document.

The clone is structural: nothing is interpreted, so it works for the whole
undocumented dynamic block object family.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Iterator, Optional

from ezdxf.entities import factory, Dictionary, DXFEntity
from ezdxf.lldxf.extendedtags import ExtendedTags
from ezdxf.lldxf.tagger import tag_compiler
from ezdxf.lldxf.tagwriter import TagCollector
from ezdxf.lldxf.types import DXFTag

if TYPE_CHECKING:
    from ezdxf.document import Drawing

__all__ = [
    "HandleMap",
    "collect_owned_subgraph",
    "clone_objects",
    "hard_owned_handles",
    "load_raw_object",
    "remap_tags",
]

# group codes holding handles: 320-369 pointers/owners, 1005 xdata handle
HANDLE_CODES = frozenset(range(320, 370)) | {1005}
# hard ownership (the target object is a child of this one)
HARD_OWNER_CODES = frozenset(range(360, 370))


class HandleMap(dict):
    """Old handle -> new handle translation table."""

    def translate(self, handle: str) -> str:
        return self.get(handle, handle)


def hard_owned_handles(entity: DXFEntity) -> Iterator[str]:
    """Yields the handles of the objects hard-owned by a raw object or a
    DICTIONARY (its entries when it is a hard owner).
    """
    if isinstance(entity, Dictionary):
        if entity.is_hard_owner:
            for _, value in entity.items():
                if isinstance(value, DXFEntity):
                    yield value.dxf.handle
                else:
                    yield str(value)
        return
    xtags = getattr(entity, "xtags", None)
    if xtags is None:
        return
    for subclass in xtags.subclasses:
        for code, value in subclass:
            if code in HARD_OWNER_CODES:
                yield str(value)


def collect_owned_subgraph(doc: Drawing, root: DXFEntity) -> list[DXFEntity]:
    """Returns `root` and every object transitively hard-owned by it, parents
    before children.
    """
    db = doc.entitydb
    seen: set[str] = set()
    out: list[DXFEntity] = []

    def walk(entity: DXFEntity) -> None:
        handle = entity.dxf.handle
        if handle in seen:
            return
        seen.add(handle)
        out.append(entity)
        for child_handle in hard_owned_handles(entity):
            child = db.get(child_handle)
            if child is not None:
                walk(child)

    walk(root)
    return out


def remap_tags(
    tags: Iterable[DXFTag], handle_map: HandleMap, new_handle: Optional[str] = None
) -> list[DXFTag]:
    """Returns `tags` with every handle translated through `handle_map`; the
    entity's own handle (group code 5/105) is replaced by `new_handle`.
    """
    out: list[DXFTag] = []
    for tag in tags:
        code, value = tag
        if (code == 5 or code == 105) and new_handle is not None:
            tag = DXFTag(code, new_handle)
        elif code in HANDLE_CODES and isinstance(value, str):
            new_value = handle_map.translate(value)
            if new_value != value:
                tag = DXFTag(code, new_value)
        out.append(tag)
    return out


def load_raw_object(tags: Iterable[DXFTag], target: Drawing) -> DXFEntity:
    """Create a DXF object in `target` from flat DXF tags (including the
    structure tag (0, DXFTYPE) and the handle tag), add it to the entity
    database and the OBJECTS section and resolve its references.
    """
    obj = factory.load(ExtendedTags(tag_compiler(iter(tags))), target)
    _bind(obj, target)
    target.objects.add_object(obj)  # type: ignore
    obj.post_load_hook(target)
    return obj


def _bind(entity: DXFEntity, doc: Drawing) -> None:
    """Like :func:`factory.bind` but without the post-bind hook, which expects
    resolved references (e.g. DICTIONARY entries) that only exist after the
    2nd loading stage.
    """
    entity.doc = doc
    doc.entitydb.add(entity)


def clone_objects(
    objects: Iterable[DXFEntity],
    target: Drawing,
    handle_map: Optional[HandleMap] = None,
    *,
    owner: Optional[str] = None,
) -> list[DXFEntity]:
    """Clone DXF objects (OBJECTS section content) into `target`.

    Every object gets a new handle; all handles referenced by the cloned objects
    that appear in `handle_map` (pre-filled by the caller, e.g. the new owner or
    the copied graphical entities) or belong to the cloned set are translated.
    Unknown handles are preserved as they are.

    Args:
        objects: objects to clone, parents before children
        target: target DXF document
        handle_map: old -> new handle table, extended in place
        owner: new owner handle of the first (root) object

    Returns:
        the cloned objects in the same order
    """
    objects = list(objects)
    if handle_map is None:
        handle_map = HandleMap()
    if not objects:
        return []
    db = target.entitydb
    for entity in objects:
        handle_map[entity.dxf.handle] = db.next_handle()
    if owner is not None:
        handle_map[objects[0].dxf.owner] = owner

    source = objects[0].doc
    dxfversion = source.dxfversion if source is not None else target.dxfversion
    clones: list[DXFEntity] = []
    for entity in objects:
        tags = TagCollector.dxftags(entity, dxfversion=dxfversion)
        tags = remap_tags(tags, handle_map, handle_map[entity.dxf.handle])
        clone = factory.load(ExtendedTags(tag_compiler(iter(tags))), target)
        _bind(clone, target)
        target.objects.add_object(clone)  # type: ignore
        clones.append(clone)
    # 2nd loading stage: resolve extension dicts, dictionary entries, ...
    for clone in clones:
        clone.post_load_hook(target)
        _prune_reactors(clone, target)
    return clones


def _prune_reactors(entity: DXFEntity, doc: Drawing) -> None:
    """Drop reactor handles pointing outside of the target document."""
    reactors = entity.get_reactors()
    if not reactors:
        return
    db = doc.entitydb
    valid = [h for h in reactors if h in db]
    if len(valid) != len(reactors):
        entity.set_reactors(valid)
