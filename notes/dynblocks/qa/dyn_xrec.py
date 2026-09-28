import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1]); db = doc.entitydb
def xr(h):
    o = db.get(h); return [(t.code, (round(t.value,4) if isinstance(t.value,float) else t.value)) for t in o.tags]
for ins in doc.modelspace().query("INSERT"):
    print("="*20, ins.dxf.name, "->", [(t.code,t.value) for t in doc.blocks.get(ins.dxf.name).block_record.get_xdata("AcDbBlockRepBTag")][-1][1])
    d = ins.get_extension_dict().dictionary["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
    for k, v in d.items():
        print(f"  node {k}: {xr(v.dxf.handle)}")
    for a in ins.attribs:
        if a.has_extension_dict:
            f = a.get_extension_dict().dictionary["ACAD_FIELD"]["TEXT"]
            # find nested field
            child = [t.value for sub in f.xtags.subclasses for t in sub if t.code==360]
            for ch in child:
                co = db.get(ch)
                vals = [(t.code,t.value) for sub in co.xtags.subclasses for t in sub if t.code in (2,301,331,90,140,1)]
                print(f"  ATTRIB {a.dxf.tag}={a.dxf.text!r} field child {ch}: {vals}")
# geometry comparison
def geo(name):
    out=[]
    for e in doc.blocks.get(name):
        if e.dxftype()=="LWPOLYLINE": out.append(("LWPOLY", [tuple(round(c,3) for c in p[:2]) for p in e.get_points()]))
        elif e.dxftype()=="LINE": out.append(("LINE", tuple(round(c,3) for c in e.dxf.start[:2]), tuple(round(c,3) for c in e.dxf.end[:2])))
    return out
for a,b in (("TAVOLO","*U7"),("ARREDO","*U3"),("DIV_DIN","*U6")):
    print("="*20, a, "vs", b)
    print("  ", geo(a)); print("  ", geo(b))
# stretch action targets
for h in ("2F6","2D2","2D3","2E4","2E5","37E","37F","380","381"):
    e = db.get(h); print("target", h, e.dxftype(), getattr(e.dxf,"tag",""))
# LETTO / SED: how many entities visible per state, entity types
for name in ("LETTO","SED"):
    blk = doc.blocks.get(name); br = blk.block_record
    graph = br.get_extension_dict().dictionary["ACAD_ENHANCEDBLOCK"]
    vp = [db.get(v) for c,v in [(t.code,t.value) for sub in graph.xtags.subclasses for t in sub] if c==360 and db.get(v).dxftype()=="BLOCKVISIBILITYPARAMETER"][0]
    tags = [(t.code,t.value) for sub in vp.xtags.subclasses for t in sub]
    states = {}
    cur=None
    for c,v in tags:
        if c==303: cur=v; states[cur]=[]
        elif c==332 and cur: states[cur].append(v)
    print(name, "states:", {k: len(v) for k,v in states.items()}, "total entities", len(list(blk)))
    # which entities are ATTDEF among each state's list?
    for k,v in states.items():
        print("   ", k, "types:", {db.get(h).dxftype() for h in v}, "attdefs in state:", [db.get(h).dxf.tag for h in v if db.get(h).dxftype()=="ATTDEF"])
    print("   entities not in any state:", [(e.dxftype(), getattr(e.dxf,'tag','')) for e in blk if e.dxf.handle not in {h for v in states.values() for h in v}][:12])
