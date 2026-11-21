"""Compare saved expanded graphs and timelines while retaining their separate selection and coverage."""

from decimal import Decimal, localcontext

from polarstellar.analysis.trace_filters import DIRECT_TYPES, instant
from polarstellar.stellar.activity import ledger_amount
from polarstellar.stellar.assets import validate_asset
from polarstellar.stellar.models import Network, OperationLink
from polarstellar.stellar.service import validate_account
from polarstellar.stellar.transaction import validate_hash
from polarstellar.storage.transaction_comparison import MAX_PROJECTION_BYTES, canonical
from polarstellar.storage.watchlist_comparison import MAX_FIELDS, equal, field_changes

COVERAGE = (
    "Compared selected saved relationships and operation observations only. Newly reported or "
    "unreported evidence may reflect filters, expansion, or pagination. Each side retains its "
    "own selection and source coverage. Unreported evidence is unknown. Totals describe loaded "
    "direct transfers per asset, without cross-hop sums, running balances, ownership, fund "
    "continuity or complete-history claims. Timeline ordering and gap annotations stay in scope."
)
FLOW_FIELDS = (
    "network",
    "sender",
    "recipient",
    "asset",
    "amount",
    "source_asset",
    "source_amount",
    "destination_asset",
    "destination_amount",
    "operation",
    "transaction",
    "created",
    "operation_type",
    "path",
    "limit_name",
    "limit_amount",
)
AMOUNTS = {"amount", "total", "source_amount", "destination_amount", "limit_amount"}


def asset_identity(value):
    """Validate exact classic asset identity, including an issuer for any issued XLM."""
    if value == "XLM":
        return
    code, separator, issuer = value.partition(":")
    if not separator or not issuer or validate_asset(code, issuer) != (code, issuer):
        raise ValueError("Invalid saved asset identity.")


def equivalent(key, before, after):
    """Compare bounded evidence objects with exact decimal/time equality and stable array order."""
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(
            equivalent((name,), value, after[name]) for name, value in before.items()
        )
    if key[-1] in AMOUNTS and before is not None:
        return equal(("balances", "amount"), before, after)
    if key[-1] == "created":
        return instant(before) == instant(after)
    return before == after


def checked_flow(value, network, direct):
    """Validate observation identity and monetary semantics without reconstructing absent history."""
    if value["network"] != network:
        raise ValueError("Observation belongs to a different network.")
    for name in ("sender", "recipient"):
        if validate_account(value[name]) != value[name]:
            raise ValueError("Invalid saved endpoint.")
    if value["sender"] == value["recipient"]:
        raise ValueError("Unsupported self-transfer observation.")
    identifier = value["operation"]
    if (
        not isinstance(identifier, str)
        or not identifier.isascii()
        or not identifier.isdecimal()
        or len(identifier) > 64
    ):
        raise ValueError("Invalid saved operation identity.")
    validate_hash(value["transaction"])
    instant(value["created"])
    if direct:
        asset_identity(value["asset"])
        ledger_amount(value, "amount")
        # Early graph snapshots may omit operation_type. Keep it absent/unknown
        # instead of guessing whether the original instruction was payment/funding.
        if value.get("operation_type", "") not in (*DIRECT_TYPES, ""):
            raise ValueError("Unsupported direct-transfer type.")
    else:
        restored = {name: value[name] for name in FLOW_FIELDS if name in value}
        restored["network"] = Network(network)
        restored["path"] = tuple(value["path"])
        for name in ("source_amount", "destination_amount", "limit_amount"):
            if value[name] is not None:
                restored[name] = ledger_amount(value, name, allow_zero=name == "limit_amount")
        # The existing model rejects fabricated merge amounts and path limits that
        # conflict with the reported sent/received amounts.
        OperationLink(**restored)
    return {
        "evidence_kind": "direct_transfer" if direct else "operation_link",
        **{name: value[name] for name in FLOW_FIELDS if name in value},
    }


