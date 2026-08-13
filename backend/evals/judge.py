"""
Phase B judge — a DeepEvalBaseLLM adapter wrapping llm_client.call_tool(), so
the faithfulness judge inherits the existing circuit breaker and provider
abstraction (see llm_client.py) instead of a second, parallel LLM-calling
path. Deliberately configured independent of production via provider_override/
model_override (added to call_tool()/active_model_info() specifically for
this): EVAL_JUDGE_PROVIDER/EVAL_JUDGE_MODEL are separate env vars from
AGENT_LLM_PROVIDER/AGENT_LLM_MODEL — point them at a stronger tier than
production (e.g. prod=Haiku, judge=Sonnet) to reduce the model grading its
own class of mistakes, per this project's confirmed eval-architecture design
decision.

No default judge model is guessed here. An unset EVAL_JUDGE_MODEL fails
loudly at construction time rather than silently falling back to some
assumed-correct model ID this project has never actually verified against a
real account/provider.
"""
import os

from deepeval.models import DeepEvalBaseLLM

from chat import llm_client

EVAL_JUDGE_PROVIDER = os.environ.get("EVAL_JUDGE_PROVIDER") or None  # None => same provider as production
EVAL_JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL") or None

TOOL_NAME = "respond"
TOOL_SCHEMA = {
    "description": "Return your complete response to the prompt, verbatim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "response": {
                "type": "string",
                "description": "Your full response, exactly as the prompt asked for — including "
                               "any JSON structure it requested, with no extra commentary.",
            },
        },
        "required": ["response"],
    },
}


class HouseJudgeModel(DeepEvalBaseLLM):
    """DeepEval's metrics (FaithfulnessMetric, GEval, ...) prompt the judge in
    plain text and expect a plain-text (often JSON-shaped) string back.
    llm_client.call_tool() only speaks structured tool-calls, so every judge
    call is wrapped in a single generic `respond` tool whose one field is
    exactly the text DeepEval asked for — relayed back verbatim, never
    reformatted, so DeepEval's own JSON-parsing of that string still works.
    """

    def __init__(self, provider_override: str | None = None, model_override: str | None = None):
        self.provider_override = provider_override if provider_override is not None else EVAL_JUDGE_PROVIDER
        self.model_override = model_override if model_override is not None else EVAL_JUDGE_MODEL
        if not self.model_override:
            raise RuntimeError(
                "EVAL_JUDGE_MODEL is not set — Phase B evals need an explicit judge model "
                "(ideally a stronger tier than AGENT_LLM_MODEL/production's model). "
                "See evals/judge.py's module docstring."
            )
        # Deliberately skips DeepEvalBaseLLM.__init__ (which would call
        # self.load_model() before model_override is set above) — setting
        # attributes directly and making load_model() a no-op returning self
        # is simpler here since there's no separate SDK client to build.

    def load_model(self):
        return self

    def generate(self, prompt: str) -> str:
        result = llm_client.call_tool(
            system_prompt=(
                "You are an exacting evaluation judge. Respond to the prompt exactly as "
                "instructed, with no commentary outside of what was asked for."
            ),
            user_message=prompt,
            tool_name=TOOL_NAME,
            tool_schema=TOOL_SCHEMA,
            model_override=self.model_override,
            provider_override=self.provider_override,
            # Own circuit-breaker bucket, isolated from production's — see
            # call_tool()'s circuit_key docstring. Without this, an unset
            # EVAL_JUDGE_PROVIDER (judge sharing production's provider) would
            # let the judge's failures fail-fast the production-side answer
            # under test within the same run, and vice versa.
            circuit_key=f"eval-judge:{self.provider_override or llm_client.PROVIDER}",
        )
        if result is None:
            raise RuntimeError(
                f"eval judge ({self.get_model_name()}) call failed — see stderr above for the "
                "underlying provider error"
            )
        return result.get("response", "")

    async def a_generate(self, prompt: str) -> str:
        # No separate async HTTP path wired up (call_tool() is sync) — DeepEval's
        # default async_mode still works correctly through this, just without
        # real concurrency across judge calls within one metric.
        return self.generate(prompt)

    def get_model_name(self) -> str:
        provider, model = llm_client.active_model_info(
            model_override=self.model_override, provider_override=self.provider_override,
        )
        return f"{provider}:{model}"
