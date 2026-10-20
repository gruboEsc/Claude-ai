from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from .config import Config

BETAS = [
    "server-side-fallback-2026-07-01",  # fallbacks="default": re-run refused requests on a fallback model
    "compact-2026-01-12",  # server-side compaction of very long dialogs
    "thinking-binding-controls-2026-08-01",  # drop stale thinking instead of failing (see THINKING)
]
# If the bot's system prompt or tool set changes between deploys, thinking blocks in
# older dialogs no longer match their prefix; dropping them keeps those dialogs working.
THINKING = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
MAX_STEPS = 8  # model calls per reply: pause_turn resumes and client tool rounds

# (input, output, cache read) $ per 1M tokens
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

ToolRunner = Callable[[str, dict], Awaitable[str]]


@dataclass
class Reply:
    text: str = ""
    turns: list[dict] = field(default_factory=list)  # turns to append to history, in order
    refused: bool = False
    truncated: bool = False
    searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


def cost(model: str, inp: int, out: int, cached: int) -> float | None:
    if model not in PRICES:
        return None
    p_in, p_out, p_cache = PRICES[model]
    return (inp * p_in + out * p_out + cached * p_cache) / 1_000_000


class Claude:
    def __init__(self, cfg: Config, tools: list[dict]):
        self.cfg = cfg
        self.client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
        # Fixed for the life of the process: a tool set that changed mid-dialog
        # would restart the prompt cache.
        self.tools = list(tools)
        if cfg.web_search:
            self.tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 5})

    async def reply(
        self,
        system: str,
        history: list[dict],
        effort: str,
        on_text: Callable[[str], Awaitable[None]],
        run_tool: ToolRunner,
    ) -> Reply:
        """Stream one assistant reply for `history` (which ends with the new user turn)."""
        result = Reply()
        messages = list(history)

        for _ in range(MAX_STEPS):
            if result.text and not result.text.endswith("\n"):
                result.text += "\n\n"
            async with self.client.beta.messages.stream(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=system,
                messages=messages,
                tools=self.tools,
                thinking=THINKING,
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
                context_management={"edits": [{"type": "compact_20260112"}]},
                fallbacks="default",
                betas=BETAS,
            ) as stream:
                async for chunk in stream.text_stream:
                    result.text += chunk
                    await on_text(result.text)
                message = await stream.get_final_message()

            usage = message.usage
            result.input_tokens += usage.input_tokens + (usage.cache_creation_input_tokens or 0)
            result.cached_tokens += usage.cache_read_input_tokens or 0
            result.output_tokens += usage.output_tokens
            if usage.server_tool_use:
                result.searches += usage.server_tool_use.web_search_requests or 0

            if message.stop_reason == "refusal":
                result.refused = True
                return result

            # Keep every block (thinking, search results, compaction, fallback) exactly
            # as returned: the API needs them verbatim on the next request.
            turn = {
                "role": "assistant",
                "content": [b.model_dump(mode="json", by_alias=True, exclude_none=True) for b in message.content],
            }
            result.turns.append(turn)
            messages.append(turn)

            if message.stop_reason == "pause_turn":
                continue
            if message.stop_reason == "tool_use":
                results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        **await _run(run_tool, block.name, block.input),
                    }
                    for block in message.content
                    if block.type == "tool_use"
                ]
                tool_turn = {"role": "user", "content": results}
                result.turns.append(tool_turn)
                messages.append(tool_turn)
                continue

            result.truncated = message.stop_reason == "max_tokens"
            break

        return result


async def _run(run_tool: ToolRunner, name: str, args: object) -> dict:
    # Tool inputs stream eagerly and arrive unvalidated, so a malformed one is
    # reported back to the model as an error instead of being executed.
    if not isinstance(args, dict):
        return {"content": "Invalid tool input: expected a JSON object.", "is_error": True}
    try:
        return {"content": await run_tool(name, args)}
    except ValueError as e:
        return {"content": str(e), "is_error": True}
