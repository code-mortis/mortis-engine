import asyncio

import pytest

from mortis_engine.llm_http import InferenceError
from mortis_engine.review import file_policy
from mortis_engine.schemas import Usage
from mortis_engine.security_scans import policy
from mortis_engine.security_scans.inference import ask

SHA = "a" * 40


def source_text(name):
    return f"def {name}(value):\n    return value.strip()\n"


class Item:
    def __init__(self, root, names=("assigned.py", "support.py")):
        self.sha = SHA
        self.policy = file_policy.policy({})
        self.files = root / "files"
        self.files.mkdir()
        self.inventory = {}
        self.sources = {}
        self.reads = []
        for name in names:
            path = f"src/{name}"
            text = source_text(name.removesuffix(".py").replace("-", "_"))
            target = self.files / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self.inventory[path] = {"code": True}
            self.sources[path] = text

    def source(self, path, sha=None):
        self.reads.append((path, sha))
        return self.sources[path]

    @staticmethod
    def redact(value):
        return value

    @staticmethod
    def stopped():
        return False


class Session:
    def __init__(self, responses):
        self.records = {}
        self.responses = list(responses)
        self.calls = []

    async def ask(self, stage, key, **kwargs):
        self.calls.append({"stage": stage, "key": key, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, CachedResponse):
            return response.value
        if callable(response):
            response = response(kwargs)
        return kwargs["validate"](response)


class CachedResponse:
    def __init__(self, value):
        self.value = value


def options(marker=policy.SCAN_POLICY):
    return {
        "language": "English",
        "effort": "low",
        "max_output_tokens": 2048,
        "token_budget": None,
        "assessment_token_budget": None,
        "verification_token_budget": None,
        "timeout_seconds": None,
        "_scan_policy": marker,
    }


def analysis_context(item, assigned="src/assigned.py"):
    return {"assigned_path": assigned, "head_sha": SHA, "diff": "",
            "sources": {"current": {assigned: item.sources[assigned]}}}


def empty_analysis(_kwargs=None):
    return {"findings": [], "required_paths": []}


def test_v2_analysis_sends_assigned_file_and_sorted_scanner_rule_hints(tmp_path):
    item = Item(tmp_path)
    session = Session([empty_analysis])
    context = analysis_context(item)
    context["scanner_rules"] = ["Z100", "A200", "Z100"]

    result, context_paths, complete = asyncio.run(
        ask(item, session, "analysis", context, "analysis:assigned", options()))

    request = session.calls[0]
    assert request["context"]["sources"]["current"] == {"src/assigned.py": item.sources["src/assigned.py"]}
    assert request["context"]["scanner_rules"] == ["A200", "Z100"]
    assert request["instructions"] == policy.COMPACT_ANALYSIS_INSTRUCTIONS
    assert result["findings"] == []
    assert context_paths == ["src/assigned.py"]
    assert complete is True


def test_unmarked_prepared_input_uses_legacy_policy():
    assert policy.modern_scan({}, prepared=True) is False
    assert policy.modern_scan({}, prepared=False) is True
    assert policy.modern_scan({"_scan_policy": None}, prepared=False) is False
    assert policy.modern_scan({"_scan_policy": policy.SCAN_POLICY}, prepared=True) is True


def test_legacy_analysis_keeps_small_corpus_prefill(tmp_path):
    item = Item(tmp_path)
    session = Session([empty_analysis])

    asyncio.run(ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options(None)))

    sources = session.calls[0]["context"]["sources"]["current"]
    assert sorted(sources) == ["src/assigned.py", "src/support.py"]
    assert "Audit the assigned file" in session.calls[0]["instructions"]


@pytest.mark.parametrize("marker", [policy.SCAN_POLICY, None])
def test_unavailable_required_source_marks_analysis_incomplete_for_both_policies(tmp_path, marker):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        {"findings": [], "required_paths": ["src/absent.py"]},
        empty_analysis,
    ])

    result, _paths, complete = asyncio.run(
        ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options(marker)))

    assert result["findings"] == []
    assert complete is False
    assert len(session.calls) == 2
    assert session.calls[1]["context"]["validation_feedback"]["required_paths"] == ["src/absent.py"]


