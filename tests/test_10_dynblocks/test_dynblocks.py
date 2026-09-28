#  Copyright (c) 2026, Euclide srl
#  License: MIT License
"""Dynamic block support, checked against AutoCAD 2018 ground truth: the
fixture holds five dynamic block definitions and one placed reference of each,
so the geometry AutoCAD baked into the anonymous ``*U`` blocks is the expected
result of our own evaluation.
"""
from pathlib import Path

import pytest

import ezdxf
from ezdxf import dynblkhelper
from ezdxf.math import Vec3
from ezdxf.addons import dynblocks
from ezdxf.addons.dynblocks import DynamicBlockValues as Values

FIXTURE = Path(__file__).parent / "data" / "ca_foscari.dxf"

# anonymous block of the master -> (definition, values AutoCAD used)
GROUND_TRUTH = {
    "*U7": ("TAVOLO", Values(linear={"Larghezza Tavolo": 0.7, "Profondità Tavolo": 1.0})),
    "*U6": ("DIV_DIN", Values(linear={"Larghezza Divano": 1.5})),  # depth untouched
    "*U3": ("ARREDO", Values(linear={"Larghezza Armadio": 1.0, "Profondità Armadio": 0.4})),
    "*U9": ("LETTO", Values(lookup={"Controllo dinamico1": "SINGOLO"})),
    "*U10": ("SED", Values(lookup={"TIPO_SEDUTA": "2"})),
}


@pytest.fixture(scope="module")
def master():
    return ezdxf.readfile(FIXTURE)


def geometry(block):
    out = []
    for e in block:
        t = e.dxftype()
        inv = e.dxf.get("invisible", 0)
        if t == "LWPOLYLINE":
            out.append((t, [tuple(round(c, 6) for c in p[:2]) for p in e.get_points()], inv))
        elif t == "LINE":
            out.append((t, e.dxf.start.round(6), e.dxf.end.round(6), inv))
        elif t in ("ARC", "CIRCLE"):
            out.append((t, e.dxf.center.round(6), round(e.dxf.radius, 6), inv))
        elif t == "ATTDEF":
            out.append((t, e.dxf.tag, e.dxf.insert.round(6), inv))
    return out


class TestModel:
    def test_finds_dynamic_definitions_only(self, master):
        assert set(dynblocks.dynamic_blocks(master)) == {"ARREDO", "TAVOLO", "LETTO", "DIV_DIN", "SED"}

    def test_linear_parameters_and_stretch_actions(self, master):
        d = dynblocks.DynamicBlockDefinition.load(master, "TAVOLO")
        assert d.is_supported
        assert [p.label for p in d.linear_parameters] == ["Larghezza Tavolo", "Profondità Tavolo"]
        width = d.linear_parameter("Larghezza Tavolo")
        assert width.default_distance == pytest.approx(2.0)
        actions = d.stretch_actions_of(width)
        assert len(actions) == 1
        (handle, indices), = actions[0].targets
        assert master.entitydb.get(handle).dxftype() == "LWPOLYLINE"
        assert indices == [1, 2, 3, 4]

    def test_stretch_action_can_move_attributes(self, master):
        d = dynblocks.DynamicBlockDefinition.load(master, "ARREDO")
        depth = d.linear_parameter("Profondità Armadio")
        targets = d.stretch_actions_of(depth)[0].targets
        types = [master.entitydb.get(h).dxftype() for h, _ in targets]
        assert types == ["LWPOLYLINE", "LINE", "ATTDEF", "ATTDEF"]

    def test_visibility_states_and_lookup_table(self, master):
        d = dynblocks.DynamicBlockDefinition.load(master, "SED")
        assert d.visibility is not None
        assert d.visibility.label == "TIPOLOGIA"
        assert d.visibility.state_names == ["1_Sgabello", "2_Sedia", "3_Ufficio"]
        assert [len(s.visible) for s in d.visibility.states] == [11, 19, 168]
        assert len(d.visibility.entities) == 184 == len(list(d.block))
        lookup = d.lookup_parameter("TIPO_SEDUTA")
        action = d.lookup_action_of(lookup)
        assert action.values == ["1", "2", "3"]
        assert action.state_for_value("2") == "2_Sedia"
        assert action.value_for_state("3_Ufficio") == "3"

    def test_not_a_dynamic_block(self, master):
        with pytest.raises(dynblocks.DynamicBlockError):
            dynblocks.DynamicBlockDefinition.load(master, "*U7")

    def test_guess_field_bindings_ignores_accents(self, master):
        d = dynblocks.DynamicBlockDefinition.load(master, "TAVOLO")
        assert dynblocks.guess_field_bindings(d) == {
            "LARGHEZZA": "Larghezza Tavolo",
            "PROFONDITA": "Profondità Tavolo",
        }

    def test_field_checksum_matches_autocad(self):
        assert dynblocks.field_checksum("0.70") == 497.0
        assert dynblocks.field_checksum("1.00") == 477.0
        assert dynblocks.field_checksum("####") == 350.0


