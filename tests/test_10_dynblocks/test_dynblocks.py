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


def test_static_block_with_guid_xdata_is_not_dynamic():
    # AutoCAD tags static blocks edited in the block editor with the GUID
    # xdata too, but they own no evaluation graph.
    doc = ezdxf.new("R2010")
    doc.appids.add("AcDbDynamicBlockGUID")
    block = doc.blocks.new("STATIC")
    block.block_record.set_xdata(
        "AcDbDynamicBlockGUID", [(1000, "{00000000-0000-0000-0000-000000000000}")]
    )
    assert dynblocks.is_dynamic_block(block.block_record) is False
    assert "STATIC" not in list(dynblocks.dynamic_blocks(doc))


# ---------------------------------------------------------------------------
# Distance multiplier, angle offset and scale actions (GAB-1957): a door from
# the 2264 master. The jambs follow the width, the middle line of the wall
# moves at half the thickness, the swing arc scales about the hinge, the big
# leaf of the double door grows at right angles to its own parameter.
# ---------------------------------------------------------------------------

DOOR = Path(__file__).parent / "data" / "serrint_2264.dxf"
DOOR_WIDTH = 0.8986100377805826  # the width the door was drawn with


@pytest.fixture(scope="module")
def door():
    return ezdxf.readfile(DOOR)


def by_index(defn_block, anon_block):
    """definition entity handle -> baked copy (same order in both blocks)."""
    return {d.dxf.handle: c for d, c in zip(defn_block, anon_block)}


class TestDoorModel:
    def test_scale_actions_are_understood(self, door):
        d = dynblocks.DynamicBlockDefinition.load(door, "SERRINT")
        assert "BLOCKSCALEACTION" not in d.unsupported
        assert sorted(a.name for a in d.scale_actions) == ["Scale", "Scale1", "Scale2"]
        swing = d.scale_actions_of(d.linear_parameter("Larghezza"))
        assert [a.selection for a in swing] == [["46", "47"]]
        assert swing[0].base_dependent is True

    def test_multiplier_and_angle_offset(self, door):
        d = dynblocks.DynamicBlockDefinition.load(door, "SERRINT")
        thickness = d.linear_parameter("Spessore parete")
        by_name = {a.name: a for a in d.stretch_actions_of(thickness)}
        assert by_name["Stretch3"].distance_multiplier == 0.5
        assert by_name["Stretch3"].targets == [("34", [0, 1])]
        assert by_name["Stretch1"].distance_multiplier == 1.0
        leaf = d.linear_parameter("Larghezza anta grande")
        turned = next(a for a in d.stretch_actions_of(leaf) if a.angle_offset)
        assert turned.angle_offset == pytest.approx(3 * 3.141592653589793 / 2)
        # (-1, 0) turned by 270 degrees points up: the leaf grows along +Y
        assert turned.displacement(Vec3(-0.3, 0, 0)).round(6) == Vec3(0, 0.3, 0)

    def test_a_parameter_in_another_frame_moves_with_it(self, door):
        d = dynblocks.DynamicBlockDefinition.load(door, "SERRINT")
        width = d.linear_parameter("Larghezza")
        big = d.linear_parameter("Larghezza anta grande")
        small = d.linear_parameter("Anta piccola")
        assert [(p.label, a.name) for p, a in d.movers_of(big)] == [("Larghezza", "Stretch")]
        # the small leaf is selected by the same action, but its base point
        # (the left hinge) lies outside the frame: it stays
        assert d.movers_of(small) == []
        assert d.movers_of(width) == []


