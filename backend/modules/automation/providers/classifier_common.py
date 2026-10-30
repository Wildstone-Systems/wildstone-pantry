"""Provider-neutral typed classifier validation and sanitized failures."""

import math

from modules.automation.classifier_contract import Answer, EvaluationRequest


class ClassifierFailure(Exception):
    def __init__(self, status: str, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def _probabilities(raw, labels: set[str]) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != labels:
        raise ValueError("probability_labels")
    if any(
        isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1 or not math.isfinite(v)
        for v in raw.values()
    ):
        raise ValueError("probability_values")
    total = sum(raw.values())
    if total <= 0 or abs(total - 1) > 0.001:
        raise ValueError("probability_sum")
    return {key: value / total for key, value in raw.items()}


def validate_answers(payload: dict, request: EvaluationRequest) -> dict:
    raw = payload.get("answers")
    if not isinstance(raw, dict) or set(raw) != set(request.questions):
        raise ValueError("questions")
    answers: dict = {}
    for key, question in request.questions.items():
        entry = raw[key]
        if not isinstance(entry, dict) or entry.get("type") != question.type:
            raise ValueError("answer_type")
        if question.type == "noul":
            if set(entry) != {"type", "noul"} or entry["noul"] is None:
                raise ValueError("answer_fields")
            answers[key] = Answer.model_validate(entry).model_dump(exclude_none=True)
            continue
        required = (
            {"type", "probabilities", "confidence", "choice"}
            if question.type == "choice"
            else {"type", "probabilities", "confidence", "score", "legend"}
        )
        if set(entry) != required:
            raise ValueError("answer_fields")
        if any(value is None for value in entry.values()):
            raise ValueError("null_answer")
        labels = (
            set(question.criteria) if question.type == "choice" else {str(i) for i in range(len(question.criteria))}
        )
        probabilities = _probabilities(entry["probabilities"], labels)
        clean = {name: value for name, value in entry.items() if name != "legend"}
        clean["probabilities"] = probabilities
        answer = Answer.model_validate(clean)
        if question.type == "choice":
            if answer.choice not in labels or probabilities[answer.choice] + 0.0001 < max(probabilities.values()):
                raise ValueError("choice")
        else:
            if entry["legend"] != {str(i): text for i, text in enumerate(question.criteria)}:
                raise ValueError("legend")
            expected = sum(int(label) * value for label, value in probabilities.items())
            if abs(answer.score - expected) > 0.001:
                raise ValueError("score")
        answers[key] = answer.model_dump(exclude_none=True)
    return answers
