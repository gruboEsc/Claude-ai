from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from .config import Config

BETAS = [
    "server-side-fallback-2026-07-01",  # fallbacks="default": re-run refused requests on a fallback model
    "compact-2026-01-12",  # server-side compaction of very long dialogs
]
MAX_CONTINUATIONS = 5  # pause_turn resumes during long web searches


@dataclass
class Reply:
    text: str = ""
    turns: list[dict] = field(default_factory=list)  # assistant turns to append to history
    refused: bool = False
    truncated: bool = False
    searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class Claude:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = AsyncAnthropic(api_key=cfg.anthropic_api_key)
        self.tools = (
            [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}] if cfg.web_search else []
        )

    async def reply(
        self,
        system: str,
        history: list[dict],
        effort: str,
        on_text: Callable[[str], Awaitable[None]],
    ) -> Reply:
        """Stream one assistant reply for `history` (which ends with the new user turn)."""
        result = Reply()
        messages = list(history)

        for _ in range(MAX_CONTINUATIONS):
            async with self.client.beta.messages.stream(
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                system=system,
                messages=messages,
                tools=self.tools,
                thinking={"type": "adaptive"},
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

            if message.stop_reason != "pause_turn":
                result.truncated = message.stop_reason == "max_tokens"
                break

        return result
