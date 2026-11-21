# -*- coding: utf-8 -*-
"""Impor 编辑内核 3.0 预览（PyQt6 / QPlainTextEdit）。

这是一个独立预览窗口，不替换现有 Tk 编辑器。目标是把“内核手感”先做出来：
行号、当前行、Impor 语法高亮、minimap、基础多光标 / 列编辑、平滑滚动、
Qt 原生 IME，以及可直接打开大文件。

启动：
    python -m impor.kernel3 [文件路径]
自检：
    python -m impor.kernel3 --smoke
"""
import os
import re
import sys

try:
    from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QPropertyAnimation, QEasingCurve, QTimer
    from PyQt6.QtGui import (QAction, QColor, QFont, QKeyEvent, QPainter, QPalette,
                             QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                             QTextFormat)
    from PyQt6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMessageBox,
                                 QPlainTextEdit, QTabWidget, QTextEdit, QWidget)
    _QT_OK = True
except Exception:  # pragma: no cover - 无 PyQt6 时给出可读错误
    _QT_OK = False


if not _QT_OK:  # pragma: no cover
    def main(argv=None):
        print("内核 3.0 预览需要 PyQt6：python -m pip install PyQt6")
        return 2
else:
    BG = QColor("#11161d")
    BG_ALT = QColor("#161d26")
    FG = QColor("#d7e0ea")
    MUTED = QColor("#76839a")
    ACCENT = QColor("#4da3ff")
    CURRENT_LINE = QColor("#1d2a3a")
    NUMBER_BG = QColor("#0d1218")
    NUMBER_FG = QColor("#5d6b80")

    class LineNumberArea(QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor

        def sizeHint(self):
            return QSize(self.editor.line_number_area_width(), 0)

        def paintEvent(self, event):
            self.editor.line_number_area_paint_event(event)

    class Minimap(QWidget):
        """右侧缩略图：按块采样绘制文字轮廓与当前视口。"""

        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor
            self._cache = None
            self._pending = False
            self.setFixedWidth(88)
            editor.textChanged.connect(self._schedule)
            editor.verticalScrollBar().valueChanged.connect(self._schedule)

        def _schedule(self, *_):
            if self._pending:
                return
            self._pending = True
            QTimer.singleShot(0, self._refresh)

        def _refresh(self):
            self._pending = False
            self._rebuild()
            self.update()

        def _rebuild(self):
            w, h = self.width(), self.height()
            if w <= 0 or h <= 0:
                return
            pm = QPixmap(w, h)
            pm.fill(QColor("#0b1015"))
            p = QPainter(pm)
            try:
                doc = self.editor.document()
                total = max(1, doc.blockCount())
                max_lines = 1400
                step = max(1, total // max_lines)
                p.setFont(QFont("Consolas", 2))
                p.setPen(QColor("#53627a"))
                for idx in range(0, total, step):
                    block = doc.findBlockByNumber(idx)
                    if not block.isValid():
                        break
                    text = block.text()[:110]
                    if not text.strip():
                        continue
                    y = int(idx * h / total)
                    p.drawText(2, y, w - 4, max(2, h // max_lines + 2),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               text)
            finally:
                p.end()
            self._cache = pm

        def paintEvent(self, event):
            p = QPainter(self)
            if self._cache is not None and self._cache.size() == self.size():
                p.drawPixmap(0, 0, self._cache)
            else:
                p.fillRect(self.rect(), QColor("#0b1015"))
            p.end()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self._schedule()

    class ImporHighlighter(QSyntaxHighlighter):
        """轻量语法高亮（不依赖 Tk 的 tag 系统）。"""

        def __init__(self, document):
            super().__init__(document)
            from .lexer import KEYWORDS
            self.keyword_fmt = QTextCharFormat()
            self.keyword_fmt.setForeground(QColor("#ffb86c"))
            self.keyword_fmt.setFontWeight(QFont.Weight.Bold)
            self.string_fmt = QTextCharFormat()
            self.string_fmt.setForeground(QColor("#a5d6a7"))
            self.comment_fmt = QTextCharFormat()
            self.comment_fmt.setForeground(QColor("#6b7d8f"))
            self.number_fmt = QTextCharFormat()
            self.number_fmt.setForeground(QColor("#82aaff"))
            kw = set(KEYWORDS) | {"match", "defer", "let", "async", "await", "this",
                                  "static", "readonly", "fn", "repeat"}
            self.keyword_re = re.compile(r"\b(" + "|".join(sorted(kw, key=len, reverse=True)) + r")\b")
            self.string_re = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
            self.comment_re = re.compile(r"//.*")
            self.number_re = re.compile(r"\b\d+(?:\.\d+)?\b")

        def highlightBlock(self, text):
            for rx, fmt in ((self.number_re, self.number_fmt),
                            (self.keyword_re, self.keyword_fmt)):
                for m in rx.finditer(text):
                    self.setFormat(m.start(), m.end() - m.start(), fmt)
            string_spans = []
            for rx, fmt in ((self.string_re, self.string_fmt),
                            (self.comment_re, self.comment_fmt)):
                for m in rx.finditer(text):
                    if rx is self.comment_re and any(
                            s <= m.start() < e for (s, e) in string_spans):
                        continue
                    self.setFormat(m.start(), m.end() - m.start(), fmt)
                    if rx is self.string_re:
                        string_spans.append((m.start(), m.end()))

    class CodeEditor(QPlainTextEdit):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.path = ""
            self._extra_cursors = []
            self._current_line = None
            self._anim = None
            self._highlighter = ImporHighlighter(self.document())

            self.setFont(QFont("Consolas", 11))
            self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.setPalette(self._dark_palette())
            self.setStyleSheet(
                "QPlainTextEdit{background:#11161d;color:#d7e0ea;"
                "selection-background-color:#28466a;border:0;padding:2px;}"
            )

            self.line_number_area = LineNumberArea(self)
            self.minimap = Minimap(self)
            self.blockCountChanged.connect(self.update_line_number_area_width)
            self.updateRequest.connect(self.update_line_number_area)
            self.cursorPositionChanged.connect(self.highlight_current_line)
            self.update_line_number_area_width(0)
            self.highlight_current_line()

        @staticmethod
        def _dark_palette():
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Base, BG)
            pal.setColor(QPalette.ColorRole.Text, FG)
            pal.setColor(QPalette.ColorRole.Window, BG)
            pal.setColor(QPalette.ColorRole.WindowText, FG)
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#28466a"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            return pal

        # ---------- 行号 / minimap ----------
        def line_number_area_width(self):
            digits = max(3, len(str(max(1, self.blockCount()))))
            return 14 + self.fontMetrics().horizontalAdvance("9") * digits

        def update_line_number_area_width(self, _=0):
            right = self.minimap.width() if self.minimap.isVisible() else 0
            self.setViewportMargins(self.line_number_area_width(), 0, right, 0)

        def update_line_number_area(self, rect, dy):
            if dy:
                self.line_number_area.scroll(0, dy)
            else:
                self.line_number_area.update(0, rect.y(),
                                             self.line_number_area.width(), rect.height())
            if rect.contains(self.viewport().rect()):
                self.update_line_number_area_width(0)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            cr = self.contentsRect()
            self.line_number_area.setGeometry(
                QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))
            sbw = 0 if self.verticalScrollBar().isHidden() else self.verticalScrollBar().width()
            self.minimap.setGeometry(cr.right() + 1 - sbw - self.minimap.width(), 0,
                                     self.minimap.width(), cr.height())
            self.update_line_number_area_width(0)

        def line_number_area_paint_event(self, event):
            p = QPainter(self.line_number_area)
            p.fillRect(event.rect(), NUMBER_BG)
            block = self.firstVisibleBlock()
            number = block.blockNumber()
            top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + int(self.blockBoundingRect(block).height())
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    p.setPen(NUMBER_FG)
                    p.setFont(self.font())
                    p.drawText(0, top, self.line_number_area.width() - 5,
                               self.fontMetrics().height(),
                               Qt.AlignmentFlag.AlignRight, str(number + 1))
                block = block.next()
                top = bottom
                bottom = top + int(self.blockBoundingRect(block).height())
                number += 1
            p.end()

        def highlight_current_line(self):
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(CURRENT_LINE)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            self._current_line = sel
            self.setExtraSelections([sel])

        def paintEvent(self, event):
            super().paintEvent(event)
            if not self._extra_cursors:
                return
            p = QPainter(self.viewport())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ACCENT)
            for cursor in self._extra_cursors:
                r = self.cursorRect(cursor)
                p.drawRect(QRect(r.left(), r.top(), 2, r.height()))
            p.end()

        # ---------- 多光标 / 列编辑 ----------
        def add_cursor_vertical(self, direction):
            doc = self.document()
            base = self._extra_cursors[-1] if self._extra_cursors else self.textCursor()
            block = doc.findBlock(base.position())
            col = base.position() - block.position()
            target = block.blockNumber() + direction
            if target < 0 or target >= doc.blockCount():
                return
            tb = doc.findBlockByNumber(target)
            if not tb.isValid():
                return
            cursor = QTextCursor(tb)
            cursor.setPosition(tb.position() + min(col, max(0, tb.length() - 1)))
            if any(c.position() == cursor.position() for c in self._extra_cursors):
                return
            if cursor.position() == self.textCursor().position() and not self._extra_cursors:
                return
            self._extra_cursors.append(cursor)
            self.viewport().update()

        def clear_extra_cursors(self):
            if self._extra_cursors:
                self._extra_cursors = []
                self.viewport().update()

        def _edit_at_cursors(self, fn):
            cursors = [self.textCursor()] + list(self._extra_cursors)
            for cursor in sorted(cursors, key=lambda c: c.position(), reverse=True):
                fn(cursor)
            self.setTextCursor(cursors[0])
            self._extra_cursors = cursors[1:]
            self.highlight_current_line()
            self.viewport().update()

        def insert_text_multi(self, text):
            if not self._extra_cursors:
                self.insertPlainText(text)
                return
            self._edit_at_cursors(lambda c: c.insertText(text))

        def backspace_multi(self):
            def back(c):
                block = c.block()
                if c.position() > block.position():
                    c.deletePreviousChar()
            self._edit_at_cursors(back)

        def delete_multi(self):
            def delete(c):
                if c.position() < c.block().position() + c.block().length() - 1:
                    c.deleteChar()
            self._edit_at_cursors(delete)

        def keyPressEvent(self, event: QKeyEvent):
            mods = event.modifiers()
            ctrl_alt = (Qt.KeyboardModifier.ControlModifier |
                        Qt.KeyboardModifier.AltModifier)
            if mods == ctrl_alt and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.add_cursor_vertical(-1 if event.key() == Qt.Key.Key_Up else 1)
                return
            alt_shift = (Qt.KeyboardModifier.AltModifier |
                         Qt.KeyboardModifier.ShiftModifier)
            if mods == alt_shift and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.add_cursor_vertical(-1 if event.key() == Qt.Key.Key_Up else 1)
                return
            if event.key() == Qt.Key.Key_Escape and self._extra_cursors:
                self.clear_extra_cursors()
                return
            if self._extra_cursors:
                if event.key() == Qt.Key.Key_Backspace:
                    self.backspace_multi()
                    return
                if event.key() == Qt.Key.Key_Delete:
                    self.delete_multi()
                    return
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.insert_text_multi("\n")
                    return
                if event.key() == Qt.Key.Key_Tab:
                    self.insert_text_multi("    ")
                    return
                if event.text() and not (mods & (Qt.KeyboardModifier.ControlModifier |
                                                  Qt.KeyboardModifier.MetaModifier)):
                    self.insert_text_multi(event.text())
                    return
            super().keyPressEvent(event)

        # ---------- 平滑滚动 ----------
        def wheelEvent(self, event):
            bar = self.verticalScrollBar()
            delta = event.angleDelta().y()
            if delta == 0:
                return super().wheelEvent(event)
            step = max(1, bar.singleStep()) * 3
            target = bar.value() - int(delta / 120 * step)
            target = max(bar.minimum(), min(bar.maximum(), target))
            self._anim = QPropertyAnimation(bar, b"value", self)
            self._anim.setDuration(130)
            self._anim.setStartValue(bar.value())
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start()
            event.accept()

        def set_plain_text_keep(self, text):
            self.setUndoRedoEnabled(len(text) <= 2_000_000)
            self.setPlainText(text)
            self.document().setModified(False)

    class Kernel3Window(QMainWindow):
        def __init__(self, paths=None):
            super().__init__()
            self.setWindowTitle("Impor 编辑内核 3.0 预览 · PyQt6")
            self.resize(1220, 820)
            self.tabs = QTabWidget(self)
            self.tabs.setTabsClosable(True)
            self.tabs.tabCloseRequested.connect(self._close_tab)
            self.setCentralWidget(self.tabs)
            self._build_menu()
            for path in (paths or []):
                self.open_tab(path)
            if self.tabs.count() == 0:
                self.new_tab()
            self.statusBar().showMessage(
                "Ctrl+Alt+↑/↓ 多光标 · Alt+Shift+↑/↓ 列编辑 · Esc 清除多光标 · Ctrl+S 保存")

        def _build_menu(self):
            m = self.menuBar().addMenu("文件")
            for text, seq, cb in (
                ("新建", "Ctrl+N", self.new_tab),
                ("打开…", "Ctrl+O", self.open_dialog),
                ("保存", "Ctrl+S", self.save_tab),
                ("另存为…", "Ctrl+Shift+S", self.save_tab_as),
                ("关闭标签", "Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex())),
                ("退出", "Ctrl+Q", self.close),
            ):
                a = QAction(text, self)
                a.setShortcut(seq)
                a.triggered.connect(cb)
                m.addAction(a)
            e = self.menuBar().addMenu("编辑")
            for text, seq, cb in (
                ("向下加光标", "Ctrl+Alt+Down", lambda: self._editor().add_cursor_vertical(1)),
                ("向上加光标", "Ctrl+Alt+Up", lambda: self._editor().add_cursor_vertical(-1)),
            ):
                a = QAction(text, self)
                a.setShortcut(seq)
                a.triggered.connect(cb)
                e.addAction(a)
            v = self.menuBar().addMenu("视图")
            a = QAction("切换 minimap", self)
            a.setCheckable(True)
            a.setChecked(True)
            a.triggered.connect(lambda checked: self._toggle_minimap(checked))
            v.addAction(a)

        def _editor(self):
            w = self.tabs.currentWidget()
            return w if isinstance(w, CodeEditor) else None

        def _toggle_minimap(self, checked):
            for i in range(self.tabs.count()):
                ed = self.tabs.widget(i)
                if isinstance(ed, CodeEditor):
                    ed.minimap.setVisible(checked)
                    ed.update_line_number_area_width()

        def new_tab(self):
            ed = CodeEditor()
            ed.document().modificationChanged.connect(
                lambda changed, e=ed: self._update_tab_title(e))
            self.tabs.addTab(ed, "未命名")
            self.tabs.setCurrentWidget(ed)
            ed.setFocus()
            return ed

        def open_dialog(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "打开文件", "", "Impor 脚本 (*.impor *.txt);;所有文件 (*.*)")
            if path:
                self.open_tab(path)

        def open_tab(self, path):
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                return None
            try:
                with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                    text = f.read()
            except Exception as exc:
                QMessageBox.warning(self, "打开失败", str(exc))
                return None
            ed = CodeEditor()
            ed.path = path
            ed.set_plain_text_keep(text)
            ed.document().modificationChanged.connect(
                lambda changed, e=ed: self._update_tab_title(e))
            self.tabs.addTab(ed, os.path.basename(path))
            self.tabs.setCurrentWidget(ed)
            ed.setFocus()
            return ed

        def _update_tab_title(self, ed):
            idx = self.tabs.indexOf(ed)
            if idx < 0:
                return
            name = os.path.basename(ed.path) if ed.path else "未命名"
            self.tabs.setTabText(idx, ("* " if ed.document().isModified() else "") + name)

        def save_tab(self):
            ed = self._editor()
            if ed is None:
                return False
            if not ed.path:
                return self.save_tab_as()
            try:
                with open(ed.path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(ed.toPlainText())
                ed.document().setModified(False)
                self._update_tab_title(ed)
                self.statusBar().showMessage("已保存：" + ed.path, 3000)
                return True
            except Exception as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return False

        def save_tab_as(self):
            ed = self._editor()
            if ed is None:
                return False
            path, _ = QFileDialog.getSaveFileName(
                self, "另存为", ed.path or "untitled.impor",
                "Impor 脚本 (*.impor);;所有文件 (*.*)")
            if not path:
                return False
            ed.path = os.path.abspath(path)
            return self.save_tab()

        def _maybe_save(self, ed):
            if ed is None or not ed.document().isModified():
                return True
            self.tabs.setCurrentWidget(ed)
            r = QMessageBox.question(
                self, "未保存的更改",
                "是否保存 %s？" % (os.path.basename(ed.path) if ed.path else "未命名"),
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save:
                return self.save_tab()
            return True

        def _close_tab(self, index):
            ed = self.tabs.widget(index)
            if not self._maybe_save(ed):
                return
            self.tabs.removeTab(index)
            if ed is not None:
                ed.deleteLater()
            if self.tabs.count() == 0:
                self.new_tab()

        def closeEvent(self, event):
            for i in range(self.tabs.count()):
                if not self._maybe_save(self.tabs.widget(i)):
                    event.ignore()
                    return
            event.accept()

    def _smoke():
        app = QApplication.instance() or QApplication([])
        ed = CodeEditor()
        ed.setPlainText("def a() {\n  print(1)\n  print(2)\n}")
        block = ed.document().findBlockByNumber(1)
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + 2)
        ed.setTextCursor(cursor)
        ed.add_cursor_vertical(1)
        ed.insert_text_multi("X")
        text = ed.toPlainText()
        assert "  Xprint(1)" in text, text
        assert ed.line_number_area_width() > 0
        assert ed.minimap.width() > 0
        print("SMOKE_OK lines=%d cursors=%d width=%d" %
              (ed.blockCount(), len(ed._extra_cursors), ed.line_number_area_width()))
        return 0

    def main(argv=None):
        argv = list(sys.argv[1:] if argv is None else argv)
        if "--smoke" in argv:
            return _smoke()
        if "--window-smoke" in argv:
            app = QApplication.instance() or QApplication([])
            app.setStyle("Fusion")
            win = Kernel3Window([])
            win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            win.resize(900, 600)
            win.show()

            def snap():
                pix = win.grab()
                print("WINDOW_SMOKE_OK %dx%d" % (pix.width(), pix.height()))
                app.quit()

            QTimer.singleShot(300, snap)
            return app.exec()
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setStyle("Fusion")
        paths = [a for a in argv if not a.startswith("-") and os.path.isfile(a)]
        win = Kernel3Window(paths)
        win.show()
        return app.exec()

    if __name__ == "__main__":
        sys.exit(main())
