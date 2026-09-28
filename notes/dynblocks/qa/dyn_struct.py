import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1]); db = doc.entitydb
def tags(o):
    if hasattr(o,"xtags"): return [(t.code,t.value) for sub in o.xtags.subclasses for t in sub]
    return None
print("## owners of purge preventers")
for o in doc.objects:
    if o.dxftype()=="ACDB_DYNAMICBLOCKPURGEPREVENTER_VERSION":
        t = tags(o); owner = db.get(o.dxf.owner)
        print("  ", o.dxf.handle, "owner", o.dxf.owner, owner.dxftype() if owner else None, t[:12])
        break
print("## TAVOLO block record: reactors, ext dict raw, block entity ext dict")
br = doc.blocks.get("TAVOLO").block_record
print("  reactors", br.reactors, "extdict handle", br.get_extension_dict().handle)
xd = br.get_extension_dict().dictionary
print("  extdict dict raw: hard_owned", xd.dxf.get("hard_owned"), "items", list(xd.keys()))
print("  eval graph owner", db.get("2E9").dxf.owner)
blk = doc.blocks.get("TAVOLO")
print("  BLOCK entity extdict?", blk.block.has_extension_dict, "ENDBLK extdict?", blk.endblk.has_extension_dict)
print("## *U7 block record + INSERT dicts")
ins = [i for i in doc.modelspace().query("INSERT") if i.dxf.name=="*U7"][0]
xd = ins.get_extension_dict()
d = xd.dictionary
print("  INSERT extdict hard_owned", d.dxf.get("hard_owned"), "reactors of INSERT", ins.reactors)
rep = d["AcDbBlockRepresentation"]; print("  AcDbBlockRepresentation hard_owned", rep.dxf.get("hard_owned"), "owner", rep.dxf.owner)
repdata = rep["AcDbRepData"]; print("  AcDbRepData", tags(repdata))
cache = rep["AppDataCache"]; print("  AppDataCache hard_owned", cache.dxf.get("hard_owned"))
ebd = cache["ACAD_ENHANCEDBLOCKDATA"]; print("  ENHANCEDBLOCKDATA hard_owned", ebd.dxf.get("hard_owned"))
xr = ebd["21"]; print("  xrecord 21 owner", xr.dxf.owner, "cloning", xr.dxf.get("cloning"), [(t.code,t.value) for t in xr.tags])
ub = doc.blocks.get("*U7")
print("  *U7 block flags", ub.block.dxf.flags, "record flags", ub.block_record.dxf.all_existing_dxf_attribs())
for e in ub:
    if e.dxftype()=="ATTDEF" and e.has_extension_dict: print("  *U7 ATTDEF", e.dxf.tag, "extdict keys", list(e.get_extension_dict().dictionary.keys()))
print("## ATTRIB field dict structure")
a = [a for a in ins.attribs if a.dxf.tag=="LARGHEZZA"][0]
fd = a.get_extension_dict().dictionary
print("  ATTRIB extdict hard_owned", fd.dxf.get("hard_owned"), "ACAD_FIELD dict hard_owned", fd["ACAD_FIELD"].dxf.get("hard_owned"))
f = fd["ACAD_FIELD"]["TEXT"]; print("  TEXT field:", tags(f))
child = db.get([v for c,v in tags(f) if c==360][0]); print("  child field:", tags(child))
print("## classes in source relevant")
for c in doc.classes:
    n = c.dxf.name
    if any(k in n for k in ("BLOCK","EVAL","FIELD","DYNAMIC","REPRESENTATION","XRECORD")):
        print("  ", n, c.dxf.cpp_class_name, "app", c.dxf.app_name, "proxy", c.dxf.flags, "was_a_proxy", c.dxf.was_a_proxy, "is_an_entity", c.dxf.is_an_entity)
print("## appids", [a.dxf.name for a in doc.appids])
