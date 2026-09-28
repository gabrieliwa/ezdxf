# ezdxf — fork Euclide (branch `dynblocks`)

Fork di [mozman/ezdxf](https://github.com/mozman/ezdxf) usata dal converter DXF di
at-backend (`tools/dxf-converter`). Upstream non supporta i blocchi dinamici di
AutoCAD e non li accetterà mai: la nostra aggiunta vive in
`src/ezdxf/addons/dynblocks/` (vedi il suo `README.md`) e resta nel fork.

## Ambiente

- `.venv` (Python 3.13, installazione editable): `.venv/bin/python -m pytest tests -q`.
- 19 test upstream in `tests/test_03_dxf_layouts/test_309_query_parser.py` falliscono
  già prima delle nostre modifiche (pyparsing): ignorarli.
- Tenere allineato con upstream: `git fetch upstream && git rebase upstream/master`
  (remote `upstream` già configurato). Le modifiche fuori da `addons/dynblocks/`
  sono poche e chirurgiche: `addons/importer.py` (non copia più l'extension dict
  che poi butta via) e `audit.py` (APPID non dichiarati).

## Regole imparate a caro prezzo (AutoCAD scarta il file, non avvisa)

1. **Ogni APPID usato in un XDATA deve esistere nella tabella APPID.** Altrimenti
   AutoCAD legge "premature end of object" e scarta l'intero disegno. Chi clona
   oggetti o copia XDATA deve dichiararli (`doc.appids.add`); `doc.audit()` ora
   li dichiara da solo (`AuditError.UNDEFINED_APPID`). Chiamare sempre
   `doc.audit()` prima di salvare un file destinato ad AutoCAD.
2. **La vista iniziale**: `ezdxf.new()` lascia il VPORT `*Active` sull'origine
   con altezza 1000: il file "si apre vuoto". Prima di salvare, centrare la vista
   e registrare le estensioni (`doc.set_modelspace_vport(height, center)`,
   `msp.reset_extents`, `msp.reset_limits`), come fa `frame_drawing()` nel
   converter (GAB-1513).
3. **Oggetti sconosciuti** (parametri dinamici, FIELD, …) sono `DXFTagStorage`:
   `entity.copy()` fallisce e `Importer` scarta extension dict e XDATA. Per
   portarli in un altro documento usare `addons.dynblocks.rawclone.clone_objects`
   (esporta i tag, rimappa gli handle, ricarica), mai copiare a mano.
4. **CLASSES**: ogni tipo di oggetto non standard scritto nel file deve avere la
   sua voce nella sezione CLASSES (`dynblocks.copy.ensure_class`).
5. Verifica di riferimento: le istanze salvate da AutoCAD in un master (blocchi
   `*U`) sono la verità; `tests/test_10_dynblocks` confronta la nostra
   valutazione con quelle. Per un confronto tag per tag usare gli script in
   `~/projects/ezdxf-dynblocks/qa/` (`structdiff.py`, `defdiff.py`).
6. Prova finale solo in AutoCAD (Gab): apertura, grip, tendine, REGEN,
   RESETBLOCK. Nessuna libreria aperta può validare i blocchi dinamici.
