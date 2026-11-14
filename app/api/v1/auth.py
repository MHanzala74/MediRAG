"""Auth endpoints — signup, login (JWT)."""
import hmac
from fastapi import APIRouter, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends
from app.schemas.auth import SignupRequest, TokenResponse
from app.core.security import hash_password, verify_password, create_access_token
from app.db.models import get_user, insert_user, touch_last_login
from app.core.config import settings
from app.utils.logger import logger

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", status_code=201, summary="Register a new user")
def signup(req: SignupRequest):
    if req.role == "admin":
        code = settings.admin_signup_code
        if code:
            if not hmac.compare_digest((req.admin_code or "").encode(), code.encode()):
                raise HTTPException(status_code=403, detail="Invalid admin signup code")
        elif settings.is_production:
            raise HTTPException(
                status_code=403,
                detail="Admin signup is disabled. Set ADMIN_SIGNUP_CODE to create admin accounts.",
            )
        else:
            logger.warning("Admin account created without ADMIN_SIGNUP_CODE (development mode)")
    if get_user(req.username):
        raise HTTPException(status_code=400, detail="Username already exists")
    insert_user(req.username, hash_password(req.password), req.role)
    logger.info("New user registered: %s (role=%s)", req.username, req.role)
    return {"message": f"User '{req.username}' created successfully", "role": req.role}


@router.post("/login", response_model=TokenResponse, summary="Login and get JWT token")
def login(form: OAuth2PasswordRequestForm = Depends()):
    user = get_user(form.username)
    if not user or not verify_password(form.password, user["password"]):
        logger.warning("Failed login attempt for: %s", form.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user["is_active"]:
        logger.warning("Login refused for disabled account: %s", user["username"])
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact an administrator.")

    try:
        touch_last_login(user["username"])
    except Exception as e:                      # analytics must never block a login
        logger.warning("Could not store last_login: %s", e)

    token = create_access_token({"sub": user["username"], "role": user["role"]})
    logger.info("User logged in: %s", form.username)
    return TokenResponse(
        access_token=token,
        username=user["username"],
        role=user["role"],
    )
