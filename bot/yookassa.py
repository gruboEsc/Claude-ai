"""Minimal async client for the YooKassa API v3 (https://yookassa.ru/developers/api)."""

import base64
import uuid

import aiohttp

API = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    pass


class YooKassa:
    def __init__(self, shop_id: str, secret: str):
        self.auth = "Basic " + base64.b64encode(f"{shop_id}:{secret}".encode()).decode()
        self.session: aiohttp.ClientSession | None = None

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        if self.session is None:
            self.session = aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=30))
        headers = {"Authorization": self.auth}
        if method == "POST":
            # A fresh key per call: retries of the same logical payment are not done here.
            headers["Idempotence-Key"] = str(uuid.uuid4())
        async with self.session.request(method, API + path, json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise YooKassaError(f"{resp.status}: {data.get('description') or data}")
            return data

    async def create_payment(
        self,
        *,
        rub: int,
        description: str,
        user_id: int,
        email: str,
        vat_code: int,
        return_url: str | None = None,
        save_method: bool = False,
        method_id: str | None = None,
    ) -> dict:
        amount = {"value": f"{rub}.00", "currency": "RUB"}
        body = {
            "amount": amount,
            "capture": True,
            "description": description,
            "metadata": {"user_id": str(user_id)},
            "receipt": {
                "customer": {"email": email},
                "items": [{
                    "description": description[:128],
                    "quantity": "1.00",
                    "amount": amount,
                    "vat_code": vat_code,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }],
            },
        }
        if method_id:
            body["payment_method_id"] = method_id  # renewal with a saved card, no redirect
        else:
            body["confirmation"] = {"type": "redirect", "return_url": return_url}
            if save_method:
                body["save_payment_method"] = True
        return await self._request("POST", "/payments", body)

    async def get_payment(self, payment_id: str) -> dict:
        return await self._request("GET", f"/payments/{payment_id}")

    async def close(self) -> None:
        if self.session:
            await self.session.close()
