from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    password: SecretStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

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


class UserResponse(BaseModel):
    user: User


class ErrorDetail(BaseModel):
    code: str
    message: str
    fields: list = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
