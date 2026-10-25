import copy
import json
import tempfile
import unittest
from pathlib import Path

from finops_agent.judge import validate
from finops_agent.k8s import narrate as N
from finops_agent.k8s.test_policy import make_cluster, make_facts, one
from finops_agent.llm import CassetteMissing, LLMClient, LLMError, LLMResult


def allow():
    return one(make_facts())                                              # ALLOW: cpu 2.0 -> 0.65


def deny():
    return one(make_facts(slo={"burn_rate_1h": 5, "budget_remaining": 0.01}))   # C FAIL


def unknown():
    return one(make_facts(usage={"valid_days": 3}))


def noop():
    return one(make_facts(request=0.6))


def reply(d, **over):
    r = {"why": {"status": "现状：request 当前为 2 core，3 个副本以外的内容不写。", "evidence": "证据见 E1。", "conclusion": "结论如上。"},
         "would_change": ["补充更多天数的数据"],
         "proposal": "把 request 从 2 core 调到 0.65 core。" if d["verdict"] in N.ACTIONABLE else None,
         "evidence": ["E1"]}
    r.update(over)
    return json.dumps(r, ensure_ascii=False)


class FakeClient:
    def __init__(self, text=None, mode="record", exc=None):
        self.mode, self.model, self.text, self.exc, self.prompts = mode, "fake", text, exc, []

    def complete(self, prompt):
        self.prompts.append(prompt)
        if self.exc:
            raise self.exc
        text = self.text(prompt) if callable(self.text) else self.text
        return LLMResult(text, 100, 50, 1234.0, 0.01, "k" * 64, self.mode, self.model)


def run(decisions, client):
    trace = []
    return N.narrate_all(decisions, client, trace), trace


