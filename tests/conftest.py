# -*- coding: utf-8 -*-
"""pytest 全局夹具。

无人值守环境（CI/自动化）下，模态 QMessageBox 弹窗会永久阻塞或直接崩溃
（Fatal: Aborted）。settings_dialog._save() 在写开机自启失败时会弹模态
QMessageBox.warning，导致所有调用 _save() 的测试在 CI 上卡死（曾用
pytest-timeout dump 堆栈定位到该根因）。这里用 autouse fixture 全局把
QMessageBox 的静态弹窗方法替换为 no-op——任何测试都不会因模态弹窗卡死。
需要断言弹窗行为的测试可自行 monkeypatch.setattr 覆盖。
"""

import pytest


@pytest.fixture(autouse=True)
def _no_modal_message_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    for method in ("warning", "information", "critical", "question", "about"):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: None))
