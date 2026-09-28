import ezdxf, sys
from ezdxf.lldxf.tags import Tags
doc = ezdxf.readfile(sys.argv[1])
db = doc.entitydb
def raw(o):
    # DXFTagStorage keeps raw tags in .xtags
    if hasattr(o, "xtags"):
        return [(t.code, t.value) for sub in o.xtags.subclasses for t in sub]
    return None
def show(o, indent=""):
    print(f"{indent}[{o.dxf.handle}] {o.dxftype()}")
    r = raw(o)
    if r:
        for c,v in r:
            if c in (0,5,330,100,102): 
                if c==330: print(f"{indent}   {c} {v}")
                continue
            print(f"{indent}   {c} {v!r}")
for name in sys.argv[2:]:
    blk = doc.blocks.get(name)
    br = blk.block_record
    print("="*20, name, "block_record", br.dxf.handle)
    for tag in ("AcDbDynamicBlockGUID","AcDbDynamicBlockTrueName","AcDbBlockRepBTag"):
        try: print("  xdata", tag, [(t.code,t.value) for t in br.get_xdata(tag)])
        except Exception: pass
    if br.has_extension_dict:
        xd = br.get_extension_dict()
        for k, v in xd.dictionary.items():
            print("  extdict key", k, "->", v.dxftype(), v.dxf.handle)
            if v.dxftype()=="DICTIONARY":
                for k2,v2 in v.items():
                    print("     ", k2, "->", v2.dxftype(), v2.dxf.handle)
                    show(v2, "        ")
            else:
                show(v, "     ")
    # entities: which have fields / extdict
    for e in blk:
        if e.has_extension_dict:
            print("  entity", e.dxftype(), e.dxf.handle, getattr(e.dxf,"tag",""), "extdict:", [(k, v.dxftype()) for k,v in e.get_extension_dict().dictionary.items()])
