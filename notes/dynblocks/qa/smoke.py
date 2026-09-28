import ezdxf, sys, traceback
from ezdxf.addons import dynblocks as db
from ezdxf.math import Vec3
SRC = "/Users/iwa/projects/ezdxf/tests/test_10_dynblocks/data/ca_foscari.dxf"
master = ezdxf.readfile(SRC)
print("dynamic blocks:", list(db.dynamic_blocks(master)))
for name in db.dynamic_blocks(master):
    d = db.DynamicBlockDefinition.load(master, name)
    print(f"  {name}: linear={[(p.label, round(p.default_distance,3)) for p in d.linear_parameters]} stretch={[(a.parameter_node, len(a.targets)) for a in d.stretch_actions]} vis={d.visibility.state_names if d.visibility else None} lookup={[(p.label, (d.lookup_action_of(p).values if d.lookup_action_of(p) else None)) for p in d.lookup_parameters]} unsupported={d.unsupported} bindings={db.guess_field_bindings(d)}")

def geo(block):
    out=[]
    for e in block:
        t=e.dxftype()
        if t=="LWPOLYLINE": out.append(("LWPOLY", [tuple(round(c,4) for c in p[:2]) for p in e.get_points()], e.dxf.get("invisible",0)))
        elif t=="LINE": out.append(("LINE", (round(e.dxf.start.x,4), round(e.dxf.start.y,4)), (round(e.dxf.end.x,4), round(e.dxf.end.y,4)), e.dxf.get("invisible",0)))
        elif t=="ARC": out.append(("ARC", (round(e.dxf.center.x,4), round(e.dxf.center.y,4)), round(e.dxf.radius,4), e.dxf.get("invisible",0)))
        elif t=="CIRCLE": out.append(("CIRCLE", (round(e.dxf.center.x,4), round(e.dxf.center.y,4)), round(e.dxf.radius,4), e.dxf.get("invisible",0)))
        elif t=="ATTDEF": out.append(("ATTDEF", e.dxf.tag, (round(e.dxf.insert.x,4), round(e.dxf.insert.y,4)), e.dxf.get("invisible",0)))
    return out

# ground truth from the master's own INSERTs
cases = {  # anon block -> (definition, values)
    "*U7": ("TAVOLO", db.DynamicBlockValues(linear={"Larghezza Tavolo": 0.7, "Profondità Tavolo": 1.0})),
    "*U6": ("DIV_DIN", db.DynamicBlockValues(linear={"Larghezza Divano": 1.5, "Profondità Divano": 0.5998078010432835})),
    "*U3": ("ARREDO", db.DynamicBlockValues(linear={"Larghezza Armadio": 1.0, "Profondità Armadio": 0.4})),
    "*U9": ("LETTO", db.DynamicBlockValues(lookup={"Controllo dinamico1": "SINGOLO"})),
    "*U10": ("SED", db.DynamicBlockValues(lookup={"TIPO_SEDUTA": "2"})),
}
doc = ezdxf.new("R2010", setup=True)
msp = doc.modelspace()
ok_all = True
for anon, (name, values) in cases.items():
    defn = db.copy_dynamic_block(master, name, doc)
    warnings = []
    ref = db.insert_dynamic_block(defn, msp, (1, 2), values, dxfattribs={"layer": "A-Arredo", "rotation": 0},
                                  attribs={"CODICE_VANO": "ZZZ"}, field_bindings=db.guess_field_bindings(defn), warnings=warnings)
    mine = geo(doc.blocks.get(ref.dxf.name)); truth = geo(master.blocks.get(anon))
    ok = mine == truth
    ok_all &= ok
    print(f"{name} -> {ref.dxf.name}: geometry {'OK' if ok else 'MISMATCH'} warnings={warnings} attribs=" + str({a.dxf.tag: a.dxf.text for a in ref.attribs if a.dxf.tag in ('LARGHEZZA','PROFONDITA','CODICE_VANO')}))
    if not ok:
        for a,b in zip(mine, truth):
            if a!=b: print("   mine ", a); print("   truth", b)
out = "/Users/iwa/projects/ezdxf-dynblocks/qa/out_smoke.dxf"
auditor = doc.audit()
print("audit errors:", len(auditor.errors), [str(e.message)[:100] for e in auditor.errors[:5]], "fixes:", len(auditor.fixes), [str(f.message)[:100] for f in auditor.fixes[:5]])
doc.saveas(out)
# reload and re-check
doc2 = ezdxf.readfile(out)
a2 = doc2.audit()
print("reload audit errors:", len(a2.errors), "fixes:", len(a2.fixes), [str(f.message)[:120] for f in a2.fixes[:8]])
for ins in doc2.modelspace().query("INSERT"):
    d = ezdxf.dynblkhelper.get_dynamic_block_definition(ins) if hasattr(ezdxf,"dynblkhelper") else None
    from ezdxf import dynblkhelper
    d = dynblkhelper.get_dynamic_block_definition(ins)
    xd = ins.get_extension_dict().dictionary
    ebd = xd["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
    print(" ", ins.dxf.name, "->", d.name if d else None, "xrecords:", {k: [(t.code, t.value) for t in v.tags if t.code in (1,)] for k,v in ebd.items()})
print("ALL OK" if ok_all else "SOME MISMATCH")
