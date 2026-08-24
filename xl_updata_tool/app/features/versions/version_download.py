"""版本 Bundle 下载计划计算，不依赖 Qt。"""


def calculate_missing_downloads(sub_bundles, target_hashes) -> list[str]:
    """根据数据库状态和目标 hash 集合返回稳定排序的待下载列表。"""
    downloaded = {row[0] for row in sub_bundles if row[2]}
    return sorted(set(target_hashes) - downloaded)
