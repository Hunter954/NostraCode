from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import calendar
import re
from decimal import Decimal
from typing import Tuple

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")

def brazil_now() -> datetime:
    return datetime.now(BRAZIL_TZ).replace(tzinfo=None)

def brazil_today() -> date:
    return brazil_now().date()

from .extensions import db
from .models import Invoice, Project, RailwayService, RailwayUsageSnapshot
from .services.railway_api import (
    RailwayAPIError,
    get_latest_deployment,
    get_project,
    get_service_domains,
    get_usage,
    parse_period,
    pick_environment,
    pick_service,
    _nodes,
)


def sync_project_from_railway(project: Project) -> Project:
    if not project.railway_project_id:
        raise RailwayAPIError("Este projeto não tem railway_project_id configurado.")

    now = brazil_now()
    railway_project = get_project(project.railway_project_id)
    if not railway_project:
        raise RailwayAPIError("Projeto não encontrado na Railway ou token sem acesso.")

    services = _nodes(railway_project.get("services"))
    environments = _nodes(railway_project.get("environments"))
    selected_environment = pick_environment(environments, project.railway_environment_id)
    selected_service = pick_service(services, project.railway_service_id)

    project.railway_internal_name = railway_project.get("name") or project.railway_internal_name
    if not project.name and railway_project.get("name"):
        project.name = railway_project["name"]
    if selected_environment:
        project.railway_environment_id = selected_environment.get("id")
        project.environment = selected_environment.get("name") or project.environment
    if selected_service:
        project.railway_service_id = selected_service.get("id")

    deployment_error = None
    latest_deployment = None
    try:
        latest_deployment = get_latest_deployment(
            project.railway_project_id,
            project.railway_environment_id,
            project.railway_service_id,
        )
    except Exception as exc:
        deployment_error = str(exc)

    if latest_deployment:
        project.latest_deployment_status = latest_deployment.get("status")
        if latest_deployment.get("status") in {"SUCCESS", "ONLINE"}:
            project.status = "online"
        elif latest_deployment.get("status"):
            project.status = latest_deployment.get("status").lower()

    domain_url, domain_raw = get_service_domains(
        project.railway_project_id,
        project.railway_environment_id,
        project.railway_service_id,
    )
    if domain_url:
        project.public_url = domain_url

    current_cost, estimated_cost, usage_raw = get_usage(project.railway_project_id)
    if current_cost is not None:
        project.current_cost = current_cost
    if estimated_cost is not None:
        project.estimated_cost = estimated_cost
        project.monthly_value = estimated_cost

    period_start, period_end = parse_period(usage_raw)
    db.session.add(RailwayUsageSnapshot(
        project_id=project.id,
        current_cost=project.current_cost or Decimal("0.00"),
        estimated_cost=project.estimated_cost or Decimal("0.00"),
        currency=usage_raw.get("currency") or "BRL",
        usage_period_start=period_start,
        usage_period_end=period_end,
        raw_payload=usage_raw,
    ))

    # Atualiza/espelha os serviços conhecidos para visualização no painel.
    for service in services:
        service_id = service.get("id")
        if not service_id:
            continue
        record = RailwayService.query.filter_by(project_id=project.id, railway_service_id=service_id).first()
        if not record:
            record = RailwayService(project_id=project.id, railway_service_id=service_id)
            db.session.add(record)
        record.name = service.get("name") or record.name
        if selected_environment:
            record.environment_id = selected_environment.get("id")
            record.environment_name = selected_environment.get("name")
        if selected_service and selected_service.get("id") == service_id and latest_deployment:
            record.latest_deployment_status = latest_deployment.get("status")
        record.last_sync_at = now

    warning = deployment_error
    usage_error = usage_raw.get("usage_error") if isinstance(usage_raw, dict) else None
    if usage_error:
        warning = f"{warning or ''} Uso/custos não sincronizados automaticamente: {usage_error}".strip()
    domain_error = domain_raw.get("domain_error") if isinstance(domain_raw, dict) else None
    if domain_error:
        warning = f"{warning or ''} URL pública não sincronizada automaticamente: {domain_error}".strip()

    refresh_project_invoice(project, period_start, period_end)

    project.last_sync_at = now
    project.last_cost_update = now
    project.sync_status = "sincronizado" if not warning else "parcial"
    project.sync_error = warning
    db.session.commit()
    return project


