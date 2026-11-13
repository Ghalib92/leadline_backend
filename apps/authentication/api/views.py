import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .. import services
from ..selectors import get_user_by_email
from .serializers import (
    ChangeEmailSerializer,
    ChangePasswordSerializer,
    EmailSerializer,
    LoginSerializer,
    RegistrationSerializer,
    ResetPasswordSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)

logger = logging.getLogger(__name__)

INVALID_CODE = {"detail": "Invalid or expired code.", "code": "invalid_code"}


class PublicAuthView(APIView):
    """Anonymous endpoint, rate-limited per client under `throttle_scope`."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]

    def get_authenticate_header(self, request):
        # Without this DRF turns AuthenticationFailed (bad login) into a 403.
        return 'Bearer realm="api"'


class RegisterView(PublicAuthView):
    """
    POST /auth/register/
    { "email", "password", "profile": { "first_name", "last_name" } }

    Creates an unverified account and emails a verification code. No session is
    issued until the email is verified.
    """

    throttle_scope = "registration"
    serializer_class = RegistrationSerializer

    def post(self, request):
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.register_user(**serializer.validated_data)

        detail = "Account created. Check your email for a verification code."
        try:
            services.send_verification_otp(user)
        except Exception:
            # The account exists; tell the client so it can offer "resend".
            detail = (
                "Account created, but we could not send the verification email. "
                "Please use the resend option."
            )

        return Response(
            {"detail": detail, "email": user.email, "requires_verification": True},
            status=status.HTTP_201_CREATED,
        )


class LoginView(PublicAuthView):
    """
    POST /auth/login/
    { "email", "password" } -> { "refresh", "access", "user" }
    """

    throttle_scope = "login"
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.user

        if not user.is_verified:
            return Response(
                {
                    "detail": "Please verify your email before logging in.",
                    "code": "email_not_verified",
                    "email": user.email,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(services.issue_session(user), status=status.HTTP_200_OK)


class RefreshTokenView(TokenRefreshView):
    """
    POST /auth/refresh-token/
    { "refresh" } -> { "access", "refresh" }

    Refresh tokens rotate: the one sent is blacklisted and a new one returned.
    """


class LogoutView(TokenBlacklistView):
    """
    POST /auth/logout/
    { "refresh" }

    Blacklists the refresh token. The access token lives out its short lifetime.
    """


class VerifyEmailView(PublicAuthView):
    """
    POST /auth/verify-email/
    { "email", "otp_code" } -> session

    The code proves the mailbox — the same proof login asks for — so a
    successful verification signs the user straight in.
    """

    throttle_scope = "otp"
    serializer_class = VerifyEmailSerializer

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = get_user_by_email(serializer.validated_data["email"])
        if user is None:
            return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)

        if user.is_verified:
            return Response(
                {"detail": "Email already verified. You can log in."},
                status=status.HTTP_200_OK,
            )

        if not services.verify_email(user, serializer.validated_data["otp_code"]):
            return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"detail": "Email verified successfully.", **services.issue_session(user)},
            status=status.HTTP_200_OK,
        )


class ResendVerificationView(PublicAuthView):
    """
    POST /auth/resend-verification/
    { "email" }

    Always 200 with the same body, so it does not reveal whether the email is
    registered or already verified.
    """

    throttle_scope = "otp"
    serializer_class = EmailSerializer

    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = get_user_by_email(email)
        if user is not None and user.is_active and not user.is_verified:
            try:
                services.send_verification_otp(user)
            except Exception:
                logger.exception("Resend verification email failed for %s", email)

        return Response(
            {
                "detail": "If an unverified account exists, a new code has been sent.",
                "email": email,
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(PublicAuthView):
    """
    POST /auth/forgot-password/
    { "email" }

    Always 200 with the same body, so it does not reveal whether the email is
    registered.
    """

    throttle_scope = "otp"
    serializer_class = EmailSerializer

    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = get_user_by_email(email)
        if user is not None and user.is_active:
            try:
                services.send_password_reset_otp(user)
            except Exception:
                logger.exception("Password reset email failed for %s", email)

        return Response(
            {
                "detail": "If an account exists with this email, a code has been sent.",
                "email": email,
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(PublicAuthView):
    """
    POST /auth/reset-password/
    { "email", "otp_code", "new_password" } -> session

    Every other session is signed out.
    """

    throttle_scope = "otp"
    serializer_class = ResetPasswordSerializer

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = get_user_by_email(data["email"])
        if user is None or not user.is_active:
            return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)

        if not services.reset_password(user, data["otp_code"], data["new_password"]):
            return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"detail": "Password reset successfully.", **services.issue_session(user)},
            status=status.HTTP_200_OK,
        )


class ChangePasswordView(APIView):
    """
    POST /auth/change-password/
    { "old_password", "new_password", "confirm_password" } -> session

    Every other session is signed out; this one gets fresh tokens.
    """

    permission_classes = [IsAuthenticated]

    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        services.change_password(request.user, serializer.validated_data["new_password"])

        return Response(
            {"detail": "Password changed successfully.", **services.issue_session(request.user)},
            status=status.HTTP_200_OK,
        )


class ChangeEmailView(APIView):
    """
    POST /auth/change-email/
    { "new_email", "password" }

    The new address must be verified; existing refresh tokens are revoked.
    """

    permission_classes = [IsAuthenticated]

    serializer_class = ChangeEmailSerializer

    def post(self, request):
        serializer = ChangeEmailSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        services.change_email(request.user, serializer.validated_data["new_email"])

        detail = "Email changed. Verify the new address using the code sent to it."
        try:
            services.send_verification_otp(request.user)
        except Exception:
            detail = (
                "Email changed, but the verification email failed. Please use the resend option."
            )

        return Response(
            {"detail": detail, "email": request.user.email, "requires_verification": True},
            status=status.HTTP_200_OK,
        )


class ProfileView(APIView):
    """
    GET  /auth/profile/
    PUT  /auth/profile/   { "profile": { "first_name", "last_name" } }
    PATCH /auth/profile/  (same body; both are partial)
    """

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get(self, request):
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)

    def put(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Profile updated successfully.", "data": serializer.data},
            status=status.HTTP_200_OK,
        )

    patch = put