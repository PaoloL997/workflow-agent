from pathlib import Path

from pydantic_ai import Agent, DeferredToolRequests

from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.tools import toolset

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text(encoding="utf-8")

agent = Agent(
    model=config.llm_model,
    deps_type=AgentDeps,
    instructions=INSTRUCTIONS,
    output_type=[str, DeferredToolRequests],
    toolsets=[toolset]
)