"""Bounded expansion of observed transfers, without inferring custody or fund continuity."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext

import networkx as nx

from polarstellar.analysis.trace_filters import (
    FILTER_REASONS,
    OPERATION_TYPES,
    TraceFilters,
    instant,
)
from polarstellar.stellar.activity import PAGE_SIZE, ActivityKind
from polarstellar.stellar.models import Transfer
from polarstellar.storage.export import document, plain

MAX_ACCOUNTS = 10
MAX_PAGES = 5
MAX_DEPTH = 3
MAX_PATHS = 100
CANVAS_NODES = 40


@dataclass(frozen=True)
class Connection:
    """An exact per-asset directional total with unique operations as supporting evidence."""

    sender: str
    recipient: str
    asset: str
    total: Decimal
    evidence: tuple[Transfer, ...]


class Trace:
    """Hold a bounded collection of account pages on one immutable network and root."""

    def __init__(self, root, network, pages=()):
        """Seed from at most five already-loaded root pages, preserving their provenance."""
        self.root, self.network = root, network
        self.pages = {}
        self.seed_truncated = len(pages) > MAX_PAGES
        for page in pages[:MAX_PAGES]:
            self.add_page(root, page)

    def build(self, view_filters=None):
        """Deduplicate operations across accounts and return connections, exclusions and distances.

        Conflicting observations of an operation are excluded rather than silently choosing
        one amount. Missing, failed, self, foreign-network and unsupported transfers never
        contribute to totals. Graph distance is undirected discovery distance, not flow direction.
        """
        records, conflicts = {}, set()
        for address, pages in self.pages.items():
            for page in pages:
                for record in page.records:
                    flow = record.transfer
                    if flow is not None and (
                        flow.network != self.network or address not in (flow.sender, flow.recipient)
                    ):
                        conflicts.add(record.identifier)
                    previous = records.get(record.identifier)
                    if previous is not None and previous != record:
                        conflicts.add(record.identifier)
                    records[record.identifier] = record
        excluded = Counter()
        groups = {}
        graph = nx.Graph()
        graph.add_node(self.root)
        for identifier, record in records.items():
            flow = record.transfer
            if identifier in conflicts:
                excluded["Conflicting or mismatched observation"] += 1
            elif flow is None:
                excluded[record.exclusion or "Unsupported operation"] += 1
            elif flow.sender == flow.recipient:
                excluded["Self transfer"] += 1
            elif flow.operation_type not in OPERATION_TYPES:
                excluded["Unsupported transfer operation type"] += 1
            else:
                try:
                    instant(flow.created)
                except ValueError:
                    excluded["Invalid/unknown transfer time"] += 1
                    continue
                # Expansion eligibility/depth is based on all valid loaded relationships,
                # independent of local display filters. Hiding links must not relax caps.
                graph.add_edge(flow.sender, flow.recipient)
                reason = view_filters.reason(flow) if view_filters is not None else ""
                if reason:
                    excluded[reason] += 1
                else:
                    groups.setdefault((flow.sender, flow.recipient, flow.asset), []).append(flow)
        connections = []
        for (sender, recipient, asset), evidence in sorted(groups.items()):
            with localcontext() as context:
                context.prec = 80
                total = sum((flow.amount for flow in evidence), Decimal(0))
            connections.append(Connection(sender, recipient, asset, total, tuple(evidence)))
        distances = dict(nx.single_source_shortest_path_length(graph, self.root))
        return connections, dict(excluded), distances

    def reason(self, address):
        """Explain why another page cannot be fetched, or return an empty string if allowed."""
        pages = self.pages.get(address, [])
        _, _, distances = self.build()
        if address not in distances or distances[address] >= MAX_DEPTH:
            return "Expansion stops at three hops from the root."
        if not pages and len(self.pages) >= MAX_ACCOUNTS:
            return "Limit reached: ten fetched accounts."
        if len(pages) >= MAX_PAGES:
            return "Limit reached: five pages (100 records) per account."
        if pages and pages[-1].next_cursor is None:
            return "End of available Horizon results; not proof of complete lifetime history."
        return ""

    def add_page(self, address, page):
        """Validate page identity, bounds and cursor order before mutating saved trace state."""
        if self.reason(address):
            raise ValueError(self.reason(address))
        if (page.address, page.network, page.kind) != (
            address,
            self.network,
            ActivityKind.PAYMENTS,
        ):
            raise ValueError("Trace page belongs to another account or network.")
        if len(page.records) > PAGE_SIZE:
            raise ValueError("Trace page exceeds the page size.")
        pages = self.pages.get(address, [])
        previous = int(pages[-1].next_cursor) if pages else None
        for record in page.records:
            if not record.cursor.isascii() or not record.cursor.isdecimal():
                raise ValueError("Invalid trace cursor.")
            cursor = int(record.cursor)
            if previous is not None and cursor >= previous:
                raise ValueError("Trace cursor did not advance.")
            previous = cursor
        if page.next_cursor is not None and (
            not page.records or page.next_cursor != page.records[-1].cursor
        ):
            raise ValueError("Invalid next cursor.")
        self.pages.setdefault(address, []).append(page)

    def paths(self, target, asset, incoming=False, view_filters=None):
        """Find at most 100 simple directed, same-asset paths of up to three edges.

        These are observed connections, not chronological fund tracing. A route never
        revisits an account, crosses asset identities, or sums amounts across its hops.
        """
        connections, _, _ = self.build(view_filters) if view_filters is not None else self.build()
        start, end = (target, self.root) if incoming else (self.root, target)
        adjacency = {}
        for edge in connections:
            if edge.asset == asset:
                adjacency.setdefault(edge.sender, []).append(edge.recipient)
        results = []

        def visit(path):
            """Traverse only bounded simple paths, stopping as soon as the result cap is met."""
            if len(results) >= MAX_PATHS:
                return
            if path[-1] == end and len(path) > 1:
                results.append(tuple(path))
                return
            if len(path) - 1 >= MAX_DEPTH:
                return
            for neighbor in adjacency.get(path[-1], []):
                if neighbor not in path:
                    visit([*path, neighbor])

        visit([start])
        return results

    def export(self, asset=None, target=None, incoming=False, view_filters=None):
        """Freeze all filtered edges and page coverage for exports and offline investigations."""
        connections, excluded, distances = (
            self.build(view_filters) if view_filters is not None else self.build()
        )
        filtered = {key: excluded.pop(key) for key in FILTER_REASONS if key in excluded}
        accounts = []
        for address, pages in self.pages.items():
            times, unknown_times = [], 0
            for page in pages:
                for record in page.records:
                    try:
                        # Compare instants, not strings: offsets can otherwise reverse
                        # min/max ordering. Coverage remains for all fetched records.
                        created = record.transfer.created if record.transfer else record.values[0]
                        times.append(instant(created))
                    except (ValueError, IndexError):
                        unknown_times += 1
            provenance = []
            for page in pages:
                item = plain(page)
                del item["records"]
                provenance.append(item)
            accounts.append(
                {
                    "address": address,
                    "pages": provenance,
                    "loaded_records": sum(len(page.records) for page in pages),
                    "oldest_record_time": min(times).isoformat() if times else None,
                    "newest_record_time": max(times).isoformat() if times else None,
                    "unknown_record_times": unknown_times,
                    "next_cursor": pages[-1].next_cursor,
                    "end_of_available_results": pages[-1].next_cursor is None,
                    "expansion_status": self.reason(address) or "More pages available",
                }
            )
        metadata = {
            "root": self.root,
            "network": self.network,
            "accounts": accounts,
            "unfetched_accounts": sorted(set(distances) - set(self.pages)),
            "excluded_unique_operations": excluded,
            "filtered_unique_operations": filtered,
            "trace_filters": (view_filters or TraceFilters()).metadata(),
            "matching_unique_operations_before_asset_filter": sum(
                len(edge.evidence) for edge in connections
            ),
            "expansion_scope": "Unfiltered valid loaded relationships; display filters never change fetch/depth limits",
            "asset_filter": asset,
            "target": target,
            "direction": "To root" if incoming else "From root",
            "paths": self.paths(target, asset, incoming, view_filters) if target and asset else [],
            "limits": {
                "accounts": MAX_ACCOUNTS,
                "pages_per_account": MAX_PAGES,
                "hops": MAX_DEPTH,
                "paths": MAX_PATHS,
                "canvas_nodes": CANVAS_NODES,
            },
            "seed_truncated": self.seed_truncated,
            "coverage": "Loaded successful direct payments/funding only; applied time/type filters select evidence locally. Deduplicated by "
            "operation ID. Paths are observed connections, not proof of the same funds, "
            "chronological flow, ownership, or complete history. No cross-hop totals.",
        }
        metadata["paths_may_be_truncated"] = len(metadata["paths"]) >= MAX_PATHS
        return document(
            "expanded_graph",
            metadata,
            [edge for edge in connections if asset is None or edge.asset == asset],
        )
