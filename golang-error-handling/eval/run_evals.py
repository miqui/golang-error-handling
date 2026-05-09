#!/usr/bin/env python3
"""
run_evals.py — Eval runner for the golang-error-handling agent skill.
"""
from __future__ import annotations
import argparse, json, os, re, sys, time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
try:
    import anthropic
except ImportError:
    sys.exit("ERROR: anthropic package not installed. Run: pip install anthropic")
@dataclass
class AssertionResult:
    id: str
    text: str
    passed: bool
    method: str = ""
    reason: str = ""
@dataclass
class EvalResult:
    id: int
    name: str
    prompt: str
    trap: str
    response: str
    assertions: list[AssertionResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    duration_s: float = 0.0
    @property
    def pass_rate(self) -> float:
        total = self.passed + self.failed
        return self.passed / total if total else 0.0
@dataclass
class RunSummary:
    skill_file: str
    model: str
    judge_model: str
    timestamp: str
    total_evals: int
    total_assertions: int
    passed_assertions: int
    failed_assertions: int
    eval_results: list[EvalResult] = field(default_factory=list)
    @property
    def overall_pass_rate(self) -> float:
        return self.passed_assertions / self.total_assertions if self.total_assertions else 0.0
def _first_error_string_capitalized(code: str) -> bool:
    literals = re.findall(r'(?:errors\.New|fmt\.Errorf|oops\.\w+)\s*\(\s*"([^"]+)"', code)
    return any((lit.split('%')[0].strip() and lit.split('%')[0].strip()[0].isupper()) for lit in literals)
def check_deterministic(assertion_text: str, code: str) -> Optional[bool]:
    a = assertion_text.lower()
    if "uses slog" in a and "not log.printf" in a:
        return bool(re.search(r"\bslog\b", code)) and not bool(re.search(r"\blog\.Printf\b", code))
    if "uses slog" in a:
        return bool(re.search(r"\bslog\b", code))
    if "errors.is" in a and "sentinel" in a:
        return bool(re.search(r"\berrors\.Is\b", code))
    if "errors.as" in a and "type extraction" in a:
        return bool(re.search(r"\berrors\.As\b", code))
    if "errors.join" in a:
        return bool(re.search(r"\berrors\.Join\b", code))
    if "errors.new" in a and ("sentinel" in a or "fmt.errorf" in a):
        return bool(re.search(r"\berrors\.New\b", code)) and not bool(re.search(r"\bvar\s+Err\w+\s*=\s*fmt\.Errorf", code))
    if "lowercase" in a and "error" in a and "no punctuation" not in a:
        if _first_error_string_capitalized(code):
            return False
        literals = re.findall(r'(?:errors\.New|fmt\.Errorf|oops\.\w+)\s*\(\s*"([^"]+)"', code)
        if not literals:
            return None
        return True
    if "no panic" in a:
        return not bool(re.search(r"\bpanic\s*\(", code))
    if "%w" in a and "not" in a and ("boundary" in a or "translated" in a or "break" in a):
        return not bool(re.search(r"%w", code))
    return None
def build_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")
    return anthropic.Anthropic(api_key=api_key)
def call_model(client: anthropic.Anthropic, model: str, system: str, user: str, max_tokens: int = 4096, retries: int = 3, base_backoff: float = 5.0) -> str:
    for attempt in range(retries):
        try:
            msg = client.messages.create(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
            return msg.content[0].text
        except anthropic.RateLimitError:
            time.sleep(base_backoff * (2 ** attempt))
        except anthropic.APIError:
            if attempt == retries - 1:
                raise
            time.sleep(base_backoff)
    raise RuntimeError("Max retries exceeded")
_JUDGE_SYSTEM = "You are a precise Go code reviewer evaluating one assertion about a code response. Reply with EXACTLY one of: PASS: <one-sentence reason> or FAIL: <one-sentence reason>."
def judge_assertion(client: anthropic.Anthropic, judge_model: str, response: str, assertion: dict) -> AssertionResult:
    user = f"## Assertion\nID: {assertion['id']}\nText: {assertion['text']}\n\n## Go code to evaluate\n```go\n{response}\n```\n\nDoes the code satisfy this assertion?"
    raw = call_model(client, judge_model, _JUDGE_SYSTEM, user, max_tokens=300).strip()
    passed = raw.upper().startswith("PASS")
    m = re.match(r"(?:PASS|FAIL):\s*(.*)", raw, re.IGNORECASE)
    return AssertionResult(id=assertion["id"], text=assertion["text"], passed=passed, method="llm-judge", reason=(m.group(1).strip() if m else raw))
def run_single_eval(client: anthropic.Anthropic, skill_prompt: str, eval_case: dict, model: str, judge_model: str, verbose: bool) -> EvalResult:
    t0 = time.time()
    response = call_model(client, model, skill_prompt, eval_case["prompt"])
    result = EvalResult(id=eval_case["id"], name=eval_case["name"], prompt=eval_case["prompt"], trap=eval_case.get("trap", ""), response=response, duration_s=round(time.time()-t0, 2))
    for assertion in eval_case["assertions"]:
        det = check_deterministic(assertion["text"], response)
        ar = AssertionResult(id=assertion["id"], text=assertion["text"], passed=det, method="deterministic", reason="regex/AST check") if det is not None else judge_assertion(client, judge_model, response, assertion)
        result.assertions.append(ar)
        result.passed += int(ar.passed)
        result.failed += int(not ar.passed)
    return result
def run_evals(skill_file: str, evals_file: str, model: str, judge_model: str, out_file: Optional[str], filter_ids: Optional[list[int]], verbose: bool) -> RunSummary:
    skill_prompt = Path(skill_file).read_text(encoding="utf-8")
    raw = json.loads(Path(evals_file).read_text(encoding="utf-8"))
    evals = raw.get("evals", raw) if isinstance(raw, dict) else raw
    if filter_ids:
        evals = [e for e in evals if e["id"] in filter_ids]
    client = build_client()
    summary = RunSummary(skill_file=skill_file, model=model, judge_model=judge_model, timestamp=datetime.now(timezone.utc).isoformat(), total_evals=len(evals), total_assertions=sum(len(e["assertions"]) for e in evals), passed_assertions=0, failed_assertions=0)
    for eval_case in evals:
        result = run_single_eval(client, skill_prompt, eval_case, model, judge_model, verbose)
        summary.eval_results.append(result)
        summary.passed_assertions += result.passed
        summary.failed_assertions += result.failed
    if out_file:
        out_path = Path(out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
    return summary
def main() -> None:
    p = argparse.ArgumentParser(description="Run evals for the golang-error-handling skill.")
    p.add_argument("--skill", default="skill.md")
    p.add_argument("--evals", default="eval/eval.json")
    p.add_argument("--model", default="claude-opus-4-5")
    p.add_argument("--judge", default="claude-opus-4-5")
    p.add_argument("--out", default=None)
    p.add_argument("--ids", nargs="+", type=int, metavar="N")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    run_evals(args.skill, args.evals, args.model, args.judge, args.out, args.ids, args.verbose)
if __name__ == "__main__":
    main()
