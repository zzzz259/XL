"""Importer Feature 的组合根工厂。"""

from dataclasses import dataclass

from app.shared.contracts import FeatureDescriptor, FeatureRuntime

from .controller import ImportController
from .service import ImporterService

DESCRIPTOR = FeatureDescriptor("importer", "导入AS", "file-import")


@dataclass
class ImporterPagePort:
    """Importer 没有独立页面时提供的无 Qt 生命周期端口。"""

    loading: bool = False

    def set_loading(self, loading: bool) -> None:
        self.loading = loading


def create_feature(context, parent=None) -> FeatureRuntime:
    page = ImporterPagePort()
    controller = ImportController(
        ImporterService(context.data_dir / "material", context.output_dir / "lua"),
        parent,
    )
    return FeatureRuntime(DESCRIPTOR, page, controller)
