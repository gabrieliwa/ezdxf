"""Compare a dynamic block DEFINITION copied by ezdxf with the original."""
import ezdxf, sys, difflib
from ezdxf.lldxf.tagwriter import TagCollector

def dump(doc, name):
    db = doc.entitydb
    blk = doc.blocks.get(name); rec = blk.block_record
    roles = {rec.dxf.handle: "RECORD", blk.block.dxf.handle: "BLOCK", doc.blocks.get(name).endblk.dxf.handle: "ENDBLK"}
    ents = list(blk)
    for i, e in enumerate(ents): roles[e.dxf.handle] = f"ENT{i}"
    objs = []
    def walk(o, path):
        objs.append((path, o)); roles.setdefault(o.dxf.handle, path)
        if o.dxftype() == "DICTIONARY":
            for k, v in o.items(): walk(v, f"{path}/{k}")
        else:
            xt = getattr(o, "xtags", None)
            if xt:
                for sub in xt.subclasses:
                    for c, v in sub:
                        if c == 360 and db.get(v) is not None: walk(db.get(v), f"{path}/360")
    walk(rec.get_extension_dict().dictionary, "XDICT")
    for i, e in enumerate(ents):
        if e.has_extension_dict: walk(e.get_extension_dict().dictionary, f"XDICT_ENT{i}")
    # node ids as roles for graph nodes
    def fmt(o):
        out = []
        for c, v in TagCollector.dxftags(o, dxfversion=doc.dxfversion):
            if c in (5, 105) or (320 <= c < 370) or c == 1005: v = roles.get(v, "?" + str(v))
            if isinstance(v, float): v = round(v, 4)
            out.append(f"{c}={v}")
        return " ".join(out)
    lines = ["RECORD: " + fmt(rec), "BLOCK: " + fmt(blk.block)]
    for path, o in objs: lines.append(f"{path} [{o.dxftype()}]: " + fmt(o))
    for i, e in enumerate(ents): lines.append(f"ENT{i} [{e.dxftype()}]: " + fmt(e))
    return lines

a = dump(ezdxf.readfile(sys.argv[1]), sys.argv[3]); b = dump(ezdxf.readfile(sys.argv[2]), sys.argv[3])
n = 0
for line in difflib.unified_diff(a, b, "autocad", "ezdxf", lineterm="", n=0):
    print(line[:300]); n += 1
print("diff lines:", n)
