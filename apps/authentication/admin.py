from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    extra = 0
    fields = ["first_name", "last_name", "updated_at"]
    readonly_fields = ["updated_at"]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)

    list_display = [
        "email",
        "is_staff",
        "is_superuser",
        "is_verified",
        "is_active",
        "date_joined",
        "last_login",
    ]
    list_filter = ["is_staff", "is_superuser", "is_verified", "is_active", "date_joined"]
    search_fields = ["email", "profile__first_name", "profile__last_name"]
    ordering = ["-date_joined"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_verified",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    readonly_fields = ("date_joined", "updated_at", "last_login")

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "is_verified",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )

    def get_inline_instances(self, request, obj=None):
        # The profile row is created by a signal when the user is saved.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "first_name", "last_name", "updated_at"]
    search_fields = ["first_name", "last_name", "user__email"]
    list_filter = ["updated_at"]
    ordering = ["-updated_at"]
    readonly_fields = ["updated_at"]
