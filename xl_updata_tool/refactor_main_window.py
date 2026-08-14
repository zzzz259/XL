# -*- coding: utf-8 -*-
"""临时脚本：清理 main_window.py 中已拆分的静态方法并替换函数调用"""
import re

filepath = r'c:\Users\Administrator\Desktop\xl\xl_updata_tool\app\ui\main_window.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 删除从 "# --- 配对识别 ---" 到 "_run_spine_export" 结束的所有静态方法
# 找 "    # --- 配对识别 ---" 和 "    def _force_reload_preview(self):"
start_marker = '    # --- 配对识别 ---'
end_marker = '    def _force_reload_preview(self):'
start_idx = content.find(start_marker)
end_idx = content.find(end_marker)
if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + content[end_idx:]
    print(f"Deleted spine static methods (lines {start_marker} to {end_marker})")
else:
    print(f"WARNING: Could not find markers. start={start_idx}, end={end_idx}")

# 2. 删除单独的 spine 静态方法（_extract_skin_name_from_png 等）
# 这些方法被移到 spine_adapter.py，需要删除

# 删除 _extract_skin_name_from_png
pattern = r"    @staticmethod\s*\n    def _extract_skin_name_from_png\(png_path\):.*?(?=\n    @staticmethod|\n    def \w|\n\nclass |\n# )"
# 逐个删除需要清理的静态方法
methods_to_delete = [
    '_extract_skin_name_from_png',
    '_is_composite_png',
    '_find_composite_sources',
    '_export_spine_media_file',
    '_get_ffmpeg_path',
    '_ffmpeg_composite_videos',
    '_cleanup_temp',
]

for method_name in methods_to_delete:
    # Find the method and its body
    pattern = rf"(    @staticmethod\s*\n    def {method_name}\(.*?)(?=\n    @staticmethod|\n    def \w|\n\nclass |\n# |\n    def _force|\Z)"
    matches = list(re.finditer(pattern, content, re.DOTALL))
    if matches:
        # Remove the last match (furthest in file)
        m = matches[-1]
        content = content[:m.start()] + content[m.end():]
        print(f"Deleted method: {method_name}")
    else:
        print(f"WARNING: Could not find method: {method_name}")

# 3. 替换 MainWindow._xxx 调用为模块级函数
replacements = {
    'MainWindow._extract_skin_name_from_png': 'extract_skin_name_from_png',
    'MainWindow._is_composite_png': 'is_composite_png',
    'MainWindow._find_composite_sources': 'find_composite_sources',
    'MainWindow._export_spine_media_file': 'export_spine_media_file',
    'MainWindow._ffmpeg_composite_videos': 'ffmpeg_composite_videos',
    'MainWindow._cleanup_temp': 'cleanup_temp',
    'MainWindow._get_ffmpeg_path': 'get_ffmpeg_path',
    'MainWindow._extract_motion_names': 'extract_motion_names',
    'MainWindow._run_spine_export': 'run_spine_export',
}

# Only replace within the MainWindow class (after the class definition)
class_start = content.find('class MainWindow(QMainWindow):')
if class_start != -1:
    before = content[:class_start]
    class_content = content[class_start:]
    for old, new in replacements.items():
        class_content = class_content.replace(old, new)
    content = before + class_content
    print("Replaced MainWindow._xxx calls with module-level functions")
else:
    print("ERROR: Could not find MainWindow class")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done!")