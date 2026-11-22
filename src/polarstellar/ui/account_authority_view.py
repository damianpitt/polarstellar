"""Read-only presentation of reported account authority and reserve sponsorship."""

from PySide6.QtWidgets import QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from polarstellar.stellar.account_authority import SIGNER_VALIDATORS


def reported(value):
    """Display absent data distinctly from an explicitly reported zero or false."""
    if value is None:
        return "Not reported"
    if type(value) is bool:
        return "Yes" if value else "No"
    return str(value)


class AccountAuthorityView(QWidget):
    """Keep public configuration readable with the account's original network/source evidence."""

    def __init__(self):
        """Build separate readable/raw views without adding any network actions."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        tabs.addTab(self.details, "Details")
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setMinimumHeight(140)
        tabs.addTab(self.raw, "Raw reported fields")
        layout.addWidget(tabs)
        self.set_account(None)

    def set_account(self, account):
        """Replace both views together after a successful lookup, or clear them on invalidation."""
        if account is None:
            self.details.setPlainText("Inspect an account to view its reported configuration.")
            self.raw.clear()
            return
        source = (
            f"{account.address}\n{account.network.value}\n"
            f"Source  {account.source}\n"
            f"Retrieved  {account.fetched_at.isoformat()}\n{account.cache_status}"
        )
        authority = account.authority
        if authority is None:
            self.details.setPlainText(
                source + "\n\nAuthority fields are unavailable in this observation. "
                "Inspect again with Use local cache off for available live configuration."
            )
            self.raw.setPlainText(source + "\n\nNo authority fields were retained.")
            return
        lines = [
            source,
            "",
            "Threshold weights",
            f"Low  {reported(authority.low_threshold)}",
            f"Medium  {reported(authority.med_threshold)}",
            f"High  {reported(authority.high_threshold)}",
        ]
        master = next(
            (
                signer.weight
                for signer in authority.signers or ()
                if signer.kind == "ed25519_public_key" and signer.key == account.address
            ),
            None,
        )
        lines += ["", f"Master key weight  {reported(master)}", "", "Account issuer flags"]
        lines += [
            f"{label}  {reported(getattr(authority, name))}"
            for name, label in (
                ("auth_required", "Authorization required"),
                ("auth_revocable", "Authorization revocable"),
                ("auth_immutable", "Issuer flags immutable"),
                ("auth_clawback_enabled", "Clawback enabled"),
            )
        ]
        lines += [
            "",
            "Reserve sponsorship and ledger context",
            f"Account reserve sponsor  {reported(authority.sponsor)}",
            f"Reserves sponsored by this account  {reported(authority.num_sponsoring)}",
            f"Reserves sponsored for this account  {reported(authority.num_sponsored)}",
            f"Subentry count  {reported(authority.subentry_count)}",
            f"Account last modified ledger  {reported(authority.last_modified_ledger)}",
            "",
            "Signers",
        ]
        if authority.signers is None:
            lines.append("Signer selection not reported or unavailable.")
        elif not authority.signers:
            lines.append("The source returned an empty signer selection.")
        for index, signer in enumerate(authority.signers or (), 1):
            kind = signer.kind.replace("_", " ")
            validation = (
                "Encoding checked" if signer.kind in SIGNER_VALIDATORS else "Unrecognized type"
            )
            lines += [
                "",
                f"{index}. {kind} • weight {signer.weight} • {validation}",
                signer.key,
                f"Signer reserve sponsor  {reported(signer.sponsor)}",
            ]
        if authority.warnings:
            lines += ["", "Unavailable or unrecognized fields", *authority.warnings]
        lines += [
            "",
            (
                "Public configuration observed at retrieval time. Effective authorization depends on "
                "the transaction, operation requirements and signer type."
            ),
            (
                "Reserve sponsorship describes reserve payment responsibility. Key ownership and "
                "transaction approval are outside this inspection."
            ),
            "Balance/trustline reserve sponsors are shown in Balances.",
        ]
        self.details.setPlainText("\n".join(lines))
        self.raw.setPlainText(source + "\n\n" + (authority.raw or "Raw fields were not retained."))
