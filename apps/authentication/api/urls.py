from django.urls import path

from . import views

app_name = "authentication"

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("refresh-token/", views.RefreshTokenView.as_view(), name="refresh_token"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="verify_email"),
    path(
        "resend-verification/",
        views.ResendVerificationView.as_view(),
        name="resend_verification",
    ),
    path("forgot-password/", views.ForgotPasswordView.as_view(), name="forgot_password"),
    path("reset-password/", views.ResetPasswordView.as_view(), name="reset_password"),
    path("change-password/", views.ChangePasswordView.as_view(), name="change_password"),
    path("change-email/", views.ChangeEmailView.as_view(), name="change_email"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
]
