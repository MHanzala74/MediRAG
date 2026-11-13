from pydantic import BaseModel, Field, field_validator

class SignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=100)
    role: str     = Field(default="user", pattern="^(user|admin)$")
    admin_code: str | None = Field(default=None, max_length=200,
                                   description="Required to create an admin when ADMIN_SIGNUP_CODE is set")

    @field_validator("username")
    @classmethod
    def strip(cls, v): return v.strip().lower()

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
