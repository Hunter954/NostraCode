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
    return os.getenv("MERCADO_PAGO_PUBLIC_KEY") or os.getenv("MP_PUBLIC_KEY")


def mercadopago_access_token():
    return os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or os.getenv("MP_ACCESS_TOKEN")


def mercadopago_environment():
    """Returns test or production. Default is test to avoid accidental live charges."""
    return (os.getenv("MERCADO_PAGO_ENVIRONMENT") or os.getenv("MP_ENV") or "test").strip().lower()


def mercadopago_test_payer_email():
    # Mantido apenas por compatibilidade com ambientes que ja tenham a variavel.
    # O fluxo de cartao/Brick nao usa esta variavel automaticamente.
    return (os.getenv("MERCADO_PAGO_TEST_PAYER_EMAIL") or "").strip()


def mercadopago_configured():
    return bool(mercadopago_public_key() and mercadopago_access_token())


def validate_mercadopago_credentials():
    public_key = mercadopago_public_key()
    access_token = mercadopago_access_token()
    environment = mercadopago_environment()

    if not public_key or not access_token:
        raise MercadoPagoConfigError("Configure MERCADO_PAGO_PUBLIC_KEY e MERCADO_PAGO_ACCESS_TOKEN.")

    if environment not in {"test", "production", "prod", "live"}:
        raise MercadoPagoConfigError("MERCADO_PAGO_ENVIRONMENT deve ser test ou production.")

    # O Mercado Pago pode exibir credenciais de teste com prefixo APP_USR-.
    # Nao valide ambiente pelo prefixo; deixe a API validar o par de credenciais.
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
        "X-Idempotency-Key": idempotency_key or str(uuid.uuid4()),
    }
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
            "Mercado Pago recusou por mistura de ambiente real/teste. Para pagamento com cartao no Checkout Transparente/Brick, "
            "use as credenciais da tela Credenciais de teste da sua conta real e preencha no checkout um e-mail de pagador comum, "
            "diferente do e-mail da sua conta Mercado Pago. Nao force e-mail de conta de teste compradora no Brick de cartao."
        )
    if "invalid access token" in combined or "access token" in combined or error_payload.get("status") == 401:
        return "Mercado Pago recusou as credenciais. Confira se o ACCESS TOKEN pertence a mesma aplicacao da PUBLIC KEY."
    return error_payload.get("message") or "Mercado Pago recusou a transacao."


def create_card_payment(invoice, payment_data, idempotency_key=None):
    """Create a Checkout Transparente payment using data returned by Card Payment Brick."""
    validate_mercadopago_credentials()

    token = payment_data.get("token")
    payment_method_id = payment_data.get("payment_method_id")
    installments = payment_data.get("installments") or 1
    issuer_id = payment_data.get("issuer_id")
    payer = payment_data.get("payer") or {}
    payer_email = payer.get("email") or invoice.client.email

    # No Card Payment Brick, use um e-mail comum de pagador, diferente do e-mail
    # da conta Mercado Pago vendedora. Nao substitua automaticamente por conta
    # de teste compradora, pois isso pode causar mistura real/teste nesse fluxo.
    if not token or not payment_method_id or not payer_email:
        raise ValueError("Dados de pagamento incompletos.")

    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
    external_reference = invoice_external_reference(invoice)

    payload = {
        "transaction_amount": float(Decimal(invoice.total).quantize(Decimal("0.01"))),
        "token": token,
        "description": f"Fatura {invoice.number} - {invoice.project.name}",
        "installments": int(installments),
        "payment_method_id": payment_method_id,
        "payer": {
            "email": payer_email,
        },
        "external_reference": external_reference,
        "notification_url": f"{base_url}/webhooks/mercadopago",
        "metadata": {
            "invoice_id": invoice.id,
            "invoice_number": invoice.number,
            "project_id": invoice.project_id,
            "mp_environment": mercadopago_environment(),
        },
    }

    identification = payer.get("identification") or {}
    if identification.get("type") and identification.get("number"):
        payload["payer"]["identification"] = {
            "type": identification.get("type"),
            "number": identification.get("number"),
        }
    if issuer_id:
        payload["issuer_id"] = str(issuer_id)

    statement_descriptor = os.getenv("MERCADO_PAGO_STATEMENT_DESCRIPTOR")
    if statement_descriptor:
        payload["statement_descriptor"] = statement_descriptor[:22]

    response = requests.post(
        f"{MP_API_BASE}/v1/payments",
        json=payload,
        headers=_headers(idempotency_key),
        timeout=20,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except Exception:
            error_payload = {"message": response.text, "status": response.status_code}
        raise MercadoPagoPaymentError(error_payload)
    return response.json()


def fetch_payment(payment_id):
    validate_mercadopago_credentials()
    response = requests.get(
        f"{MP_API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {mercadopago_access_token()}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
