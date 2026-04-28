import os
import uuid
from decimal import Decimal

import requests


MP_API_BASE = "https://api.mercadopago.com"


def mercadopago_public_key():
    return os.getenv("MERCADO_PAGO_PUBLIC_KEY") or os.getenv("MP_PUBLIC_KEY")


def mercadopago_access_token():
    return os.getenv("MERCADO_PAGO_ACCESS_TOKEN") or os.getenv("MP_ACCESS_TOKEN")


def mercadopago_configured():
    return bool(mercadopago_public_key() and mercadopago_access_token())


def invoice_external_reference(invoice):
    return f"invoice-{invoice.id}"


def _headers(idempotency_key=None):
    token = mercadopago_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key or str(uuid.uuid4()),
    }
    return headers


def create_card_payment(invoice, payment_data, idempotency_key=None):
    """Create a Checkout Transparente payment using data returned by Card Payment Brick."""
    if not mercadopago_access_token():
        raise RuntimeError("MERCADO_PAGO_ACCESS_TOKEN não configurado.")

    token = payment_data.get("token")
    payment_method_id = payment_data.get("payment_method_id")
    installments = payment_data.get("installments") or 1
    issuer_id = payment_data.get("issuer_id")
    payer = payment_data.get("payer") or {}
    payer_email = payer.get("email") or invoice.client.email

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
            error_payload = {"message": response.text}
        raise RuntimeError(error_payload)
    return response.json()


def fetch_payment(payment_id):
    token = mercadopago_access_token()
    if not token:
        return None
    response = requests.get(
        f"{MP_API_BASE}/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
