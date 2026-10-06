# Blocchi dinamici AutoCAD — analisi e piano d'azione

Data: 2026-09-28. File analizzato: `~/Desktop/MASTER-AT_Blocchi-dinamici_Ca-Foscari.dxf` (AC1032, 5 definizioni dinamiche, 5 istanze piazzate).

## 1. Cosa c'è nel DXF

### Tipo A — "dimensionali" (ARREDO, TAVOLO, DIV_DIN)

| Blocco  | Parametri lineari (etichetta → default)                    | Attributi collegati (FIELD)       |
|---------|------------------------------------------------------------|-----------------------------------|
| ARREDO  | Larghezza Armadio 1.00 · Profondità Armadio 0.40           | LARGHEZZA, PROFONDITA             |
| TAVOLO  | Larghezza Tavolo 1.90 (2.0 di grip) · Profondità Tavolo 0.60 | LARGHEZZA, PROFONDITA           |
| DIV_DIN | Larghezza Divano 0.80 · Profondità Divano 0.60             | solo LARGHEZZA (PROFONDITA no!)   |

Struttura, per ogni parametro:
- `BLOCKLINEARPARAMETER`: punto base, punto finale, etichetta, distanza di default.
- `BLOCKSTRETCHACTION`: cornice di stiramento + elenco delle entità e degli **indici dei vertici** che si spostano con il parametro (es. TAVOLO: polilinea, vertici 1-4; ARREDO: anche gli ATTDEF CODICE_VANO e DESCRIZIONE). È tutto interpretabile: lo stiramento è "sposta quei vertici di (nuova distanza − default) lungo la direzione del parametro".
- L'attributo LARGHEZZA/PROFONDITA è un **FIELD** `Object(...).Distance` → sull'istanza diventa `Parameter(N).UpdatedDistance`, formato `%lu2%pr2` (2 decimali). È un legame **a senso unico**: parametro → attributo, aggiornato al REGEN. Confermato il comportamento descritto da Gab: cambiare l'attributo non muove nulla.

### Tipo B — "a tendina" (LETTO, SED)

| Blocco | Parametro visibilità (etichetta) | Stati                              | Tabella di consultazione (lookup)        | Attributo "gemello" (NON collegato)          |
|--------|----------------------------------|------------------------------------|------------------------------------------|----------------------------------------------|
| LETTO  | Tipologia                        | Singolo (30 ent.) · Doppio (42)    | SINGOLO→Singolo · DOPPIO→Doppio          | TIPOLOGIA_LETTO, prompt `SIN=Singolo/DOP=Doppio` |
| SED    | TIPOLOGIA                        | 1_Sgabello (11) · 2_Sedia (19) · 3_Ufficio (168) | 1→1_Sgabello · 2→2_Sedia · 3→3_Ufficio | TIPOLOGIA_INGOMBRO, prompt `1=SGABELLO/2=SEDIA/3=SEDIA_UFFICIO` |

Come funziona in AutoCAD: nella palette Proprietà (o dal grip a triangolo) compaiono **due tendine**: lo stato di visibilità e la proprietà di lookup ("Controllo dinamico1" / "TIPO_SEDUTA"). Scegliere una voce nasconde tutte le entità che non stanno in quello stato. L'attributo TIPOLOGIA_* è un testo separato: **nessun legame** con la tendina in nessuna direzione. Il disegnatore ha però allineato i codici (SED: 1/2/3 = codici del prompt; LETTO invece usa SINGOLO/DOPPIO nella tabella ma SIN/DOP nel prompt: incoerenza da segnalare).

### Come AutoCAD salva un'istanza piazzata (la chiave di tutto)

Ogni INSERT nel disegno non punta a TAVOLO ma a un blocco anonimo `*U7` che contiene la **geometria già trasformata** (vertici stirati; per la visibilità: le entità nascoste hanno il flag `invisible`). Verificato: TAVOLO 1.90×0.60 → `*U7` 0.70×1.00; DIV_DIN 0.80 → `*U6` 1.50; `*U9` (LETTO) 30 visibili/32 invisibili = Singolo; `*U10` (SED) 19 visibili = 2_Sedia.

Intorno ci sono:
- XDATA `AcDbBlockRepBTag` sul BLOCK_RECORD di `*U7` → handle della definizione TAVOLO;
- extension dict dell'INSERT → `AcDbBlockRepresentation` → `AcDbRepData` (handle definizione) + `ACAD_ENHANCEDBLOCKDATA` → un XRECORD per nodo con il valore corrente (2 punti per un parametro lineare, la stringa dello stato per la visibilità/lookup);
- gli ATTRIB LARGHEZZA/PROFONDITA con il FIELD e il valore in cache ("0.70").

Quindi AutoCAD **non ricalcola niente** all'apertura: legge il `*U` cotto. Se produciamo noi lo stesso pacchetto, AutoCAD dovrebbe vedere un'istanza dinamica vera, con grip e tendine funzionanti. Da verificare in AutoCAD (vedi rischi).

