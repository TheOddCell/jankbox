import sys
from PyQt5 import QtWidgets, QtCore


class chatAppGUI(QtCore.QObject):
    """Fullscreen host display: game code top-left, live chat feed with
    history below. Pass an instance of this as the `gui` argument to host()."""

    _code_set = QtCore.pyqtSignal(str)
    _message_added = QtCore.pyqtSignal(str)
    _messages_set = QtCore.pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

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
        """)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.code_label, alignment=QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        layout.addWidget(self.chat_list)
        self.window.setLayout(layout)

        # signals cross threads safely (websocket thread -> Qt main thread)
        self._code_set.connect(self.code_label.setText)
        self._message_added.connect(self._append_message)
        self._messages_set.connect(self._replace_messages)

    def set_code(self, code):
        self._code_set.emit(f"Game Code: {code}")

    def add_message(self, text):
        self._message_added.emit(text)

    def set_messages(self, texts):
        """Replaces the whole displayed history, oldest first, e.g. after
        a message is deleted."""
        self._messages_set.emit(texts)

    def _append_message(self, text):
        self.chat_list.addItem(text)
        self.chat_list.scrollToBottom()

    def _replace_messages(self, texts):
        self.chat_list.clear()
        self.chat_list.addItems(texts)
        self.chat_list.scrollToBottom()

    def run(self):
        self.window.showFullScreen()
        self.app.exec_()
