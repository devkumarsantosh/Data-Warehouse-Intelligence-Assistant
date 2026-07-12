from cdwia.common.models import QueryPath
from cdwia.control_plane.classifier import DeterministicClassifier
from cdwia.control_plane.planner import should_invoke_planner


def test_clear_sql_question_classified_high_confidence():
    result = DeterministicClassifier().classify("Which team spent the most on EC2 last month?")
    assert result.path == QueryPath.SQL
    assert result.confidence >= 0.75


def test_clear_document_question_classified_high_confidence():
    result = DeterministicClassifier().classify("What is our tagging policy for S3 buckets?")
    assert result.path == QueryPath.DOCUMENT
    assert result.confidence >= 0.75


def test_hybrid_question_flagged_for_llm_fallback():
    result = DeterministicClassifier().classify(
        "Our storage cost increased by 45%. How can we optimize it?"
    )
    assert result.path == QueryPath.HYBRID
    assert should_invoke_planner(result) is True


def test_ambiguous_question_defaults_to_low_confidence_hybrid():
    result = DeterministicClassifier().classify("Tell me something interesting")
    assert result.confidence < 0.75
    assert should_invoke_planner(result) is True
