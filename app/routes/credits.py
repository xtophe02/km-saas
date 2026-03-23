"""Stripe credits routes."""
import stripe
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import get_current_user
from app.models import User, CreditTransaction
from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()
stripe.api_key = settings.stripe_secret_key

# Credit packages: {credits: price_id}
PACKAGES = {
    1: {"price_id": settings.stripe_price_1, "label": "1 credit", "price": "5.00"},
    5: {"price_id": settings.stripe_price_5, "label": "5 credits", "price": "20.00"},
    10: {"price_id": settings.stripe_price_10, "label": "10 credits", "price": "40.00"},
}

BATCH_PACKAGE = {
    10: {"price_id": settings.stripe_price_batch_10, "label": "10 credits (batch)", "price": "20.00"},
}


@router.post("/credits/checkout")
async def credits_checkout(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    credits_amount = int(form.get("credits", 1))
    is_batch = form.get("batch", "0") == "1"

    # Determine which package to use
    if is_batch and user.batch_enabled:
        package = BATCH_PACKAGE.get(credits_amount)
    else:
        package = PACKAGES.get(credits_amount)

    if not package or not package["price_id"]:
        return RedirectResponse("/dashboard", status_code=303)

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": package["price_id"], "quantity": 1}],
            mode="payment",
            success_url=str(request.url_for("credits_success")) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=str(request.url_for("dashboard")),
            metadata={"user_id": str(user.id), "credits": str(credits_amount)},
        )
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        return RedirectResponse("/dashboard", status_code=303)


@router.get("/credits/success", response_class=HTMLResponse)
async def credits_success(request: Request, session_id: str = None, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("credits_success.html", {"request": request, "user": user})


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session["metadata"]["user_id"])
        credits_amount = int(session["metadata"]["credits"])
        stripe_session_id = session["id"]

        # Idempotency check
        existing = db.query(CreditTransaction).filter(
            CreditTransaction.stripe_session_id == stripe_session_id
        ).first()
        if not existing:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                user.credits += credits_amount
                db.add(CreditTransaction(
                    user_id=user_id, amount=credits_amount,
                    stripe_session_id=stripe_session_id,
                    description=f"Purchased {credits_amount} credit(s)",
                ))
                db.commit()

    return {"status": "ok"}
