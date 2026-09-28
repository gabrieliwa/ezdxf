import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1])
db = doc.entitydb
SKIP = {0,5,100,102,330}
def raw(o):
    if hasattr(o, "xtags"):
        return [(t.code, t.value) for sub in o.xtags.subclasses for t in sub if t.code not in SKIP]
    return []
def compact(o, lim=900):
    out=[]
    for c,v in raw(o):
        if isinstance(v,float): v=round(v,4)
        out.append(f"{c}={v!r}")
    return " ".join(out)[:lim]
mode = sys.argv[2]
if mode == "fields":
    for h in sys.argv[3:]:
        o = db.get(h); print(f"[{h}] {o.dxftype()}: {compact(o, 2000)}")
elif mode == "nodes":
    for name in sys.argv[3:]:
        blk = doc.blocks.get(name); br = blk.block_record
        print("="*30, name)
        graph = br.get_extension_dict().dictionary["ACAD_ENHANCEDBLOCK"]
        for c,v in raw(graph):
            if c==360:
                o = db.get(v); print(f"  NODE [{v}] {o.dxftype()}: {compact(o, 1500)}")
        for e in blk:
            if e.dxftype()=="ATTDEF":
                fld = ""
                if e.has_extension_dict and "ACAD_FIELD" in e.get_extension_dict().dictionary: fld=" [FIELD]"
                print(f"  ATTDEF {e.dxf.tag}: default={e.dxf.text!r}{fld}")
elif mode == "insert":
    msp = doc.modelspace()
    for ins in msp.query("INSERT"):
        print("="*30, "INSERT", ins.dxf.name, ins.dxf.handle)
        xd = ins.get_extension_dict().dictionary
        def walk(d, ind="  "):
            for k,v in d.items():
                print(f"{ind}{k} -> {v.dxftype()} [{v.dxf.handle}]" + ("" if v.dxftype()=="DICTIONARY" else ": "+compact(v, 700)))
                if v.dxftype()=="DICTIONARY": walk(v, ind+"   ")
        walk(xd)
        # anonymous block record
        br = doc.blocks.get(ins.dxf.name).block_record
        print("  *U block_record xdata AcDbBlockRepBTag:", [(t.code,t.value) for t in br.get_xdata("AcDbBlockRepBTag")])
        if br.has_extension_dict:
            print("  *U block_record extdict:"); walk(br.get_extension_dict().dictionary, "     ")
        # attribs with fields?
        for a in ins.attribs:
            if a.has_extension_dict:
                print(f"  ATTRIB {a.dxf.tag} extdict keys:", list(a.get_extension_dict().dictionary.keys()))
