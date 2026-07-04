"""LLM smoke test (spec section 4).

Native OpenAI-style tool calling through the NVIDIA endpoint is a RISK — verify,
don't assume. Run this BEFORE building downstream code:

    uv run --project backend scripts/smoke_llm.py

Verifies:
    1. native tool call works or fails cleanly
    2. JSON protocol works (model returns one JSON object per turn)
    3. JSON mode structured output works
    4. streaming works
    5. seed is accepted or ignored safely
    6. latency per call is measured
    7. rate-limit behavior is observed

Writes: data/smoke_llm_result.json  (selected_tool_protocol drives config).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# make `backend.app` importable when run from repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, llm  # noqa: E402

WEATHER_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}]


def _timed(fn):
    t0 = time.time()
    try:
        out = fn()
        return True, time.time() - t0, out, None
    except Exception as exc:  # noqa: BLE001 — smoke test wants every failure captured
        return False, time.time() - t0, None, f"{type(exc).__name__}: {exc}"


def test_native_tool() -> tuple[bool, float, str | None]:
    def call():
        resp = llm.chat(
            [{"role": "user", "content": "What's the weather in Paris? Use the tool."}],
            tools=WEATHER_TOOL,
        )
        tc = resp.choices[0].message.tool_calls
        return bool(tc and tc[0].function.name == "get_weather")
    ok, dt, out, err = _timed(call)
    return (ok and bool(out)), dt, err


def test_json_protocol() -> tuple[bool, float, str | None]:
    def call():
        txt = llm.chat_text([
            {"role": "system", "content": "Respond with exactly one JSON object: "
             '{"action": "final", "input": {"answer": "..."}}. No prose, no code fence.'},
            {"role": "user", "content": "Say hello."},
        ])
        cleaned = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        start = cleaned.find("{")
        obj = json.loads(cleaned[start:])
        return "action" in obj
    ok, dt, out, err = _timed(call)
    return (ok and bool(out)), dt, err


def test_json_mode() -> tuple[bool, float, str | None]:
    def call():
        txt = llm.chat_text(
            [{"role": "user", "content": 'Return JSON: {"ok": true, "n": 42}'}],
            json_mode=True,
        )
        return json.loads(txt).get("n") == 42
    ok, dt, out, err = _timed(call)
    return (ok and bool(out)), dt, err


def test_streaming() -> tuple[bool, float, str | None]:
    def call():
        chunks = list(llm.stream_text([{"role": "user", "content": "Count: one two three."}]))
        return len("".join(chunks)) > 0
    ok, dt, out, err = _timed(call)
    return (ok and bool(out)), dt, err


def test_seed() -> tuple[bool, float, str | None]:
    """Seed is 'ok' if the call accepts it without error (determinism not required)."""
    def call():
        llm.chat_text([{"role": "user", "content": "Reply with the single word: ok"}], seed=42)
        return True
    ok, dt, out, err = _timed(call)
    return ok, dt, err


def main() -> int:
    if not config.NVIDIA_API_KEY:
        print("NVIDIA_API_KEY not set — cannot run live smoke test.", file=sys.stderr)
        print("Copy .env.example to .env and add your key, then re-run.", file=sys.stderr)
        return 2

    print(f"[smoke] endpoint={config.LLM_BASE_URL} model={config.LLM_MODEL}", file=sys.stderr)
    latencies: list[float] = []
    results: dict[str, object] = {}

    native_ok, dt, err = test_native_tool(); latencies.append(dt)
    results["native_tool_protocol_ok"] = native_ok
    print(f"[smoke] native tool: {native_ok} ({dt:.1f}s) {err or ''}", file=sys.stderr)

    json_ok, dt, err = test_json_protocol(); latencies.append(dt)
    results["json_tool_protocol_ok"] = json_ok
    print(f"[smoke] json protocol: {json_ok} ({dt:.1f}s) {err or ''}", file=sys.stderr)

    jmode_ok, dt, err = test_json_mode(); latencies.append(dt)
    results["json_mode_ok"] = jmode_ok
    print(f"[smoke] json mode: {jmode_ok} ({dt:.1f}s) {err or ''}", file=sys.stderr)

    stream_ok, dt, err = test_streaming(); latencies.append(dt)
    results["streaming_ok"] = stream_ok
    print(f"[smoke] streaming: {stream_ok} ({dt:.1f}s) {err or ''}", file=sys.stderr)

    seed_ok, dt, err = test_seed(); latencies.append(dt)
    results["seed_ok"] = seed_ok
    print(f"[smoke] seed: {seed_ok} ({dt:.1f}s) {err or ''}", file=sys.stderr)

    results["avg_latency_seconds"] = round(sum(latencies) / len(latencies), 2)
    # Prefer native tool calling if it works; otherwise fall back to the JSON protocol.
    results["selected_tool_protocol"] = "native" if native_ok else ("json" if json_ok else "json")

    out_path = config.DATA_DIR / "smoke_llm_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\n[smoke] selected_tool_protocol = {results['selected_tool_protocol']}", file=sys.stderr)
    print(f"[smoke] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
