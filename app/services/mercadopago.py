import os
import uuid
from decimal import Decimal

import requests


MP_API_BASE = "https://api.mercadopago.com"


class MercadoPagoConfigError(RuntimeError):
    pass


class MercadoPagoPaymentError(RuntimeError):
    def __init__(self, payload):
        self.payload = payload
        message = humanize_mercadopago_error(payload)
        super().__init__(message)


def mercadopago_public_key():
    # Mantido por compatibilidade. Checkout Pro nao precisa da Public Key no frontend.
    return os.getenv("MERCADO_PAGO_PUBLIC_KEY") or os.getenv("MP_PUBLIC_KEY")


def mercadopago_access_token():
    return os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or os.getenv("MP_ACCESS_TOKEN")


def mercadopago_environment():
    """Returns test or production. Default is test to avoid accidental live charges."""
    return (os.getenv("MERCADO_PAGO_ENVIRONMENT") or os.getenv("MP_ENV") or "test").strip().lower()


def mercadopago_test_payer_email():
    # Checkout Pro nao precisa desta variavel; mantida para nao quebrar ambientes existentes.
    return (os.getenv("MERCADO_PAGO_TEST_PAYER_EMAIL") or "").strip()


def mercadopago_configured():
    # Checkout Pro precisa somente do Access Token no backend.
    return bool(mercadopago_access_token())


def validate_mercadopago_credentials():
    access_token = mercadopago_access_token()
    environment = mercadopago_environment()

    if not access_token:
        raise MercadoPagoConfigError("Configure MERCADO_PAGO_ACCESS_TOKEN para usar o Checkout Pro.")

    if environment not in {"test", "production", "prod", "live"}:
        raise MercadoPagoConfigError("MERCADO_PAGO_ENVIRONMENT deve ser test ou production.")

    if environment in {"production", "prod", "live"} and access_token.startswith("TEST-"):
        raise MercadoPagoConfigError(
            "Ambiente production nao pode usar ACCESS TOKEN de teste. "
            "Use as credenciais da tela Credenciais de producao."
        )

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


def humanize_mercadopago_error(error_payload):
    if not isinstance(error_payload, dict):
        return "Mercado Pago recusou a transacao. Confira as credenciais e os dados do pagamento."

    message = str(error_payload.get("message") or "")
    causes = error_payload.get("cause") or []
    cause_text = " ".join(str(cause.get("description", "")) for cause in causes if isinstance(cause, dict))
    combined = f"{message} {cause_text}".lower()

    if "unauthorized use of live credentials" in combined:
        return (
            "Mercado Pago recusou por mistura de ambiente real/teste. No Checkout Pro, use o ACCESS TOKEN da tela "
            "Credenciais de teste para testar, ou mude MERCADO_PAGO_ENVIRONMENT=production e use credenciais de producao."
        )
    if "invalid access token" in combined or "access token" in combined or error_payload.get("status") == 401:
        return "Mercado Pago recusou as credenciais. Confira se o ACCESS TOKEN pertence a aplicacao correta."
    return error_payload.get("message") or "Mercado Pago recusou a transacao."


def _public_base_url():
    return (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "http://localhost:5000").rstrip("/")


def create_checkout_preference(invoice, idempotency_key=None):
    """Create a Mercado Pago Checkout Pro preference and return the API payload."""
    validate_mercadopago_credentials()

    base_url = _public_base_url()
    external_reference = invoice_external_reference(invoice)
    amount = Decimal(invoice.total).quantize(Decimal("0.01"))

    payload = {
        "items": [
            {
                "id": str(invoice.id),
                "title": f"Fatura {invoice.number} - {invoice.project.name}",
                "description": f"Nostra Codes - {invoice.period}",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(amount),
            }
        ],
        "payer": {
            "name": invoice.client.name,
            "email": invoice.client.email,
        },
        "external_reference": external_reference,
        "notification_url": f"{base_url}/webhooks/mercadopago",
        "back_urls": {
            "success": f"{base_url}/invoices/{invoice.id}/payment/success",
            "failure": f"{base_url}/invoices/{invoice.id}/payment/failure",
            "pending": f"{base_url}/invoices/{invoice.id}/payment/pending",
        },
        "auto_return": "approved",
        "metadata": {
            "invoice_id": invoice.id,
            "invoice_number": invoice.number,
            "project_id": invoice.project_id,
            "mp_environment": mercadopago_environment(),
        },
    }

    statement_descriptor = os.getenv("MERCADO_PAGO_STATEMENT_DESCRIPTOR")
    if statement_descriptor:
        payload["statement_descriptor"] = statement_descriptor[:22]

    response = requests.post(
        f"{MP_API_BASE}/checkout/preferences",
        json=payload,
        headers=_headers(idempotency_key or str(uuid.uuid4())),
        timeout=20,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = {"message": response.text, "status": response.status_code}
        raise MercadoPagoPaymentError(error_payload)
    return response.json()


def checkout_redirect_url(preference):
    environment = mercadopago_environment()
    if environment == "test":
        return preference.get("sandbox_init_point") or preference.get("init_point")
    return preference.get("init_point") or preference.get("sandbox_init_point")


def fetch_payment(payment_id):
    validate_mercadopago_credentials()
    response = requests.get(
        f"{MP_API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {mercadopago_access_token()}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
