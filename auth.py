from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from werkzeug.security import generate_password_hash, check_password_hash
from starlette.requests import Request

from models import SessionLocal, User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_current_user(request: Request):
    """Get the logged-in user from session, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user
    finally:
        db.close()


def require_login(request: Request):
    """Dependency that redirects to login if not authenticated."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return user


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("auth/signup.html", {"request": request})


@router.post("/signup", response_class=HTMLResponse)
async def signup(request: Request, email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse("auth/signup.html", {
            "request": request, "error": "Passwords do not match"
        })

    if len(password) < 6:
        return templates.TemplateResponse("auth/signup.html", {
            "request": request, "error": "Password must be at least 6 characters"
        })

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            return templates.TemplateResponse("auth/signup.html", {
                "request": request, "error": "Email already registered"
            })

        user = User(
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        request.session["user_id"] = user.id
        return RedirectResponse("/dashboard", status_code=303)
    finally:
        db.close()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user or not check_password_hash(user.password_hash, password):
            return templates.TemplateResponse("auth/login.html", {
                "request": request, "error": "Invalid email or password"
            })

        request.session["user_id"] = user.id
        return RedirectResponse("/dashboard", status_code=303)
    finally:
        db.close()


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login", status_code=303)
