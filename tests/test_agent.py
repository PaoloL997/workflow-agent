from workflow_agent.db import connect

def test_agent():
    from workflow_agent.agent import agent
    from workflow_agent.deps import AgentDeps

    deps = AgentDeps(user="paolo", db=connect())
    out = agent.run_sync("ciao", deps=deps)

    assert isinstance(out.output, str)
