"""Transfer tickets: let the browser send or fetch one file straight from the API.

The browser never holds a token: every call goes through the UI server, which adds
it. Files are the exception. The UI's host (Vercel) caps a request or response at
4.5 MB, and documents go up to 25 MB. So the UI server, signed in, asks for a ticket
for one path and method; the browser then uses it there directly, within a few
minutes. A ticket is good only on views that list TicketAuthentication, and the view
still checks the person's membership as usual.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import Resolver404, resolve
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import exceptions, serializers
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

SALT = "leadline.transfer-ticket"
KEYWORD = "Ticket"
METHODS = ("GET", "POST")


def make_ticket(user, *, path: str, method: str) -> str:
    return signing.dumps({"u": str(user.pk), "p": path, "m": method}, salt=SALT, compress=True)


class TicketAuthentication(BaseAuthentication):
    """`Authorization: Ticket <ticket>`, or `?ticket=` for a download link."""

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if header and header[0].decode().lower() == KEYWORD.lower():
            if len(header) != 2:
                raise exceptions.AuthenticationFailed("Invalid ticket header.")
            ticket = header[1].decode()
        elif request.method == "GET" and request.query_params.get("ticket"):
            ticket = request.query_params["ticket"]
        else:
            return None
        try:
            data = signing.loads(ticket, salt=SALT, max_age=settings.TRANSFER_TICKET_SECONDS)
        except signing.SignatureExpired as exc:
            raise exceptions.AuthenticationFailed("This link has expired. Try again.") from exc
        except signing.BadSignature as exc:
            raise exceptions.AuthenticationFailed("Invalid ticket.") from exc
        if data.get("p") != request.path or data.get("m") != request.method:
            raise exceptions.AuthenticationFailed("This ticket is for another request.")
        user = get_user_model().objects.filter(pk=data.get("u"), is_active=True).first()
        if user is None:
            raise exceptions.AuthenticationFailed("Invalid ticket.")
        return user, None

    def authenticate_header(self, request):
        return KEYWORD


class TicketRequestSerializer(serializers.Serializer):
    # "/api/v1/organizations/<id>/documents/", or relative to the API root as the UI's
    # proxy names it: "organizations/<id>/documents".
    path = serializers.CharField(max_length=500)
    method = serializers.ChoiceField(choices=METHODS)


def _accepts_tickets(path: str) -> bool:
    try:
        match = resolve(path)
    except Resolver404:
        return False
    view = getattr(match.func, "view_class", None)
    return bool(view) and TicketAuthentication in getattr(view, "authentication_classes", ())


@extend_schema(
    tags=["Transfers"],
    request=TicketRequestSerializer,
    responses=inline_serializer(
        "TransferTicket",
        {"url": serializers.URLField(), "ticket": serializers.CharField()},
    ),
)
class TransferTicketView(APIView):
    """A ticket for one file transfer, for the signed-in person."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TicketRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        path, method = serializer.validated_data["path"], serializer.validated_data["method"]
        if not path.startswith("/"):
            # Relative to the API root, as the UI calls it ("organizations/<id>/documents").
            root = request.path.removesuffix("transfers/ticket/")
            path = f"{root}{path.strip('/')}/"
        if ".." in path.split("/") or not _accepts_tickets(path):
            raise serializers.ValidationError({"path": "No file transfer at this path."})
        ticket = make_ticket(request.user, path=path, method=method)
        return Response({"url": request.build_absolute_uri(path), "ticket": ticket})
