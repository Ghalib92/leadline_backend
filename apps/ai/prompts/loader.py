import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from apps.ai.exceptions import PromptError

PROMPTS_DIR = Path(__file__).parent

_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_FILE = re.compile(r"^v([1-9][0-9]*)\.md$")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Prompt:
    agent: str
    version: int
    template: str

    @property
    def id(self) -> str:
        """Recorded on every run, e.g. "judge/v2"."""
        return f"{self.agent}/v{self.version}"

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.template))

    def render(self, **values: object) -> str:
        missing = self.variables - values.keys()
        unexpected = values.keys() - self.variables
        if missing or unexpected:
            raise PromptError(
                f"{self.id}: missing variables {sorted(missing)}, "
                f"unexpected variables {sorted(unexpected)}."
            )
        return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), self.template)


def available_versions(agent: str, directory: Path = PROMPTS_DIR) -> list[int]:
    folder = _agent_folder(agent, directory)
    if not folder.is_dir():
        return []
    versions = (_VERSION_FILE.match(path.name) for path in folder.iterdir())
    return sorted(int(match.group(1)) for match in versions if match)


@cache
def load_prompt(agent: str, version: int | None = None, directory: Path = PROMPTS_DIR) -> Prompt:
    """Load a prompt; without a version, the latest one."""
    versions = available_versions(agent, directory)
    if not versions:
        raise PromptError(f"No prompt files for agent {agent!r} in {directory}.")
    if version is None:
        version = versions[-1]
    elif version not in versions:
        raise PromptError(f"Prompt {agent}/v{version} does not exist. Available: {versions}.")
    template = (_agent_folder(agent, directory) / f"v{version}.md").read_text()
    return Prompt(agent=agent, version=version, template=template)


def _agent_folder(agent: str, directory: Path) -> Path:
    if not _AGENT_NAME.match(agent):
        raise PromptError(f"Invalid agent name {agent!r}.")
    return directory / agent