## 2. Cosa fa oggi la nostra pipeline

- **Import master** (`at-backend/src/modules/masters/dxf-meta.ts`, `parseDxfBlocks`): taglia il DXF a testo, tiene solo `BLOCK…ENDBLK` dei blocchi con nome, **butta via la sezione OBJECTS e i `*U`**. Tutta la parte dinamica sparisce; restano solo geometria di default, flag invisible dello stato salvato e ATTDEF (tag/prompt/default). Attributi creati tutti TEXT/USER.
- **Export** (`convert_survey_export.py`): un INSERT per asset, scala uniforme, blocco copiato con `Importer` (che comunque scarta extension dict e XDATA), ATTRIB riempiti dai `parameters[]` dell'export JSON. I numeri arrivano come `"1.5"`, mai `"1.50"`; nessuna unità.
- **App**: la geometria del blocco viene parsata sul tablet dal testo DXF in primitive JSON (`polyline/arc/circle/text…`) e disegnata una volta per famiglia con una sola trasformazione affine. Nessuna dimensione per asset. La libreria degli schemi funzionali sa già leggere `*U` e stati di visibilità (`domain/schematics/library/dxf.ts`), ma solo per i diagrammi.
- **Dashboard**: la planimetria disegna solo icone.

## 3. Modello proposto

Introdurre nel master, per ogni blocco, le **proprietà dinamiche** estratte dal DXF:

```
LINEAR    { id, label, base, end, defaultDistance, stretch: [{entityIndex, vertexIndices, frame}] }
VISIBILITY{ id, label, states: [{name, entityIndices}], lookup: {value → state} }
```

e nell'editor master il **legame proprietà ↔ attributo**:
- `Larghezza Tavolo` ↔ LARGHEZZA (NUMBER, unità m, 2 decimali) — suggerito in automatico dal FIELD o dal nome;
- `Tipologia` ↔ TIPOLOGIA_LETTO (ENUM con le voci degli stati; la tabella lookup dà i codici: SIN/DOP o 1/2/3).

Regola: in app il rilevatore compila **solo l'attributo**. Da lì derivano sia la geometria (app + DXF) sia il testo dell'attributo nel DXF. Il verso "parametro → attributo" di AutoCAD lo emuliamo noi scrivendo entrambi coerenti.

## 4. Piano per fasi

### Fase 0 — Decisioni (Gab)
1. Livello di fedeltà in export: **L1 "cotto"** (blocco statico per variante, funziona in ogni CAD, non più modificabile a grip) o **L2 "vivo"** (istanza dinamica vera, modificabile in AutoCAD). Proposta: L1 subito, L2 dopo su fork ezdxf, stesso motore geometrico.
2. Legame attributo ↔ proprietà: automatico + correggibile nell'editor.
3. Correzioni al master Ca' Foscari (§6).

### Fase 1 — Import master (backend + dashboard) · ~2-3 gg
- Nuovo estrattore che legge la sezione OBJECTS: parametri lineari, azioni Stira (entità + indici vertici), parametri visibilità (stati + entità), tabelle lookup. In TS riusando il parser degli schemi funzionali, oppure in Python ezdxf (già in venv). Proposta: Python, perché serve identico nel converter.
- Nuova colonna `master_blocks.dynamic jsonb`; conservare anche il DXF master intero (serve per L2).
- Wizard: gli ATTDEF con FIELD nascono NUMBER/m/2 decimali; gli stati diventano enumOptions dell'attributo gemello; pannello "Proprietà dinamiche" nella scheda famiglia con il legame.

