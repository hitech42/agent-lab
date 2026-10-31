"""Helpers shared by the manual scripts (never imported by the package or CI)."""

from agent_lab.harness.tracing import AttemptTrace


def format_trace(trace: AttemptTrace) -> str:
    lines = []
    for t in trace.turns:
        calls = ", ".join(
            f"{c.name}{'!' if c.is_error else ''}({c.duration_s:.2f}s)" for c in t.tool_calls
        )
        lines.append(
            f"turn {t.turn}: in={t.input_tokens} out={t.output_tokens} "
            f"cache_w={t.cache_creation_input_tokens} cache_r={t.cache_read_input_tokens} "
            f"stop={t.stop_reason} {t.latency_s:.2f}s (total {t.total_latency_s:.2f}s) "
            f"retries={t.retries}" + (f" tools=[{calls}]" if calls else "")
        )
    lines.append(
        f"total: in={trace.total_input_tokens} out={trace.total_output_tokens} "
        f"cache_w={trace.total_cache_creation_input_tokens} "
        f"cache_r={trace.total_cache_read_input_tokens} "
        f"tool_calls={trace.tool_call_count} tool_errors={trace.tool_error_count} "
        f"latency={trace.total_latency_s:.2f}s"
    )
    return "\n".join(lines)
