#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""AutoCAD dynamic blocks: parse a definition, copy it between documents and
insert references with evaluated parameters (stretch and visibility states).

Example::

    import ezdxf
    from ezdxf.addons import dynblocks

    master = ezdxf.readfile("master.dxf")
    doc = ezdxf.new("R2010")
    table = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
    dynblocks.insert_dynamic_block(
        table, doc.modelspace(), (10, 5),
        dynblocks.DynamicBlockValues(linear={"Larghezza Tavolo": 0.7, "Profondità Tavolo": 1.0}),
        dxfattribs={"rotation": 90, "layer": "A-Arredo"},
        attribs={"CODICE_VANO": "001"},
        field_bindings={"LARGHEZZA": "Larghezza Tavolo", "PROFONDITA": "Profondità Tavolo"},
    )
"""
from .model import (
    DynamicBlockDefinition,
    DynamicBlockError,
    LinearParameter,
    LookupAction,
    LookupParameter,
    StretchAction,
    VisibilityParameter,
    VisibilityState,
    dynamic_blocks,
    is_dynamic_block,
)
from .copy import copy_dynamic_block, ensure_appids, ensure_class, register_fields
from .instance import (
    DynamicBlockValues,
    field_checksum,
    guess_field_bindings,
    insert_dynamic_block,
)
from .bake import apply_linear, apply_visibility, current_visibility_state, move_vertices

__all__ = [
    "DynamicBlockDefinition",
    "DynamicBlockError",
    "DynamicBlockValues",
    "LinearParameter",
    "LookupAction",
    "LookupParameter",
    "StretchAction",
    "VisibilityParameter",
    "VisibilityState",
    "apply_linear",
    "apply_visibility",
    "copy_dynamic_block",
    "current_visibility_state",
    "dynamic_blocks",
    "ensure_appids",
    "ensure_class",
    "field_checksum",
    "guess_field_bindings",
    "insert_dynamic_block",
    "is_dynamic_block",
    "move_vertices",
    "register_fields",
]
