import asyncio
from decimal import Decimal

import httpx
import pytest
from stellar_sdk import StrKey

from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService, validate_account

ADDRESS = StrKey.encode_ed25519_public_key(bytes(32))
ISSUER = StrKey.encode_ed25519_public_key(bytes([1]) * 32)


def account_data():
    return {
        "account_id": ADDRESS,
        "sequence": "123",
        "home_domain": "example.org",
        "balances": [
            {"asset_type": "native", "balance": "123456789.1234567"},
            {
                "asset_type": "credit_alphanum4",
                "asset_code": "USD",
                "asset_issuer": ISSUER,
                "balance": "0.0000001",
                "limit": "1000.0000000",
                "is_authorized": False,
            },
        ],
    }


def test_checksum_validation_precedes_network():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json=account_data())

    service = AccountService(HorizonProvider(httpx.MockTransport(handler)))
    assert validate_account(f"  {ADDRESS}\n") == ADDRESS
    for value in ["", "G" * 56, ADDRESS[:-1] + "A", "https://example.com", "C" * 56]:
        with pytest.raises(AccountError):
            asyncio.run(service.lookup(value, Network.MAINNET))
    assert calls == []


def test_exact_balances_and_network_routing():
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        assert request.url.path == f"/accounts/{ADDRESS}"
        return httpx.Response(200, json=account_data())

    service = AccountService(HorizonProvider(httpx.MockTransport(handler)))
    for network in Network:
        result = asyncio.run(service.lookup(ADDRESS, network))
        assert result.network == network
        assert result.balances[0].amount == Decimal("123456789.1234567")
        assert result.balances[1].identity == ISSUER
        assert result.balances[1].amount == Decimal("0.0000001")
        assert result.balances[1].authorized is False
        assert result.balances[1].limit == Decimal(1000)
    assert hosts == ["horizon.stellar.org", "horizon-testnet.stellar.org"]


@pytest.mark.parametrize(
    "status,message", [(404, "not found on Testnet"), (429, "rate limiting"), (503, "unavailable")]
)
def test_http_errors(status, message):
    provider = HorizonProvider(httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(AccountError, match=message):
        asyncio.run(provider.get_account(ADDRESS, Network.TESTNET))


@pytest.mark.parametrize(
    "error,message",
    [(httpx.ReadTimeout, "timed out"), (httpx.ConnectError, "Check your connection")],
)
def test_connection_errors(error, message):
    def handler(request):
        raise error("private error detail", request=request)

    provider = HorizonProvider(httpx.MockTransport(handler))
    with pytest.raises(AccountError, match=message):
        asyncio.run(provider.get_account(ADDRESS, Network.MAINNET))


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"account_id": ADDRESS, "balances": None},
        {**account_data(), "account_id": ISSUER},
        {**account_data(), "balances": [{"asset_type": "native", "balance": "NaN"}]},
    ],
)
def test_malformed_data(data):
    provider = HorizonProvider(httpx.MockTransport(lambda r: httpx.Response(200, json=data)))
    with pytest.raises(AccountError, match="incomplete or unsupported"):
        asyncio.run(provider.get_account(ADDRESS, Network.MAINNET))
