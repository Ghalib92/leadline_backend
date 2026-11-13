import factory

from ..models import User

DEFAULT_PASSWORD = "Str0ng-Passw0rd!"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    password = factory.django.Password(DEFAULT_PASSWORD)
    is_verified = True
