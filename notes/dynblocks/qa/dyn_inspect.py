import ezdxf, sys
from ezdxf import dynblkhelper as dbh
from collections import Counter
doc = ezdxf.readfile(sys.argv[1])
print("version", doc.dxfversion, "acad", doc.header.get("$ACADVER"))
msp = doc.modelspace()
print("modelspace entities", Counter(e.dxftype() for e in msp))
print("object types", Counter(o.dxftype() for o in doc.objects))
print()
print("=== BLOCK RECORDS ===")
for blk in doc.blocks:
    br = blk.block_record
    name = blk.name
    if name.startswith("*Model") or name.startswith("*Paper"): continue
    dyn = dbh.is_dynamic_block_definition(br)
    rep = dbh.get_dynamic_block_record_handle(br)
    truename = ""
    try:
        truename = br.get_xdata("AcDbDynamicBlockTrueName").get_first_value(1000, "")
    except Exception: pass
    ents = Counter(e.dxftype() for e in blk)
    attdefs = [e.dxf.tag for e in blk if e.dxftype()=="ATTDEF"]
    xd = br.has_extension_dict
    print(f"{name:30s} dyn={dyn!s:5} rep->{rep or '-':6} truename={truename!r} extdict={xd} ents={dict(ents)} attdefs={attdefs}")
print()
print("=== INSERTS in modelspace ===")
for ins in msp.query("INSERT"):
    d = dbh.get_dynamic_block_definition(ins)
    attrs = {a.dxf.tag: a.dxf.text for a in ins.attribs}
    print(f"{ins.dxf.name:20s} -> dyn def {d.name if d else None:20s} layer={ins.dxf.layer} pos={tuple(round(c,1) for c in ins.dxf.insert)} rot={ins.dxf.rotation} scale=({ins.dxf.xscale},{ins.dxf.yscale}) extdict={ins.has_extension_dict} attribs={attrs}")
