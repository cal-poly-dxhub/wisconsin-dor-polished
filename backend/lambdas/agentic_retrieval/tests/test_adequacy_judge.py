"""Tests for the post-retrieval adequacy judge and its handler wiring.

Covers: finding parsing for all three verdicts (Bedrock mocked), fail-open,
the deterministic clarification-reply resolver, the Phase B finding block, the
handler flag matrix, choices emission, and the chat-history round-trip of a
pending clarification.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pydantic = pytest.importorskip("pydantic")


def _tool_response(**payload):
    """A Bedrock Converse response carrying one record_finding tool use."""
    return {
        "output": {
            "message": {"content": [{"toolUse": {"name": "record_finding", "input": payload}}]}
        },
        "stopReason": "tool_use",
    }


CHUNKS = [
    {
        "doc_id": "statutes-70",
        "heading": "70.32 Real property, how valued",
        "start_page": 23,
        "text": "Real property shall be valued by the assessor ...",
    },
    {
        "doc_id": "wpam-2026",
        "heading": "Chapter 9",
        "start_page": 501,
        "text": "The cost approach estimates replacement cost less depreciation.",
    },
]


class TestJudgeAnswerPlan:
    def _judge(self, fresh_modules, response):
        (adequacy_judge,) = fresh_modules("adequacy_judge")
        adequacy_judge.config.bedrock = MagicMock()
        adequacy_judge.config.bedrock.converse.return_value = response
        return adequacy_judge

    def test_answer_verdict(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(
                verdict="ANSWER",
                supported="Section 70.32 sets the valuation standard.",
                unsupported="",
                rationale="Plan traces to the cited statute.",
            ),
        )
        finding = aj.judge_answer_plan("how is my land valued?", [], "explain 70.32", CHUNKS, {})
        assert finding.verdict == "ANSWER"
        assert finding.supported == "Section 70.32 sets the valuation standard."
        assert finding.unsupported == ""
        assert finding.clarification is None
        assert finding.model_id == aj.JUDGE_MODEL_ID
        assert finding.latency_ms >= 0

    def test_decline_verdict(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(
                verdict="DECLINE",
                supported="The assistant covers Wisconsin property assessment and taxation.",
                unsupported="",
                rationale="No retrieved chunk bears on the question.",
            ),
        )
        finding = aj.judge_answer_plan("what color is the sky?", [], "", [], {})
        assert finding.verdict == "DECLINE"
        assert "Wisconsin property" in finding.supported

    def test_clarify_verdict_carries_source_derived_options(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(
                verdict="CLARIFY",
                supported="Use-value applies to agricultural land only.",
                unsupported="",
                rationale="The cited chunks split three ways on classification.",
                clarification_axis="property classification",
                clarification_question="How is the land classified on your assessment notice?",
                clarification_options=[
                    "Agricultural",
                    "Agricultural forest",
                    "Undeveloped",
                    "Answer in general terms",
                ],
            ),
        )
        finding = aj.judge_answer_plan("how is my land valued?", [], "plan", CHUNKS, {})
        assert finding.verdict == "CLARIFY"
        assert finding.clarification is not None
        assert finding.clarification.axis == "property classification"
        assert finding.clarification.options[-1] == "Answer in general terms"
        assert len(finding.clarification.options) == 4

    def test_evidence_text_is_sent_to_the_model(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(verdict="ANSWER", supported="s", unsupported="", rationale="r"),
        )
        aj.judge_answer_plan("q", [], "the plan", CHUNKS, {"statutes-70": "vector-search"})
        sent = aj.config.bedrock.converse.call_args.kwargs
        context = sent["messages"][0]["content"][0]["text"]
        # The judge must see EVIDENCE — chunk text, headings, doc ids, pages.
        assert "Real property shall be valued by the assessor" in context
        assert "70.32 Real property, how valued" in context
        assert "doc_id=statutes-70" in context
        assert "page=23" in context
        assert "found_via=vector-search" in context
        assert "the plan" in context
        # Structured output is forced through the tool, not free text.
        assert sent["toolConfig"]["toolChoice"] == {"tool": {"name": "record_finding"}}

    def test_context_is_capped(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(verdict="ANSWER", supported="s", unsupported="", rationale="r"),
        )
        huge = [{"doc_id": f"d-{i}", "text": "x" * 5000} for i in range(200)]
        aj.judge_answer_plan("q", [], "plan", huge, {})
        context = aj.config.bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
        assert len(context) <= aj.MAX_CONTEXT_CHARS

    def test_history_flags_a_prior_clarification(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(verdict="ANSWER", supported="s", unsupported="", rationale="r"),
        )
        history = [
            {
                "query": "how is my land valued?",
                "answer": "It depends on classification.",
                "clarification": {
                    "axis": "property classification",
                    "question": "How is the land classified?",
                    "options": ["Agricultural", "Undeveloped", "Answer in general terms"],
                    "original_query": "how is my land valued?",
                },
            }
        ]
        aj.judge_answer_plan("Agricultural", history, "plan", CHUNKS, {})
        context = aj.config.bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
        assert "the assistant asked the user a clarifying question" in context
        assert "property classification" in context

    def test_model_id_override(self, fresh_modules):
        aj = self._judge(
            fresh_modules,
            _tool_response(verdict="ANSWER", supported="s", unsupported="", rationale="r"),
        )
        finding = aj.judge_answer_plan("q", [], "p", CHUNKS, {}, model_id="us.some.other-model")
        assert finding.model_id == "us.some.other-model"
        assert aj.config.bedrock.converse.call_args.kwargs["modelId"] == "us.some.other-model"

    def test_fails_open_on_bedrock_error(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        aj.config.bedrock = MagicMock()
        aj.config.bedrock.converse.side_effect = RuntimeError("throttled")
        finding = aj.judge_answer_plan("q", [], "p", CHUNKS, {})
        assert finding.verdict == "ANSWER"
        assert finding.supported == ""
        assert finding.unsupported == ""
        assert finding.clarification is None
        assert finding.rationale == "judge unavailable"

    def test_fails_open_when_no_tool_use_in_response(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        aj.config.bedrock = MagicMock()
        aj.config.bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": "I think it's fine."}]}}
        }
        finding = aj.judge_answer_plan("q", [], "p", CHUNKS, {})
        assert finding.verdict == "ANSWER"
        assert finding.rationale == "judge unavailable"

    def test_parses_json_emitted_as_plain_text(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        aj.config.bedrock = MagicMock()
        aj.config.bedrock.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": (
                                '{"verdict": "DECLINE", "supported": "property tax only", '
                                '"unsupported": "", "rationale": "nothing relevant"}'
                            )
                        }
                    ]
                }
            }
        }
        finding = aj.judge_answer_plan("q", [], "p", [], {})
        assert finding.verdict == "DECLINE"


CLARIFY_PAYLOAD = {
    "verdict": "CLARIFY",
    "supported": "Objections go to different bodies by classification.",
    "unsupported": "",
    "rationale": "The cited chunks fork on classification.",
    "clarification_axis": "property classification",
    "clarification_question": "How is the property classified?",
    "clarification_options": ["Agricultural", "Manufacturing", "Answer in general terms"],
}


class TestStructuralGuards:
    """The two schema fields that override a contradictory verdict."""

    def test_decline_with_relevant_material_is_coerced_to_answer(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "relevant_material_found": True,
                "verdict": "DECLINE",
                "supported": "Board of Assessors material was retrieved.",
                "unsupported": "",
                "rationale": "The named case itself is not in the material.",
            }
        )
        assert finding.verdict == "ANSWER"
        # The rationale becomes the unsupported line so Phase B says what is missing.
        assert finding.unsupported == "The named case itself is not in the material."

    def test_decline_stands_when_no_relevant_material(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "relevant_material_found": False,
                "verdict": "DECLINE",
                "supported": "Wisconsin property tax only.",
                "unsupported": "",
                "rationale": "Nothing retrieved bears on the question.",
            }
        )
        assert finding.verdict == "DECLINE"

    def test_clarify_is_coerced_when_the_question_states_the_fact(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            dict(CLARIFY_PAYLOAD, question_omits_the_fact=False, relevant_material_found=True)
        )
        # "how do I appeal my agricultural classification" already named the fact.
        assert finding.verdict == "ANSWER"
        assert finding.clarification is None
        assert finding.rationale == "The cited chunks fork on classification."
        assert finding.supported == "Objections go to different bodies by classification."

    def test_clarify_stands_when_the_question_omits_the_fact(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            dict(CLARIFY_PAYLOAD, question_omits_the_fact=True, relevant_material_found=True)
        )
        assert finding.verdict == "CLARIFY"
        assert finding.clarification is not None
        assert finding.clarification.axis == "property classification"

    def test_clarify_stands_when_the_flag_is_missing(self, fresh_modules):
        """Fail open toward the judge's own verdict — only an explicit false coerces."""
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(dict(CLARIFY_PAYLOAD))
        assert finding.verdict == "CLARIFY"
        assert finding.clarification is not None

    def test_clarify_stands_when_the_flag_is_null(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(dict(CLARIFY_PAYLOAD, question_omits_the_fact=None))
        assert finding.verdict == "CLARIFY"
        assert finding.clarification is not None

    def test_the_guard_does_not_touch_an_answer_verdict(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "relevant_material_found": True,
                "question_omits_the_fact": False,
                "verdict": "ANSWER",
                "supported": "s",
                "unsupported": "u",
                "rationale": "r",
            }
        )
        assert finding.verdict == "ANSWER"
        assert finding.unsupported == "u"


