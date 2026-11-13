from rest_framework import status
from rest_framework.exceptions import APIException


class Conflict(APIException):
    """The request is valid but clashes with the current state (e.g. a run in progress)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This conflicts with the current state."
    default_code = "conflict"

    def __init__(self, detail=None, code=None):
        # Same body shape as the other services' errors: {"detail": …, "code": …}.
        body = {"detail": detail or self.default_detail, "code": code or self.default_code}
        super().__init__(body)
