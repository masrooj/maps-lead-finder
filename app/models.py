"""SQLAlchemy models for Maps Lead Finder + AppointHive Sales OS."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(40), default="owner")  # owner|sales|viewer
    timezone: Mapped[str] = mapped_column(String(80), default="Australia/Sydney")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")  # pbkdf2; empty → owner may use LOGIN_PASSWORD
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"
    __table_args__ = (UniqueConstraint("product_line", "slug", name="uq_stage_line_slug"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(40), index=True)  # STANDALONE|SAAS
    slug: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False)
    is_lost: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    country: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    international_phone: Mapped[str] = mapped_column(String(100), default="")
    website: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    google_maps_url: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_review_date: Mapped[str] = mapped_column(String(100), default="")
    latest_review_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latest_review_text: Mapped[str] = mapped_column(Text, default="")
    social_links_json: Mapped[str] = mapped_column(Text, default="{}")
    review_response_time: Mapped[str] = mapped_column(String(150), default="Not provided by Places API")
    business_status: Mapped[str] = mapped_column(String(80), default="")
    primary_type: Mapped[str] = mapped_column(String(200), default="")
    editorial_summary: Mapped[str] = mapped_column(Text, default="")
    contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    lost: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    instagram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    marked_for_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(80), default="")
    search_labels: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Sales OS fields
    product_line: Mapped[str] = mapped_column(String(40), default="STANDALONE", index=True)
    pipeline_stage_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(120), default="")
    postcode: Mapped[str] = mapped_column(String(40), default="")
    timezone: Mapped[str] = mapped_column(String(80), default="")
    deal_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    estimated_close_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_action: Mapped[str] = mapped_column(String(500), default="")
    lead_source: Mapped[str] = mapped_column(String(120), default="")
    date_identified: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_staff: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    locations_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_booking_software: Mapped[str] = mapped_column(String(200), default="")
    main_pain: Mapped[str] = mapped_column(Text, default="")
    buying_trigger: Mapped[str] = mapped_column(Text, default="")
    objections: Mapped[str] = mapped_column(Text, default="")
    lost_reason: Mapped[str] = mapped_column(String(500), default="")
    qualification_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    score_override_reason: Mapped[str] = mapped_column(String(500), default="")
    score_criteria_json: Mapped[str] = mapped_column(Text, default="{}")
    scores_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_out_channels: Mapped[str] = mapped_column(String(200), default="")
    permission_status_json: Mapped[str] = mapped_column(Text, default="{}")
    tax_id: Mapped[str] = mapped_column(String(120), default="")
    billing_entity_name: Mapped[str] = mapped_column(String(300), default="")
    billing_address: Mapped[str] = mapped_column(Text, default="")
    linked_prospect_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment_links_json: Mapped[str] = mapped_column(Text, default="[]")
    # SaaS line fields (unused until saas_enabled)
    mrr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trial_starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    saas_plan_code: Mapped[str] = mapped_column(String(80), default="")
    be_installation_id: Mapped[str] = mapped_column(String(120), default="")


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    product_line: Mapped[str] = mapped_column(String(40), default="")
    country: Mapped[str] = mapped_column(String(8), default="")
    purpose: Mapped[str] = mapped_column(String(80), default="")
    governance_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ActivityLog(Base):
    """Legacy activity log — kept for compatibility; prefer Activity."""
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(80), default="")
    channel: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class StageHistory(Base):
    __tablename__ = "stage_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    from_stage_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    to_stage_id: Mapped[int] = mapped_column(Integer)
    changed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    preferred_channel: Mapped[str] = mapped_column(String(40), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    activity_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(String(500), default="")
    outcome: Mapped[str] = mapped_column(String(500), default="")
    next_action: Mapped[str] = mapped_column(String(500), default="")
    channel: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(40), default="normal")  # low|normal|high
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)  # open|done|cancelled
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_line: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    billing_period: Mapped[str] = mapped_column(String(40), default="ONE_TIME")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PlanPrice(Base):
    __tablename__ = "plan_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanFeature(Base):
    __tablename__ = "plan_features"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    feature_key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(300), default="")
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    limit_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    reference: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    discount_reason: Mapped[str] = mapped_column(String(500), default="")
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    tax_label: Mapped[str] = mapped_column(String(80), default="")
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    line_items_json: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_url: Mapped[str] = mapped_column(String(500), default="")
    terms_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class BillingEntity(Base):
    __tablename__ = "billing_entities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    legal_name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(String(8), default="")
    tax_id: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    billing_entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    number: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    provider: Mapped[str] = mapped_column(String(40), default="")  # stripe|paypal|payoneer|skrill|manual
    provider_ref: Mapped[str] = mapped_column(String(200), default="")
    hosted_url: Mapped[str] = mapped_column(Text, default="")
    pay_instructions: Mapped[str] = mapped_column(Text, default="")
    line_items_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    status: Mapped[str] = mapped_column(String(40), default="succeeded", index=True)
    provider: Mapped[str] = mapped_column(String(40), default="manual")
    method: Mapped[str] = mapped_column(String(80), default="")
    provider_ref: Mapped[str] = mapped_column(String(200), default="")
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    event_id: Mapped[str] = mapped_column(String(200), unique=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CompliancePack(Base):
    __tablename__ = "compliance_packs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    bullets_md: Mapped[str] = mapped_column(Text, default="")
    required_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    enforce: Mapped[bool] = mapped_column(Boolean, default=False)
    counsel_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Suppression(Base):
    __tablename__ = "suppressions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # email|phone|domain
    value: Mapped[str] = mapped_column(String(320), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    cursor_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DiscoveryNote(Base):
    __tablename__ = "discovery_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    existing_system: Mapped[str] = mapped_column(Text, default="")
    current_workflow: Mapped[str] = mapped_column(Text, default="")
    painful_issue: Mapped[str] = mapped_column(Text, default="")
    frequency_impact: Mapped[str] = mapped_column(Text, default="")
    desired_outcome: Mapped[str] = mapped_column(Text, default="")
    buying_authority: Mapped[str] = mapped_column(Text, default="")
    purchase_timing: Mapped[str] = mapped_column(Text, default="")
    switching_concerns: Mapped[str] = mapped_column(Text, default="")
    required_capabilities: Mapped[str] = mapped_column(Text, default="")
    call_notes: Mapped[str] = mapped_column(Text, default="")
    next_action: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class DemoNote(Base):
    __tablename__ = "demo_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    demo_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(200), default="")
    features_demonstrated: Mapped[str] = mapped_column(Text, default="")
    prospect_reaction: Mapped[str] = mapped_column(Text, default="")
    unresolved_concerns: Mapped[str] = mapped_column(Text, default="")
    product_gaps: Mapped[str] = mapped_column(Text, default="")
    agreed_next_step: Mapped[str] = mapped_column(Text, default="")
    call_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SavedView(Base):
    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SalesTerritory(Base):
    __tablename__ = "sales_territories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    country: Mapped[str] = mapped_column(String(120), default="", index=True)
    region: Mapped[str] = mapped_column(String(120), default="")
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ContactQueueMode(Base):
    """Configurable contact-queue ranking / filter presets (CRUD)."""
    __tablename__ = "contact_queue_modes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    filter_json: Mapped[str] = mapped_column(Text, default="{}")
    boost_json: Mapped[str] = mapped_column(Text, default="{}")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class DemoAsset(Base):
    """Demo videos / links to share with prospects."""
    __tablename__ = "demo_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(40), default="link")  # link|video|file
    url: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class BeDelivery(Base):
    """AppointHive Booking Engine handover row (purchase / soft request / upgrade)."""
    __tablename__ = "be_deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str] = mapped_column(String(40), default="purchase")  # purchase|upgrade|request
    purchase_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    inbound_request_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    revision_id: Mapped[str] = mapped_column(String(120), default="")
    from_revision_id: Mapped[str] = mapped_column(String(120), default="")
    installation_id: Mapped[str] = mapped_column(String(120), default="")
    pay_url: Mapped[str] = mapped_column(Text, default="")
    quoted_amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(10), default="AUD")
    crm_lead_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    status: Mapped[str] = mapped_column(String(40), default="sent", index=True)
    last_event_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
