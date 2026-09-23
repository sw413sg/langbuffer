"""Compact package cards in three collapsible manager sections."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from langbuffer.language_packages import package_present, package_ready


class PackageRow(QFrame):
    def __init__(self, owner, role, key, spec=None, parent=None, extra=False):
        super().__init__(parent)
        self.owner, self.role, self.key, self.spec = owner, role, key, spec
        self.extra = extra
        self.setObjectName('packageCard')
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)
        self.use_button = QPushButton(self)
        self.use_button.setObjectName('languageChoice')
        self.use_button.setCheckable(True)
        self.use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.use_button)
        self.recommendation_label = QLabel(self)
        self.recommendation_label.setObjectName('modelRecommendation')
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setVisible(role == 'recognition')
        layout.addWidget(self.recommendation_label)
        self.status_label = QLabel(self)
        self.status_label.setObjectName('packageStatus')
        layout.addWidget(self.status_label)
        self.details = QWidget(self)
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(0, 7, 0, 0)
        detail_layout.setSpacing(8)
        self.detail_label = QLabel(self.details)
        self.detail_label.setWordWrap(True)
        detail_layout.addWidget(self.detail_label)
        actions = QVBoxLayout()
        self.download_button = QPushButton(self.details)
        self.remove_button = QPushButton(self.details)
        for button in (self.download_button, self.remove_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        detail_layout.addLayout(actions)
        layout.addWidget(self.details)
        self.details.hide()
        self.use_button.clicked.connect(lambda: owner.choose_row(self))
        self.download_button.clicked.connect(lambda: owner.download_row(self))
        self.remove_button.clicked.connect(lambda: owner.remove_row(self))

    def set_expanded(self, expanded):
        self.details.setVisible(expanded)
        self.setProperty('expandedPackage', expanded)
        self.style().unpolish(self)
        self.style().polish(self)

    def refresh(self):
        owner, spec = self.owner, self.spec
        tr = owner.text
        installed = spec is None or package_ready(spec)
        present = spec is not None and package_present(spec)
        readonly = spec is not None and spec.get('readonly', False)
        available = owner._available()
        if self.role == 'recognition':
            active = owner.model_name() == self.key
            compatible = (self.key == 'small') == (owner.source_language != 'en')
            name = self.key
            description = {'small.en': 'For English audio when you prefer the larger model.',
                           'base.en': 'For English audio when you need a lighter model.',
                           'small': 'For audio in other input languages; selected automatically.'}[self.key]
            self.recommendation_label.setText(tr(description))
            detail = ''
            hint = '' if compatible else tr('Select a non-English input language to use this model.'
                                           if self.key == 'small' else
                                           'Select an English input language to use this model.')
        else:
            active_language = owner.source_language if self.role == 'input' else owner.target_language
            active = self.key == active_language and not self.extra
            compatible = not self.extra
            name = (owner.language_name(self.key) if not self.extra else
                    tr('{source} → {target}', source=owner.language_name(spec['source']),
                       target=owner.language_name(spec['target'])))
            detail = tr('{source} → {target}', source=owner.language_name(spec['source']),
                        target=owner.language_name(spec['target'])) if spec else tr('No translation package needed')
            hint = ''
        self.use_button.setText(name)
        self.use_button.setChecked(active)
        self.use_button.setEnabled(available)
        self.use_button.setToolTip(hint)
        self.use_button.setAccessibleName(tr('{action}: {name}', action=tr('In use' if active else 'Use'), name=name))
        if readonly:
            status = tr('Included · read-only' if installed else 'Included · incomplete')
        elif spec is None:
            status = tr('Ready')
        else:
            status = tr('Installed' if installed else 'Incomplete' if present else 'Not installed')
        self.status_label.setText(status)
        self.download_button.setText(tr('Download package'))
        self.download_button.setVisible(spec is not None and not installed and not readonly)
        self.download_button.setEnabled(available and spec is not None and not installed and not readonly)
        self.download_button.setToolTip(tr('Package size: {size}', size=owner.package_size(spec)) if spec else '')
        self.download_button.setAccessibleName(tr('{action}: {name}', action=tr('Download package'), name=name))
        confirming = spec is not None and owner._remove_pending == spec['id']
        self.remove_button.setText(tr('Confirm removal' if confirming else 'Remove'))
        self.remove_button.setVisible(present and not readonly)
        self.remove_button.setEnabled(available and not owner.panel.controller.running and present and not readonly)
        self.remove_button.setAccessibleName(tr('{action}: {name}', action=tr('Remove'), name=name))
        if confirming:
            detail = tr('Remove this package? Click again to confirm.')
        elif spec is not None and not readonly:
            detail = (detail + ' · ' if detail else '') + owner.package_size(spec)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.setProperty('activePackage', active)
        self.style().unpolish(self)
        self.style().polish(self)


class PackageSection:
    def __init__(self, owner, role, title=None):
        self.owner, self.role, self.title_key = owner, role, title
        self.widget = QWidget(owner.dialog)
        self.widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.header = QPushButton(self.widget) if title else None
        if self.header:
            self.header.setObjectName('languageSection')
            self.header.setCheckable(True)
            self.header.setCursor(Qt.CursorShape.PointingHandCursor)
            self.header.clicked.connect(self.set_expanded)
            layout.addWidget(self.header)
        self.body = QWidget(self.widget)
        self.layout = QGridLayout(self.body)
        self.layout.setContentsMargins(0, 0, 0, 6)
        self.layout.setHorizontalSpacing(10)
        self.layout.setVerticalSpacing(10)
        for column in range(3):
            self.layout.setColumnStretch(column, 1)
        layout.addWidget(self.body)
        if self.header:
            self.body.hide()
        self.rows = {}

    def add(self, key, spec=None, extra=False):
        row = PackageRow(self.owner, self.role, key, spec, self.body, extra=extra)
        index = len(self.rows)
        self.rows[key] = row
        self.layout.addWidget(row, index // 3, index % 3, Qt.AlignmentFlag.AlignTop)
        return row

    def set_expanded(self, expanded):
        if self.header:
            if expanded:
                for role in ('input', 'output', 'recognition'):
                    if role != self.role:
                        other = self.owner.sections.get(role)
                        if other is not None:
                            other.set_expanded(False)
            self.header.setChecked(expanded)
            self.body.setVisible(expanded)
            self.widget.updateGeometry()
            self.refresh()
            self.owner.schedule_dialog_fit()

    def reveal(self, key):
        for name, row in self.rows.items():
            row.set_expanded(name == key)
        self.owner.schedule_dialog_fit()

    def refresh(self):
        if self.header:
            self.header.setText(('−  ' if self.header.isChecked() else '+  ') +
                                self.owner.text(self.title_key))
        for row in self.rows.values():
            row.refresh()
