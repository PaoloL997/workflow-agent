from pydantic_ai import Agent, DeferredToolRequests 
# DeferredToolRequest Conversazione terminata perché l'utente deve approvare l'uso di un tool specifico

from src.workflow_agent.deps import AgentDeps

agent = Agent(
    model="test",
    deps_type=AgentDeps,
    instructions="Sei un assistente che aiuta gli utenti dell’app Workflow a consultare e correggere i dati di commesse, documenti e revisioni",
    output_type=[str, DeferredToolRequests]
)