def sync_all_projects() -> Tuple[int, int, int]:
    projects = Project.query.filter(Project.railway_project_id.isnot(None), Project.railway_project_id != "").all()
    ok = 0
    failed = 0
    for project in projects:
        try:
            sync_project_from_railway(project)
            ok += 1
        except Exception as exc:
            project.sync_status = "erro"
            project.sync_error = str(exc)
            project.last_sync_at = brazil_now()
            db.session.commit()
            failed += 1
    return len(projects), ok, failed

RAILWAY_BILLING_DAY = 27
INVOICE_DAYS_BEFORE_BILLING_DAY = 5
INVOICE_VISIBLE_DAYS_BEFORE_DUE = 5
OPEN_INVOICE_STATUSES = ["pendente", "aguardando pagamento", "atrasado", "cancelado"]


def _add_months(value: date, months: int = 1) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _billing_anchor(year: int, month: int, day: int = RAILWAY_BILLING_DAY) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def current_billing_cycle(today: date | None = None) -> tuple[date, date]:
    """Return the Railway billing cycle currently accumulating usage, day 27 -> 27."""
    today = today or brazil_today()
    this_month_anchor = _billing_anchor(today.year, today.month)
    if today >= this_month_anchor:
        start = this_month_anchor
        end = _add_months(start, 1)
    else:
        end = this_month_anchor
        start = _add_months(end, -1)
    return start, end


def invoice_billing_cycle(today: date | None = None) -> tuple[date, date]:
    """Return the billing cycle that is being previewed/closed for payment."""
    today = today or brazil_today()
    due_date = _billing_anchor(today.year, today.month)
    start = _add_months(due_date, -1)
    return start, due_date


def format_billing_period(start: date, end: date) -> str:
    return start.strftime("%d/%m/%Y") + " a " + end.strftime("%d/%m/%Y")


def format_invoice_month_label(anchor: date) -> str:
    months = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]
    return f"{months[anchor.month - 1]}/{anchor.year}"



def should_show_invoice(due_date: date, today: date | None = None) -> bool:
    today = today or brazil_today()
    return today >= due_date - timedelta(days=INVOICE_VISIBLE_DAYS_BEFORE_DUE)


MONTHS_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def _invoice_billing_anchor(invoice: Invoice) -> date:
    """Find the monthly Railway billing day (27) represented by the invoice."""
    period = (invoice.period or "").strip().lower()

    dates = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", period)
    if dates:
        day, month, year = map(int, dates[-1])
        return _billing_anchor(year, month)

    month_label = re.search(r"([a-zç]+)\s*(?:/|de)?\s*(\d{4})", period)
    if month_label and month_label.group(1) in MONTHS_PT:
        return _billing_anchor(int(month_label.group(2)), MONTHS_PT[month_label.group(1)])

    base = invoice.due_date or brazil_today()
    return _billing_anchor(base.year, base.month)




def invoice_period_dates(invoice: Invoice) -> tuple[date | None, date | None]:
    """Return explicit start/end dates when invoice.period uses dd/mm/yyyy ranges."""
    period = (invoice.period or "").strip().lower()
    dates = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", period)
    if len(dates) >= 2:
        start_day, start_month, start_year = map(int, dates[0])
        end_day, end_month, end_year = map(int, dates[-1])
        return date(start_year, start_month, start_day), date(end_year, end_month, end_day)
    return None, None


def invoice_is_future_cycle(invoice: Invoice, today: date | None = None) -> bool:
    """Keep future/preview Railway cycles out of recent invoice lists.

    Real payable invoices in this app use month labels (ex.: Abril/2026). A
    date-range invoice that starts on the active Railway cycle, or an open
    date-range invoice that ends exactly as the active cycle starts, is a
    generated preview and belongs only in Próximas faturas.
    """
    today = today or brazil_today()
    start, end = invoice_period_dates(invoice)
    if not start or not end:
        return False
    current_start, current_end = current_billing_cycle(today)
    if start >= current_start:
        return True
    if invoice.status != "pago" and end == current_start:
        return True
    return False

def invoice_payable_date(invoice: Invoice) -> date:
    """Return the first day the client is allowed to pay this invoice.

    Payments always open five days before the Railway billing day (27).
    Example: April cycle closes on 27/04, so payment opens on 22/04.
    """
    return _invoice_billing_anchor(invoice) - timedelta(days=INVOICE_DAYS_BEFORE_BILLING_DAY)

