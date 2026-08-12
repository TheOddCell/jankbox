import sys
from PyQt5 import QtWidgets, QtCore


class chatAppGUI(QtCore.QObject):
    """Fullscreen host display: game code top-left, live chat feed with
    history below (click a message to delete it), plus admin controls at
    the bottom. Pass an instance of this as the `gui` argument to host()."""

    _code_set = QtCore.pyqtSignal(str)
    _messages_set = QtCore.pyqtSignal(list)  # list of (id, text)

    def __init__(self):
        super().__init__()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

        # wired up by chatApp once it has a host/wsapp to act on
        self.on_delete_requested = None        # callable(entry_id)
        self.on_admin_grant_requested = None   # callable(username)
        self.on_admin_revoke_requested = None  # callable(username)

        self.window = QtWidgets.QWidget()
        self.window.setWindowTitle("Jackbox Host")
        self.window.setStyleSheet("background-color: #101010;")

        self.code_label = QtWidgets.QLabel("Game Code: ----")
        self.code_label.setStyleSheet("color: white; font-size: 48px; font-weight: bold; padding: 20px;")
        self.code_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        self.chat_list = QtWidgets.QListWidget()
        self.chat_list.setStyleSheet("""
            QListWidget { background-color: #181818; color: white; font-size: 22px; border: none; }
            QListWidget::item { padding: 8px; }
            QListWidget::item:hover { background-color: #282828; }
        """)
        self.chat_list.itemClicked.connect(self._on_item_clicked)

        self.admin_input = QtWidgets.QLineEdit()
        self.admin_input.setPlaceholderText("username")
        self.admin_input.setStyleSheet("background-color: #181818; color: white; font-size: 16px; padding: 8px;")

        self.grant_button = QtWidgets.QPushButton("Make Admin")
        self.revoke_button = QtWidgets.QPushButton("Remove Admin")
        for button in (self.grant_button, self.revoke_button):
            button.setStyleSheet("background-color: #282828; color: white; font-size: 16px; padding: 8px;")
        self.grant_button.clicked.connect(self._on_grant_clicked)
        self.revoke_button.clicked.connect(self._on_revoke_clicked)

        admin_row = QtWidgets.QHBoxLayout()
        admin_row.addWidget(self.admin_input)
        admin_row.addWidget(self.grant_button)
        admin_row.addWidget(self.revoke_button)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.code_label, alignment=QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        layout.addWidget(self.chat_list)
        layout.addLayout(admin_row)
        self.window.setLayout(layout)

        # signals cross threads safely (websocket thread -> Qt main thread)
        self._code_set.connect(self.code_label.setText)
        self._messages_set.connect(self._replace_messages)

    def set_code(self, code):
        self._code_set.emit(f"Game Code: {code}")

    def set_messages(self, entries):
        """entries: list of (id, text) tuples, most recent first."""
        self._messages_set.emit(entries)

    def _replace_messages(self, entries):
        self.chat_list.clear()
        for entry_id, text in entries:
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, entry_id)
            self.chat_list.addItem(item)

    def _on_item_clicked(self, item):
        if self.on_delete_requested:
            self.on_delete_requested(item.data(QtCore.Qt.UserRole))

    def _on_grant_clicked(self):
        username = self.admin_input.text().strip()
        if username and self.on_admin_grant_requested:
            self.on_admin_grant_requested(username)

    def _on_revoke_clicked(self):
        username = self.admin_input.text().strip()
        if username and self.on_admin_revoke_requested:
            self.on_admin_revoke_requested(username)

    def run(self):
        self.window.showFullScreen()
        self.app.exec_()
