"""Compare the DXF structure of a dynamic block reference produced by ezdxf
with AutoCAD's own, handle-agnostic: handles are replaced by role labels."""
import ezdxf, sys, re
from ezdxf.lldxf.tagwriter import TagCollector

def dump(doc, insert_name):
    db = doc.entitydb
    ins = [i for i in doc.modelspace().query("INSERT") if i.dxf.name == insert_name][0]
    ub = doc.blocks.get(insert_name)
    roles = {}
    def role(h, name):
        roles.setdefault(h, name)
    role(ins.dxf.handle, "INSERT"); role(ub.block_record.dxf.handle, "U_RECORD"); role(ub.block.dxf.handle, "U_BLOCK")
    role(doc.modelspace().block_record.dxf.handle, "MSP")
    defrec = db.get([t.value for t in ub.block_record.get_xdata("AcDbBlockRepBTag") if t.code == 1005][0])
    role(defrec.dxf.handle, "DEF_RECORD")
    for i, e in enumerate(ub):
        role(e.dxf.handle, f"U_ENT{i}")
    for i, a in enumerate(ins.attribs):
        role(a.dxf.handle, f"ATTRIB_{a.dxf.tag}")
    objs = []
    def walk(o, path):
        objs.append((path, o))
        role(o.dxf.handle, path)
        if o.dxftype() == "DICTIONARY":
            for k, v in o.items():
                walk(v, f"{path}/{k}")
        else:
            xt = getattr(o, "xtags", None)
            if xt:
                for sub in xt.subclasses:
                    for c, v in sub:
                        if c == 360 and db.get(v) is not None:
                            walk(db.get(v), f"{path}/360")
    walk(ins.get_extension_dict().dictionary, "XDICT")
    for a in ins.attribs:
        if a.has_extension_dict:
            walk(a.get_extension_dict().dictionary, f"XDICT_{a.dxf.tag}")
    def fmt(o):
        tags = TagCollector.dxftags(o, dxfversion=doc.dxfversion)
        out = []
        for c, v in tags:
            if c in (5, 105) or (320 <= c < 370) or c == 1005:
                v = roles.get(v, "?" + str(v))
            if isinstance(v, float): v = round(v, 4)
            out.append(f"{c}={v}")
        return " ".join(out)
    lines = []
    lines.append("INSERT: " + fmt(ins))
    lines.append("U_RECORD: " + fmt(ub.block_record))
    lines.append("U_BLOCK: " + fmt(ub.block))
    for path, o in objs:
        lines.append(f"{path} [{o.dxftype()}]: " + fmt(o))
    for i, e in enumerate(ub):
        lines.append(f"U_ENT{i} [{e.dxftype()}]: " + fmt(e))
    for a in ins.attribs:
        lines.append(f"ATTRIB {a.dxf.tag}: " + fmt(a))
    return lines

master = ezdxf.readfile(sys.argv[1]); mine = ezdxf.readfile(sys.argv[2])
a = dump(master, sys.argv[3]); b = dump(mine, sys.argv[4])
import difflib
for line in difflib.unified_diff(a, b, "autocad", "ezdxf", lineterm="", n=0):
    print(line[:400])
