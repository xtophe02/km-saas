"""Generation routes with async job pattern."""
import json
import time
import uuid
import threading
import logging
from datetime import date
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, Response, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db, SessionLocal
from app.auth import get_current_user
from app.models import User, GenerationLog, CreditTransaction
from app.config import get_settings
from app.utils.generator import TripGenerator
from app.utils.report import KMReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

# --- In-memory job store ---
_jobs = {}
_jobs_lock = threading.Lock()
_JOB_EXPIRY_SECONDS = 600  # 10 minutes


def _cleanup_expired_jobs():
    """Remove jobs older than expiry time."""
    now = time.time()
    with _jobs_lock:
        expired = [jid for jid, job in _jobs.items() if now - job["created"] > _JOB_EXPIRY_SECONDS]
        for jid in expired:
            del _jobs[jid]


def _get_active_job(user_id: int):
    """Find an active (processing) job for a user."""
    with _jobs_lock:
        for jid, job in _jobs.items():
            if job["user_id"] == user_id and job["status"] == "processing":
                return jid
    return None


def _run_generation(job_id: str, user_id: int, form: dict, km_limit: float = None):
    """Background thread: run TripGenerator and store result in job store."""
    try:
        with _jobs_lock:
            _jobs[job_id]["progress"] = "Building distance cache..."

        db = SessionLocal()
        try:
            generator = TripGenerator(
                db=db,
                office_address=form["departure_address"],
                target_kilometers=form["target_km"],
                table_name=form["code_sites_table"],
                num_days=form["num_days"],
            )

            with _jobs_lock:
                _jobs[job_id]["progress"] = "Generating trips..."

            trips = generator.generate_trips(
                month=form["month"], year=form["year"], km_limit=km_limit,
            )

            db.add(GenerationLog(
                user_id=user_id,
                consultant_name=form["consultant_name"],
                month=form["month"],
                year=form["year"],
                target_km=form["target_km"],
                num_days=form["num_days"],
                action="preview",
            ))
            db.commit()

            total_km = sum(t["total_distance"] for t in trips)
            trips_json = json.dumps(trips)

            with _jobs_lock:
                _jobs[job_id]["status"] = "done"
                _jobs[job_id]["progress"] = "Done"
                _jobs[job_id]["result"] = {
                    "trips": trips,
                    "total_km": total_km,
                    "trips_json": trips_json,
                }
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["progress"] = str(e)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Check for active job
    active_job_id = _get_active_job(user.id)

    logs = (
        db.query(GenerationLog)
        .filter(GenerationLog.user_id == user.id)
        .order_by(GenerationLog.created_at.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "logs": logs,
        "active_job_id": active_job_id,
    })


@router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Check for active job — redirect back to processing page
    active_job_id = _get_active_job(user.id)
    if active_job_id:
        return templates.TemplateResponse("processing.html", {
            "request": request, "user": user, "job_id": active_job_id,
        })

    now = date.today()
    return templates.TemplateResponse("generate.html", {
        "request": request, "user": user,
        "current_month": now.month, "current_year": now.year,
        "max_km_per_day": settings.max_km_per_day,
        "preview": None, "form": {},
    })


@router.post("/generate/preview", response_class=HTMLResponse)
async def generate_preview(
    request: Request,
    consultant_name: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    target_km: float = Form(...),
    num_days: int = Form(...),
    departure_address: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # If there's already an active job, redirect to it
    active_job_id = _get_active_job(user.id)
    if active_job_id:
        return templates.TemplateResponse("processing.html", {
            "request": request, "user": user, "job_id": active_job_id,
        })

    form = {
        "consultant_name": consultant_name, "month": month, "year": year,
        "target_km": target_km, "num_days": num_days,
        "departure_address": departure_address,
    }
    errors = _validate_form(form)

    if errors:
        return templates.TemplateResponse("generate.html", {
            "request": request, "user": user,
            "current_month": month, "current_year": year,
            "max_km_per_day": settings.max_km_per_day,
            "preview": None, "form": form, "errors": errors,
        })

    km_limit = settings.free_tier_km_limit if user.credits <= 0 else None
    is_free_tier = user.credits <= 0

    _cleanup_expired_jobs()

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "processing",
            "progress": "Starting...",
            "created": time.time(),
            "result": None,
            "form": form,
            "user_id": user.id,
            "is_free_tier": is_free_tier,
        }

    form["code_sites_table"] = user.code_sites_table

    thread = threading.Thread(
        target=_run_generation,
        args=(job_id, user.id, form, km_limit),
        daemon=True,
    )
    thread.start()

    return templates.TemplateResponse("processing.html", {
        "request": request, "user": user, "job_id": job_id,
    })


