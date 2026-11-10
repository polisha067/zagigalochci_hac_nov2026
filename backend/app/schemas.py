from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Credentials(EmailRequest):
    password: SecretStr

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not 8 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Пароль должен содержать от 8 до 128 символов")
        return value


class RegisterRequest(Credentials):
    name: str = Field(min_length=2, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class User(BaseModel):
    id: str
    email: EmailStr
    name: str
    created_at: datetime
    email_verified_at: datetime


class UserResponse(BaseModel):
    user: User


class VerificationResponse(UserResponse):
    account_created: bool


class VerificationRequired(BaseModel):
    status: str = "verification_required"
    verification_id: str
    email: EmailStr
    expires_in: int
    resend_after: int


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verification_id: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")


class VerifyEmailRequest(ResendRequest):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class PasswordResetRequested(BaseModel):
    status: str = "accepted"
    verification_id: str
    expires_in: int
    resend_after: int


class ResetPasswordRequest(VerifyEmailRequest):
    new_password: SecretStr

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value):
        return Credentials.validate_password(value)


class ErrorDetail(BaseModel):
    code: str
    message: str
    fields: list = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
