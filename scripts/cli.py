# scripts/cli.py
import os
from collections.abc import AsyncIterable

import httpx
from pydantic_ai import DeferredToolRequests, DeferredToolResults, RunContext
from pydantic_ai.messages import AgentStreamEvent, FunctionToolCallEvent, FunctionToolResultEvent

from workflow_agent.agent import agent
from workflow_agent.config import config
from workflow_agent.deps import AgentDeps
from workflow_agent.rag.retrieval import ChromaDB

chromadb = ChromaDB()


async def show_tools(ctx: RunContext[AgentDeps], events: AsyncIterable[AgentStreamEvent]) -> None:
    """Print each tool call and its result as the agent runs."""
    async for event in events:
        if isinstance(event, FunctionToolCallEvent):
            print(f"  [TOOL] {event.part.tool_name}({event.part.args_as_dict()})")
        elif isinstance(event, FunctionToolResultEvent):
            content = str(event.part.content)
            if len(content) > 300:
                content = content[:300] + "..."
            print(f"  [TOOL] {event.part.tool_name} -> {content}")


def ask_approvals(requests: DeferredToolRequests) -> DeferredToolResults:
    approvals = {}
    for call in requests.approvals:
        print(f"\n[PROPOSTA] {call.tool_name}({call.args})")
        approvals[call.tool_call_id] = input("Approvi? [s/n] ").strip().lower() == "s"
    return DeferredToolResults(approvals=approvals)


def main() -> None:
    with httpx.Client(
        base_url=config.workflow_base_url,
        headers={"Authorization": f"Token {os.environ['WORKFLOW_TOKEN']}"},
        timeout=30,
    ) as client:
        deps = AgentDeps(user="paolo", client=client, chromadb=chromadb)
        history = []

        while True:
            text = input("\n> ").strip()
            if text in ("", "exit", "quit"):
                break

            result = agent.run_sync(
                text, deps=deps, message_history=history, event_stream_handler=show_tools
            )
            while isinstance(result.output, DeferredToolRequests):
                result = agent.run_sync(
                    message_history=result.all_messages(),
                    deferred_tool_results=ask_approvals(result.output),
                    deps=deps,
                    event_stream_handler=show_tools,
                )
            history = result.all_messages()
            print(f"\n{result.output}")


if __name__ == "__main__":
    main()