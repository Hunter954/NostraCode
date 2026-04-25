import os
import requests


def create_payment_preference(invoice):
    token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
    external_reference = f"invoice-{invoice.id}"

    if not token:
        return {
            "payment_link": f"/invoices/{invoice.id}",
            "preference_id": "demo-mode",
            "external_reference": external_reference,
            "demo": True,
        }

    payload = {
        "items": [
            {
                "title": f"Fatura {invoice.number} - {invoice.project.name}",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": float(invoice.total),
            }
        ],
        "payer": {"email": invoice.client.email, "name": invoice.client.name},
        "external_reference": external_reference,
        "notification_url": f"{base_url}/webhooks/mercadopago",
        "back_urls": {
            "success": f"{base_url}/invoices/{invoice.id}?payment=success",
            "pending": f"{base_url}/invoices/{invoice.id}?payment=pending",
            "failure": f"{base_url}/invoices/{invoice.id}?payment=failure",
        },
        "auto_return": "approved",
    }
    response = requests.post(
        "https://api.mercadopago.com/checkout/preferences",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "payment_link": data.get("init_point"),
        "preference_id": data.get("id"),
        "external_reference": external_reference,
        "demo": False,
    }


def fetch_payment(payment_id):
    token = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
    if not token:
        return None
    response = requests.get(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()