class NarrateTest(unittest.TestCase):
    def test_prompt_is_isolated_to_one_decision(self):
        a = allow()
        b = {**deny(), "id": "K-other-secret-cpu", "workload": "secret", "namespace": "other"}
        client = FakeClient(lambda p: reply(a))
        run([a, b], client)
        self.assertEqual(len(client.prompts), 2)
        pa = next(p for p in client.prompts if a["id"] in p)
        self.assertNotIn("K-other-secret-cpu", pa)
        self.assertNotIn("secret", pa)
        self.assertIn("<untrusted_metadata>", pa)

    def test_llm_narrative_and_verdict_unchanged(self):
        d = allow()
        (out,), trace = run([d], FakeClient(reply(d)))
        n = out["narrative"]
        self.assertEqual(n["source"], "llm")
        self.assertEqual(set(n["why"]), {"status", "evidence", "conclusion"})
        self.assertTrue(n["proposal"])
        self.assertEqual({k: v for k, v in out.items() if k != "narrative"}, {k: v for k, v in d.items() if k != "narrative"})
        e = trace[0]
        self.assertEqual((e["source"], e["op"], e["mode"], e["tokens_in"], e["tokens_out"], e["cost_usd"]), ("llm", "narrate", "record", 100, 50, 0.01))
        self.assertTrue(e["accepted"])

    def test_llm_cannot_change_verdict(self):
        d = deny()
        text = json.dumps({**json.loads(reply(d)), "verdict": "ALLOW"})
        (out,), _ = run([d], FakeClient(text))
        self.assertEqual(out["verdict"], "DENY")
        # a sentence that claims another verdict is redacted
        bad = reply(d, why={"status": "现状。", "evidence": "证据。", "conclusion": "其实应该 ALLOW 降配。"})
        (out,), trace = run([d], FakeClient(bad))
        self.assertEqual(out["verdict"], "DENY")
        self.assertIn(validate.REDACTED, out["narrative"]["why"]["conclusion"])
        self.assertNotIn("ALLOW", out["narrative"]["why"]["conclusion"])
        self.assertEqual(out["narrative"]["redactions"], 1)

    def test_unknown_condition_status_is_not_a_wrong_verdict(self):
        d = one(make_facts(slo=None))                    # C UNKNOWN, verdict REQUIRE_APPROVAL
        ok = reply(d, why={"status": "现状。", "evidence": "条件 C 为 UNKNOWN。", "conclusion": "需要审批。"})
        (out,), _ = run([d], FakeClient(ok))
        self.assertNotIn(validate.REDACTED, out["narrative"]["why"]["evidence"])

    def test_ungrounded_number_sentence_is_redacted(self):
        d = allow()
        bad = reply(d, why={"status": "request 当前为 2 core。", "evidence": "预计能省 8888 USD/月。", "conclusion": "可以降配。"})
        (out,), trace = run([d], FakeClient(bad))
        w = out["narrative"]["why"]
        self.assertEqual(w["evidence"], validate.REDACTED)
        self.assertIn("2 core", w["status"])
        self.assertEqual(out["narrative"]["redactions"], 1)
        self.assertNotIn("8888", json.dumps(out, ensure_ascii=False))          # the number does not reach the decision ...
        self.assertIn("8888", json.dumps(trace[0]["redactions"], ensure_ascii=False))   # ... only the trace

    def test_zero_ungrounded_numbers_in_output(self):
        d = allow()
        bad = reply(d, proposal="省 12345 USD。把 request 从 2 core 调到 0.65 core，影响 2 个副本。")
        (out,), _ = run([d], FakeClient(bad))
        b = N.grounding_brief(d)
        n = out["narrative"]
        text = " ".join(n["why"].values()) + " ".join(n["would_change"]) + n["proposal"]
        self.assertEqual(validate.ungrounded_numbers(text, b), [])

    def test_bad_reply_falls_back_to_template_with_reason(self):
        d = allow()
        for text in ("not json", reply(d, proposal=None), reply(d, evidence=["E999"]), reply(d, would_change=[])):
            (out,), trace = run([d], FakeClient(text))
            self.assertEqual(out["narrative"]["source"], "template")
            self.assertFalse(trace[0]["accepted"])
            self.assertTrue(trace[0]["reject_reason"])
            self.assertEqual(out["verdict"], "ALLOW")

    def test_proposal_only_for_actionable_verdicts(self):
        d = deny()
        (out,), trace = run([d], FakeClient(reply(d, proposal="把 request 调低")))
        self.assertEqual(out["narrative"]["source"], "template")
        self.assertIsNone(out["narrative"]["proposal"])

    def test_record_mode_llm_failure_uses_template(self):
        d = allow()
        (out,), trace = run([d], FakeClient(exc=LLMError("claude exited 1")))
        self.assertEqual(out["narrative"]["source"], "template")
        self.assertFalse(trace[0]["ok"])
        self.assertIn("claude exited 1", trace[0]["error"])

    def test_replay_missing_cassette_fails_loudly(self):
        with tempfile.TemporaryDirectory() as t:
            client = LLMClient(mode="replay", cassette_dir=Path(t))
            with self.assertRaises(CassetteMissing):
                N.narrate_all([allow()], client, [])

    def test_replay_roundtrip_from_a_cassette(self):
        d = allow()
        with tempfile.TemporaryDirectory() as t:
            c = LLMClient(mode="replay", cassette_dir=Path(t))
            prompt = N.render_prompt(d)
            key = __import__("finops_agent.llm", fromlist=["cassette_key"]).cassette_key(c.model, prompt)
            (Path(t) / f"{key}.json").write_text(json.dumps({"text": reply(d), "tokens_in": 7, "tokens_out": 3, "latency_ms": 5.0, "cost_usd": 0.001}))
            trace = []
            (out,) = N.narrate_all([d], c, trace)
            self.assertEqual((out["narrative"]["source"], trace[0]["mode"], trace[0]["tokens_in"]), ("llm", "replay", 7))

    def test_prompt_is_deterministic(self):
        self.assertEqual(N.render_prompt(allow()), N.render_prompt(allow()))

    def test_template_covers_every_verdict_and_is_grounded(self):
        for d in (allow(), deny(), unknown(), noop(), one(make_facts(slo=None))):
            n = N.template_narrative(d)
            self.assertEqual(bool(n["proposal"]), d["verdict"] in N.ACTIONABLE, d["verdict"])
            self.assertTrue(all(n["why"].values()))
            self.assertTrue(1 <= len(n["would_change"]) <= 3)
            text = " ".join(n["why"].values()) + " ".join(n["would_change"]) + (n["proposal"] or "")
            self.assertEqual(validate.ungrounded_numbers(text, N.grounding_brief(d)), [], d["verdict"])

    def test_template_mode_makes_no_llm_calls(self):
        d = allow()
        out = N.narrate_all([d], None, [], use_llm=False)
        self.assertEqual(out[0]["narrative"]["source"], "template")
        self.assertEqual(out[0]["verdict"], "ALLOW")

    def test_narrate_does_not_mutate_input(self):
        d = allow()
        before = copy.deepcopy(d)
        run([d], FakeClient(reply(d)))
        self.assertEqual(d, before)


if __name__ == "__main__":
    unittest.main()
