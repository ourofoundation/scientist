"""Explorer candidate loading against a fake Ouro client."""

from scientist.computational.explorer import SystemExplorer


def meta(sid, formula, e_hull, **extra):
    return {
        "filename": f"{sid}.cif",
        "id": sid,
        "formula": formula,
        "e_above_hull": e_hull,
        "num_atoms": 4,
        "space_group_number": 123,
        **extra,
    }


class FakeOuro:
    def __init__(self):
        self.exports = []

    def explore_chemical_system(self, **kwargs):
        return {
            "summary": {
                "run_phases": [{"structure_id": "run1"}, {"structure_id": "run2"}],
                "num_successful": 2,
            },
            "action_id": "act",
            "report": {"id": "post1"},
        }

    def export_candidates(self, system, structure_ids=None, **kwargs):
        self.exports.append(structure_ids)
        if structure_ids:
            structures = [
                meta("run2", "B2CoFe3", 0.03),
                meta("run1", "B4Co2Fe6", 0.01, equivalence_group="run1"),
            ]
        else:
            structures = [
                meta("known", "Fe2B", 0.0),
                meta("run1", "B4Co2Fe6", 0.01),
                meta("rep", "CoFe", 0.002, equivalents=[{"id": "run2"}]),
            ]
        return {
            "structures": structures,
            "cifs": {f"cifs/{m['filename']}": "data_x" for m in structures},
        }


def test_run_phases_come_first_and_are_labelled():
    ouro = FakeOuro()
    exploration = SystemExplorer(ouro).explore("fe-co-b")

    assert ouro.exports == [["run1", "run2"], None]
    got = [(c["formula"], c["origin"]) for c in exploration.candidates]
    assert got == [
        ("B4Co2Fe6", "this exploration"),
        ("B2CoFe3", "this exploration"),
        # Known structure whose equivalent cell this run reproduced.
        ("CoFe", "this exploration"),
        ("Fe2B", "already in GGen database"),
    ]
    assert exploration.candidates[0]["identity"] == "run1"
    assert exploration.summary["found_by_this_exploration"] == [
        "B4Co2Fe6",
        "B2CoFe3",
        "CoFe",
    ]
    assert exploration.assets == {"action_id": "act", "report": "post1"}
