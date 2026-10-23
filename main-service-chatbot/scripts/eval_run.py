"""Runs a set of golden questions against a running service and reports which still behave.

    python scripts/eval_run.py scripts/eval_questions.example.json --base-url http://localhost:8000 \
        --email admin@example.com --password ...

Each case is {"question": "...", "expect_sources": ["Policy.pdf"], "expect_no_answer": false,
"must_contain": ["encrypt"]}; only `question` is required.

- expect_sources: file names (substring match) that must be among the passages kept after the rerank.
- expect_no_answer: the evidence gate must have stopped the turn ("no_evidence").
- must_contain: words the answer must include (case-insensitive).

It needs an administrator account (with DISABLE_AUTH on, none), because the passages come from
GET /admin/chat-trace/{history_id}. Exits 1 if any case fails, so it can run before a release.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx


def login(client: httpx.Client, email: str | None, password: str | None) -> dict:
    """Authorization headers for an administrator; none when no credentials are given (DISABLE_AUTH)."""
    if not email:
        return {}
    response = client.post("/auth/authenticate", data={"username": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def check_case(case: dict, answer: str, trace: dict) -> list[str]:
    """What is wrong with one case's result; empty means it passed."""
    problems = []
    outcome = trace.get("outcome")
    if case.get("expect_no_answer") and outcome != "no_evidence":
        problems.append(f"expected the no-evidence gate, got outcome '{outcome}'")
    if not case.get("expect_no_answer") and outcome == "no_evidence":
        problems.append("the evidence gate stopped a question that should have been answered")
    kept = " ".join(hit.get("file", "") for hit in (trace.get("trace") or {}).get("reranked", [])).lower()
    for name in case.get("expect_sources", []):
        if name.lower() not in kept:
            problems.append(f"expected source '{name}' among the kept passages; got: {kept or 'none'}")
    for word in case.get("must_contain", []):
        if word.lower() not in (answer or "").lower():
            problems.append(f"answer does not contain '{word}'")
    return problems


def run(args) -> int:
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failed = 0
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        headers = login(client, args.email, args.password)
        for case in cases:
            body = {"chat": [{"role": "user", "content": case["question"]}]}
            reply = client.post("/chat", json=body, headers=headers)
            if reply.status_code != 200:
                problems = [f"POST /chat returned {reply.status_code}: {reply.text[:200]}"]
            else:
                data = reply.json()
                traced = client.get(f"/admin/chat-trace/{data['history_id']}", headers=headers)
                trace = traced.json() if traced.status_code == 200 else {}
                if traced.status_code != 200 and (case.get("expect_sources") or case.get("expect_no_answer")):
                    problems = [f"no trace ({traced.status_code}); is TRACE_ENABLED on and the caller an admin?"]
                else:
                    problems = check_case(case, data.get("response", ""), trace)
            print(("PASS" if not problems else "FAIL"), "-", case["question"])
            for problem in problems:
                print("     ", problem)
            failed += bool(problems)
    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("cases", help="JSON file: a list of cases")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--timeout", type=float, default=120.0)
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
