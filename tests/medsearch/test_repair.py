from __future__ import annotations

import json
from pathlib import Path

from med_lit_mcp.medsearch.cli import _create_plan, build_parser
from med_lit_mcp.medsearch.models import Question
from med_lit_mcp.medsearch.query import compile_strategy
from med_lit_mcp.medsearch.repair import repair_question


def question(population: list[dict], intervention: list[dict] | None = None) -> Question:
    return Question.from_dict(
        {
            "schema_version": "2",
            "framework": "PICO",
            "question": "Does CGM improve HbA1c in adults with type 2 diabetes?",
            "components": {
                "population": {"groups": population},
                "intervention": {"groups": intervention or [
                    {"label": "CGM", "text": "continuous glucose monitoring", "resolved_mesh": ["Continuous Glucose Monitoring"]}
                ]},
            },
        }
    )


def pubmed(q: Question) -> str:
    return compile_strategy(q, mode="quick", limit_per_source=10, sources=["pubmed"]).strategies["pubmed"].query


def test_an_age_beside_a_condition_moves_to_an_age_group() -> None:
    q = question([{
        "label": "adults with T2D", "text": "adults with type 2 diabetes",
        "synonyms": ["type 2 diabetes", "adult-onset diabetes"],
        "candidate_mesh": ["Diabetes Mellitus, Type 2", "Adult"],
        "resolved_mesh": ["Diabetes Mellitus, Type 2", "Adult"],
    }])
    notes, unrepairable = repair_question(q)
    assert (unrepairable, len(notes)) == ([], 1)
    condition, age = q.components["population"].groups
    assert condition.resolved_mesh == ["Diabetes Mellitus, Type 2"]
    assert "adult-onset diabetes" in condition.synonyms  # an age-qualified term stays with its condition
    assert (age.label, age.text, age.synonyms, age.resolved_mesh) == ("age", "adults", ["adult"], ["Adult"])
    query = pubmed(q)
    assert '("Adult"[Mesh] OR "adults"[tiab] OR "adult"[tiab])' in query
    assert '"Diabetes Mellitus, Type 2"[Mesh] OR "adults with type 2 diabetes"[tiab]' in query


def test_an_age_group_as_drafted_is_left_alone() -> None:
    drafted = [
        {"label": "population", "text": "adults", "synonyms": ["adult", "adults with diabetes"], "resolved_mesh": ["Adult"]},
        {"label": "condition", "text": "type 2 diabetes"},
    ]
    q = question(drafted)
    assert repair_question(q) == ([], [])
    assert [group.to_dict() for group in q.components["population"].groups] == [
        group.to_dict() for group in question(drafted).components["population"].groups
    ]


def test_moved_ages_join_an_existing_age_group() -> None:
    q = question([
        {"label": "condition", "text": "type 2 diabetes", "synonyms": ["older adults"]},
        {"label": "elderly", "text": "aged", "resolved_mesh": ["Aged"]},
    ])
    repair_question(q)
    groups = q.components["population"].groups
    assert [group.label for group in groups] == ["condition", "elderly"]
    assert groups[1].synonyms == ["older adults"]


def test_sentence_like_terms_are_dropped_when_the_group_has_others() -> None:
    q = question(
        [{"label": "students", "text": "medical students"}],
        [{"label": "task", "text": "student interviews (history-taking practice, OSCE-style interviews)",
          "synonyms": ["history-taking", "clinical interview"]}],
    )
    notes, unrepairable = repair_question(q)
    task = q.components["intervention"].groups[0]
    assert (task.text, task.synonyms, unrepairable) == ("history-taking", ["clinical interview"], [])
    assert "too long to match as a phrase" in notes[0]
    alone = question([{"label": "students", "text": "medical or health-professions students and trainees"}])
    assert repair_question(alone)[1] == ["population/students"]


def test_planning_repairs_a_saved_question(tmp_path: Path) -> None:
    saved = question([{
        "label": "adults with T2D", "text": "type 2 diabetes",
        "candidate_mesh": ["Diabetes Mellitus, Type 2", "Adult"], "resolved_mesh": ["Diabetes Mellitus, Type 2", "Adult"],
    }])
    path = tmp_path / "question.json"
    path.write_text(json.dumps(saved.to_dict()))
    args = build_parser().parse_args(
        ["plan", str(path), "--no-mesh", "--sources", "pubmed", "--output", str(tmp_path / "run")]
    )
    import asyncio

    _, strategy = asyncio.run(_create_plan(args, mode="quick"))
    assert '("Adult"[Mesh] OR "adults"[tiab] OR "adult"[tiab])' in strategy.strategies["pubmed"].query
    assert any("moved" in warning and "age group" in warning for warning in strategy.warnings)
    assert [group.label for group in strategy.question.components["population"].groups] == ["adults with T2D", "age"]
