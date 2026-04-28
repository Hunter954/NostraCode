import os
import uuid
from decimal import Decimal

import requests


MP_API_BASE = "https://api.mercadopago.com"
TEST_USER_EMAIL_SUFFIX = "@testuser.com"
PRODUCTION_ENVS = {"production", "prod", "live"}
TEST_ENVS = {"test", "sandbox", "testing", "development", "dev"}


class MercadoPagoConfigError(RuntimeError):
    pass


class MercadoPagoPaymentError(RuntimeError):
    def __init__(self, payload):
        self.payload = payload
        message = humanize_mercadopago_error(payload)
        super().__init__(message)


def mercadopago_public_key():
    return (os.getenv("MERCADO_PAGO_PUBLIC_KEY") or os.getenv("MP_PUBLIC_KEY") or "").strip()


def mercadopago_access_token():
    return (os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or os.getenv("MP_ACCESS_TOKEN") or "").strip()


def mercadopago_environment():
    """Return normalized Mercado Pago environment: test or production.

    Mercado Pago can show test credentials that also start with APP_USR-. Because of
    that, do not infer test/production only by the token prefix.
    """
    raw = (os.getenv("MERCADO_PAGO_ENVIRONMENT") or os.getenv("MP_ENV") or "test").strip().lower()
    if raw in PRODUCTION_ENVS:
        return "production"
    return "test"


def mercadopago_test_payer_email():
    return (os.getenv("MERCADO_PAGO_TEST_PAYER_EMAIL") or "").strip()


def mercadopago_configured():
    return bool(mercadopago_public_key() and mercadopago_access_token())


def credential_summary():
    """Safe, non-secret summary for diagnostics/logs/UI."""
    public_key = mercadopago_public_key()
    access_token = mercadopago_access_token()
    return {
        "environment": mercadopago_environment(),
        "public_key_prefix": public_key.split("-", 1)[0] if public_key else "missing",
        "access_token_prefix": access_token.split("-", 1)[0] if access_token else "missing",
        "has_test_payer_email": bool(mercadopago_test_payer_email()),
    }


def validate_mercadopago_credentials():
    public_key = mercadopago_public_key()
    access_token = mercadopago_access_token()
    raw_environment = (os.getenv("MERCADO_PAGO_ENVIRONMENT") or os.getenv("MP_ENV") or "test").strip().lower()
    environment = mercadopago_environment()

    if not public_key or not access_token:
        raise MercadoPagoConfigError("Configure MERCADO_PAGO_PUBLIC_KEY e MERCADO_PAGO_ACCESS_TOKEN.")

    if raw_environment not in TEST_ENVS | PRODUCTION_ENVS:
        raise MercadoPagoConfigError("MERCADO_PAGO_ENVIRONMENT deve ser test ou production.")

    if environment == "test":
        test_payer_email = mercadopago_test_payer_email()
        if not test_payer_email:
            raise MercadoPagoConfigError(
                "Configure MERCADO_PAGO_TEST_PAYER_EMAIL com o e-mail de uma conta compradora de teste do Mercado Pago."
            )
        if "@" not in test_payer_email:
            raise MercadoPagoConfigError(
                "MERCADO_PAGO_TEST_PAYER_EMAIL precisa ser o e-mail da conta de teste, não apenas o usuário/ID. "
                "Crie/copiei uma conta compradora em Mercado Pago > Contas de teste."
            )

    # Important: test credentials may start with APP_USR-, depending on the Mercado Pago panel.
    # We only enforce that production does not accidentally use old TEST- keys.
    if environment == "production" and (public_key.startswith("TEST-") or access_token.startswith("TEST-")):
        raise MercadoPagoConfigError(
            "Ambiente production não pode usar credenciais TEST-. Use as credenciais de produção do Mercado Pago."
        )

    return True


def invoice_external_reference(invoice):
    return f"invoice-{invoice.id}"


def _headers(idempotency_key=None):
    validate_mercadopago_credentials()
    return {
        "Authorization": f"Bearer {mercadopago_access_token()}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key or str(uuid.uuid4()),
    }


def humanize_mercadopago_error(error_payload):
    if not isinstance(error_payload, dict):
        return "Mercado Pago recusou a transação. Confira as credenciais e os dados do pagamento."

    message = str(error_payload.get("message") or "")
    causes = error_payload.get("cause") or []
    cause_text = " ".join(str(cause.get("description", "")) for cause in causes if isinstance(cause, dict))
    combined = f"{message} {cause_text}".lower()

    if "unauthorized use of live credentials" in combined:
        return (
            "Mercado Pago recusou por mistura de ambiente real/teste. Use, ao mesmo tempo: "
            "credenciais da tela Credenciais de teste da mesma aplicação, uma conta compradora de teste em "
            "MERCADO_PAGO_TEST_PAYER_EMAIL e cartões de teste. Não use sua conta real como comprador no teste."
        )
    if "invalid access token" in combined or "access token" in combined or error_payload.get("status") == 401:
        return "Mercado Pago recusou as credenciais. Confira se o ACCESS TOKEN pertence à mesma aplicação da PUBLIC KEY."
    if "payer" in combined and "email" in combined:
        return "Mercado Pago recusou o pagador. Em teste, use o e-mail de uma conta compradora de teste."
    return error_payload.get("message") or "Mercado Pago recusou a transação."


def create_card_payment(invoice, payment_data, idempotency_key=None):
    """Create a Checkout Transparente payment using data returned by Card Payment Brick."""
    validate_mercadopago_credentials()

    token = payment_data.get("token")
    payment_method_id = payment_data.get("payment_method_id")
    installments = payment_data.get("installments") or 1
    issuer_id = payment_data.get("issuer_id")
    payer = payment_data.get("payer") or {}
    payer_email = payer.get("email") or invoice.client.email

    if mercadopago_environment() == "test":
        payer_email = mercadopago_test_payer_email()

    if not token or not payment_method_id or not payer_email:
        raise ValueError("Dados de pagamento incompletos.")

    base_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("BASE_URL") or "http://localhost:5000").rstrip("/")
    external_reference = invoice_external_reference(invoice)

    payload = {
        "transaction_amount": float(Decimal(invoice.total).quantize(Decimal("0.01"))),
        "token": token,
        "description": f"Fatura {invoice.number} - {invoice.project.name}",
        "installments": int(installments),
        "payment_method_id": payment_method_id,
        "payer": {"email": payer_email},
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
