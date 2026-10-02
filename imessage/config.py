import os
from dataclasses import dataclass, field

DEFAULT_CONFIG = {
    "type": "photon_imessage",
    "id": "photon_imessage",
    "enable": False,
    "project_id": "",
    "project_secret": "",
    "bot_number": "",
    "node_path": "node",
    "auto_install": True,
    "mark_read": True,
    "typing_indicator": True,
    "reply_to_message": False,
    "allow_groups": True,
    "allowed_senders": [],
    "allowed_chats": [],
    "receive_reactions": False,
    "markdown": True,
    "link_preview": True,
    "max_text_length": 4000,
    "max_attachment_mb": 20,
    "media_retention_hours": 24,
    "media_cache_mb": 512,
    "rpc_timeout": 60,
    "startup_timeout": 120,
}


@dataclass(frozen=True)
class Config:
    id: str
    project_id: str = field(repr=False)
    project_secret: str = field(repr=False)
    bot_number: str = ""
    node_path: str = "node"
    auto_install: bool = True
    mark_read: bool = True
    typing_indicator: bool = True
    reply_to_message: bool = False
    allow_groups: bool = True
    allowed_senders: tuple[str, ...] = ()
    allowed_chats: tuple[str, ...] = ()
    receive_reactions: bool = False
    markdown: bool = True
    link_preview: bool = True
    max_text_length: int = 4000
    max_attachment_mb: int = 20
    media_retention_hours: int = 24
    media_cache_mb: int = 512
    rpc_timeout: int = 60
    startup_timeout: int = 120

    @classmethod
    def parse(cls, values: dict):
        data = {k: values.get(k, v) for k, v in DEFAULT_CONFIG.items()}
        for key in ("type", "enable"):
            data.pop(key)
        for key in ("id", "node_path", "bot_number"):
            if not isinstance(data[key], str):
                raise ValueError(f"{key} must be a string")
            data[key] = data[key].strip()
        if not data["id"] or not data["node_path"]:
            raise ValueError("id and node_path must not be empty")
        for key, env in (
            ("project_id", "PHOTON_PROJECT_ID"),
            ("project_secret", "PHOTON_PROJECT_SECRET"),
        ):
            data[key] = str(data[key] or os.environ.get(env, "")).strip()
            if not data[key]:
                raise ValueError(f"Configure {key} or set {env}")
        for key, value in DEFAULT_CONFIG.items():
            if (
                isinstance(value, bool)
                and key in data
                and not isinstance(data[key], bool)
            ):
                raise ValueError(f"{key} must be a boolean")
        for key in ("allowed_senders", "allowed_chats"):
            if not isinstance(data[key], list) or not all(
                isinstance(x, str) and x.strip() for x in data[key]
            ):
                raise ValueError(f"{key} must be a list of nonempty strings")
            data[key] = tuple(x.strip() for x in data[key])
        ranges = {
            "max_text_length": (100, 20000),
            "max_attachment_mb": (1, 100),
            "media_retention_hours": (1, 720),
            "media_cache_mb": (20, 4096),
            "rpc_timeout": (5, 300),
            "startup_timeout": (10, 600),
        }
        for key, (low, high) in ranges.items():
            if type(data[key]) is not int or not low <= data[key] <= high:
                raise ValueError(f"{key} must be an integer in {low}..{high}")
        if data["media_cache_mb"] < data["max_attachment_mb"]:
            raise ValueError("media_cache_mb must be >= max_attachment_mb")
        return cls(**data)
