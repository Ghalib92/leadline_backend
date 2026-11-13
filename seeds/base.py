from dataclasses import dataclass


@dataclass(frozen=True)
class SeedUser:
    email: str
    password: str
    first_name: str
    last_name: str
    group: str
    is_staff: bool = False
    is_superuser: bool = False