def project(snapshot, root, network):
    """Project bounded operation/relationship records while preserving all source scope separately."""
    metadata, records = snapshot["metadata"], snapshot["records"]
    if (metadata["root"], metadata["network"]) != (root, network):
        raise ValueError("Graph/timeline root or network disagrees with the saved entry.")
    accounts = checked_accounts(metadata["accounts"], network)
    if root not in accounts:
        raise ValueError("Saved root has no account coverage.")
    if len(records) > 1000:
        raise ValueError("Comparison exceeds 1000 saved graph/timeline rows.")
    fields, timeline_context = {}, []
    operations = set()
    size = 0

    def put(key, value):
        """Refuse excess fields or bytes before exposing a partial result."""
        nonlocal size
        if key in fields:
            raise ValueError("Duplicate relationship or operation identity.")
        size += len(canonical(value).encode("utf-8"))
        if len(fields) >= MAX_FIELDS or size > MAX_PROJECTION_BYTES:
            raise ValueError("Comparison exceeds 2000 records or 4 MB of projected values.")
        fields[key] = value

    def observation(value, direct):
        """Keep each selected operation once, with its complete transaction and asset identity."""
        flow = checked_flow(value, network, direct)
        identifier = flow["operation"]
        if identifier in operations:
            raise ValueError("Duplicate operation evidence.")
        operations.add(identifier)
        put(("operations", identifier), flow)
        return flow

    for row in records:
        kind = row.get("record_kind")
        if snapshot["kind"] == "trace_timeline":
            if kind != "timeline_operation" or row["evidence_kind"] not in (
                "direct_transfer",
                "operation_link",
            ):
                raise ValueError("Unsupported timeline record.")
            flow = observation(row["evidence"], row["evidence_kind"] == "direct_transfer")
            validate_timeline_row(row, flow)
            for source in row["observed_in"]:
                pages = accounts[source["account"]]
                number = source["page_number"]
                if type(number) is not int or not 1 <= number <= len(pages):
                    raise ValueError("Invalid timeline source page.")
                page = pages[number - 1]
                if (
                    source["account"] not in (flow["sender"], flow["recipient"])
                    or any(
                        source[name] != page[name]
                        for name in ("source", "fetched_at", "cache_status")
                    )
                    or source["page_next_cursor"] != page["next_cursor"]
                ):
                    raise ValueError("Timeline provenance disagrees with saved account coverage.")
            timeline_context.append(
                {
                    key: row[key]
                    for key in (
                        "operation",
                        "observed_in",
                        "original_created",
                        "created_utc",
                        "same_timestamp_loaded_operations",
                        "same_timestamp_selected_operations",
                        "seconds_since_earlier_selected_timestamp",
                        "timing",
                    )
                    if key in row
                }
            )
        elif kind == "operation_link":
            observation(row, False)
        elif kind == "direct_transfer_total" or (kind is None and "total" in row):
            sender, recipient, asset = row["sender"], row["recipient"], row["asset"]
            if not isinstance(row["evidence"], list) or not row["evidence"]:
                raise ValueError("Relationship has no supporting observations.")
            amounts, identifiers = [], []
            for value in row["evidence"]:
                flow = observation(value, True)
                if (flow["sender"], flow["recipient"], flow["asset"]) != (sender, recipient, asset):
                    raise ValueError("Relationship evidence has incompatible endpoints or asset.")
                amounts.append(Decimal(flow["amount"]))
                identifiers.append(flow["operation"])
            # Match tracing's precision when verifying totals. This never computes
            # a change amount or sums between different assets or hops.
            with localcontext() as context:
                context.prec = 80
                total = format(sum(amounts, Decimal(0)), "f")
            if not isinstance(row["total"], str) or len(row["total"]) > 128:
                raise ValueError("Invalid saved total.")
            if not equal(("balances", "amount"), row["total"], total):
                raise ValueError("Saved relationship total disagrees with its evidence.")
            put(
                ("relationships", sender, recipient, asset),
                {"total": row["total"], "supporting_operations": sorted(identifiers)},
            )
        else:
            raise ValueError("Unsupported graph record.")

    scope = {
        "document_created_at": snapshot.get("exported_at"),
        "software_version": snapshot.get("software_version"),
        "saved_row_count": len(records),
        "compared_operation_count": len(operations),
        "selection": {
            key: metadata.get(key)
            for key in (
                "asset_filter",
                "trace_filters",
                "target",
                "direction",
                "paths",
                "paths_may_be_truncated",
                "timeline",
            )
        },
        "coverage": {
            key: metadata.get(key)
            for key in (
                "coverage",
                "limits",
                "seed_truncated",
                "unfetched_accounts",
                "excluded_unique_operations",
                "filtered_unique_operations",
                "matching_unique_operations_before_asset_filter",
                "separate_operation_links",
                "routes_scope",
                "operation_link_semantics",
                "expansion_scope",
            )
        },
        "accounts": metadata.get("accounts"),
    }
    if snapshot["kind"] == "trace_timeline":
        scope["timeline_observations"] = timeline_context
    if len(canonical(scope).encode("utf-8")) > MAX_PROJECTION_BYTES:
        raise ValueError("Comparison scope exceeds 4 MB.")
    return fields, scope


