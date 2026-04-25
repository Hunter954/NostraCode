from datetime import datetime, date
from decimal import Decimal
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    company = db.Column(db.String(180))
    whatsapp = db.Column(db.String(60))
    document = db.Column(db.String(60))
    accepted_terms = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), default="client")
    is_active_client = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    projects = db.relationship("Project", backref="client", lazy=True)
    invoices = db.relationship("Invoice", backref="client", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    railway_internal_name = db.Column(db.String(160))
    railway_project_id = db.Column(db.String(160))
    railway_environment_id = db.Column(db.String(160))
    railway_service_id = db.Column(db.String(160))
    public_url = db.Column(db.String(255))
    environment = db.Column(db.String(60), default="produção")
    status = db.Column(db.String(60), default="ativo")
    plan = db.Column(db.String(80), default="Starter")
    monthly_value = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    usage_limit = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    current_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    estimated_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    management_fee = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    last_cost_update = db.Column(db.DateTime, default=datetime.utcnow)
    last_sync_at = db.Column(db.DateTime)
    sync_status = db.Column(db.String(40), default="manual")
    sync_error = db.Column(db.Text)
    latest_deployment_status = db.Column(db.String(80))
    previous_month_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship("Invoice", backref="project", lazy=True)
    railway_services = db.relationship("RailwayService", backref="project", lazy=True, cascade="all, delete-orphan")
    usage_snapshots = db.relationship("RailwayUsageSnapshot", backref="project", lazy=True, cascade="all, delete-orphan")

    @property
    def total_forecast(self):
        # A cobrança exibida ao cliente agora vem somente do Railway.
        return self.estimated_cost or Decimal("0.00")

    @property
    def usage_percent(self):
        # Sem limite manual de uso; mantido apenas para compatibilidade com telas antigas.
        return 0


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    period = db.Column(db.String(40), nullable=False)
    railway_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    management_fee = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    discounts = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    fines = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    total = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    status = db.Column(db.String(40), default="pendente")
    due_date = db.Column(db.Date, default=date.today)
    payment_link = db.Column(db.String(500))
    mp_preference_id = db.Column(db.String(180))
    mp_external_reference = db.Column(db.String(180), index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    payments = db.relationship("Payment", backref="invoice", lazy=True)


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(40), default="pago")
    method = db.Column(db.String(80))
    transaction_code = db.Column(db.String(180))
    receipt_url = db.Column(db.String(500))


class RailwayService(db.Model):
    __tablename__ = "railway_services"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    railway_service_id = db.Column(db.String(160), nullable=False, index=True)
    name = db.Column(db.String(180))
    environment_id = db.Column(db.String(160))
    environment_name = db.Column(db.String(100))
    latest_deployment_status = db.Column(db.String(80))
    public_url = db.Column(db.String(500))
    last_sync_at = db.Column(db.DateTime, default=datetime.utcnow)


class RailwayUsageSnapshot(db.Model):
    __tablename__ = "railway_usage_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    current_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    estimated_cost = db.Column(db.Numeric(10, 2), default=Decimal("0.00"))
    currency = db.Column(db.String(12), default="BRL")
    usage_period_start = db.Column(db.DateTime)
    usage_period_end = db.Column(db.DateTime)
    raw_payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
