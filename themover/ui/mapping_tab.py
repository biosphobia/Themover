"""Mapping editor: bindings table, feedback rules, templates, save/load."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from themover.mapping import vocabulary as V
from themover.mapping.profile import Binding, FeedbackRule, Profile
from themover.mapping.templates import TEMPLATES, load_template
from themover.ui.context import AppContext


class BindingDialog(QDialog):
    def __init__(self, binding: Optional[Binding] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit binding")
        self.setMinimumWidth(460)
        b = binding or Binding("c0.button.move", "key.space")
        form = QFormLayout(self)
        self.source = QComboBox(); self.source.setEditable(True); self.source.addItems(V.all_sources()); self.source.setCurrentText(b.source)
        self.target = QComboBox(); self.target.setEditable(True); self.target.addItems(V.all_targets()); self.target.setCurrentText(b.target)
        self.mode = QComboBox(); self.mode.addItems(V.MODES); self.mode.setCurrentText(b.mode)
        self.compare = QComboBox(); self.compare.addItems(V.COMPARES); self.compare.setCurrentText(b.compare)
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(-10000, 10000); self.threshold.setDecimals(2); self.threshold.setValue(b.threshold)
        self.range_lo = QDoubleSpinBox(); self.range_lo.setRange(-10000, 10000); self.range_lo.setValue(b.input_range[0])
        self.range_hi = QDoubleSpinBox(); self.range_hi.setRange(-10000, 10000); self.range_hi.setValue(b.input_range[1])
        self.deadzone = QDoubleSpinBox(); self.deadzone.setRange(0, 0.95); self.deadzone.setSingleStep(0.05); self.deadzone.setValue(b.deadzone)
        self.scale = QDoubleSpinBox(); self.scale.setRange(-100, 100); self.scale.setSingleStep(0.1); self.scale.setValue(b.scale)
        self.invert = QCheckBox(); self.invert.setChecked(b.invert)
        self.curve = QComboBox(); self.curve.addItems(V.CURVES); self.curve.setCurrentText(b.curve)
        self.smoothing = QDoubleSpinBox(); self.smoothing.setRange(0, 0.95); self.smoothing.setSingleStep(0.05); self.smoothing.setValue(b.smoothing)
        self.tap_ms = QSpinBox(); self.tap_ms.setRange(10, 5000); self.tap_ms.setValue(b.tap_ms)
        self.repeat_ms = QSpinBox(); self.repeat_ms.setRange(20, 5000); self.repeat_ms.setValue(b.repeat_ms)
        self.comment = QLineEdit(b.comment)
        self.enabled = QCheckBox(); self.enabled.setChecked(b.enabled)
        rng = QHBoxLayout(); rng.addWidget(self.range_lo); rng.addWidget(QLabel("to")); rng.addWidget(self.range_hi)
        form.addRow("Source", self.source)
        form.addRow("Target", self.target)
        form.addRow("Mode", self.mode)
        form.addRow("Button threshold", self.threshold)
        form.addRow("Compare", self.compare)
        form.addRow("Axis input range", rng)
        form.addRow("Deadzone", self.deadzone)
        form.addRow("Scale", self.scale)
        form.addRow("Invert", self.invert)
        form.addRow("Curve", self.curve)
        form.addRow("Smoothing", self.smoothing)
        form.addRow("Tap length (ms)", self.tap_ms)
        form.addRow("Repeat every (ms)", self.repeat_ms)
        form.addRow("Comment", self.comment)
        form.addRow("Enabled", self.enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        b = self.value()
        problems = Profile(bindings=[b]).validate()
        if problems:
            QMessageBox.warning(self, "Invalid binding", "\n".join(problems))
            return
        self.accept()

    def value(self) -> Binding:
        return Binding(
            source=self.source.currentText().strip(), target=self.target.currentText().strip(), mode=self.mode.currentText(),
            threshold=self.threshold.value(), compare=self.compare.currentText(), input_range=[self.range_lo.value(), self.range_hi.value()],
            deadzone=self.deadzone.value(), scale=self.scale.value(), invert=self.invert.isChecked(), curve=self.curve.currentText(),
            smoothing=self.smoothing.value(), tap_ms=self.tap_ms.value(), repeat_ms=self.repeat_ms.value(),
            comment=self.comment.text(), enabled=self.enabled.isChecked(),
        )


class FeedbackDialog(QDialog):
    def __init__(self, rule: Optional[FeedbackRule] = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit feedback rule")
        f = rule or FeedbackRule(when="c0.trigger", controller=0, rumble=0.5)
        form = QFormLayout(self)
        self.when = QComboBox(); self.when.setEditable(True); self.when.addItems([""] + V.all_sources()); self.when.setCurrentText(f.when)
        self.rumble_from = QComboBox(); self.rumble_from.setEditable(True); self.rumble_from.addItems([""] + V.all_sources()); self.rumble_from.setCurrentText(f.rumble_from)
        self.controller = QSpinBox(); self.controller.setRange(0, 1); self.controller.setValue(f.controller)
        self.rumble = QDoubleSpinBox(); self.rumble.setRange(0, 1); self.rumble.setSingleStep(0.1); self.rumble.setValue(f.rumble)
        self.duration = QSpinBox(); self.duration.setRange(0, 5000); self.duration.setValue(f.duration_ms)
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(-10000, 10000); self.threshold.setValue(f.threshold)
        self.led = QLineEdit(",".join(str(c) for c in f.led) if f.led else "")
        self.led_duration = QSpinBox(); self.led_duration.setRange(0, 5000); self.led_duration.setValue(f.led_duration_ms)
        self.comment = QLineEdit(f.comment)
        form.addRow("Pulse when source >", self.when)
        form.addRow("…or rumble follows", self.rumble_from)
        form.addRow("Controller (0/1)", self.controller)
        form.addRow("Rumble strength", self.rumble)
        form.addRow("Rumble ms", self.duration)
        form.addRow("Threshold", self.threshold)
        form.addRow("LED r,g,b (optional)", self.led)
        form.addRow("LED ms", self.led_duration)
        form.addRow("Comment", self.comment)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self) -> FeedbackRule:
        led = None
        if self.led.text().strip():
            try:
                led = [int(x) for x in self.led.text().split(",")][:3]
            except ValueError:
                led = None
        return FeedbackRule(
            when=self.when.currentText().strip(), controller=self.controller.value(), rumble=self.rumble.value(),
            duration_ms=self.duration.value(), led=led, led_duration_ms=self.led_duration.value(), threshold=self.threshold.value(),
            rumble_from=self.rumble_from.currentText().strip(), comment=self.comment.text(),
        )


class MappingTab(QWidget):
    COLS = ("on", "source", "mode", "target", "options", "comment")

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Profile name")
        self.game_edit = QLineEdit()
        self.game_edit.setPlaceholderText("Game")
        top.addWidget(QLabel("Name")); top.addWidget(self.name_edit, 2)
        top.addWidget(QLabel("Game")); top.addWidget(self.game_edit, 2)
        self.sens = QDoubleSpinBox(); self.sens.setRange(0.2, 3.0); self.sens.setSingleStep(0.1); self.sens.setToolTip("Gesture sensitivity: lower = gestures trigger more easily")
        top.addWidget(QLabel("Gesture sensitivity")); top.addWidget(self.sens)
        root.addLayout(top)

        tools = QHBoxLayout()
        self.template_combo = QComboBox()
        for key in TEMPLATES:
            self.template_combo.addItem(TEMPLATES[key]["name"], key)
        self.load_template_btn = QPushButton("Load template")
        self.add_btn = QPushButton("+ Binding")
        self.edit_btn = QPushButton("Edit")
        self.dup_btn = QPushButton("Duplicate")
        self.del_btn = QPushButton("Remove")
        self.save_btn = QPushButton("Save profile")
        self.save_btn.setObjectName("accent2")
        self.export_btn = QPushButton("Export…")
        self.import_btn = QPushButton("Import…")
        for w in (self.template_combo, self.load_template_btn, self.add_btn, self.edit_btn, self.dup_btn, self.del_btn):
            tools.addWidget(w)
        tools.addStretch(1)
        for w in (self.import_btn, self.export_btn, self.save_btn):
            tools.addWidget(w)
        root.addLayout(tools)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([c.title() for c in self.COLS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 3)

        fb_head = QHBoxLayout()
        fb_title = QLabel("Rumble & LED feedback")
        fb_title.setObjectName("h2")
        self.fb_add = QPushButton("+ Rule"); self.fb_edit = QPushButton("Edit"); self.fb_del = QPushButton("Remove")
        fb_head.addWidget(fb_title); fb_head.addStretch(1); fb_head.addWidget(self.fb_add); fb_head.addWidget(self.fb_edit); fb_head.addWidget(self.fb_del)
        root.addLayout(fb_head)
        self.fb_table = QTableWidget(0, 4)
        self.fb_table.setHorizontalHeaderLabels(["Controller", "Trigger", "Effect", "Comment"])
        self.fb_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fb_table.verticalHeader().setVisible(False)
        root.addWidget(self.fb_table, 1)

        self.load_template_btn.clicked.connect(self._load_template)
        self.add_btn.clicked.connect(self._add)
        self.edit_btn.clicked.connect(self._edit)
        self.table.doubleClicked.connect(self._edit)
        self.dup_btn.clicked.connect(self._duplicate)
        self.del_btn.clicked.connect(self._delete)
        self.save_btn.clicked.connect(self._save)
        self.export_btn.clicked.connect(self._export)
        self.import_btn.clicked.connect(self._import)
        self.fb_add.clicked.connect(self._fb_add)
        self.fb_edit.clicked.connect(self._fb_edit)
        self.fb_table.doubleClicked.connect(self._fb_edit)
        self.fb_del.clicked.connect(self._fb_delete)
        self.name_edit.editingFinished.connect(self._meta_changed)
        self.game_edit.editingFinished.connect(self._meta_changed)
        self.sens.valueChanged.connect(self._meta_changed)
        ctx.profile_changed.connect(self._on_profile_changed)
        self.populate(ctx.profile)

    # ------------------------------------------------------------- render
    def _on_profile_changed(self, profile, reason: str) -> None:
        self.populate(profile)

    def populate(self, profile: Profile) -> None:
        self.name_edit.blockSignals(True); self.game_edit.blockSignals(True); self.sens.blockSignals(True)
        self.name_edit.setText(profile.name)
        self.game_edit.setText(profile.game)
        self.sens.setValue(profile.gesture_sensitivity or 1.0)
        self.name_edit.blockSignals(False); self.game_edit.blockSignals(False); self.sens.blockSignals(False)
        self.table.setRowCount(len(profile.bindings))
        for r, b in enumerate(profile.bindings):
            opts = b.describe().split("(", 1)[1].rstrip(")") if "(" in b.describe() else ""
            for c, text in enumerate(["✓" if b.enabled else "–", b.source, b.effective_mode(), b.target, opts, b.comment]):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)
        self.fb_table.setRowCount(len(profile.feedback))
        for r, f in enumerate(profile.feedback):
            trig = f"follows {f.rumble_from}" if f.rumble_from else f"{f.when} > {f.threshold:g}"
            eff = []
            if f.rumble:
                eff.append(f"rumble {f.rumble:g}" + ("" if f.rumble_from else f" for {f.duration_ms}ms"))
            if f.led:
                eff.append(f"LED {f.led} for {f.led_duration_ms}ms")
            for c, text in enumerate([f"P{f.controller + 1}", trig, ", ".join(eff), f.comment]):
                self.fb_table.setItem(r, c, QTableWidgetItem(text))

    def _current_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _commit(self, profile: Profile) -> None:
        self.ctx.apply_profile(profile, reason="edited")

    # ------------------------------------------------------------- actions
    def _meta_changed(self) -> None:
        p = self.ctx.profile.copy()
        p.name = self.name_edit.text().strip() or p.name
        p.game = self.game_edit.text().strip()
        p.gesture_sensitivity = self.sens.value()
        self._commit(p)

    def _load_template(self) -> None:
        key = self.template_combo.currentData()
        self.ctx.profile_key = key
        self._commit(load_template(key))

    def _add(self) -> None:
        dlg = BindingDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            p = self.ctx.profile.copy()
            p.bindings.append(dlg.value())
            self._commit(p)

    def _edit(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        dlg = BindingDialog(self.ctx.profile.bindings[row], parent=self)
        if dlg.exec() == QDialog.Accepted:
            p = self.ctx.profile.copy()
            p.bindings[row] = dlg.value()
            self._commit(p)
            self.table.selectRow(row)

    def _duplicate(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        p = self.ctx.profile.copy()
        p.bindings.insert(row + 1, Binding.from_dict(p.bindings[row].to_dict()))
        self._commit(p)

    def _delete(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        p = self.ctx.profile.copy()
        del p.bindings[row]
        self._commit(p)

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:", text=self.ctx.profile.name)
        if ok and name.strip():
            p = self.ctx.profile.copy()
            p.name = name.strip()
            self.ctx.profile = p
            self.ctx.save_current(name.strip())
            self.ctx.profile_changed.emit(p, "saved")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export profile", f"{self.ctx.profile.name}.json", "JSON (*.json)")
        if path:
            self.ctx.profile.save(path)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "", "JSON (*.json)")
        if path:
            try:
                self._commit(Profile.load(path))
            except Exception as exc:
                QMessageBox.warning(self, "Import failed", str(exc))

    def _fb_row(self) -> int:
        rows = self.fb_table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _fb_add(self) -> None:
        dlg = FeedbackDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            p = self.ctx.profile.copy()
            p.feedback.append(dlg.value())
            self._commit(p)

    def _fb_edit(self) -> None:
        row = self._fb_row()
        if row < 0:
            return
        dlg = FeedbackDialog(self.ctx.profile.feedback[row], parent=self)
        if dlg.exec() == QDialog.Accepted:
            p = self.ctx.profile.copy()
            p.feedback[row] = dlg.value()
            self._commit(p)

    def _fb_delete(self) -> None:
        row = self._fb_row()
        if row < 0:
            return
        p = self.ctx.profile.copy()
        del p.feedback[row]
        self._commit(p)