class TestFindingToolSchema:
    def test_both_structural_flags_are_required(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        schema = aj.FINDING_TOOL_CONFIG["tools"][0]["toolSpec"]["inputSchema"]["json"]
        assert schema["required"] == [
            "relevant_material_found",
            "question_omits_the_fact",
            "verdict",
            "supported",
            "unsupported",
            "rationale",
        ]
        omits = schema["properties"]["question_omits_the_fact"]
        assert omits["type"] == "boolean"
        # The description must aim the judge at the QUESTION, not the plan/material.
        assert "QUESTION" in omits["description"]
        assert "agricultural" in omits["description"]


class TestParseFinding:
    def test_unknown_verdict_degrades_to_answer(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {"verdict": "MAYBE", "supported": "s", "unsupported": "", "rationale": "r"}
        )
        assert finding.verdict == "ANSWER"

    def test_clarify_without_options_degrades_to_answer(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "verdict": "CLARIFY",
                "supported": "s",
                "unsupported": "",
                "rationale": "r",
                "clarification_question": "Which one?",
                "clarification_options": ["only one"],
            }
        )
        assert finding.verdict == "ANSWER"
        assert finding.clarification is None

    def test_clarify_without_question_degrades_to_answer(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "verdict": "CLARIFY",
                "supported": "s",
                "unsupported": "",
                "rationale": "r",
                "clarification_options": ["a", "b", "general"],
            }
        )
        assert finding.verdict == "ANSWER"

    def test_blank_options_are_dropped(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        finding = aj.parse_finding(
            {
                "verdict": "CLARIFY",
                "supported": "s",
                "unsupported": "",
                "rationale": "r",
                "clarification_axis": "year",
                "clarification_question": "Which year?",
                "clarification_options": ["2025", "  ", "2026", "Answer in general terms"],
            }
        )
        assert finding.clarification.options == ["2025", "2026", "Answer in general terms"]


class TestResolvePendingClarification:
    PENDING = {
        "axis": "property classification",
        "question": "How is the land classified?",
        "options": [
            "Agricultural",
            "Agricultural forest",
            "Undeveloped",
            "Answer in general terms",
        ],
        "original_query": "how is my land valued?",
    }

    def _history(self, aj=None):
        return [{"query": "how is my land valued?", "answer": "...", "clarification": self.PENDING}]

    def test_option_match_annotates_the_original_question(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        effective, resolution = aj.resolve_pending_clarification("Agricultural", self._history())
        assert effective == "how is my land valued? (property classification: Agricultural)"
        assert resolution["mode"] == "option"
        assert resolution["choice"] == "Agricultural"

    def test_option_match_is_case_insensitive(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        effective, resolution = aj.resolve_pending_clarification(
            "agricultural FOREST", self._history()
        )
        assert effective.endswith("(property classification: Agricultural forest)")
        assert resolution["mode"] == "option"

    def test_escape_option_restores_the_bare_question(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        effective, resolution = aj.resolve_pending_clarification(
            "Answer in general terms", self._history()
        )
        assert effective == "how is my land valued?"
        assert resolution["mode"] == "escape"

    def test_short_free_text_is_treated_as_the_answer(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        effective, resolution = aj.resolve_pending_clarification(
            "it's a cranberry marsh", self._history()
        )
        assert effective == (
            "how is my land valued? (property classification: it's a cranberry marsh)"
        )
        assert resolution["mode"] == "free_text"

    def test_a_new_question_passes_through_unchanged(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        new_q = "When does the Board of Review meet in my municipality?"
        effective, resolution = aj.resolve_pending_clarification(new_q, self._history())
        assert effective == new_q
        assert resolution is None

    def test_long_free_text_passes_through_unchanged(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        long_q = " ".join(["word"] * 13)
        effective, resolution = aj.resolve_pending_clarification(long_q, self._history())
        assert effective == long_q
        assert resolution is None

    def test_no_pending_clarification_is_a_no_op(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        history = [{"query": "prior", "answer": "prior answer"}]
        assert aj.resolve_pending_clarification("Agricultural", history) == ("Agricultural", None)

    def test_no_history_is_a_no_op(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        assert aj.resolve_pending_clarification("Agricultural", []) == ("Agricultural", None)
        assert aj.resolve_pending_clarification("Agricultural", None) == ("Agricultural", None)

    def test_only_the_latest_turn_counts(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        history = [
            {"query": "old", "answer": "a", "clarification": self.PENDING},
            {"query": "newer", "answer": "b"},
        ]
        assert aj.resolve_pending_clarification("Agricultural", history) == ("Agricultural", None)


class TestFindingBlock:
    def test_block_carries_verdict_supported_and_unsupported(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        block = aj.render_finding_block(
            aj.Finding(
                verdict="ANSWER",
                supported="70.32 sets the standard.",
                unsupported="the plan's 30-day deadline appears in no chunk",
            )
        )
        assert block.startswith("## RETRIEVAL FINDING")
        assert "Verdict: ANSWER" in block
        assert "70.32 sets the standard." in block
        assert "30-day deadline" in block

    def test_empty_unsupported_says_so_explicitly(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        block = aj.render_finding_block(aj.Finding(verdict="ANSWER", supported="s", unsupported=""))
        assert "every claim in the plan traces to a cited chunk" in block

    def test_clarify_block_carries_question_and_options(self, fresh_modules):
        (aj,) = fresh_modules("adequacy_judge")
        block = aj.render_finding_block(
            aj.Finding(
                verdict="CLARIFY",
                supported="s",
                unsupported="",
                clarification=aj.Clarification(
                    axis="assessment year",
                    question="Which assessment year do you mean?",
                    options=["2025", "2026", "Answer in general terms"],
                ),
            )
        )
        assert "Which assessment year do you mean?" in block
        assert "2025; 2026; Answer in general terms" in block

    def test_phase_b_context_includes_the_block_only_when_given(self, fresh_modules):
        aj, phase_b = fresh_modules("adequacy_judge", "loop.phase_b")
        without = phase_b.build_answer_context("q", [], set(), {}, {}, "the plan")
        assert "RETRIEVAL FINDING" not in without

        with_finding = phase_b.build_answer_context(
            "q",
            [],
            set(),
            {},
            {},
            "the plan",
            finding=aj.Finding(verdict="DECLINE", supported="property tax only", unsupported=""),
        )
        assert "## RETRIEVAL FINDING" in with_finding
        assert "Verdict: DECLINE" in with_finding
        # The block sits after the answer plan, so the finding reads as a
        # correction to it.
        assert with_finding.index("## Answer Plan") < with_finding.index("## RETRIEVAL FINDING")


class TestChatHistoryClarificationRoundTrip:
    def _mock_table(self):
        table = MagicMock()
        resource = MagicMock()
        resource.Table.return_value = table
        return table, resource

    def test_save_then_load_preserves_the_clarification(self, fresh_modules):
        (chat_history,) = fresh_modules("chat_history")
        table, resource = self._mock_table()
        clarification = {
            "axis": "property classification",
            "question": "How is the land classified?",
            "options": ["Agricultural", "Undeveloped", "Answer in general terms"],
            "original_query": "how is my land valued?",
        }
        with (
            patch.object(chat_history, "dynamodb_resource", resource),
            patch.object(chat_history, "CHAT_HISTORY_TABLE", "T"),
        ):
            chat_history.save_chat_history(
                "s-1", "q-1", "how is my land valued?", "answer", clarification=clarification
            )
            saved = table.put_item.call_args.kwargs["Item"]
            assert saved["clarification"] == clarification

            table.query.return_value = {"Items": [saved]}
            history = chat_history.get_chat_history("s-1")

        assert history[0]["clarification"] == clarification

    def test_turns_without_a_clarification_are_unchanged(self, fresh_modules):
        (chat_history,) = fresh_modules("chat_history")
        table, resource = self._mock_table()
        with (
            patch.object(chat_history, "dynamodb_resource", resource),
            patch.object(chat_history, "CHAT_HISTORY_TABLE", "T"),
        ):
            chat_history.save_chat_history("s-1", "q-1", "q", "a")
            saved = table.put_item.call_args.kwargs["Item"]
            assert "clarification" not in saved

            table.query.return_value = {"Items": [{"query": "q", "answer": "a"}]}
            history = chat_history.get_chat_history("s-1")
        assert history == [{"query": "q", "answer": "a"}]

    def test_a_clarification_with_no_options_is_not_stored(self, fresh_modules):
        (chat_history,) = fresh_modules("chat_history")
        table, resource = self._mock_table()
        with (
            patch.object(chat_history, "dynamodb_resource", resource),
            patch.object(chat_history, "CHAT_HISTORY_TABLE", "T"),
        ):
            chat_history.save_chat_history(
                "s-1", "q-1", "q", "a", clarification={"axis": "x", "question": "y", "options": []}
            )
            assert "clarification" not in table.put_item.call_args.kwargs["Item"]


def _loop_result(**overrides):
    base = {
        "fallback_answer": "ans",
        "cited_doc_ids": [],
        "all_chunks": [],
        "all_doc_ids": set(),
        "discovery": {},
        "fetched_opinions": {},
        "high_confidence_faq": None,
        "faq_entries": [],
        "trace_log": [],
        "connection_alive": True,
        "answer_plan": "the plan",
        "seeded_flowchart": None,
        "seeded_flowchart_score": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestHandlerFlagMatrix:
    """Judge off ⇒ the old path. Judge on + gate off ⇒ no scope short-circuit."""

    def _setup(self, fresh_modules, monkeypatch, env, verdict="PROCEED"):
        handler, disambiguation = fresh_modules(
            "handler", "disambiguation", env={"ENABLE_DISAMBIGUATION": "true", **env}
        )
        monkeypatch.setattr(
            disambiguation, "classify_query", lambda q, h, allow_topic_shift=False: verdict
        )
        mock_ws = MagicMock()
        monkeypatch.setattr(
            handler, "get_ws_connection_from_session", MagicMock(return_value=mock_ws)
        )
        monkeypatch.setattr(handler, "get_chat_history", lambda sid: [])
        saved: dict = {}
        monkeypatch.setattr(
            handler,
            "save_chat_history",
            lambda *a, **kw: saved.update({"args": a, "kwargs": kw}),
        )
        monkeypatch.setattr(handler, "send_resources_and_finalize", MagicMock())
        monkeypatch.setattr(handler, "build_rag_documents", lambda *a, **kw: [])
        monkeypatch.setattr(handler, "build_cited_faq_resource", lambda *a, **kw: None)
        monkeypatch.setattr(handler.asyncio, "run", lambda coro: coro.close())
        run_loop = MagicMock(return_value=_loop_result())
        monkeypatch.setattr(handler, "run_agentic_loop", run_loop)
        judge = MagicMock()
        monkeypatch.setattr(handler, "judge_answer_plan", judge)
        return handler, mock_ws, run_loop, judge, saved

    def test_judge_off_keeps_the_out_of_scope_short_circuit(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules, monkeypatch, {}, verdict="OUT_OF_SCOPE"
        )
        assert handler.ADEQUACY_JUDGE_ENABLED is False
        assert handler.SCOPE_GATE_ENABLED is True
        handler.handler(
            {"query": "what color is the sky?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        run_loop.assert_not_called()
        judge.assert_not_called()

    def test_judge_off_keeps_the_disambiguate_short_circuit(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules, monkeypatch, {}, verdict="DISAMBIGUATE"
        )
        handler.handler(
            {"query": "how is my property assessed?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        run_loop.assert_not_called()
        judge.assert_not_called()

    def test_judge_on_gate_off_skips_out_of_scope_and_retrieves(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules,
            monkeypatch,
            {"ADEQUACY_JUDGE_ENABLED": "true", "SCOPE_GATE_ENABLED": "false"},
            verdict="OUT_OF_SCOPE",
        )
        judge.return_value = SimpleNamespace(
            verdict="DECLINE",
            clarification=None,
            latency_ms=12,
            model_id="m",
            rationale="nothing relevant",
        )
        handler.handler(
            {"query": "what is the Markarian case?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        run_loop.assert_called_once()
        judge.assert_called_once()

    def test_judge_on_gate_off_skips_disambiguate_and_retrieves(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules,
            monkeypatch,
            {"ADEQUACY_JUDGE_ENABLED": "true", "SCOPE_GATE_ENABLED": "false"},
            verdict="DISAMBIGUATE",
        )
        judge.return_value = SimpleNamespace(
            verdict="ANSWER", clarification=None, latency_ms=1, model_id="m", rationale="ok"
        )
        handler.handler(
            {"query": "how is my property assessed?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        run_loop.assert_called_once()

    def test_judge_on_gate_off_still_honors_topic_shift(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules,
            monkeypatch,
            {
                "ADEQUACY_JUDGE_ENABLED": "true",
                "SCOPE_GATE_ENABLED": "false",
                "ENABLE_TOPIC_SHIFT": "true",
            },
            verdict="TOPIC_SHIFT",
        )
        handler.handler(
            {"query": "what about TID base values?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        # TOPIC_SHIFT is about conversation continuity, not scope — unchanged.
        run_loop.assert_not_called()
        judge.assert_not_called()

    def test_judge_on_gate_on_keeps_the_short_circuit(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules,
            monkeypatch,
            {"ADEQUACY_JUDGE_ENABLED": "true"},
            verdict="OUT_OF_SCOPE",
        )
        handler.handler(
            {"query": "what color is the sky?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        run_loop.assert_not_called()
        judge.assert_not_called()

    def test_judge_receives_the_fallback_answer_as_the_plan(self, fresh_modules, monkeypatch):
        handler, mock_ws, run_loop, judge, _ = self._setup(
            fresh_modules, monkeypatch, {"ADEQUACY_JUDGE_ENABLED": "true"}
        )
        run_loop.return_value = _loop_result(fallback_answer="I could not finish.")
        judge.return_value = SimpleNamespace(
            verdict="ANSWER", clarification=None, latency_ms=1, model_id="m", rationale="ok"
        )
        handler.handler(
            {"query": "q", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        assert judge.call_args.args[2] == "I could not finish."


class TestHandlerClarificationDelivery:
    def _setup(self, fresh_modules, monkeypatch, finding, history=None):
        handler, disambiguation = fresh_modules(
            "handler",
            "disambiguation",
            env={"ENABLE_DISAMBIGUATION": "false", "ADEQUACY_JUDGE_ENABLED": "true"},
        )
        mock_ws = MagicMock()
        monkeypatch.setattr(
            handler, "get_ws_connection_from_session", MagicMock(return_value=mock_ws)
        )
        monkeypatch.setattr(handler, "get_chat_history", lambda sid: history or [])
        saved: dict = {}
        monkeypatch.setattr(
            handler,
            "save_chat_history",
            lambda *a, **kw: saved.update({"args": a, "kwargs": kw}),
        )
        monkeypatch.setattr(handler, "send_resources_and_finalize", MagicMock())
        monkeypatch.setattr(handler, "build_rag_documents", lambda *a, **kw: [])
        monkeypatch.setattr(handler, "build_cited_faq_resource", lambda *a, **kw: None)
        monkeypatch.setattr(handler.asyncio, "run", lambda coro: coro.close())
        # Under a finding the fallback text becomes the plan and Phase B writes
        # the answer (one prose path) — stub Phase B so only the choices
        # message reaches the socket in these delivery tests.
        monkeypatch.setattr(handler, "build_answer_context", lambda *a, **kw: "ctx")
        monkeypatch.setattr(handler, "send_resources", MagicMock())
        monkeypatch.setattr(handler, "stream_answer", MagicMock(return_value="answer"))
        monkeypatch.setattr(handler, "finalize_answer_links", lambda answer, *a, **kw: answer)
        run_loop = MagicMock(return_value=_loop_result())
        monkeypatch.setattr(handler, "run_agentic_loop", run_loop)
        monkeypatch.setattr(handler, "judge_answer_plan", MagicMock(return_value=finding))
        return handler, mock_ws, run_loop, saved

    def _clarify_finding(self, handler):
        from adequacy_judge import Clarification, Finding

        return Finding(
            verdict="CLARIFY",
            supported="s",
            unsupported="",
            clarification=Clarification(
                axis="property classification",
                question="How is the land classified?",
                options=["Agricultural", "Undeveloped", "Answer in general terms"],
            ),
        )

    def test_choices_sent_and_clarification_persisted(self, fresh_modules, monkeypatch):
        handler, mock_ws, _, saved = self._setup(fresh_modules, monkeypatch, None)
        monkeypatch.setattr(
            handler, "judge_answer_plan", MagicMock(return_value=self._clarify_finding(handler))
        )
        handler.handler(
            {"query": "how is my land valued?", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        assert mock_ws.client.post_to_connection.call_count == 1
        import json as _json

        body = _json.loads(mock_ws.client.post_to_connection.call_args.kwargs["Data"])
        assert body["streamId"] == "choices"
        assert body["body"]["responseType"] == "choices"
        assert body["body"]["content"]["choices"] == [
            "Agricultural",
            "Undeveloped",
            "Answer in general terms",
        ]
        persisted = saved["kwargs"]["clarification"]
        assert persisted["axis"] == "property classification"
        assert persisted["original_query"] == "how is my land valued?"

    def test_no_choices_when_the_finding_is_answer(self, fresh_modules, monkeypatch):
        from adequacy_judge import Finding

        handler, mock_ws, _, saved = self._setup(
            fresh_modules,
            monkeypatch,
            Finding(verdict="ANSWER", supported="s", unsupported=""),
        )
        handler.handler(
            {"query": "q", "query_id": "q-1", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        assert mock_ws.client.post_to_connection.call_count == 0
        assert saved["kwargs"]["clarification"] is None

    def test_a_chip_reply_is_resolved_before_retrieval(self, fresh_modules, monkeypatch):
        from adequacy_judge import Finding

        history = [
            {
                "query": "how is my land valued?",
                "answer": "It depends.",
                "clarification": {
                    "axis": "property classification",
                    "question": "How is the land classified?",
                    "options": ["Agricultural", "Undeveloped", "Answer in general terms"],
                    "original_query": "how is my land valued?",
                },
            }
        ]
        handler, mock_ws, run_loop, saved = self._setup(
            fresh_modules,
            monkeypatch,
            Finding(verdict="ANSWER", supported="s", unsupported=""),
            history=history,
        )
        handler.handler(
            {"query": "Agricultural", "query_id": "q-2", "session_id": "s-1"},
            SimpleNamespace(aws_request_id="r-1"),
        )
        # The loop sees the folded question, not the bare chip label...
        assert run_loop.call_args.args[0] == (
            "how is my land valued? (property classification: Agricultural)"
        )
        # ...while the raw user message is still what gets persisted.
        assert saved["args"][2] == "Agricultural"
