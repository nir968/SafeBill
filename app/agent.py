import json
import logging

from google import genai
from google.genai import errors as genai_errors
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_tools import get_user_bills_summary, search_user_bills
from app.config import settings

logger = logging.getLogger("safebill.agent")

# Hard cap on the number of tool-call round trips in one question, so a
# confused or looping model can't turn one /api/chat request into an
# unbounded chain of Gemini calls.
_MAX_TOOL_ROUNDS = 5

_GET_BILLS_SUMMARY_TOOL = {
    "type": "function",
    "name": "get_bills_summary",
    "description": (
        "Get a summary of the current user's bills: total pending and paid "
        'amounts per currency, total bill count, and a breakdown of bills '
        'by category. Use this for questions like "how much do I owe", '
        '"how many bills do I have", or "what are my expenses by category".'
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_SEARCH_BILLS_TOOL = {
    "type": "function",
    "name": "search_bills",
    "description": (
        "Search the current user's bills by institution name (partial "
        'match), category, or payment status. Use this for questions like '
        '"what did I pay to X", "show me my fines", or "what\'s still '
        'pending from the electric company".'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Free text to search for - an institution name, or an "
                    'exact category (e.g. "הוצאה קבועה") or status '
                    '(e.g. "paid", "analyzed") value.'
                ),
            }
        },
        "required": ["query"],
    },
}

_AGENT_SYSTEM_PROMPT = """You are the SafeBill Agent, a financial assistant that answers questions about one specific user's bills and expenses.

Rules:
- Always answer in Hebrew, in a warm, concise, helpful tone - a few sentences, not a report.
- Use the get_bills_summary and search_bills tools to fetch real data before answering any question involving amounts, bill counts, categories, or payment status. Never guess, estimate, or recall a financial figure from earlier in the conversation instead of calling a tool.
- If a question is about a specific institution, category, or status, call search_bills with that as the query. For overall totals or "how much do I owe" style questions, call get_bills_summary.
- Always state a currency next to every amount you mention (e.g. "150 ₪" or "$40"), never a bare number.
- If the user asks something unrelated to their bills or expenses, politely explain that you can only help with questions about their SafeBill data - do not attempt to answer it anyway."""


class AgentError(Exception):
    """The agent's LLM call failed - network, auth, rate limit, or a
    server-side error from the provider. Callers (the /api/chat endpoint)
    are expected to turn this into an HTTP error, not swallow it - same
    layered error-handling shape as VisionProviderError elsewhere in this
    app."""


# Zero-arg client: resolves GEMINI_API_KEY (or GOOGLE_API_KEY) from the
# environment, same as GeminiVisionProvider in app/vision.py. Never
# hardcode a key here.
_client = genai.Client()


async def ask_agent(db: AsyncSession, user_id: int, question: str) -> str:
    """
    Answers a free-text question about `user_id`'s bills, using tool-calling
    to fetch real data before answering. Shared by both the Telegram bot's
    text handler and the web dashboard's chat widget - both call this
    indirectly via POST /api/chat (app/routers/web.py); telegram_bot.py is
    a separate deployable process and never imports this module directly.

    Runs on the Gemini Interactions API (google-genai), not the Anthropic
    Messages API this file used previously. That SDK's tool-calling has no
    "tool runner" convenience wrapper (no automatic-dispatch equivalent to
    Anthropic's `client.beta.messages.tool_runner` - see the removed
    version of this file in git history) - the round-trip loop below
    (call, inspect for function_call outputs, execute, send results back
    via previous_interaction_id, repeat) is hand-rolled to match it. Tool
    calls/results live on `interaction.steps` (a FunctionCallStep /
    FunctionResultStep union), not `interaction.outputs` - there is no
    `.outputs` attribute on this SDK's Interaction resource.

    Security note, unchanged from the previous version: `user_id` is
    deliberately NOT a parameter either tool's JSON schema exposes to the
    model - both _GET_BILLS_SUMMARY_TOOL and _SEARCH_BILLS_TOOL declare
    only the arguments the model is allowed to choose (none, and `query`,
    respectively). The actual Python callables that run when the model
    asks for one of these tools are closures over `db` and `user_id`
    defined below, dispatched by tool *name* - the model can select which
    tool to call and, for search_bills, what to search for, but it can
    never supply or override whose data is being queried. If user_id were
    a model-fillable argument instead, a manipulated or merely confused
    conversation could make the model call search_bills against someone
    else's user_id and leak their financial data.
    """

    async def _get_bills_summary(**_ignored_args) -> dict:
        return await get_user_bills_summary(db, user_id)

    async def _search_bills(query: str) -> list:
        return await search_user_bills(db, user_id, query)

    tool_impls = {
        "get_bills_summary": _get_bills_summary,
        "search_bills": _search_bills,
    }
    tools = [_GET_BILLS_SUMMARY_TOOL, _SEARCH_BILLS_TOOL]

    try:
        interaction = await _client.aio.interactions.create(
            model=settings.gemini_agent_model,
            input=[
                {
                    "type": "text",
                    "text": f"{_AGENT_SYSTEM_PROMPT}\n\nשאלת המשתמש: {question}",
                }
            ],
            tools=tools,
        )

        rounds = 0
        while rounds < _MAX_TOOL_ROUNDS:
            function_calls = [
                step for step in (interaction.steps or []) if step.type == "function_call"
            ]
            if not function_calls:
                break

            results = []
            for call in function_calls:
                impl = tool_impls.get(call.name)
                if impl is None:
                    logger.warning("ask_agent: model requested unknown tool %r", call.name)
                    tool_result = {"error": f"Unknown tool: {call.name}"}
                else:
                    tool_result = await impl(**(call.arguments or {}))

                results.append(
                    {
                        "type": "function_result",
                        "name": call.name,
                        "call_id": call.id,
                        # Serialized explicitly rather than passed as a raw
                        # dict/list - keeps the boundary to the model
                        # unambiguous regardless of whether this SDK
                        # surface accepts non-string results, matching how
                        # every other LLM boundary in this app deals in
                        # JSON strings, not native Python objects.
                        "result": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

            interaction = await _client.aio.interactions.create(
                model=settings.gemini_agent_model,
                previous_interaction_id=interaction.id,
                input=results,
            )
            rounds += 1
        else:
            # Loop exhausted _MAX_TOOL_ROUNDS via the while condition,
            # not via `break` - the model kept calling tools without
            # ever settling on a final answer.
            logger.warning(
                "ask_agent: hit the %d-round tool-call cap for user %s without a final answer",
                _MAX_TOOL_ROUNDS,
                user_id,
            )
    except genai_errors.ClientError as exc:
        raise AgentError(f"הסוכן דחה את הבקשה: {exc}") from exc
    except genai_errors.ServerError as exc:
        raise AgentError(f"שגיאת שרת בסוכן: {exc}") from exc
    except genai_errors.APIError as exc:
        raise AgentError(f"שגיאה בשירות ה-AI: {exc}") from exc

    # SDK-derived: concatenated text from the trailing model_output step,
    # already walked out of interaction.steps for us - see Interaction's
    # _populate_output_helpers in google.genai's interaction.py.
    answer = (interaction.output_text or "").strip()

    if not answer:
        logger.warning("ask_agent: no text output produced for user %s", user_id)
        return "מצטער, לא הצלחתי לנסח תשובה. נסה לשאול בצורה אחרת."

    return answer
