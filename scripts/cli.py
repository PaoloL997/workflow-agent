# scripts/cli.py
import os

import httpx
from pydantic_ai import DeferredToolRequests, DeferredToolResults

from workflow_agent.agent import agent
from workflow_agent.config import config
from workflow_agent.deps import AgentDeps


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
        deps = AgentDeps(user="paolo", client=client)
        history = []

        while True:
            text = input("\n> ").strip()
            if text in ("", "exit", "quit"):
                break

            result = agent.run_sync(text, deps=deps, message_history=history)
            while isinstance(result.output, DeferredToolRequests):
                result = agent.run_sync(
                    message_history=result.all_messages(),
                    deferred_tool_results=ask_approvals(result.output),
                    deps=deps,
                )
            history = result.all_messages()
            print(f"\n{result.output}")


if __name__ == "__main__":
    main()