@router.get("/generate/status/{job_id}")
async def generate_status(job_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"status": "error", "progress": "Not authenticated"}, status_code=401)

    with _jobs_lock:
        job = _jobs.get(job_id)

    if not job:
        return JSONResponse({"status": "error", "progress": "Job not found or expired"})

    if job["user_id"] != user.id:
        return JSONResponse({"status": "error", "progress": "Unauthorized"}, status_code=403)

    return JSONResponse({
        "status": job["status"],
        "progress": job["progress"],
    })


@router.get("/generate/result/{job_id}", response_class=HTMLResponse)
async def generate_result(job_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with _jobs_lock:
        job = _jobs.get(job_id)

    if not job or job["user_id"] != user.id:
        return RedirectResponse("/generate", status_code=303)

    if job["status"] == "error":
        form = job["form"]
        return templates.TemplateResponse("generate.html", {
            "request": request, "user": user,
            "current_month": form.get("month", date.today().month),
            "current_year": form.get("year", date.today().year),
            "max_km_per_day": settings.max_km_per_day,
            "preview": None, "form": form,
            "errors": [job["progress"]],
        })

    if job["status"] != "done":
        return templates.TemplateResponse("processing.html", {
            "request": request, "user": user, "job_id": job_id,
        })

    form = job["form"]
    result = job["result"]

    return templates.TemplateResponse("generate.html", {
        "request": request, "user": user,
        "current_month": form["month"], "current_year": form["year"],
        "max_km_per_day": settings.max_km_per_day,
        "preview": {
            "trips": result["trips"],
            "total_km": result["total_km"],
            "trips_json": result["trips_json"],
        },
        "form": form, "is_free_tier": job.get("is_free_tier", False),
    })


@router.post("/generate/pdf")
async def generate_pdf(
    request: Request,
    consultant_name: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    target_km: float = Form(...),
    num_days: int = Form(...),
    departure_address: str = Form(...),
    trips_json: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    is_free_tier = user.credits <= 0

    try:
        # Use trips from the preview (same data the user saw)
        if trips_json:
            trips = json.loads(trips_json)
        else:
            # Fallback: regenerate (shouldn't normally happen)
            km_limit = settings.free_tier_km_limit if is_free_tier else None
            generator = TripGenerator(
                db=db, office_address=departure_address,
                target_kilometers=target_km, table_name=user.code_sites_table,
                num_days=num_days,
            )
            trips = generator.generate_trips(month=month, year=year, km_limit=km_limit)

        if not trips:
            return RedirectResponse("/generate", status_code=303)

        report = KMReportGenerator()
        report_date = date(year, month, 1)
        pdf_bytes = report.generate_report(consultant_name, report_date, trips)

        # Only deduct credit if not free tier
        if not is_free_tier:
            user.credits -= 1
            db.add(CreditTransaction(
                user_id=user.id, amount=-1, description="PDF generation",
            ))
        db.add(GenerationLog(
            user_id=user.id, consultant_name=consultant_name,
            month=month, year=year, target_km=target_km,
            num_days=num_days, action="pdf" if not is_free_tier else "free_pdf",
        ))
        db.commit()

        total_distance = sum(t["total_distance"] for t in trips)
        filename = f"{consultant_name.replace(' ', '_')}_{total_distance:.1f}_KM_{month:02d}{year}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        return RedirectResponse("/generate", status_code=303)


def _validate_form(form: dict) -> list:
    errors = []
    settings_obj = get_settings()

    if not form.get("consultant_name", "").strip():
        errors.append("Consultant name is required")
    if not form.get("departure_address", "").strip():
        errors.append("Departure address is required")

    km = form.get("target_km", 0)
    days = form.get("num_days", 0)

    if km <= 0:
        errors.append("Total kilometers must be positive")
    if days <= 0 or days > 31:
        errors.append("Number of days must be between 1 and 31")
    if km > 0 and days > 0 and km > days * settings_obj.max_km_per_day:
        errors.append(f"Maximum {settings_obj.max_km_per_day:.0f} km/day ({days} days = {days * settings_obj.max_km_per_day:.0f} km max)")

    month = form.get("month", 0)
    year = form.get("year", 0)
    if month < 1 or month > 12:
        errors.append("Invalid month")
    if year < 2020 or year > 2030:
        errors.append("Year must be between 2020 and 2030")

    return errors
