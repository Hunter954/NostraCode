import os
import uuid
from decimal import Decimal

import requests


MP_API_BASE = "https://api.mercadopago.com"


class MercadoPagoConfigError(RuntimeError):
    pass


class MercadoPagoPaymentError(RuntimeError):
    def __init__(self, payload, status_code=None):
        self.payload = payload
        self.status_code = status_code or (payload.get("status") if isinstance(payload, dict) else None)
        super().__init__(humanize_mercadopago_error(payload, status_code=status_code))


def _response_payload(response):
    try:
        payload = response.json()
    except Exception:
        payload = {"message": response.text}
    if isinstance(payload, dict):
        payload.setdefault("http_status", response.status_code)
    return payload


def _raise_mp_error(response):
    raise MercadoPagoPaymentError(_response_payload(response), status_code=response.status_code)


def mercadopago_public_key():
    return os.getenv("MERCADO_PAGO_PUBLIC_KEY") or os.getenv("MP_PUBLIC_KEY")


def mercadopago_access_token():
    return os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or os.getenv("MP_ACCESS_TOKEN")


def mercadopago_environment():
    return (os.getenv("MERCADO_PAGO_ENVIRONMENT") or os.getenv("MP_ENV") or "test").strip().lower()


def mercadopago_test_payer_email():
    return (os.getenv("MERCADO_PAGO_TEST_PAYER_EMAIL") or "").strip()


def mercadopago_configured():
    return bool(mercadopago_access_token())


def validate_mercadopago_credentials():
    access_token = mercadopago_access_token()
    environment = mercadopago_environment()

    if not access_token:
        raise MercadoPagoConfigError("Configure MERCADO_PAGO_ACCESS_TOKEN para usar o Checkout Pro.")
    if environment not in {"test", "production", "prod", "live"}:
        raise MercadoPagoConfigError("MERCADO_PAGO_ENVIRONMENT deve ser test ou production.")
    if environment in {"production", "prod", "live"} and access_token.startswith("TEST-"):
        raise MercadoPagoConfigError("Ambiente production nao pode usar ACCESS TOKEN de teste.")
    return True


def invoice_external_reference(invoice):
    return f"invoice-{invoice.id}"


def _headers(idempotency_key=None):
    validate_mercadopago_credentials()
    headers = {
        "Authorization": f"Bearer {mercadopago_access_token()}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


def humanize_mercadopago_error(error_payload, status_code=None):
    if not isinstance(error_payload, dict):
        return "Mercado Pago recusou a transacao. Confira as credenciais, ambiente e o ID do pagamento."

    message = str(error_payload.get("message") or error_payload.get("error") or "")
    causes = error_payload.get("cause") or []
    cause_text = " ".join(str(cause.get("description", "")) for cause in causes if isinstance(cause, dict))
    combined = f"{message} {cause_text}".lower()
    status = status_code or error_payload.get("status") or error_payload.get("http_status")

    if "unauthorized use of live credentials" in combined:
        return (
            "Mercado Pago recusou por ambiente/credencial incompatível com esse pagamento. "
            "Use o ACCESS TOKEN da mesma aplicação e do mesmo ambiente em que o pagamento foi criado. "
            "Detalhe MP: Unauthorized use of live credentials."
        )
    if status == 404 or "not found" in combined:
        return "Mercado Pago não encontrou esse pagamento. O sistema precisa do payment_id real do Mercado Pago para estornar."
    if "invalid access token" in combined or "access token" in combined or status == 401:
        return "Mercado Pago recusou as credenciais. Confira se o ACCESS TOKEN pertence à aplicação correta e se foi feito redeploy."
    if "already refunded" in combined or "refunded" in combined:
        return "Esse pagamento já consta como estornado no Mercado Pago."
    return str(error_payload.get("message") or error_payload.get("error") or "Mercado Pago recusou a transacao.")


def mercadopago_error_debug(error_payload):
    if not isinstance(error_payload, dict):
        return str(error_payload)
    parts = []
    for key in ("http_status", "status", "error", "message"):
        if error_payload.get(key):
            parts.append(f"{key}: {error_payload.get(key)}")
    causes = error_payload.get("cause") or []
    if causes:
        parts.append("cause: " + "; ".join(str(c.get("description", c)) for c in causes if isinstance(c, dict)))
    return " | ".join(parts) or str(error_payload)


def _public_base_url():
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "http://localhost:5000").rstrip("/")


