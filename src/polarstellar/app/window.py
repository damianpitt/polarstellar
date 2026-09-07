"""Initial application shell. No network requests are made here."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PolarStellar — Navigate the Stellar network")
        self.resize(1180, 760)
        self.setMinimumSize(800, 520)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        header = QHBoxLayout()
        brand = QLabel("✦  POLARSTELLAR")
        brand.setStyleSheet("font-size: 22px; font-weight: 600;")
        header.addWidget(brand)
        header.addStretch()
        header.addWidget(QLabel("Network"))
        self.network = QComboBox()
        self.network.addItems(["Mainnet", "Testnet"])
        self.network.setAccessibleName("Network")
        header.addWidget(self.network)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Account, transaction hash, or contract ID — coming soon")
        self.search.setAccessibleName("Investigation search")
        self.search.setEnabled(False)
        layout.addWidget(self.search)
        content = QHBoxLayout()
        navigation = QListWidget()
        navigation.addItems(["Overview", "Activity", "Assets", "Operations", "Graph", "Contracts"])
        navigation.setFixedWidth(180)
        navigation.setCurrentRow(0)
        content.addWidget(navigation)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.message, 1)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Pre-alpha skeleton • Mainnet selected • Not connected")
        navigation.currentTextChanged.connect(self.show_section)
        self.network.currentTextChanged.connect(self.show_network)
        self.show_section("Overview")
        self.setStyleSheet("""
            QWidget { background: #171b24; color: #e5e9f2; font-size: 14px; }
            QLineEdit, QComboBox, QListWidget {
                background: #202634; border: 1px solid #343e52;
                border-radius: 5px; padding: 10px;
            }
            QListWidget::item { padding: 12px 6px; }
            QListWidget::item:selected { background: #34446a; }
            QStatusBar { color: #a7b3cc; }
        """)

    def show_section(self, section: str) -> None:
        self.message.setText(
            f"{section}\n\nNavigate the Stellar network.\n\n"
            "This workspace is the starting point for PolarStellar.\n"
            "Live account inspection, transaction decoding, and tracing are planned."
        )

    def show_network(self, network: str) -> None:
        self.statusBar().showMessage(
            f"Pre-alpha skeleton • {network} selected • Not connected"
        )
