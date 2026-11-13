from typing import TypedDict


class ProposalState(TypedDict, total=False):
    requirements: dict  # apps.ai.schemas.proposal.ProposalRequirements
    sections: list[dict]  # outline, then evidence and content are added
    review: dict | None
    stop: str  # why writing stopped early (tokens or time ran out)