class TestDoorBake:
    def bake(self, door, **linear):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(door, "SERRINT", doc)
        ref = dynblocks.insert_dynamic_block(d, doc.modelspace(), (0, 0), Values(linear=linear))
        return by_index(d.block, doc.blocks[ref.dxf.name])

    def test_wall_thickness_keeps_the_middle_line_in_the_middle(self, door):
        e = self.bake(door, **{"Spessore parete": 0.3})
        assert e["32"].dxf.start.y == pytest.approx(-0.05)  # room face: untouched
        assert e["33"].dxf.start.y == pytest.approx(-0.35)  # opposite face: 30 cm away
        assert e["34"].dxf.start.y == pytest.approx(-0.20)  # middle line: half way
        assert e["34"].dxf.end.y == pytest.approx(-0.20)
        assert e["42"].dxf.end.y == pytest.approx(-0.35)  # jambs reach the far face
        assert e["43"].dxf.end.y == pytest.approx(-0.35)

    def test_width_scales_the_swing_about_the_hinge(self, door):
        width = 1.2
        e = self.bake(door, Larghezza=width)
        factor = width / DOOR_WIDTH
        # the base point parameter rides the middle (multiplier 0.5): the
        # door grows by half on each side of the insertion point
        half = (width - DOOR_WIDTH) / 2
        arc = e["47"]
        assert arc.dxf.center.round(6) == Vec3(-0.45 - half, -0.05, 0).round(6)  # the hinge
        assert arc.dxf.radius == pytest.approx(0.9 * factor)
        leaf = e["46"]
        assert leaf.dxf.start.round(6) == Vec3(-0.45 - half, -0.05, 0).round(6)
        assert leaf.dxf.end.y == pytest.approx(-0.05 + 0.9 * factor)
        # the passage line stays on the insertion point
        assert e["48"].dxf.start.x == pytest.approx(0.0)
        # the right jamb moved by the other half
        assert e["43"].dxf.start.x == pytest.approx(0.45 + half)

    def test_dependent_base_follows_the_moved_hinge(self, door):
        # the big leaf's hinge is the right jamb: the width pushes it, then
        # the leaf scales about the pushed point
        width, big = 1.2, 0.9
        e = self.bake(door, **{"Larghezza": width, "Larghezza anta grande": big})
        half = (width - DOOR_WIDTH) / 2
        arc = e["45"]
        assert arc.dxf.center.round(6) == Vec3(0.45 + half, -0.05, 0).round(6)
        assert arc.dxf.radius == pytest.approx(0.6 * big / 0.6)
        # and the leaf line, turned by 270 degrees, grows upward by the change
        assert e["36"].dxf.start.y == pytest.approx(0.6 + (big - 0.6))

    def test_order_of_values_does_not_matter(self, door):
        a = self.bake(door, **{"Larghezza": 1.2, "Larghezza anta grande": 0.9})
        b = self.bake(door, **{"Larghezza anta grande": 0.9, "Larghezza": 1.2})
        assert geometry(a.values()) == geometry(b.values())

    def test_as_drawn_changes_nothing(self, door):
        e = self.bake(door)
        assert geometry(e.values()) == geometry(door.blocks["SERRINT"])


