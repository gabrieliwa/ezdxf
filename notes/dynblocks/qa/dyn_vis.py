import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1]); db = doc.entitydb
def geo(name):
    out=[]
    for e in doc.blocks.get(name):
        if e.dxftype()=="LWPOLYLINE": out.append(("LWPOLY", [tuple(round(c,3) for c in p[:2]) for p in e.get_points()]))
        elif e.dxftype()=="LINE": out.append(("LINE", (round(e.dxf.start.x,3), round(e.dxf.start.y,3)), (round(e.dxf.end.x,3), round(e.dxf.end.y,3))))
    return out
for a,b in (("ARREDO","*U3"),("DIV_DIN","*U6")):
    print("="*20, a, "vs", b)
    print("  ", geo(a)); print("  ", geo(b))
for h in ("2F6","2D2","2D3","2E4","2E5","37E","37F","380","381"):
    e = db.get(h); print("stretch target", h, e.dxftype(), getattr(e.dxf,"tag",""))
for name, u in (("LETTO","*U9"),("SED","*U10")):
    blk = doc.blocks.get(name); br = blk.block_record
    graph = br.get_extension_dict().dictionary["ACAD_ENHANCEDBLOCK"]
    vp = [db.get(v) for c,v in [(t.code,t.value) for sub in graph.xtags.subclasses for t in sub] if c==360 and db.get(v).dxftype()=="BLOCKVISIBILITYPARAMETER"][0]
    tags = [(t.code,t.value) for sub in vp.xtags.subclasses for t in sub]
    states = {}; cur=None; allents=[]
    seen303=0
    for c,v in tags:
        if c==331: allents.append(v)
        if c==303: cur=v; states[cur]=[]
        elif c==332 and cur: states[cur].append(v)
    print("="*20, name, "entities listed:", len(allents), "block entities:", len(list(blk)), "states:", {k: len(v) for k,v in states.items()})
    for k,v in states.items():
        print("   ", k, "types:", {db.get(h).dxftype() for h in v}, "attdefs:", [db.get(h).dxf.tag for h in v if db.get(h).dxftype()=="ATTDEF"])
    notin = [e for e in blk if e.dxf.handle not in set(allents)]
    print("   entities not governed by visibility:", [(e.dxftype(), getattr(e.dxf,'tag','')) for e in notin])
    # anonymous block: which entities does it contain? count + does it have invisible ones?
    ub = doc.blocks.get(u)
    print("   ", u, "entities:", len(list(ub)), "invisible flag set:", sum(1 for e in ub if e.dxf.get("invisible",0)), "visible:", sum(1 for e in ub if not e.dxf.get("invisible",0)))
    print("   ", name, "invisible flag set in definition:", sum(1 for e in blk if e.dxf.get("invisible",0)))
