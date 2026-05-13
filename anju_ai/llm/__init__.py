"""LLM client layer.

Single interface, multiple providers. Adding a new provider requires
implementing one class:

    class LLMClient(Protocol):
        async def complete(
            prompt: str,
            schema: type[BaseModel],
            model: str,
            max_input_tokens: int,
            max_output_tokens: int,
            temperature: float = 0.2,
            timeout_s: float = 30.0,
        ) -> LLMResponse: ...

    @dataclass
    class LLMResponse:
        parsed: BaseModel
        raw_text: str
        tokens_in: int
        tokens_out: int
        latency_ms: int
        cost_inr: float

Phase 0: GeminiClient + ClaudeClient stubs (no real calls yet)
Phase 2: GeminiClient implemented (for catalyst_review)
Phase 3: ClaudeClient implemented (for weekly_critic)

Prompts live in anju_ai/llm/prompts/ as markdown files with YAML frontmatter
(see docs/AGENT_PROTOCOL.md §3).
"""
