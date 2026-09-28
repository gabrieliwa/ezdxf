import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1])
db = doc.entitydb
SKIP = {0,5,100,102,330}
def raw(o):
    if hasattr(o, "xtags"):
        return [(t.code, t.value) for sub in o.xtags.subclasses for t in sub if t.code not in SKIP]
    return []
def compact(o):
    out=[]; 
    for c,v in raw(o):
        if isinstance(v,float): v=round(v,4)
        out.append(f"{c}={v!r}")
    return " ".join(out)
def graph_nodes(graph):
    tags = raw(graph)
    return [v for c,v in tags if c==360]
for name in sys.argv[2:]:
    blk = doc.blocks.get(name); br = blk.block_record
    print("="*30, name)
    xd = br.get_extension_dict()
    graph = xd.dictionary["ACAD_ENHANCEDBLOCK"]
    for h in graph_nodes(graph):
        o = db.get(h)
        print(f"  NODE [{h}] {o.dxftype()}: {compact(o)}")
    # ATTDEF fields
    for e in blk:
        if e.dxftype()=="ATTDEF" and e.has_extension_dict:
            d = e.get_extension_dict().dictionary
            for k,v in d.items():
                print(f"  ATTDEF {e.dxf.tag} extdict {k} -> {v.dxftype()} [{v.dxf.handle}]")
                if v.dxftype()=="DICTIONARY":
                    for k2,v2 in v.items():
                        print(f"      {k2} -> {v2.dxftype()} [{v2.dxf.handle}]: {compact(v2)[:600]}")
        elif e.dxftype()=="ATTDEF":
            pass
    # ATTDEF text values (default) to detect field markers
    for e in blk:
        if e.dxftype()=="ATTDEF":
            print(f"  ATTDEF {e.dxf.tag}: default={e.dxf.text!r} prompt={e.dxf.prompt!r} pos={tuple(round(c,2) for c in e.dxf.insert)} h={e.dxf.height}")