def test_v2_duplicate_source_request_does_not_count_as_missing(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        {"findings": [], "required_paths": ["src/assigned.py"]},
        empty_analysis,
    ])

    _result, _paths, complete = asyncio.run(
        ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options()))

    assert complete is True
    assert len(session.calls) == 2
    assert session.calls[1]["context"]["validation_feedback"]["required_paths"] == []
    assert item.reads == []


def test_known_output_limit_uses_one_distinct_key_with_same_cap_and_effort(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        InferenceError("output_limit", "limited", usage=Usage(total_tokens=2048), consumption_unknown=False),
        empty_analysis,
    ])

    result, _paths, complete = asyncio.run(
        ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options()))

    first, retry = session.calls
    assert first["stage"] == retry["stage"] == "security_analysis"
    assert first["key"] == "analysis:assigned:pass:0"
    assert retry["key"] == first["key"] + ":output-limit-recovery:v2"
    assert retry["max_output_tokens"] == first["max_output_tokens"] == 2048
    assert retry["effort"] == first["effort"] == "low"
    assert retry["instructions"] == policy.ANALYSIS_RECOVERY_INSTRUCTIONS
    assert retry["context"]["output_limit_recovery"] is True
    assert result["findings"] == []
    assert complete is True


@pytest.mark.parametrize("citation", ["source_1", "source_2", "unavailable"])
def test_analysis_recovery_keeps_a_distinct_proposal_on_a_reported_line(tmp_path, citation):
    item = Item(tmp_path)
    context = analysis_context(item)
    context["sources"]["current"]["src/support.py"] = item.sources["src/support.py"]
    context["reported_checks"] = [{"rule": "CODE-1", "title": "Missing function annotation",
                                   "start_line": 1, "end_line": 1}]
    proposal = {
        "title": "Missing validation before normalization",
        "category": "validation",
        "severity": "low",
        "trigger": "The function accepts an unchecked value.",
        "impact": "An invalid value can interrupt validation.",
        "remediation": "Validate the value before normalizing it.",
        "verification": "Check how the function handles invalid values.",
        "evidence": [{"source_id": citation, "start_line": 1, "end_line": 1}],
    }
    session = Session([
        InferenceError("output_limit", "limited", usage=Usage(total_tokens=2048), consumption_unknown=False),
        {"findings": [proposal], "required_paths": []},
    ])
    if citation != "source_1":
        error = "must cite its assigned current file" if citation == "source_2" else "source_id was not supplied"
        with pytest.raises(ValueError, match=error):
            asyncio.run(ask(item, session, "analysis", context, "analysis:assigned", options()))
        return

    result, _, complete = asyncio.run(ask(item, session, "analysis", context, "analysis:assigned", options()))
    request = session.calls[1]
    compact_fields = request["schema"]["$defs"]["SecurityRecoveryProposal"]["properties"]
    assert compact_fields.keys() == proposal.keys()
    assert request["context"]["reported_checks"] == context["reported_checks"]
    assert complete is True
    assert result["findings"] == [{
        **proposal, "path": "src/assigned.py",
        "explanation": "The function accepts an unchecked value. An invalid value can interrupt validation.",
        "evidence": [{"revision": "current", "path": "src/assigned.py", "start_line": 1, "end_line": 1,
                      "quote": "def assigned(value):\n"}],
    }]


def test_output_limit_recovery_runs_only_once_across_source_passes(tmp_path):
    item = Item(tmp_path)
    session = Session([
        InferenceError("output_limit", "limited", usage=Usage(total_tokens=2048), consumption_unknown=False),
        {"findings": [], "required_paths": ["src/support.py"]},
        InferenceError("output_limit", "limited again", usage=Usage(total_tokens=2048), consumption_unknown=False),
    ])

    with pytest.raises(InferenceError):
        asyncio.run(ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options()))

    assert [call["key"] for call in session.calls] == [
        "analysis:assigned:pass:0",
        "analysis:assigned:pass:0:output-limit-recovery:v2",
        "analysis:assigned:pass:1",
    ]