def create_checkout_preference(invoice, idempotency_key=None):
    validate_mercadopago_credentials()
    base_url = _public_base_url()
    external_reference = invoice_external_reference(invoice)
    amount = Decimal(invoice.total).quantize(Decimal("0.01"))

    payload = {
        "items": [{
            "id": str(invoice.id),
            "title": f"Fatura {invoice.number} - {invoice.project.name}",
            "description": f"Nostra Codes - {invoice.period}",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": float(amount),
        }],
        "payer": {"name": invoice.client.name, "email": invoice.client.email},
        "external_reference": external_reference,
        "notification_url": f"{base_url}/webhooks/mercadopago",
        "back_urls": {
            "success": f"{base_url}/invoices/{invoice.id}/payment/success",
            "failure": f"{base_url}/invoices/{invoice.id}/payment/failure",
            "pending": f"{base_url}/invoices/{invoice.id}/payment/pending",
        },
        "auto_return": "approved",
        "metadata": {"invoice_id": invoice.id, "invoice_number": invoice.number, "project_id": invoice.project_id, "mp_environment": mercadopago_environment()},
    }
    statement_descriptor = os.getenv("MERCADO_PAGO_STATEMENT_DESCRIPTOR")
    if statement_descriptor:
        payload["statement_descriptor"] = statement_descriptor[:22]

    response = requests.post(f"{MP_API_BASE}/checkout/preferences", json=payload, headers=_headers(idempotency_key or str(uuid.uuid4())), timeout=20)
    if response.status_code >= 400:
        _raise_mp_error(response)
    return response.json()


def checkout_redirect_url(preference):
    environment = mercadopago_environment()
    if environment == "test":
        return preference.get("sandbox_init_point") or preference.get("init_point")
    return preference.get("init_point") or preference.get("sandbox_init_point")


def fetch_payment(payment_id):
    validate_mercadopago_credentials()
    response = requests.get(f"{MP_API_BASE}/v1/payments/{payment_id}", headers={"Authorization": f"Bearer {mercadopago_access_token()}"}, timeout=15)
    if response.status_code >= 400:
        _raise_mp_error(response)
    return response.json()


def search_payment_by_external_reference(external_reference):
    validate_mercadopago_credentials()
    if not external_reference:
        return None
    response = requests.get(
        f"{MP_API_BASE}/v1/payments/search",
        params={"external_reference": external_reference, "sort": "date_created", "criteria": "desc"},
        headers={"Authorization": f"Bearer {mercadopago_access_token()}"},
        timeout=15,
    )
    if response.status_code >= 400:
        _raise_mp_error(response)
    results = (response.json() or {}).get("results") or []
    return results[0] if results else None


def create_subscription_preference(subscription, idempotency_key=None):
    validate_mercadopago_credentials()
    base_url = _public_base_url()
    amount = Decimal(subscription.amount).quantize(Decimal("0.01"))
    external_reference = subscription.mp_external_reference

    payload = {
        "items": [{
            "id": f"subscription-{subscription.id}",
            "title": f"Assinatura {subscription.plan_months} meses - {subscription.project.name}",
            "description": "Nostra Codes - plano pre-pago por projeto",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": float(amount),
        }],
        "payer": {"name": subscription.client.name, "email": subscription.client.email},
        "external_reference": external_reference,
        "notification_url": f"{base_url}/webhooks/mercadopago",
        "back_urls": {
            "success": f"{base_url}/projects/{subscription.project_id}/subscription/return/success?subscription_id={subscription.id}",
            "failure": f"{base_url}/projects/{subscription.project_id}/subscription/return/failure?subscription_id={subscription.id}",
            "pending": f"{base_url}/projects/{subscription.project_id}/subscription/return/pending?subscription_id={subscription.id}",
        },
        "auto_return": "approved",
        "metadata": {"type": "project_subscription", "subscription_id": subscription.id, "project_id": subscription.project_id, "plan_months": subscription.plan_months, "mp_environment": mercadopago_environment()},
    }
    statement_descriptor = os.getenv("MERCADO_PAGO_STATEMENT_DESCRIPTOR")
    if statement_descriptor:
        payload["statement_descriptor"] = statement_descriptor[:22]

    response = requests.post(f"{MP_API_BASE}/checkout/preferences", json=payload, headers=_headers(idempotency_key or str(uuid.uuid4())), timeout=20)
    if response.status_code >= 400:
        _raise_mp_error(response)
    return response.json()


def refund_payment(payment_id, idempotency_key=None):
    validate_mercadopago_credentials()
    response = requests.post(f"{MP_API_BASE}/v1/payments/{payment_id}/refunds", json={}, headers=_headers(idempotency_key or str(uuid.uuid4())), timeout=20)
    if response.status_code >= 400:
        _raise_mp_error(response)
    return response.json()
