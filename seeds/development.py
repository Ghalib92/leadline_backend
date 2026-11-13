from .base import SeedUser

GROUPS = [
    "Administrators",
    "Developers",
    "Viewers",
]

USERS = [
    SeedUser(
        email="admin@example.com",
        password="Admin123!ChangeMe",
        first_name="Template",
        last_name="Admin",
        group="Administrators",
        is_staff=True,
        is_superuser=True,
    ),
    SeedUser(
        email="developer@example.com",
        password="Developer123!ChangeMe",
        first_name="Frontend",
        last_name="Developer",
        group="Developers",
    ),
    SeedUser(
        email="viewer@example.com",
        password="Viewer123!ChangeMe",
        first_name="Demo",
        last_name="Viewer",
        group="Viewers",
    ),
]