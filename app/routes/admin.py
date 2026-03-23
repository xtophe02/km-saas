"""Admin routes."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.auth import get_current_user, hash_password
from app.models import User, GenerationLog, CreditTransaction

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


def _require_admin(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        return None
    return user


@router.get("/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    users = db.query(User).order_by(User.created_at.desc()).all()

    result = db.execute(text("SELECT DISTINCT table_name FROM code_sites ORDER BY table_name")).fetchall()
    tables = [row[0] for row in result]

    return templates.TemplateResponse("admin_users.html", {
        "request": request, "user": admin, "users": users, "tables": tables,
    })


@router.post("/users", response_class=HTMLResponse)
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    default_address: str = Form(""),
    code_sites_table: str = Form("code_sites"),
    batch_enabled: bool = Form(False),
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        users = db.query(User).order_by(User.created_at.desc()).all()
        result = db.execute(text("SELECT DISTINCT table_name FROM code_sites ORDER BY table_name")).fetchall()
        tables = [row[0] for row in result]
        return templates.TemplateResponse("admin_users.html", {
            "request": request, "user": admin, "users": users, "tables": tables,
            "error": f"User with email {email} already exists",
        })

    new_user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        default_address=default_address,
        code_sites_table=code_sites_table,
        credits=0,
        is_admin=False,
        batch_enabled=batch_enabled,
    )
    db.add(new_user)
    db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/credits")
async def admin_set_credits(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    new_credits = int(form.get("credits", 0))
    if new_credits < 0:
        return RedirectResponse("/admin/users", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        old_credits = user.credits
        diff = new_credits - old_credits
        user.credits = new_credits
        db.add(CreditTransaction(
            user_id=user_id, amount=diff,
            description=f"Admin set credits: {old_credits} -> {new_credits}",
        ))
        db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/role")
async def admin_toggle_role(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    role = form.get("role", "user")

    user = db.query(User).filter(User.id == user_id).first()
    if user and user.id != admin.id:
        user.is_admin = (role == "admin")
        db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/batch")
async def admin_toggle_batch(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    batch_enabled = form.get("batch_enabled", "off") == "on"

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.batch_enabled = batch_enabled
        db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/password")
async def admin_reset_password(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    new_password = form.get("password", "").strip()
    if not new_password or len(new_password) < 6:
        return RedirectResponse("/admin/users", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.password_hash = hash_password(new_password)
        db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def admin_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    user = db.query(User).filter(User.id == user_id).first()
    if user and user.id != admin.id:
        # Delete related records first
        db.query(GenerationLog).filter(GenerationLog.user_id == user_id).delete()
        db.query(CreditTransaction).filter(CreditTransaction.user_id == user_id).delete()
        db.delete(user)
        db.commit()

    return RedirectResponse("/admin/users", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if not admin:
        return RedirectResponse("/login", status_code=303)

    logs = (
        db.query(GenerationLog, User.name, User.email)
        .join(User, GenerationLog.user_id == User.id)
        .order_by(GenerationLog.created_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse("admin_logs.html", {
        "request": request, "user": admin, "logs": logs,
    })
