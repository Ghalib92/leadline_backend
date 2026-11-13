from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainSerializer

from ..models import User, UserProfile
from ..validators import validate_new_password, validate_otp_code


def _normalized_email(value):
    return User.objects.normalize_email(value)


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ["first_name", "last_name", "updated_at"]
        read_only_fields = ["updated_at"]


class UserSerializer(serializers.ModelSerializer):
    """
    The signed-in user. Only the profile is writable: account flags are set by
    the verification flows and by staff, never by the account holder.
    """

    profile = UserProfileSerializer(required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "is_verified",
            "is_superuser",
            "date_joined",
            "last_login",
            "profile",
        ]
        read_only_fields = [
            "id",
            "email",
            "is_verified",
            "is_superuser",
            "date_joined",
            "last_login",
        ]

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", None)
        if profile_data:
            # Through the relation, so the response reads the updated row.
            profile = getattr(instance, "profile", None) or UserProfile.objects.create(
                user=instance
            )
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()
        return instance


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    profile = UserProfileSerializer(required=False)

    def validate_email(self, value):
        email = _normalized_email(value)
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("This email is already registered.")
        return email

    def validate(self, attrs):
        # Unsaved user so the similarity validator can compare against the email.
        validate_new_password(attrs["password"], user=User(email=attrs["email"]), field="password")
        return attrs


class LoginSerializer(TokenObtainSerializer):
    """Checks the credentials only; the view decides whether to issue a session."""


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return _normalized_email(value)


class VerifyEmailSerializer(EmailSerializer):
    otp_code = serializers.CharField(
        min_length=6, max_length=6, write_only=True, validators=[validate_otp_code]
    )


class ResetPasswordSerializer(VerifyEmailSerializer):
    new_password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        validate_new_password(
            attrs["new_password"], user=User(email=attrs["email"]), field="new_password"
        )
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    def validate_old_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value

    def validate_new_password(self, value):
        return validate_new_password(value, user=self.context["request"].user)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        if attrs["new_password"] == attrs["old_password"]:
            raise serializers.ValidationError(
                {"new_password": "New password must be different from the old password."}
            )
        return attrs


class ChangeEmailSerializer(serializers.Serializer):
    new_email = serializers.EmailField(max_length=255)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    def validate_new_email(self, value):
        email = _normalized_email(value)
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("This email is already registered.")
        return email

    def validate_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Password is incorrect.")
        return value
