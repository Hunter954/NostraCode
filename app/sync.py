from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Tuple

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

    now = datetime.utcnow()
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
            project.last_sync_at = datetime.utcnow()
            db.session.commit()
            failed += 1
    return len(projects), ok, failed

def _current_period_label(today: date | None = None) -> str:
    today = today or date.today()
    months = [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]
    return f"{months[today.month - 1]}/{today.year}"


def _next_due_date(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=7)


def _next_invoice_number() -> str:
    prefix = str(date.today().year)
    count = Invoice.query.filter(Invoice.number.like(f"{prefix}-%")).count() + 1
    while True:
        number = f"{prefix}-{count:04d}"
        if not Invoice.query.filter_by(number=number).first():
            return number
        count += 1


def clear_unpaid_project_invoices(project: Project) -> int:
    """Remove faturas abertas antigas quando o Railway Project ID muda."""
    removable_statuses = ["pendente", "aguardando pagamento", "atrasado", "cancelado"]
    invoices = Invoice.query.filter(
        Invoice.project_id == project.id,
        Invoice.status.in_(removable_statuses),
    ).all()
    for invoice in invoices:
        db.session.delete(invoice)
    return len(invoices)


def refresh_project_invoice(project: Project, period_start=None, period_end=None) -> Invoice | None:
    """Create/update the current open invoice from Railway values.

    Paid invoices are preserved as history. Open invoices for the same project are
    replaced so the table never shows stale totals after a Railway ID/cost update.
    """
    clear_unpaid_project_invoices(project)

    amount = project.estimated_cost if project.estimated_cost is not None else project.current_cost
    amount = Decimal(amount or "0.00").quantize(Decimal("0.01"))

    period_date = period_start.date() if period_start else date.today()
    invoice = Invoice(
        number=_next_invoice_number(),
        client_id=project.client_id,
        project_id=project.id,
        period=_current_period_label(period_date),
        railway_cost=amount,
        management_fee=Decimal("0.00"),
        discounts=Decimal("0.00"),
        fines=Decimal("0.00"),
        total=amount,
        status="pendente",
        due_date=_next_due_date(),
    )
    db.session.add(invoice)
    return invoice