### Fase 2 — Export L1 (Python, nessun fork) · ~2-3 gg
- Motore `dynblocks.py`: `apply_linear(block, param, distance)` (sposta i vertici elencati dall'azione Stira di Δ lungo la direzione del parametro; entità LWPOLYLINE, LINE, ARC, ATTDEF), `apply_visibility(block, state)` (rimuove o marca invisible).
- Per ogni asset: chiave variante (`TAVOLO$L0.70$P1.00`, `LETTO$Doppio`), blocco variante creato una volta, INSERT + ATTRIB con valori formattati (`0.70`).
- Test di verità: le 5 istanze del file Ca' Foscari sono il **ground truth** (`*U3/6/7/9/10`): la nostra cottura deve coincidere al millimetro.

### Fase 3 — App (mobile) · ~3-4 gg
- Stesso motore in TS sulle primitive già parsate (stretch = sposta vertici; visibilità = filtra).
- `SkBlockLayer`: cache dei path per **variante** invece che per famiglia; hit quad dal bbox della variante.
- Package edificio: aggiungere il descrittore dinamico ai `blocks[]`.
- Dashboard viewer: opzionale (oggi solo icone).

### Fase 4 — Export L2 "vivo" (fork ezdxf) · ~1-2 settimane + iterazioni con AutoCAD
- Fork `mozman/ezdxf` (clone in `~/projects/ezdxf`, v1.4.4) → branch `dynblocks`, installato nel venv del converter da git.
- Nel fork: copia degli oggetti "grezzi" (`DXFTagStorage.copy`) con rimappatura handle (330/331/332/340/360/1005), copia extension dict + XDATA del BLOCK_RECORD, `Importer` che porta il sottografo dinamico; helper per creare `*U` + `AcDbBlockRepresentation` + XRECORD dei valori + FIELD degli ATTRIB.
- Riferimento per i codici DXF: ACadSharp (C#, MIT), LibreDWG `dwg2.spec` (solo lettura), articolo Lazebny sul grafo di valutazione.
- Validazione: file di prova aperto in AutoCAD da Gab (grip funzionanti? REGEN stabile? RESETBLOCK ok? nessun "file corrotto"?).

### Fase 5 — Chiusura
- Fixture Ca' Foscari nei test del converter e dell'app; doc in at-roadmap; issue Linear per fase.

## 5. Rischi
- **L2 non verificabile senza AutoCAD**: nessuna libreria aperta valida il risultato; serve il loop con Gab. Se AutoCAD scarta la nostra cache, ripiego = L1 (che resta comunque corretto a video).
- Azioni non presenti nel file (Scala, Ruota, Capovolgi, Matrice, Polare): il motore parte con Stira + Visibilità + Lookup; le altre vanno aggiunte se compariranno in altri master.
- Stira su ARC/CIRCLE: gli indici vertice hanno semantica diversa (centro/raggio); gestire i casi del file, alzare warning sugli altri.
- ezdxf upstream non accetterà mai la L2: il fork va mantenuto noi (o vendorizzato come modulo esterno che usa solo API pubbliche, preferibile se basta).

## 6. Da segnalare a Gab sul master Ca' Foscari
- DIV_DIN: PROFONDITA non è collegata al parametro "Profondità Divano" (solo LARGHEZZA lo è).
- LETTO: tabella lookup SINGOLO/DOPPIO vs prompt SIN/DOP.
- LETTO piazzato su layer `0`, gli altri su `A-Arredo`.
- ARREDO: l'azione Stira sposta anche CODICE_VANO e DESCRIZIONE (voluto?).

## 7. Riferimenti
- ezdxf upstream: https://github.com/mozman/ezdxf (`src/ezdxf/dynblkhelper.py`, sola lettura).
- ACadSharp: https://github.com/DomCR/ACadSharp (`samples/dynamic-blocks`, `DynamicBlockTests.cs`).
- LibreDWG `src/dwg2.spec`; Lazebny "Mysteries of Autodesk's Caves, part 6".
- Script di ispezione usati: `~/.claude/jobs/378beeb9/tmp/dyn_*.py` (da copiare in `tools/dxf-converter/qa/` se servono).


---

## Aggiornamento 2026-09-28 (pomeriggio): decisioni di Gab e stato del fork

Decisioni: niente L1, si va diretti a **L2**; LETTO ↔ TIPOLOGIA_LETTO (SIN/DOP), SED ↔ TIPOLOGIA_INGOMBRO (1/2/3); DIV_DIN funziona su entrambe le dimensioni (l'istanza porta il FIELD anche su PROFONDITA, solo la definizione non ce l'ha). Legame attributo→parametro in AutoCAD: **non esiste in modo nativo** (gli attributi non possono pilotare i parametri; solo LISP/reactor esterni), quindi l'escamotage è il nostro "regen virtuale" in export.

Fork: `github.com/gabrieliwa/ezdxf`, branch `dynblocks`, clone in `~/projects/ezdxf` (venv `.venv`, Python 3.13, editable). Commit b2c02b9d7.

Modulo `ezdxf.addons.dynblocks`:
- `DynamicBlockDefinition.load(doc, name)`: parametri lineari, azioni Stira (entità + indici vertici), visibilità (stati → entità), lookup (tabella valore ↔ stato).
- `copy_dynamic_block(master, name, doc)`: blocco + xdata + FIELD degli ATTDEF + grafo intero (clone grezzo con rimappatura handle), CLASSES e APPID.
- `insert_dynamic_block(defn, layout, punto, DynamicBlockValues(linear=…, visibility=…, lookup=…), dxfattribs=…, attribs=…, field_bindings=…)`: blocco `*U` cotto + AcDbBlockRepresentation + XRECORD per nodo + ATTRIB con FIELD `Parameter(n).UpdatedDistance` (checksum = somma pesata dei caratteri).
- Verifica: le 5 istanze di AutoCAD del master riprodotte al micron; struttura tag-per-tag identica salvo cosmetica; audit ezdxf pulito; 21 test.

File di prova per AutoCAD: `~/Desktop/TEST-blocchi-dinamici-ezdxf.dxf` (9 istanze con valori diversi dal master + istruzioni nel disegno).

Prossimo passo (dopo l'ok di AutoCAD): integrazione nel converter at-backend: il converter deve ricevere il master DXF intero (oggi i per-block DXF sono senza OBJECTS) e una mappa famiglia → {attributo: parametro}; per ogni asset con blocco dinamico chiama `insert_dynamic_block` al posto di `add_blockref`.
