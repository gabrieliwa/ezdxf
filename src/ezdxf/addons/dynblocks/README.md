# ezdxf.addons.dynblocks — AutoCAD dynamic blocks

Parse a dynamic block definition, copy it into another document and insert
references whose parameters are already evaluated ("virtual REGEN"). The output
mirrors what AutoCAD writes, so the references stay dynamic in AutoCAD (grips,
drop downs, attribute fields).

```python
import ezdxf
from ezdxf.addons import dynblocks

master = ezdxf.readfile("master.dxf")
doc = ezdxf.new("R2010", setup=True)
table = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
dynblocks.insert_dynamic_block(
    table, doc.modelspace(), (10, 5),
    dynblocks.DynamicBlockValues(linear={"Larghezza Tavolo": 0.7, "Profondità Tavolo": 1.0}),
    dxfattribs={"rotation": 90, "layer": "A-Arredo"},
    attribs={"CODICE_VANO": "001"},
    field_bindings=dynblocks.guess_field_bindings(table),  # {"LARGHEZZA": "Larghezza Tavolo", ...}
)
# visibility / lookup blocks:
bed = dynblocks.copy_dynamic_block(master, "LETTO", doc)
dynblocks.insert_dynamic_block(bed, doc.modelspace(), (0, 0),
    dynblocks.DynamicBlockValues(lookup={"Controllo dinamico1": "DOPPIO"}))   # or visibility="Doppio"
doc.audit()          # declares APPIDs, checks the structure
doc.saveas("out.dxf")  # remember to frame the view first (see CLAUDE.md)
```

## What AutoCAD stores (reverse engineered from AutoCAD 2018 output)

Definition (`BLOCK_RECORD`):
- xdata `AcDbDynamicBlockGUID`, `AcDbDynamicBlockTrueName`, `AcDbBlockRepETag`;
- extension dict `ACAD_ENHANCEDBLOCK` → `ACAD_EVALUATION_GRAPH` → nodes (360):
  `BLOCKLINEARPARAMETER` (1010 base, 1011 end, 305 label), `BLOCKSTRETCHACTION`
  (92 parameter node, 1011×n frame, 331 entity + 74/94 vertex indices),
  `BLOCKVISIBILITYPARAMETER` (301 label, 331 governed entities, 303 state + 332
  visible entities), `BLOCKLOOKUPPARAMETER` (303 label), `BLOCKLOOKUPACTION`
  (92×93 table of 302 cells, columns 303/94/304/282), grips;
- `AcDbDynamicBlockRoundTripPurgePreventer`;
- ATTDEFs bound to a parameter carry an `ACAD_FIELD` dictionary with a FIELD
  (`Object(...).Distance`, no usable object reference: the binding is guessed
  from the label, `guess_field_bindings`).

Reference (what `insert_dynamic_block` produces):
- anonymous `*U` block with the **evaluated** geometry (stretched vertices,
  `invisible` flag on entities outside the visibility state); its record has
  xdata `AcDbBlockRepBTag` (1005 → definition record);
- INSERT extension dict `AcDbBlockRepresentation` → `AcDbRepData`
  (`ACDB_BLOCKREPRESENTATION_DATA`, 340 → definition) + `AppDataCache` →
  `ACAD_ENHANCEDBLOCKDATA` → one XRECORD per node: linear parameter (base, end,
  (0,0,-1)), stretch action (40 = 0), visibility (1 = state name), lookup
  (1 = value); the 1071 pairs are per-class magic numbers (`XRECORD_MAGIC`);
- ATTRIBs with a FIELD pair `Parameter(n).UpdatedDistance \f "%lu2%pr2"`, cached
  value and text, checksum = Σ (i+1)·ord(c) (`field_checksum`), registered in
  the root `ACAD_FIELDLIST`.

AutoCAD never re-evaluates on load: it trusts the `*U` block and the cached
values, and only recomputes when a grip or property changes.

## Supported / not supported

Supported: linear parameters + stretch actions (LWPOLYLINE, POLYLINE, LINE, ARC,
CIRCLE, ELLIPSE, TEXT/ATTDEF/MTEXT/INSERT/POINT, SOLID/TRACE/3DFACE vertices),
visibility parameters, lookup parameters/actions. Any other node type is kept as
raw tags (`DynamicBlockDefinition.unsupported`) and not evaluated: Move, Scale,
Rotate, Flip, Array, Polar, XY parameters would need their own bake functions.

## Verification

`tests/test_10_dynblocks` uses a real AutoCAD master (Ca' Foscari furniture)
as ground truth: the five references AutoCAD saved are reproduced exactly.
Structural tag-by-tag diffs against AutoCAD: `notes/dynblocks/qa/` in this repository.
Final validation is only possible in AutoCAD.
