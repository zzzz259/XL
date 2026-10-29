import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.features.preview.page import PreviewPage
from app.features.preview.dialogs.export_settings import ExportSettingsDialog
from app.features.preview.portrait_config.model import AssetRef, PortraitScene, ScenePart, SpineState, SceneDiagnostic
from app.features.preview.resource_model import PreviewResourceCatalog, SpineSkinRecord
from app.features.preview.scene_capabilities import SceneCapabilities, portrait_scene_capabilities


@pytest.fixture(scope='module')
def qapp():
    yield QApplication.instance() or QApplication([])


def scene_with(diagnostics=()):
    skel, atlas, obj = (AssetRef('bundle', n) for n in (1, 2, 3))
    part = ScenePart('role', obj, 'role', spine=SpineState(skel, skeleton_json=skel, atlases=(atlas,)))
    return PortraitScene('402', '402', 'skin', '10080', 'skin', obj, ('bundle',), (part,),
                         available_assets=(skel, atlas, obj), diagnostics=diagnostics)


@pytest.mark.parametrize('diagnostic,state', [
    (SceneDiagnostic('unsupported_renderer', '不支持的动态效果'), 'partial'),
    (SceneDiagnostic('portrait_engineering_upgrade_required', '旧版需要更新', severity='warning'), 'legacy'),
])
def test_scene_basic_choice_is_selectable_but_never_enables_complete_export(qapp, diagnostic, state):
    scene = scene_with((diagnostic,))
    page = PreviewPage()
    page.set_spine_catalog(PreviewResourceCatalog.from_records((), portrait_scenes=(scene,)))
    item = page.spine_tree.topLevelItem(0).child(0)
    assert item.flags() & Qt.ItemIsUserCheckable
    item.setCheckState(0, Qt.Checked)
    page.spine_tree.setCurrentItem(item)
    capability = portrait_scene_capabilities(scene)
    assert capability.state == state
    assert capability.label in item.text(1)
    assert capability.reason in item.toolTip(1)
    assert page.btn_export_selected.text() == '导出选中场景'
    assert not page.btn_export_selected.isEnabled()
    assert page.btn_export_basic.isEnabled()
    assert page.btn_refresh_portrait.isEnabled()
    received = []
    page.export_basic_requested.connect(lambda: received.append('basic'))
    page.portrait_refresh_requested.connect(lambda: received.append('refresh'))
    page.btn_export_basic.click()
    page.btn_refresh_portrait.click()
    assert received == ['basic', 'refresh']
    page.close()


def test_busy_cancellation_restores_context_labels_and_actions(qapp):
    scene = scene_with()
    page = PreviewPage()
    page.set_spine_catalog(PreviewResourceCatalog.from_records((), portrait_scenes=(scene,)))
    page.spine_tree.topLevelItem(0).child(0).setCheckState(0, Qt.Checked)
    page.set_export_busy(True)
    assert page.btn_export_selected.text() == '取消导出'
    assert not page.btn_refresh_portrait.isEnabled()
    page.set_export_busy(True, cancelling=True)
    assert not page.btn_export_selected.isEnabled()
    page.set_export_busy(False)
    assert page.btn_export_selected.text() == '导出选中场景'
    assert page.btn_export_selected.accessibleName() == '导出选中场景'
    assert page.btn_export_selected.isEnabled()
    page.close()


def test_raw_selection_clears_scene_specific_actions(qapp):
    scene = scene_with()
    raw = SpineSkinRecord('10080', 'E:/hero.skel', 'E:/hero.atlas', 'default', 'raw', 'hero', 'ready')
    page = PreviewPage()
    page.set_spine_catalog(PreviewResourceCatalog.from_records((raw,), portrait_scenes=(scene,)))
    scene_item = page.spine_tree.topLevelItem(0).child(0)
    page.spine_tree.setCurrentItem(scene_item)
    page.set_selected_spine_records((raw,))
    assert page.btn_export_selected.text() == '导出选中 Spine'
    assert not page.btn_export_basic.isEnabled()
    assert not page.btn_refresh_portrait.isEnabled()
    page.close()


def test_scene_options_require_explicit_basic_and_preserve_confirmed_interaction_choice(qapp):
    cap = SceneCapabilities('partial', '特效暂不支持', '材质不支持', can_export_basic=True,
                            has_effects=True, interaction_count=2)
    dialog = ExportSettingsDialog('hero.skel', 'hero.atlas', scene_capabilities=(cap,))
    assert dialog.scene_export_options() == {'include_interactions': True, 'include_effects': True, 'allow_basic': False}
    assert '2' in dialog.scene_options.summary.text()
    assert '材质不支持' in dialog.scene_options.summary.text()
    dialog.scene_options.basic_checkbox.click()
    assert dialog.scene_export_options() == {'include_interactions': True, 'include_effects': False, 'allow_basic': True}
    dialog.scene_options.interactions_checkbox.click()
    assert not dialog.scene_export_options()['include_interactions']
    dialog.close()


def test_raw_dialog_has_no_irrelevant_scene_choices(qapp):
    dialog = ExportSettingsDialog('hero.skel', 'hero.atlas')
    assert dialog.scene_options.isHidden()
    assert dialog.scene_export_options() == {'include_interactions': True, 'include_effects': True, 'allow_basic': False}
    dialog.close()
