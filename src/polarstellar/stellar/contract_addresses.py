"""Lossless contract-event address descriptions without ownership or transfer inference."""

from stellar_sdk import Address, StrKey, xdr


def address_fields(address):
    """Describe one supported ScAddress and preserve a muxed account's complete identity.

    G/C/M addresses are supported by the locked SDK. Other variants raise ValueError
    so callers can retain raw XDR instead of coercing them into account identities.
    The encoded address is round-tripped; its routing ID never passes through a float
    or replaces the full M-address as an evidence identifier. No network access occurs.
    """
    if address is None or address.type not in (
        xdr.SCAddressType.SC_ADDRESS_TYPE_ACCOUNT,
        xdr.SCAddressType.SC_ADDRESS_TYPE_CONTRACT,
        xdr.SCAddressType.SC_ADDRESS_TYPE_MUXED_ACCOUNT,
    ):
        raise ValueError("Unsupported contract address variant")
    encoded = Address.from_xdr_sc_address(address).address
    if Address(encoded).to_xdr_sc_address().to_xdr_bytes() != address.to_xdr_bytes():
        raise ValueError("Address encoding did not preserve the original XDR")
    result = {
        "value": encoded,
        "address_kind": address.type.name.removeprefix("SC_ADDRESS_TYPE_"),
    }
    if address.type == xdr.SCAddressType.SC_ADDRESS_TYPE_MUXED_ACCOUNT:
        body = address.muxed_account
        identifier = body.id.uint64
        if type(identifier) is not int or not 0 <= identifier <= 2**64 - 1:
            raise ValueError("Invalid unsigned routing ID")
        result.update(
            account=StrKey.encode_ed25519_public_key(body.ed25519.uint256),
            # The decimal string is lossless in JSON/CSV consumers, even beyond 2**53.
            # Zero is a real routing ID and must not be treated as an absent value.
            routing_id=str(identifier),
            note="Routing information only; no ownership or fund-flow inference.",
        )
    return result
