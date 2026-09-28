import ezdxf, sys
doc = ezdxf.readfile(sys.argv[1]); db = doc.entitydb
def tags(o, codes=None):
    return [(t.code,t.value) for sub in o.xtags.subclasses for t in sub if (codes is None or t.code in codes)]
print("## definition-level FIELD object refs (330/331/2)")
for h in ("2FF","304"):
    print(h, tags(db.get(h), {2,330,331,360,1}))
print("## which handle is BLOCKLINEARPARAMETER Larghezza/Profondità in TAVOLO: 2EA (node 21), 2EF (node 29)")
print("## ATTDEF prompts for the tipologia attributes")
for name in ("ARREDO","TAVOLO","DIV_DIN","LETTO","SED"):
    for e in doc.blocks.get(name):
        if e.dxftype()=="ATTDEF" and e.dxf.tag.startswith("TIPOLOGIA"):
            print(f"  {name}.{e.dxf.tag}: prompt={e.dxf.prompt!r} default={e.dxf.text!r}")
print("## lookup actions with all tags")
for h in ("32D","3C9"):
    print(h, tags(db.get(h)))
print("## visibility parameter header tags (before the 331 list)")
for h in ("325","3C1"):
    print(h, [t for t in tags(db.get(h)) if t[0] not in (331,332)])