def test_unknown_output_usage_does_not_retry(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        InferenceError("output_limit", "limited", usage=Usage(), consumption_unknown=True),
    ])

    with pytest.raises(InferenceError):
        asyncio.run(ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options()))

    assert len(session.calls) == 1


def test_legacy_policy_does_not_add_output_limit_recovery_request(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        InferenceError("output_limit", "limited", usage=Usage(total_tokens=2048), consumption_unknown=False),
    ])

    with pytest.raises(InferenceError):
        asyncio.run(ask(item, session, "analysis", analysis_context(item), "analysis:assigned", options(None)))

    assert len(session.calls) == 1


def test_incomplete_evidence_forces_cached_definitive_assessment_to_uncertain(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        {"verdict": "uncertain", "finding_id": None, "explanation": "Need a caller.",
         "evidence": [], "required_paths": ["src/absent.py"]},
        CachedResponse({"verdict": "confirmed", "finding_id": None, "explanation": "Confirmed.",
                        "evidence": [{"revision": "current", "path": "src/assigned.py",
                                      "start_line": 1, "end_line": 2,
                                      "quote": "def assigned(value):\n"}], "required_paths": []}),
    ])
    context = {"proposal": {"path": "src/assigned.py"}, "history": [], "head_sha": SHA, "diff": "",
               "sources": {"current": {"src/assigned.py": item.sources["src/assigned.py"]}}}

    result, _paths, complete = asyncio.run(
        ask(item, session, "assessment", context, "assessment:one", options()))

    assert result["verdict"] == "uncertain"
    assert complete is False


def test_incomplete_evidence_forces_cached_fixed_verification_to_uncertain(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        {"verdict": "uncertain", "explanation": "Need another path.", "change_explanation": "",
         "original_issue_present": False, "evidence": [], "required_paths": ["src/absent.py"]},
        CachedResponse({"verdict": "fixed", "explanation": "Fixed.", "change_explanation": "Guard added.",
                        "original_issue_present": True,
                        "evidence": [{"revision": "original", "path": "src/assigned.py",
                                      "start_line": 1, "end_line": 2, "quote": "def assigned(value):\n"},
                                     {"revision": "current", "path": "src/assigned.py",
                                      "start_line": 1, "end_line": 2, "quote": "def assigned(value):\n"}],
                        "required_paths": []}),
    ])
    context = {"finding": {"path": "src/assigned.py"}, "original_sha": "b" * 40, "head_sha": SHA,
               "diff": "diff --git a/src/assigned.py b/src/assigned.py\n+guard\n",
               "sources": {"original": {"src/assigned.py": item.sources["src/assigned.py"]},
                           "current": {"src/assigned.py": item.sources["src/assigned.py"]}}}

    result, _paths, complete = asyncio.run(
        ask(item, session, "verification", context, "verification:one", options()))

    assert result["verdict"] == "uncertain"
    assert complete is False


def test_v2_verification_can_request_original_source_when_current_is_already_supplied(tmp_path):
    item = Item(tmp_path, names=("assigned.py",))
    session = Session([
        {"verdict": "uncertain", "explanation": "Need original source.", "change_explanation": "",
         "original_issue_present": False, "evidence": [], "required_paths": ["src/assigned.py"]},
        {"verdict": "fixed", "explanation": "The original trigger is gone.",
         "change_explanation": "The guard blocks it.", "original_issue_present": True,
         "evidence": [{"source_id": "source_1", "start_line": 1, "end_line": 2},
                      {"source_id": "source_2", "start_line": 1, "end_line": 2}],
         "required_paths": []},
    ])
    context = {"finding": {"path": "src/assigned.py"}, "original_sha": "b" * 40, "head_sha": SHA,
               "diff": "diff --git a/src/assigned.py b/src/assigned.py\n+guard\n",
               "sources": {"original": {},
                           "current": {"src/assigned.py": item.sources["src/assigned.py"]}}}

    result, _paths, complete = asyncio.run(
        ask(item, session, "verification", context, "verification:one", options()))

    assert result["verdict"] == "fixed"
    assert complete is True
    assert item.reads == [("src/assigned.py", "b" * 40)]
