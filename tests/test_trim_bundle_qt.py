# -*- coding: utf-8 -*-
"""trim_bundle_qt 裁剪白名单回归测试。

根因：Qt 6.8+ 的 ffmpeg 多媒体后端插件（plugins/multimedia/
libffmpegmediaplugin.so）动态链接 Qt6Qml/Qt6Quick 家族中的 5 个库
（QRhi 视频渲染路径所需）。早期裁剪把它们当纯死重删除后，Linux 打包版
插件 dlopen 失败（"No QtMultimedia backends found"），QMediaPlayer /
QAudioDecoder 全部不可用——压缩音效（如 duck 音效包的 mp3）静默失声。
WAV 音效走 QSoundEffect 不依赖后端插件，所以表面上"没明显 bug"。

约束：豁免必须按精确库名，同名 Python 扩展（QtQml.abi3.so / QtQml.pyd）
与 QML 资源目录仍应删除。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from trim_bundle_qt import find_layout, trim_pyside6  # noqa: E402

# ffmpeg 后端插件（Qt 6.11.2 Linux 实测 ldd 依赖闭包）要求的 Qt 库
_FFMPEG_BACKEND_DEPS = (
    "libQt6Qml.so.6",
    "libQt6QmlMeta.so.6",
    "libQt6QmlModels.so.6",
    "libQt6QmlWorkerScript.so.6",
    "libQt6Quick.so.6",
)

# 仍应删除的 QML/Quick 家族成员
_STILL_REMOVED = (
    "libQt6QuickWidgets.so.6",
    "libQt6QmlCompiler.so.6",
    "libQt6Pdf.so.6",
    "libQt6VirtualKeyboard.so.6",
)


def _make_bundle(tmp_path: Path) -> Path:
    """构造最小 PyInstaller onedir 布局（_internal/PySide6/Qt/...）。"""
    lib_dir = tmp_path / "_internal" / "PySide6" / "Qt" / "lib"
    lib_dir.mkdir(parents=True)
    for name in (*_FFMPEG_BACKEND_DEPS, *_STILL_REMOVED, "libQt6Core.so.6"):
        (lib_dir / name).write_bytes(b"\0" * 1024)
    # Python 扩展（无 "6" 的 QtQml.abi3.so / QtQml.pyd 风格）：仍应删除
    pyside6 = tmp_path / "_internal" / "PySide6"
    (pyside6 / "QtQml.abi3.so").write_bytes(b"\0" * 1024)
    (pyside6 / "QtQuick.abi3.so").write_bytes(b"\0" * 1024)
    # ffmpeg 后端插件本体：不是裁剪目标，必须保留
    plugin_dir = pyside6 / "Qt" / "plugins" / "multimedia"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "libffmpegmediaplugin.so").write_bytes(b"\0" * 1024)
    # QML 资源目录：仍应整体删除
    qml_dir = pyside6 / "Qt" / "qml" / "QtQuick"
    qml_dir.mkdir(parents=True)
    (qml_dir / "Button.qml").write_text("import QtQuick")
    return tmp_path


def test_ffmpeg_backend_deps_survive_trimming(tmp_path):
    bundle = _make_bundle(tmp_path)
    pyside6_dir, lib_root = find_layout(bundle)
    trim_pyside6(pyside6_dir, lib_root)

    lib_dir = pyside6_dir / "Qt" / "lib"
    for name in _FFMPEG_BACKEND_DEPS:
        assert (lib_dir / name).is_file(), f"ffmpeg 后端依赖被误删: {name}"
    assert (lib_dir / "libQt6Core.so.6").is_file()
    assert (pyside6_dir / "Qt" / "plugins" / "multimedia" / "libffmpegmediaplugin.so").is_file()


def test_genuine_dead_weight_still_removed(tmp_path):
    bundle = _make_bundle(tmp_path)
    pyside6_dir, lib_root = find_layout(bundle)
    trim_pyside6(pyside6_dir, lib_root)

    lib_dir = pyside6_dir / "Qt" / "lib"
    for name in _STILL_REMOVED:
        assert not (lib_dir / name).exists(), f"死重未被删除: {name}"
    # 同名 Python 扩展与 QML 资源目录不属于后端依赖，必须照删
    assert not (pyside6_dir / "QtQml.abi3.so").exists()
    assert not (pyside6_dir / "QtQuick.abi3.so").exists()
    assert not (pyside6_dir / "Qt" / "qml").exists()