def refresh_invoice_status(invoice: Invoice, today: date | None = None) -> Invoice:
    """Keep open invoice status aligned with the configured payment date."""
    today = today or brazil_today()
    if invoice.status == "pago":
        return invoice
    payable_date = invoice_payable_date(invoice)
    if invoice.due_date != payable_date:
        invoice.due_date = payable_date
    if today > payable_date:
        invoice.status = "atrasado"
    elif invoice.status == "atrasado":
        invoice.status = "pendente"
    return invoice


def invoice_payment_available(invoice: Invoice, today: date | None = None) -> bool:
    """Allow checkout on/after the configured payment date."""
    today = today or brazil_today()
    refresh_invoice_status(invoice, today)
    return invoice.status != "pago" and invoice_payable_date(invoice) <= today


def _current_period_label(today: date | None = None) -> str:
    start, end = current_billing_cycle(today)
    return format_billing_period(start, end)


def _next_due_date(today: date | None = None) -> date:
    _, end = invoice_billing_cycle(today)
    return end - timedelta(days=INVOICE_DAYS_BEFORE_BILLING_DAY)


def _next_invoice_number() -> str:
    prefix = str(brazil_today().year)
    count = Invoice.query.filter(Invoice.number.like(f"{prefix}-%")).count() + 1
    while True:
        number = f"{prefix}-{count:04d}"
        if not Invoice.query.filter_by(number=number).first():
            return number
        count += 1

def clear_unpaid_project_invoices(project: Project) -> int:
    """Remove faturas abertas antigas quando o Railway Project ID muda."""
    invoices = Invoice.query.filter(
        Invoice.project_id == project.id,
        Invoice.status.in_(OPEN_INVOICE_STATUSES),
    ).all()
    for invoice in invoices:
        db.session.delete(invoice)
    return len(invoices)

def refresh_project_invoice(project: Project, period_start=None, period_end=None) -> Invoice | None:
    """Create/update the monthly Railway invoice.

    The Railway cycle closes on day 27, but the client payment date is always
    five days before that close date. After that payment date, the invoice can
    be paid; after the date passes, it is marked as overdue.
    """
    today = brazil_today()
    cycle_start, cycle_end = invoice_billing_cycle(today)
    period = format_invoice_month_label(cycle_end)
    due_date = cycle_end - timedelta(days=INVOICE_DAYS_BEFORE_BILLING_DAY)

    # A cobrança real do ciclo fica aberta apenas entre D-5 e o fechamento (dia 27).
    # Depois do dia 27, o próximo ciclo aparece somente como prévia em Próximas faturas
    # para evitar duplicar faturas já fechadas/pagas no painel.
    if not should_show_invoice(due_date, today) or today >= cycle_end:
        return None

    amount = project.estimated_cost if project.estimated_cost is not None else project.current_cost
    amount = Decimal(amount or "0.00").quantize(Decimal("0.01"))

    # Keep historical/open invoices for previous cycles visible on the project.
    # Each sync only creates or updates the current cycle invoice.

    invoice = Invoice.query.filter(
        Invoice.project_id == project.id,
        Invoice.status.in_(OPEN_INVOICE_STATUSES),
        Invoice.period == period,
    ).order_by(Invoice.created_at.desc()).first()

    if not invoice:
        invoice = Invoice(
            number=_next_invoice_number(),
            client_id=project.client_id,
            project_id=project.id,
            period=period,
            status="pendente",
            due_date=due_date,
        )
        db.session.add(invoice)

    invoice.client_id = project.client_id
    invoice.due_date = due_date

    # Before the 27th this is only a preview, so keep following Railway usage.
    # From the 27th onward, the amount is locked and will not be changed by syncs.
    if today < due_date or not invoice.total:
        invoice.railway_cost = amount
        invoice.management_fee = Decimal("0.00")
        invoice.discounts = Decimal("0.00")
        invoice.fines = Decimal("0.00")
        invoice.total = amount

    if today >= due_date:
        project.previous_month_cost = invoice.total or amount

    refresh_invoice_status(invoice, today)

    if invoice.status == "aguardando pagamento" and today < due_date:
        invoice.payment_link = None
        invoice.mp_preference_id = None
        invoice.mp_external_reference = None
        invoice.status = "pendente"

    return invoice
