"""Scene-specific choices expressed as output intent, independent of renderer details."""
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget


class SceneExportOptions(QWidget):
    def __init__(self, capabilities=(), parent=None):
        super().__init__(parent)
        capabilities = tuple(capabilities)
        self.setObjectName('sceneExportOptions')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        count = sum(cap.interaction_count for cap in capabilities)
        text = '；'.join(dict.fromkeys(
            cap.label + (f'：{cap.reason}' if cap.reason else '') for cap in capabilities
        ))
        if count:
            text += f'；额外导出 {count} 组配置交互视频（保留目标、轨道及收尾动作）'
        self.summary = QLabel(text, self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.interactions_checkbox = QCheckBox('导出配置交互视频', self)
        self.interactions_checkbox.setChecked(True)
        self.interactions_checkbox.setVisible(count > 0)
        layout.addWidget(self.interactions_checkbox)
        self.basic_checkbox = QCheckBox('仅导出基础画面（不含动态特效）', self)
        self.basic_checkbox.setVisible(any(cap.can_export_basic and not cap.can_export for cap in capabilities))
        layout.addWidget(self.basic_checkbox)
        self.effects_note = QLabel('动态特效按工程配置自动合成；透明 PNG 的发亮边缘外观受查看背景影响，可选纯色背景。', self)
        self.effects_note.setWordWrap(True)
        self.effects_note.setVisible(any(cap.has_effects for cap in capabilities))
        layout.addWidget(self.effects_note)
        self.setVisible(bool(capabilities))

    def options(self):
        basic = self.basic_checkbox.isChecked()
        return {'include_interactions': self.interactions_checkbox.isChecked(),
                'include_effects': not basic, 'allow_basic': basic}
