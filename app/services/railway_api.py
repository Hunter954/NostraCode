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
    if response.status_code == 401:
        raise RailwayAPIError("Token Railway inválido ou sem permissão.")
    if response.status_code == 403:
        raise RailwayAPIError("Token Railway sem permissão para este recurso.")
    if response.status_code == 429:
        raise RailwayAPIError("Limite de requisições da API Railway atingido. Tente novamente mais tarde.")
    response.raise_for_status()

    payload = response.json()
    if payload.get("errors"):
        message = "; ".join([err.get("message", str(err)) for err in payload["errors"]])
        raise RailwayAPIError(message)
    return payload.get("data") or {}


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


def _find_first_number(value: Any, names: Tuple[str, ...]) -> Optional[Decimal]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                amount = _to_decimal(item)
                if amount is not None:
                    return amount
        for item in value.values():
            found = _find_first_number(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_number(item, names)
            if found is not None:
                return found
    return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    # Algumas APIs retornam centavos. Se vier um número muito alto, tratamos
    # como centavos para evitar exibir valores absurdos no painel.
    if amount > Decimal("10000"):
        amount = amount / Decimal("100")
    return amount.quantize(Decimal("0.01"))


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


def get_latest_deployment(project_id: str, environment_id: Optional[str], service_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not environment_id or not service_id:
        return None
    queries = [
        (
            """
            query LatestDeployment($projectId: String!, $environmentId: String!, $serviceId: String!) {
              deployments(input: { projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId, first: 1 }) {
                edges { node { id status createdAt updatedAt serviceId environmentId } }
              }
            }
            """,
            {"projectId": project_id, "environmentId": environment_id, "serviceId": service_id},
        ),
        (
            """
            query LatestDeployment($environmentId: String!, $serviceId: String!) {
              deployments(input: { environmentId: $environmentId, serviceId: $serviceId, first: 1 }) {
                edges { node { id status createdAt updatedAt serviceId environmentId } }
              }
            }
            """,
            {"environmentId": environment_id, "serviceId": service_id},
        ),
    ]
    last_error = None
    for query, variables in queries:
        try:
            data = graphql(query, variables)
            deployments = _nodes(data.get("deployments"))
            return deployments[0] if deployments else None
        except Exception as exc:
            last_error = exc
    if last_error:
        raise RailwayAPIError(f"Não foi possível buscar deploy: {last_error}")
    return None


def get_usage(project_id: str) -> Tuple[Optional[Decimal], Optional[Decimal], Dict[str, Any]]:
    """Best-effort usage lookup.

    A Railway API é GraphQL e pode evoluir. Por isso testamos algumas shapes
    comuns e extraímos os campos de custo por nome. Se a conta/token não expuser
    usage, a sincronização continua com projeto/serviços/deploy.
    """
    attempts = [
        """
        query ProjectUsage($projectId: String!) {
          projectUsage(projectId: $projectId) {
            currentCost estimatedCost current estimated total totalCost currency
            periodStart periodEnd billingPeriodStart billingPeriodEnd
          }
        }
        """,
        """
        query Usage($projectId: String!) {
          usage(projectId: $projectId) {
            currentCost estimatedCost current estimated total totalCost currency
            periodStart periodEnd billingPeriodStart billingPeriodEnd
          }
        }
        """,
        """
        query EstimatedUsage($projectId: String!) {
          estimatedUsage(projectId: $projectId) {
            currentCost estimatedCost current estimated total totalCost currency
            periodStart periodEnd billingPeriodStart billingPeriodEnd
          }
        }
        """,
    ]
    errors = []
    for query in attempts:
        try:
            data = graphql(query, {"projectId": project_id})
            raw = data.get("projectUsage") or data.get("usage") or data.get("estimatedUsage") or data
            current = _find_first_number(raw, ("currentCost", "current", "cost", "totalCost", "total"))
            estimated = _find_first_number(raw, ("estimatedCost", "estimated", "projectedCost", "forecastCost"))
            return current, estimated, raw if isinstance(raw, dict) else {"raw": raw}
        except Exception as exc:
            errors.append(str(exc))
    return None, None, {"usage_error": " | ".join(errors[-2:])}


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
