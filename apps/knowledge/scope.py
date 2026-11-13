"""Which of an organization's documents an AI job may read.

Every search takes a scope, so no caller can forget to restrict it, and an empty
folder list can't silently mean "everything":

- `all`: every processed document of the organization
- `folders`: only documents in `folder_ids` (none selected reads nothing)
- `none`: no documents at all
"""

from dataclasses import dataclass

ALL, FOLDERS, NONE = "all", "folders", "none"


@dataclass(frozen=True)
class KnowledgeScope:
    mode: str = ALL
    folder_ids: tuple[str, ...] = ()

    @classmethod
    def all(cls) -> "KnowledgeScope":
        return cls(ALL)

    @classmethod
    def none(cls) -> "KnowledgeScope":
        return cls(NONE)

    @classmethod
    def of_folders(cls, folder_ids) -> "KnowledgeScope":
        return cls(FOLDERS, tuple(str(fid) for fid in folder_ids))

    @classmethod
    def from_config(cls, data: dict | None) -> "KnowledgeScope":
        """From `run.config["knowledge"]`; anything missing or unknown reads all."""
        data = data or {}
        mode = data.get("mode")
        if mode == NONE:
            return cls.none()
        if mode == FOLDERS:
            return cls.of_folders(data.get("folder_ids", []))
        return cls.all()

    @classmethod
    def of_run(cls, config: dict | None) -> "KnowledgeScope":
        """The scope a run recorded; runs from before scopes used their folders if any
        were chosen, else every document."""
        config = config or {}
        if "knowledge" in config:
            return cls.from_config(config["knowledge"])
        folder_ids = [f["id"] for f in config.get("folders", []) if f.get("id")]
        return cls.of_folders(folder_ids) if folder_ids else cls.all()

    def to_config(self) -> dict:
        return {"mode": self.mode, "folder_ids": list(self.folder_ids)}

    @property
    def reads_nothing(self) -> bool:
        return self.mode == NONE or (self.mode == FOLDERS and not self.folder_ids)
