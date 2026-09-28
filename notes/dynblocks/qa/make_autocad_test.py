"""Build the AutoCAD validation drawing: every Ca' Foscari dynamic block placed
with values that differ from the master, plus labels explaining what to check."""
import ezdxf
from ezdxf.addons import dynblocks as db
from ezdxf.addons.dynblocks import DynamicBlockValues as V

MASTER = "/Users/iwa/projects/ezdxf/tests/test_10_dynblocks/data/ca_foscari.dxf"
import sys
VERSION = sys.argv[1] if len(sys.argv) > 1 else "R2010"
OUT = f"/Users/iwa/Desktop/TEST-blocchi-dinamici-ezdxf{'' if VERSION == 'R2010' else '-' + VERSION}.dxf"

master = ezdxf.readfile(MASTER)
doc = ezdxf.new(VERSION, setup=True)
doc.header["$INSUNITS"] = 6
doc.layers.add("A-Arredo", color=3)
doc.layers.add("NOTE", color=1)
msp = doc.modelspace()

def label(x, y, text):
    msp.add_mtext(text, dxfattribs={"layer": "NOTE", "char_height": 0.08, "insert": (x, y)})

cases = [
    # x, y, block, values, attribs, note
    (0, 0, "TAVOLO", V(linear={"Larghezza Tavolo": 1.2, "Profondità Tavolo": 0.8}), {"CODICE_VANO": "001"}, "TAVOLO 1.20 x 0.80 (master 1.90 x 0.60)"),
    (3, 0, "TAVOLO", V(linear={"Larghezza Tavolo": 2.5}), {"CODICE_VANO": "001"}, "TAVOLO 2.50 x 0.60, ruotato 30°"),
    (6, 0, "ARREDO", V(linear={"Larghezza Armadio": 1.6, "Profondità Armadio": 0.5}), {"CODICE_VANO": "002"}, "ARREDO 1.60 x 0.50"),
    (0, 3, "DIV_DIN", V(linear={"Larghezza Divano": 2.0, "Profondità Divano": 0.7}), {"CODICE_VANO": "003"}, "DIV_DIN 2.00 x 0.70"),
    (3, 3, "LETTO", V(lookup={"Controllo dinamico1": "DOPPIO"}), {"CODICE_VANO": "004", "TIPOLOGIA_LETTO": "DOP"}, "LETTO stato Doppio"),
    (6, 3, "LETTO", V(visibility="Singolo"), {"CODICE_VANO": "004", "TIPOLOGIA_LETTO": "SIN"}, "LETTO stato Singolo"),
    (0, 6, "SED", V(lookup={"TIPO_SEDUTA": "3"}), {"CODICE_VANO": "005", "TIPOLOGIA_INGOMBRO": "3"}, "SED stato 3_Ufficio"),
    (3, 6, "SED", V(lookup={"TIPO_SEDUTA": "1"}), {"CODICE_VANO": "005", "TIPOLOGIA_INGOMBRO": "1"}, "SED stato 1_Sgabello"),
    (6, 6, "SED", V(), {"CODICE_VANO": "005"}, "SED stato di default del master"),
]
defs = {}
for x, y, name, values, attribs, note in cases:
    defn = defs.get(name) or db.copy_dynamic_block(master, name, doc)
    defs[name] = defn
    rotation = 30 if "ruotato" in note else 0
    db.insert_dynamic_block(defn, msp, (x, y), values,
        dxfattribs={"layer": "A-Arredo", "rotation": rotation},
        attribs=attribs, field_bindings=db.guess_field_bindings(defn))
    label(x - 0.6, y - 0.4, note)

label(-0.6, 9.2, "\\pxqc;TEST BLOCCHI DINAMICI generati da ezdxf (fork gabrieliwa/ezdxf, branch dynblocks)\\P"
      "Da verificare: 1) il file si apre senza errori/AUDIT pulito  2) i blocchi sono dinamici (grip a freccia / tendina)  "
      "3) le dimensioni e gli stati corrispondono alle etichette  4) REGEN non cambia nulla  "
      "5) trascinando un grip il blocco si aggiorna e gli attributi LARGHEZZA/PROFONDITA seguono  6) RESETBLOCK riporta ai valori di default")
# GAB-1513: point the opening view at the drawing (ezdxf's default *Active VPORT sits on the origin)
from ezdxf import bbox
extents = bbox.extents(msp, fast=True)
w = float(extents.size.x); h = max(float(extents.size.y), w * 9 / 16) * 1.1
doc.set_modelspace_vport(height=h, center=(float(extents.center.x), float(extents.center.y)))
msp.reset_extents((extents.extmin.x, extents.extmin.y, 0.0), (extents.extmax.x, extents.extmax.y, 0.0))
msp.reset_limits((extents.extmin.x, extents.extmin.y), (extents.extmax.x, extents.extmax.y))
print("view center", doc.modelspace().doc.viewports.get("*Active")[0].dxf.center, "height", h)
auditor = doc.audit()
print("audit errors", len(auditor.errors), "fixes", len(auditor.fixes))
doc.saveas(OUT)
print("saved", OUT)
d2 = ezdxf.readfile(OUT); a2 = d2.audit(); print("reload audit errors", len(a2.errors), "fixes", len(a2.fixes))
