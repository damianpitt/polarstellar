"""Evidence-backed, one-hop totals for successful direct payments and funding."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext

import networkx as nx

from polarstellar.stellar.models import Network, Transfer


@dataclass(frozen=True)
class Relationship:
    counterparty: str
    direction: str
    asset: str
    total: Decimal
    evidence: tuple[Transfer, ...]


@dataclass(frozen=True)
class Analysis:
    relationships: tuple[Relationship, ...]
    excluded: dict[str, int]
    graph: nx.MultiDiGraph


def analyze(records, address: str, network: Network) -> Analysis:
    groups = {}
    excluded = Counter()
    seen = set()
    for record in records:
        if record.identifier in seen:
            continue
        seen.add(record.identifier)
        flow = record.transfer
        if flow is None:
            excluded[record.exclusion or "Unsupported record"] += 1
            continue
        if flow.network != network:
            excluded["Different network"] += 1
            continue
        if flow.sender == flow.recipient:
            excluded["Self transfer"] += 1
            continue
        if address == flow.sender:
            party, direction = flow.recipient, "Outgoing"
        elif address == flow.recipient:
            party, direction = flow.sender, "Incoming"
        else:
            excluded["Not a direct party"] += 1
            continue
        groups.setdefault((party, direction, flow.asset), []).append(flow)
    relationships = []
    graph = nx.MultiDiGraph(network=network.value)
    graph.add_node(address)
    for (party, direction, asset), evidence in groups.items():
        # Ledger amounts have seven decimal places; extra precision protects large local totals.
        with localcontext() as context:
            context.prec = 80
            total = sum((flow.amount for flow in evidence), Decimal(0))
        relation = Relationship(party, direction, asset, total, tuple(evidence))
        relationships.append(relation)
        source, target = (address, party) if direction == "Outgoing" else (party, address)
        graph.add_edge(source, target, key=asset, relationship=relation)
    relationships.sort(
        key=lambda item: (-len(item.evidence), item.counterparty, item.asset, item.direction)
    )
    return Analysis(tuple(relationships), dict(excluded), graph)