def checked_accounts(accounts, network):
    """Validate each saved account/page context before presenting its provenance in a report."""
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 10:
        raise ValueError("Unsupported saved account coverage.")
    result = {}
    for account in accounts:
        address = account["address"]
        if validate_account(address) != address or address in result:
            raise ValueError("Ambiguous saved account coverage.")
        pages = account["pages"]
        if not isinstance(pages, list) or not 1 <= len(pages) <= 5:
            raise ValueError("Unsupported saved page coverage.")
        for page in pages:
            if (page["address"], page["network"], page["kind"]) != (address, network, "payments"):
                raise ValueError("Page belongs to a different account, network, or activity type.")
        result[address] = pages
    return result


def validate_timeline_row(row, flow):
    """Check displayed identity/amounts against supporting evidence before comparing the observation."""
    direct = flow["evidence_kind"] == "direct_transfer"
    expected = {
        **{
            name: flow[name]
            for name in ("operation", "transaction", "network", "sender", "recipient")
        },
        "source_asset": flow["asset"] if direct else flow["source_asset"],
        "destination_asset": flow["asset"] if direct else flow["destination_asset"],
        "source_amount": flow["amount"] if direct else flow["source_amount"],
        "destination_amount": flow["amount"] if direct else flow["destination_amount"],
    }
    for name, value in expected.items():
        if not equivalent((name,), row[name], value):
            raise ValueError("Timeline fields disagree with supporting evidence.")
    if (
        row.get("operation_type", "") != flow.get("operation_type", "")
        or instant(row["created_utc"]) != instant(flow["created"])
        or instant(row["original_created"]) != instant(flow["created"])
    ):
        raise ValueError("Timeline type/time disagrees with supporting evidence.")
    if not isinstance(row["observed_in"], list) or not row["observed_in"]:
        raise ValueError("Timeline observation has no source provenance.")


def compare_traces(baseline, comparison, entry):
    """Compare like-kind saved observations for one root/network and retain each side's coverage."""
    root, network = validate_account(entry["identifier"]), Network(entry["network"]).value
    if root != entry["identifier"]:
        raise ValueError("Invalid saved root identity.")
    before, baseline_scope = project(baseline, root, network)
    after, comparison_scope = project(comparison, root, network)
    changes = field_changes(before, after, equivalent)
    return {
        "identity": {"kind": entry["kind"], "identifier": root, "network": network},
        "status": "Evidence differs" if changes else "No differences in compared evidence",
        "note": "Compare saved selection and coverage for both observations.",
        "coverage": COVERAGE,
        "baseline_scope": baseline_scope,
        "comparison_scope": comparison_scope,
        "baseline_provenance": {"accounts": baseline_scope.pop("accounts")},
        "comparison_provenance": {"accounts": comparison_scope.pop("accounts")},
        "changes": changes,
    }
