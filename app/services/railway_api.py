import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import requests

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"


class RailwayAPIError(RuntimeError):
    pass


def _token_headers() -> Dict[str, str]:
    """Build headers for account/workspace/project tokens.

    Account/workspace tokens use Authorization: Bearer. Project tokens use
    Project-Access-Token. Choose with RAILWAY_TOKEN_TYPE=project if needed.
    """
    token = os.getenv("RAILWAY_API_TOKEN") or os.getenv("RAILWAY_TOKEN")
    if not token:
        raise RailwayAPIError("RAILWAY_API_TOKEN não configurado.")

    token_type = (os.getenv("RAILWAY_TOKEN_TYPE") or "account").lower().strip()
    headers = {"Content-Type": "application/json"}
    if token_type == "project":
        headers["Project-Access-Token"] = token
    else:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def graphql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = requests.post(
        RAILWAY_API_URL,
        json={"query": query, "variables": variables or {}},
        headers=_token_headers(),
        timeout=30,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code == 401:
        raise RailwayAPIError("Token Railway inválido ou sem permissão.")
    if response.status_code == 403:
        raise RailwayAPIError("Token Railway sem permissão para este recurso.")
    if response.status_code == 429:
        raise RailwayAPIError("Limite de requisições da API Railway atingido. Tente novamente mais tarde.")
    if response.status_code >= 400:
        message = _graphql_error_message(payload) or response.text[:500] or response.reason
        raise RailwayAPIError(f"Railway API HTTP {response.status_code}: {message}")

    if isinstance(payload, dict) and payload.get("errors"):
        raise RailwayAPIError(_graphql_error_message(payload) or "Erro GraphQL Railway.")
    return payload.get("data") or {}


def _graphql_error_message(payload: Dict[str, Any]) -> str:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not errors:
        return ""
    return "; ".join([err.get("message", str(err)) for err in errors if isinstance(err, dict)])


def _nodes(value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("nodes"), list):
            return [x for x in value["nodes"] if isinstance(x, dict)]
        if isinstance(value.get("edges"), list):
            return [edge.get("node") for edge in value["edges"] if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]
    return []


def _dig(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _to_decimal(value: Any, quantize: Optional[Decimal] = Decimal("0.01")) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount.quantize(quantize) if quantize else amount


def _env_decimal(name: str) -> Optional[Decimal]:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    return _to_decimal(str(value).replace(",", "."), quantize=None)

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def get_project(project_id: str) -> Dict[str, Any]:
    query = """
    query GetProject($id: String!) {
      project(id: $id) {
        id
        name
        description
        createdAt
        updatedAt
        services { edges { node { id name } } }
        environments { edges { node { id name } } }
      }
    }
    """
    return graphql(query, {"id": project_id}).get("project") or {}


def get_service_domains(project_id: str, environment_id: Optional[str], service_id: Optional[str]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Best-effort lookup for the public Railway URL."""
    if not environment_id or not service_id:
        return None, {}

    attempts = [
        (
            """
            query Domains($projectId: String!, $environmentId: String!, $serviceId: String!) {
              domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
                serviceDomains { domain targetPort }
                customDomains { domain targetPort }
              }
            }
            """,
            {"projectId": project_id, "environmentId": environment_id, "serviceId": service_id},
        ),
        (
            """
            query ServiceInstanceDomains($environmentId: String!, $serviceId: String!) {
              serviceInstance(environmentId: $environmentId, serviceId: $serviceId) {
                domains {
                  serviceDomains { domain targetPort }
                  customDomains { domain targetPort }
                }
              }
            }
            """,
            {"environmentId": environment_id, "serviceId": service_id},
        ),
    ]

    errors = []
    for query, variables in attempts:
        try:
            data = graphql(query, variables)
            domain = _extract_domain(data)
            if domain:
                if not domain.startswith(("http://", "https://")):
                    domain = f"https://{domain}"
                return domain, data
            return None, data
        except Exception as exc:
            errors.append(str(exc))
    return None, {"domain_error": " | ".join(errors[-2:])}


def _extract_domain(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"domain", "url", "uri", "staticUrl"} and isinstance(item, str) and item:
                return item
            found = _extract_domain(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_domain(item)
            if found:
                return found
    return None


def get_latest_deployment(project_id: str, environment_id: Optional[str], service_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not environment_id or not service_id:
        return None

    attempts = [
        (
            """
            query ServiceInstanceDeployment($environmentId: String!, $serviceId: String!) {
              serviceInstance(environmentId: $environmentId, serviceId: $serviceId) {
                latestDeployment {
                  id status createdAt updatedAt serviceId environmentId url staticUrl
                }
              }
            }
            """,
            {"environmentId": environment_id, "serviceId": service_id},
            lambda data: _dig(data, "serviceInstance", "latestDeployment"),
        ),
        (
            """
            query LatestDeployment($input: DeploymentListInput!) {
              deployments(first: 1, input: $input) {
                edges { node { id status createdAt updatedAt serviceId environmentId url staticUrl } }
              }
            }
            """,
            {"input": {"projectId": project_id, "environmentId": environment_id, "serviceId": service_id}},
            lambda data: (_nodes(data.get("deployments")) or [None])[0],
        ),
    ]

    errors = []
    for query, variables, extractor in attempts:
        try:
            data = graphql(query, variables)
            deployment = extractor(data)
            if deployment:
                return deployment
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RailwayAPIError(f"Não foi possível buscar deploy: {' | '.join(errors[-2:])}")
    return None


def _billing_period() -> Tuple[str, str]:
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat() + "Z", end.isoformat() + "Z"


RAILWAY_DEFAULT_USAGE_RATES = {
    # Railway returns accumulated usage units, not money.
    # CPU/RAM/Volume are minute-based; egress is per GB.
    "CPU_USAGE": Decimal("0.000463"),
    "MEMORY_USAGE_GB": Decimal("0.000231"),
    "NETWORK_TX_GB": Decimal("0.05"),
    "DISK_USAGE_GB": Decimal("0.00000347"),
    "EPHEMERAL_DISK_USAGE_GB": Decimal("0.00000347"),
}


def _rate_for_measurement(measurement: str) -> Decimal:
    env_name = f"RAILWAY_RATE_{measurement}"
    return _env_decimal(env_name) or RAILWAY_DEFAULT_USAGE_RATES.get(measurement, Decimal("0.00"))


def _sum_usage_cost(items: Any, value_key: str) -> Optional[Decimal]:
    rows = items if isinstance(items, list) else []
    total = Decimal("0.00")
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        measurement = row.get("measurement")
        usage = _to_decimal(row.get(value_key), quantize=None)
        if not measurement or usage is None:
            continue
        total += usage * _rate_for_measurement(str(measurement))
        found = True
    return total.quantize(Decimal("0.01")) if found else None


def _apply_optional_brl_conversion(value: Optional[Decimal]) -> Optional[Decimal]:
    """Railway usage prices are USD. Convert only when a rate is configured.

    Set RAILWAY_USD_TO_BRL_RATE or USD_BRL_RATE in the environment to bill in
    BRL. If neither is set, the numeric value remains the Railway USD cost.
    """
    if value is None:
        return None
    rate = _env_decimal("RAILWAY_USD_TO_BRL_RATE") or _env_decimal("USD_BRL_RATE")
    if not rate:
        return value
    return (value * rate).quantize(Decimal("0.01"))

def get_usage(project_id: str) -> Tuple[Optional[Decimal], Optional[Decimal], Dict[str, Any]]:
    """Fetch current and estimated Railway project cost.

    Railway's current GraphQL schema expects usage/estimatedUsage to receive
    `measurements`. The old projectUsage/currentCost shapes can return 400.
    """
    measurements = [
        "CPU_USAGE",
        "MEMORY_USAGE_GB",
        "NETWORK_TX_GB",
        "DISK_USAGE_GB",
        "EPHEMERAL_DISK_USAGE_GB",
    ]
    start_date, end_date = _billing_period()
    raw: Dict[str, Any] = {
        "currency": "USD",
        "periodStart": start_date,
        "periodEnd": end_date,
    }
    errors = []

    try:
        data = graphql(
            """
            query ProjectUsage($projectId: String!, $measurements: [MetricMeasurement!]!, $startDate: DateTime!, $endDate: DateTime!) {
              usage(projectId: $projectId, measurements: $measurements, startDate: $startDate, endDate: $endDate) {
                measurement
                value
              }
            }
            """,
            {
                "projectId": project_id,
                "measurements": measurements,
                "startDate": start_date,
                "endDate": end_date,
            },
        )
        raw["usage"] = data.get("usage") or []
    except Exception as exc:
        errors.append(str(exc))

    try:
        data = graphql(
            """
            query EstimatedUsage($projectId: String!, $measurements: [MetricMeasurement!]!) {
              estimatedUsage(projectId: $projectId, measurements: $measurements) {
                measurement
                estimatedValue
                projectId
              }
            }
            """,
            {"projectId": project_id, "measurements": measurements},
        )
        raw["estimatedUsage"] = data.get("estimatedUsage") or []
    except Exception as exc:
        errors.append(str(exc))

    current_usd = _sum_usage_cost(raw.get("usage"), "value")
    estimated_usd = _sum_usage_cost(raw.get("estimatedUsage"), "estimatedValue")
    raw["currentCostUsd"] = str(current_usd) if current_usd is not None else None
    raw["estimatedCostUsd"] = str(estimated_usd) if estimated_usd is not None else None

    rate = _env_decimal("RAILWAY_USD_TO_BRL_RATE") or _env_decimal("USD_BRL_RATE")
    current = _apply_optional_brl_conversion(current_usd)
    estimated = _apply_optional_brl_conversion(estimated_usd)
    raw["currency"] = "BRL" if rate else "USD"

    if current is None and estimated is None and errors:
        raw["usage_error"] = " | ".join(errors[-2:])
    elif errors:
        raw["usage_warning"] = " | ".join(errors[-2:])

    return current, estimated, raw


def pick_environment(environments: List[Dict[str, Any]], preferred_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if preferred_id:
        for env in environments:
            if env.get("id") == preferred_id:
                return env
    for env in environments:
        if (env.get("name") or "").lower() in {"production", "produção", "prod"}:
            return env
    return environments[0] if environments else None


def pick_service(services: List[Dict[str, Any]], preferred_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if preferred_id:
        for service in services:
            if service.get("id") == preferred_id:
                return service
    return services[0] if services else None


def parse_period(raw_usage: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    start = None
    end = None
    for key in ("periodStart", "billingPeriodStart", "startDate", "start"):
        start = _parse_dt(raw_usage.get(key))
        if start:
            break
    for key in ("periodEnd", "billingPeriodEnd", "endDate", "end"):
        end = _parse_dt(raw_usage.get(key))
        if end:
            break
    return start, end
