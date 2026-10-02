import base64
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    chat_id: str
    phone: str
    kind: str
    user_id: str = ""

    def __post_init__(self):
        if (
            not isinstance(self.chat_id, str)
            or not self.chat_id
            or not isinstance(self.phone, str)
        ):
            raise ValueError("Invalid iMessage route")
        if self.kind not in ("dm", "group"):
            raise ValueError("Invalid iMessage chat type")

    def encode(self) -> str:
        fields = [self.chat_id, self.phone, self.kind]
        if self.user_id:
            fields.append(self.user_id)
        value = json.dumps(fields, separators=(",", ":")).encode()
        return "im1_" + base64.urlsafe_b64encode(value).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str):
        if (
            not isinstance(value, str)
            or not value.startswith("im1_")
            or len(value) > 8192
        ):
            raise ValueError(
                "Use a saved iMessage session_id; raw sender IDs are not routes"
            )
        try:
            value = value[4:]
            parts = json.loads(
                base64.b64decode(
                    value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
                )
            )
            if (
                not isinstance(parts, list)
                or len(parts) not in (3, 4)
                or not all(isinstance(x, str) for x in parts)
            ):
                raise ValueError("Invalid route fields")
            return cls(*parts)
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("Invalid iMessage session route") from exc

    def payload(self) -> dict:
        return {"chat_id": self.chat_id, "phone": self.phone, "kind": self.kind}
