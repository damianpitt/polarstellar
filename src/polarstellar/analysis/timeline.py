"""Chronological presentation of loaded observations, without claiming fund continuity."""

from collections import Counter

from polarstellar.analysis.trace_filters import instant
from polarstellar.stellar.models import Transfer
from polarstellar.storage.export import plain


def operation_key(identifier):
    """Provide deterministic identifier order for timestamp ties, without decoding execution order.

    Compare decimal identifiers by normalized length/digits rather than int conversion;
    this also handles arbitrarily long provider strings without integer parsing limits.
    A nondecimal identifier sorts consistently after decimal identifiers. Neither order
    establishes a ledger sequence, transaction order or causal relationship.
    """
    if identifier.isascii() and identifier.isdecimal():
        normalized = identifier.lstrip("0") or "0"
        return 0, len(normalized), normalized, identifier
    return 1, 0, identifier, identifier


def timeline(trace, asset=None, view_filters=None, account=None, newest_first=False):
    """Return flat timeline rows and timing metadata for matching valid unique observations.

    Selection reuses tracing's conflict/network/type/time safeguards. Account focus
    matches either endpoint; path asset selection matches either endpoint asset.
    Shared timestamps are counted against all valid loaded observations before local
    filters, so hiding peers cannot erase known timing ambiguity. No data is fetched.
    """
    all_flows, _, _ = trace.observations()
    selected, _, _ = trace.observations(view_filters)
    peers = Counter(instant(flow.created) for flow in all_flows)
    selected = [
        flow
        for flow in selected
        if (account is None or account in (flow.sender, flow.recipient))
        and (
            asset is None or flow.asset == asset
            if isinstance(flow, Transfer)
            else flow.matches_asset(asset)
        )
    ]
    # Identifier order is stable within a tie in either display direction. Only
    # timestamp groups reverse for newest-first; arbitrary ties are never a claim
    # that two observations executed in this order.
    selected.sort(key=lambda flow: operation_key(flow.operation))
    selected.sort(key=lambda flow: instant(flow.created), reverse=newest_first)
    times = sorted({instant(flow.created) for flow in selected})
    gaps = {}
    previous = None
    for created in times:
        if previous is None:
            gaps[created] = None
        else:
            difference = created - previous
            # Integer microseconds protect the exact interval from float rounding.
            micros = (
                difference.days * 86400 + difference.seconds
            ) * 1_000_000 + difference.microseconds
            gaps[created] = f"{micros // 1_000_000}.{micros % 1_000_000:06d}".rstrip("0").rstrip(
                "."
            )
        previous = created
    selected_peers = Counter(instant(flow.created) for flow in selected)
    provenance = {}
    for address, pages in trace.pages.items():
        for index, page in enumerate(pages, 1):
            for record in page.records:
                provenance.setdefault(record.identifier, []).append(
                    {
                        "account": address,
                        "page_number": index,
                        "record_cursor": record.cursor,
                        "source": page.source,
                        "fetched_at": page.fetched_at,
                        "cache_status": page.cache_status,
                        "page_next_cursor": page.next_cursor,
                    }
                )
    rows = []
    for flow in selected:
        created = instant(flow.created)
        direct = isinstance(flow, Transfer)
        rows.append(
            plain(
                {
                    "record_kind": "timeline_operation",
                    "evidence_kind": "direct_transfer" if direct else "operation_link",
                    "created_utc": created.isoformat(),
                    "original_created": flow.created,
                    "operation": flow.operation,
                    "operation_type": flow.operation_type,
                    "transaction": flow.transaction,
                    "network": flow.network,
                    "sender": flow.sender,
                    "recipient": flow.recipient,
                    "source_asset": flow.asset if direct else flow.source_asset,
                    "source_amount": flow.amount if direct else flow.source_amount,
                    "destination_asset": flow.asset if direct else flow.destination_asset,
                    "destination_amount": flow.amount if direct else flow.destination_amount,
                    "same_timestamp_loaded_operations": peers[created],
                    "same_timestamp_selected_operations": selected_peers[created],
                    "timing": f"Shared timestamp ({peers[created]} loaded operations)"
                    if peers[created] > 1
                    else "Distinct loaded timestamp",
                    "seconds_since_earlier_selected_timestamp": gaps[created],
                    "observed_in": provenance[flow.operation],
                    "evidence": flow,
                }
            )
        )
    return rows, {
        "account_focus": account,
        "order": "Newest first" if newest_first else "Oldest first",
        "selected_unique_operations": len(rows),
        "selected_timestamp_groups": len(times),
        "selected_operations_with_shared_loaded_timestamp": sum(
            peers[instant(flow.created)] > 1 for flow in selected
        ),
        "oldest_selected_time": times[0].isoformat() if times else None,
        "newest_selected_time": times[-1].isoformat() if times else None,
        "ordering": "UTC observation time; ties use stable operation identifier display order only, not execution order",
        "ambiguity_scope": "All valid unique loaded observations before filters/account focus; hidden peers remain counted",
        "gap_scope": "Elapsed seconds since the earlier distinct selected timestamp; not proof of inactivity, missing records or continuous coverage",
        "coverage": "Loaded successful supported observations only; no proof of execution order, custody, chronological fund continuity or complete history. No running balances or cross-hop totals.",
    }