class TestCopy:
    def test_copy_brings_the_evaluation_graph(self, master):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
        assert d.doc is doc
        assert dynblocks.is_dynamic_block(doc.blocks["TAVOLO"].block_record)
        assert d.guid == dynblocks.DynamicBlockDefinition.load(master, "TAVOLO").guid
        assert len(d.nodes) == 10
        # the stretch targets point at the copied entities
        width = d.linear_parameter("Larghezza Tavolo")
        handle, _ = d.stretch_actions_of(width)[0].targets[0]
        assert doc.entitydb.get(handle).dxf.owner == doc.blocks["TAVOLO"].block_record.dxf.handle
        for name in ("ACAD_EVALUATION_GRAPH", "BLOCKLINEARPARAMETER", "BLOCKSTRETCHACTION", "FIELD"):
            doc.classes.get(name)  # raises when missing
        for appid in dynblocks.model.DYNAMIC_APPIDS:
            assert doc.appids.has_entry(appid)

    def test_copy_keeps_attribute_fields(self, master):
        doc = ezdxf.new("R2010")
        dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
        attdef = {a.dxf.tag: a for a in doc.blocks["TAVOLO"].attdefs()}["LARGHEZZA"]
        field = attdef.get_extension_dict()["ACAD_FIELD"]["TEXT"]
        assert field.dxftype() == "FIELD"
        assert field.dxf.handle in doc.rootdict["ACAD_FIELDLIST"].handles

    def test_copy_is_idempotent(self, master):
        doc = ezdxf.new("R2010")
        a = dynblocks.copy_dynamic_block(master, "LETTO", doc)
        b = dynblocks.copy_dynamic_block(master, "LETTO", doc)
        assert a.block_record.dxf.handle == b.block_record.dxf.handle
        assert len([b for b in doc.blocks if b.name == "LETTO"]) == 1

    def test_copy_refuses_static_homonym(self, master):
        doc = ezdxf.new("R2010")
        doc.blocks.new("SED")
        with pytest.raises(dynblocks.DynamicBlockError):
            dynblocks.copy_dynamic_block(master, "SED", doc)


class TestInsert:
    @pytest.mark.parametrize("anon", list(GROUND_TRUTH))
    def test_evaluated_geometry_matches_autocad(self, master, anon):
        name, values = GROUND_TRUTH[anon]
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, name, doc)
        ref = dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), values)
        assert ref.dxf.name.startswith("*U")
        assert geometry(doc.blocks[ref.dxf.name]) == geometry(master.blocks[anon])

    def test_reference_is_linked_to_the_definition(self, master, tmp_path):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
        ref = dynblocks.insert_dynamic_block(
            d, doc.modelspace(), (1, 2), Values(linear={"Larghezza Tavolo": 0.7}),
            dxfattribs={"rotation": 90},
            attribs={"CODICE_VANO": "001"},
            field_bindings=dynblocks.guess_field_bindings(d),
        )
        path = tmp_path / "out.dxf"
        doc.saveas(path)
        doc2 = ezdxf.readfile(path)
        assert len(doc2.audit().errors) == 0
        ref2 = doc2.modelspace().query("INSERT").first
        assert dynblkhelper.get_dynamic_block_definition(ref2).name == "TAVOLO"
        xdict = ref2.get_extension_dict()
        rep = xdict["AcDbBlockRepresentation"]
        assert rep["AcDbRepData"].dxftype() == "ACDB_BLOCKREPRESENTATION_DATA"
        data = rep["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        assert sorted(data.keys(), key=int) == ["21", "28", "29", "36"]
        width_record = data["21"]
        points = [Vec3(v) for c, v in width_record.tags if c == 10]
        assert (points[1] - points[0]).magnitude == pytest.approx(0.7)
        attribs = {a.dxf.tag: a for a in ref2.attribs}
        assert attribs["LARGHEZZA"].dxf.text == "0.70"
        assert attribs["PROFONDITA"].dxf.text == "0.60"  # default depth
        assert attribs["CODICE_VANO"].dxf.text == "001"
        field = attribs["LARGHEZZA"].get_extension_dict()["ACAD_FIELD"]["TEXT"]
        child = doc2.entitydb.get([v for c, v in dynblocks.model.raw_tags(field) if c == 360][0])
        assert "Parameter(21).UpdatedDistance" in dynblocks.model.raw_tags(child).get_first_value(2)

    def test_visibility_state_from_lookup_value(self, master):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "LETTO", doc)
        ref = dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), Values(lookup={"Controllo dinamico1": "DOPPIO"}))
        block = doc.blocks[ref.dxf.name]
        assert sum(1 for e in block if not e.dxf.get("invisible", 0)) == 42
        data = ref.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        assert data["1"].tags.get_first_value(1) == "Doppio"
        assert data["11"].tags.get_first_value(1) == "DOPPIO"

    def test_visibility_state_by_name(self, master):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "SED", doc)
        ref = dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), Values(visibility="3_Ufficio"))
        data = ref.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]
        assert data["2"].tags.get_first_value(1) == "3_Ufficio"
        assert data["17"].tags.get_first_value(1) == "3"  # derived lookup value

    def test_unknown_parameter_raises(self, master):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
        with pytest.raises(dynblocks.DynamicBlockError):
            dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), Values(linear={"Altezza": 1}))

    def test_two_references_share_the_definition(self, master):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(master, "TAVOLO", doc)
        a = dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), Values(linear={"Larghezza Tavolo": 1.2}))
        b = dynblocks.insert_dynamic_block(d, doc.modelspace(), (5, 0), Values(linear={"Larghezza Tavolo": 1.8}))
        assert a.dxf.name != b.dxf.name
        assert dynblkhelper.get_dynamic_block_definition(a) is dynblkhelper.get_dynamic_block_definition(b)
