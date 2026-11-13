"""Leadline's agent layer: LangGraph graphs, agents, prompts and tools.

Layout:
    config/   which model each agent role uses (models.yaml) and its loader
    llm.py    get_chat_model(role): the only place a chat model is constructed
    prompts/  versioned prompt files, one folder per agent
    agents/   one module per agent
    graphs/   one package per LangGraph graph
    tools/    search, page fetching and opportunity-source adapters
    schemas/  Pydantic structured outputs
    events.py progress events that feed the run timeline in the UI

This package never writes to the ORM: it reads through apps/*/selectors.py and
writes through apps/*/services.py.
"""
