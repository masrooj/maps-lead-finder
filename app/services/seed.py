"""Seed stages, owner user, default plans, settings, compliance packs."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import (
    CURRENCY_BY_COUNTRY,
    DEFAULT_CURRENCY,
    DEFAULT_TIMEZONE,
    LOGIN_USERNAME,
    SAAS_ENABLED,
    SAAS_STAGES,
    STANDALONE_STAGES,
    WON_ON,
)
from app.models import (
    AppSetting,
    BillingEntity,
    CompliancePack,
    PipelineStage,
    Plan,
    PlanFeature,
    PlanPrice,
    User,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_setting(db, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row:
        return
    db.add(AppSetting(key=key, value=value, updated_at=_utcnow()))


def seed_owner(db) -> User:
    from app.config import LOGIN_PASSWORD
    from app.services.passwords import hash_password

    user = db.scalar(select(User).where(User.email == LOGIN_USERNAME.lower()))
    if user:
        if LOGIN_PASSWORD and not user.password_hash:
            user.password_hash = hash_password(LOGIN_PASSWORD)
        return user
    user = User(
        email=LOGIN_USERNAME.lower(),
        name="AppointHive Owner",
        role="owner",
        timezone=DEFAULT_TIMEZONE,
        active=True,
        password_hash=hash_password(LOGIN_PASSWORD) if LOGIN_PASSWORD else "",
    )
    db.add(user)
    db.flush()
    return user


def seed_stages(db) -> None:
    for product_line, stages in (("STANDALONE", STANDALONE_STAGES), ("SAAS", SAAS_STAGES)):
        existing = db.scalars(
            select(PipelineStage).where(PipelineStage.product_line == product_line)
        ).all()
        if existing:
            continue
        for i, (slug, name, prob, terminal) in enumerate(stages):
            db.add(
                PipelineStage(
                    product_line=product_line,
                    slug=slug,
                    name=name,
                    sort_order=i,
                    probability=prob,
                    is_terminal=terminal,
                    is_won=slug in ("won", "subscribed"),
                    is_lost=slug in ("lost", "churned"),
                    active=True,
                )
            )


def seed_plans(db) -> None:
    existing_codes = {p.code for p in db.scalars(select(Plan)).all()}
    plans = [
        (
            "standalone_starter",
            "Standalone Starter",
            "Core booking for a single location",
            0,
            1500.0,
            [
                ("staff_seats", "Staff seats", True, 5),
                ("locations", "Locations", True, 1),
                ("bookings", "Online bookings", True, None),
                ("support", "Email support", True, None),
            ],
        ),
        (
            "standalone_growth",
            "Standalone Growth",
            "More staff capacity and operational tools",
            1,
            2000.0,
            [
                ("staff_seats", "Staff seats", True, 15),
                ("locations", "Locations", True, 1),
                ("bookings", "Online bookings", True, None),
                ("queues", "Queue / walk-in support", True, None),
                ("support", "Priority email support", True, None),
            ],
        ),
        (
            "standalone_pro",
            "Standalone Pro",
            "Full feature set for complex shops",
            2,
            2800.0,
            "STANDALONE",
            "ONE_TIME",
            [
                ("staff_seats", "Staff seats", True, 30),
                ("locations", "Locations", True, 3),
                ("bookings", "Online bookings", True, None),
                ("queues", "Queue / walk-in support", True, None),
                ("reports", "Advanced reports", True, None),
                ("support", "Priority support", True, None),
            ],
        ),
        (
            "saas_starter",
            "SaaS Starter",
            "Hosted AppointHive — starter subscription (enable via saas_enabled)",
            0,
            49.0,
            "SAAS",
            "MONTH",
            [
                ("staff_seats", "Staff seats", True, 5),
                ("locations", "Locations", True, 1),
                ("bookings", "Online bookings", True, None),
            ],
        ),
        (
            "saas_growth",
            "SaaS Growth",
            "Hosted AppointHive — growth subscription",
            1,
            99.0,
            "SAAS",
            "MONTH",
            [
                ("staff_seats", "Staff seats", True, 15),
                ("locations", "Locations", True, 3),
                ("bookings", "Online bookings", True, None),
            ],
        ),
        (
            "saas_scale",
            "SaaS Scale",
            "Hosted AppointHive — scale subscription",
            2,
            199.0,
            "SAAS",
            "MONTH",
            [
                ("staff_seats", "Staff seats", True, 50),
                ("locations", "Locations", True, 10),
                ("bookings", "Online bookings", True, None),
            ],
        ),
    ]
    # Normalize early tuples that omit product_line/billing_period
    normalized = []
    for row in plans:
        if len(row) == 6:
            code, name, desc, order, amount, features = row
            normalized.append((code, name, desc, order, amount, "STANDALONE", "ONE_TIME", features))
        else:
            normalized.append(row)
    for code, name, desc, order, amount, product_line, billing_period, features in normalized:
        if code in existing_codes:
            continue
        plan = Plan(
            product_line=product_line,
            code=code,
            name=name,
            description=desc,
            sort_order=order,
            active=True,
            billing_period=billing_period,
        )
        db.add(plan)
        db.flush()
        db.add(PlanPrice(plan_id=plan.id, currency=DEFAULT_CURRENCY, amount=amount, valid_from=_utcnow()))
        db.add(PlanPrice(plan_id=plan.id, currency="USD", amount=round(amount * (0.65 if product_line == "STANDALONE" else 1.0), 2), valid_from=_utcnow()))
        for key, label, included, limit in features:
            db.add(
                PlanFeature(
                    plan_id=plan.id,
                    feature_key=key,
                    label=label,
                    included=included,
                    limit_value=limit,
                )
            )


def seed_billing_entity(db) -> None:
    if db.scalar(select(BillingEntity).limit(1)):
        return
    db.add(
        BillingEntity(
            code="appointhive_default",
            legal_name="AppointHive",
            address="",
            country="AU",
            tax_id="",
            active=True,
        )
    )


def seed_compliance(db) -> None:
    packs = [
        (
            "AU",
            "Australia outreach notes",
            "- Privacy Act / APPs apply to personal information.\n"
            "- Spam Act rules for commercial electronic messages — record permission where required.\n"
            "- Tax invoices may need ABN and GST treatment.\n"
            "- **Not legal advice** — counsel review recommended before enforce mode.",
            '["permission_status_json"]',
        ),
        (
            "NZ",
            "New Zealand outreach notes",
            "- Privacy Act 2020.\n- Unsolicited electronic messages rules.\n- **Not legal advice.**",
            "[]",
        ),
        (
            "US",
            "United States outreach notes",
            "- CAN-SPAM for commercial email; TCPA considerations for calls/SMS.\n"
            "- State privacy laws may apply.\n- **Not legal advice.**",
            "[]",
        ),
        (
            "CA",
            "Canada outreach notes",
            "- PIPEDA and CASL consent rules for CEMs are strict.\n- **Not legal advice.**",
            '["permission_status_json"]',
        ),
        (
            "GB",
            "United Kingdom outreach notes",
            "- UK GDPR + PECR marketing rules.\n- VAT invoice fields when charging VAT.\n- **Not legal advice.**",
            "[]",
        ),
        (
            "AE",
            "UAE outreach notes",
            "- Prefer consented/business contact; PDPL-style regimes vary.\n"
            "- VAT/TRN on invoices where required.\n- **Not legal advice.**",
            "[]",
        ),
    ]
    for code, title, bullets, required in packs:
        if db.scalar(select(CompliancePack).where(CompliancePack.country_code == code)):
            continue
        db.add(
            CompliancePack(
                country_code=code,
                title=title,
                bullets_md=bullets,
                required_fields_json=required,
                enforce=False,
                counsel_reviewed_at=None,
            )
        )


def seed_settings(db) -> None:
    ensure_setting(db, "default_currency", DEFAULT_CURRENCY)
    ensure_setting(db, "default_timezone", DEFAULT_TIMEZONE)
    ensure_setting(db, "won_on", WON_ON)
    ensure_setting(db, "saas_enabled", "true" if SAAS_ENABLED else "false")
    ensure_setting(db, "demo_data_enabled", "false")
    ensure_setting(db, "currency_by_country", json.dumps(CURRENCY_BY_COUNTRY))
    ensure_setting(db, "tax_rate_AU", "0.10")
    ensure_setting(db, "tax_label_AU", "GST")
    ensure_setting(db, "contact_queue_mode", "default")


def seed_sales_os(db) -> None:
    seed_owner(db)
    seed_stages(db)
    seed_plans(db)
    seed_billing_entity(db)
    seed_compliance(db)
    seed_settings(db)
    try:
        from app.services.ranking_modes import seed_ranking_modes

        seed_ranking_modes(db)
    except Exception:
        pass
    db.commit()