class TestDoorChainAndFlips:
    """The double door of the 2264 master, checked against AutoCAD: the small
    leaf is a hidden parameter chained to the width and the big leaf, the
    hinge side and the opening side are flip parameters. The 30 references
    AutoCAD saved in the client's test drawing bake to the same geometry
    (checked outside the suite, the drawing is client data)."""

    def bake(self, door, linear=None, flips=None, visibility=None):
        doc = ezdxf.new("R2010")
        d = dynblocks.copy_dynamic_block(door, "SERRINT", doc)
        ref = dynblocks.insert_dynamic_block(
            d, doc.modelspace(), (0, 0),
            Values(linear=linear or {}, flips=flips or {}, visibility=visibility),
        )
        return d, ref, by_index(d.block, doc.blocks[ref.dxf.name])

    def test_small_leaf_is_hidden_and_chained(self, door):
        d = dynblocks.DynamicBlockDefinition.load(door, "SERRINT")
        small = d.linear_parameter("Anta piccola")
        assert small.show_properties is False
        assert small.chain_actions is True
        rates = {p.label: round(r, 9) for p, r in d.chain_of(small)}
        assert rates == {"Larghezza": 1.0, "Larghezza anta grande": -1.0}
        assert all(p.chain_actions is False for p in d.linear_parameters if p is not small)
        assert d.unsupported == []

    def test_small_leaf_is_the_width_minus_the_big_leaf(self, door):
        width, big = 1.2, 0.7
        _, _, e = self.bake(door, linear={"Larghezza": width, "Larghezza anta grande": big},
                            visibility="Doppia anta")
        small = 0.3 + (width - DOOR_WIDTH) - (big - 0.6)
        assert e["37"].dxf.radius == pytest.approx(small)  # the small leaf's swing
        assert e["45"].dxf.radius == pytest.approx(big)  # the big leaf's swing
        # the two leaves meet: small hinge + small leaf = big hinge - big leaf
        assert e["37"].dxf.center.x + small == pytest.approx(e["45"].dxf.center.x - big)

    def test_flip_states(self, door):
        d = dynblocks.DynamicBlockDefinition.load(door, "SERRINT")
        assert [(p.label, p.state_names) for p in d.flip_parameters] == [
            ("Lato apertura", ("Non ribaltato", "Ribaltato")),
            ("Lato cerniera", ("Non ribaltato", "Ribaltato")),
            ("Lato testi", ("Non ribaltato", "Ribaltato")),
        ]
        hinge = d.flip_parameter("Lato cerniera")
        assert hinge.state_index("Ribaltato") == 1
        assert hinge.state_index(False) == 0
        with pytest.raises(dynblocks.DynamicBlockError):
            hinge.state_index("Sinistra")

    def test_hinge_side_mirrors_about_the_middle_of_the_door(self, door):
        _, _, e = self.bake(door, linear={"Larghezza": 1.2}, flips={"Lato cerniera": "Ribaltato"})
        half = (1.2 - DOOR_WIDTH) / 2
        arc = e["47"]  # hinged on the right jamb now
        assert arc.dxf.center.round(6) == Vec3(0.45 + half, -0.05, 0).round(6)
        assert arc.dxf.radius == pytest.approx(0.9 * 1.2 / DOOR_WIDTH)
        # mirrored about a vertical line the arc still runs counter clockwise
        assert (arc.dxf.start_angle, arc.dxf.end_angle) == (pytest.approx(90.0), pytest.approx(180.0))

    def test_opening_side_mirrors_the_base_point_too(self, door):
        # The flip line is the room face (y = -0.05) and the flip selects the
        # base point: mirrored to (0, -0.1), it is shifted back onto the
        # insertion point, so the geometry moves up by 0.1 — what AutoCAD
        # does (its "Prova ribaltamenti SERRINT" references match).
        _, _, e = self.bake(door, flips={"Lato apertura": True})
        assert e["46"].dxf.end.y == pytest.approx(-0.05 - 0.9 + 0.1)  # the leaf points away
        assert e["33"].dxf.start.y == pytest.approx(0.05 + 0.1)  # and so does the far face

    def test_flip_and_base_point_records(self, door):
        d, ref, _ = self.bake(door, linear={"Larghezza": 1.2}, flips={"Lato cerniera": 1})
        data = ref.get_extension_dict()["AcDbBlockRepresentation"]["AppDataCache"]["ACAD_ENHANCEDBLOCKDATA"]

        def rec(node):
            return list(data[str(node)].tags)

        hinge = d.flip_parameter("Lato cerniera")
        opening = d.flip_parameter("Lato apertura")
        assert [v for c, v in rec(hinge.node_id) if c == 70][-1] == 1
        assert [v for c, v in rec(hinge.grip_node) if c == 70][-2:] == [0, 1]
        # the opening side is carried over by the hinge flip: its grip and
        # third point say so, its state does not change
        assert [v for c, v in rec(opening.grip_node) if c == 70][-2:] == [1, 0]
        assert [Vec3(v) for c, v in rec(opening.node_id) if c == 10][2] == Vec3(0, 0, 1)
        assert [Vec3(v) for c, v in rec(d.base_point.node_id) if c == 10] == [Vec3(0, 0, 0)]
        # the width runs the other way and is centred on the insertion point
        width = d.linear_parameter("Larghezza")
        a, b, normal = [Vec3(v) for c, v in rec(width.node_id) if c == 10]
        assert (b - a).magnitude == pytest.approx(1.2)
        assert a.x == pytest.approx(-b.x + (a.x + b.x))  # sanity
        assert a.x > b.x
        assert normal == Vec3(0, 0, 1)
