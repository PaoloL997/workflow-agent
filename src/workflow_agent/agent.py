from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.tools import ToolDefinition

from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.tools import toolset

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")

# Istruzione aggiunta quando l'utente non ha il permesso di scrittura: i tool
# di scrittura non ci sono, quindi il modello deve dirlo e basta.
NO_WRITE_INSTRUCTIONS = """
The user has NO write permission in Workflow, so the write tools are not
available in this conversation. When the user asks for a change, do not look
for another way to make it and do not say you will do it: answer exactly
"Non hai i permessi per modificare i dati in Workflow." and nothing else.
Reading data and answering questions on how to use Workflow work as usual.
"""


def writes_allowed(ctx: RunContext[AgentDeps], tool_def: ToolDefinition) -> bool:
    """Whether `tool_def` can be offered to the model in this run.

    The write tools are the ones that need approval: offering them to a user
    without write permission would get the user to approve a change that
    Workflow then refuses with 403.
    """
    return ctx.deps.can_write or tool_def.kind != "unapproved"


agent = Agent(
    model=config.llm_model,
    deps_type=AgentDeps,
    instructions=INSTRUCTIONS,
    output_type=[str, DeferredToolRequests],
    toolsets=[toolset.filtered(writes_allowed)]
)


@agent.instructions
def write_permission(ctx: RunContext[AgentDeps]) -> str:
    """Tell the model when the user cannot write, so it says so instead of proposing."""
    return "" if ctx.deps.can_write else NO_WRITE_INSTRUCTIONS
