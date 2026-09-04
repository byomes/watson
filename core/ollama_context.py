"""core/ollama_context.py — size Ollama's num_ctx so a real prompt isn't
silently truncated by Ollama's much smaller default context window.

Background (bug_tracker #118, found 2026-09-03): several Watson jobs build
large, data-driven prompts (an embedded skills.json, a benchmarks doc, a
meeting transcript) and post them to Ollama's /api/generate with no num_ctx
override. Ollama then falls back to a much smaller default than the model's
own trained context — measured 4096 on this Beelink via `ollama ps`, even
for a model like qwen2.5:7b whose trained max is 32768 (`ollama show
qwen2.5:7b`). A prompt that exceeds the active num_ctx is silently
truncated rather than erroring — confirmed to drop the system prompt's task
instructions entirely when it's followed by a large data blob
(jobs/skillbuilder/audit.py's real weekly capability-gap prompt).
"""


def size_num_ctx(prompt: str, min_ctx: int = 4096, headroom_tokens: int = 4096) -> int:
    """Estimate num_ctx for `prompt`, sized above its actual length with real
    headroom for growth — not a bare minimum that breaks again the next time
    the prompt grows (e.g. skills.json gaining more skills over time).

    ~4 chars/token is a crude approximation for English prose, but real-world
    verification (jobs/skillbuilder/router.py, 2026-09-03) measured actual
    `prompt_eval_count` running ~25% over this estimate for a JSON-heavy
    prompt (7803 real vs. 6252 estimated on a 25009-char prompt) — JSON
    punctuation tokenizes less efficiently than prose. headroom_tokens is
    sized to comfortably absorb that gap on top of future growth, not just
    round the estimate up slightly.
    """
    approx_tokens = len(prompt) // 4
    return max(min_ctx, approx_tokens + headroom_tokens)
