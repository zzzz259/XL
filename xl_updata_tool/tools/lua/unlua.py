import os
import shutil
import subprocess
import time
from datetime import datetime

FIXED_HEAD = (b'\x1B\x4C\x75\x61\x54\x00\x19\x93\x0D\x0A\x1A\x0A\x04\x08\x08\x78'
              b'\x56\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x28\x77\x40\x01')

# 脚本所在目录，用于构建相对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# unluac.jar 路径（使用已确认可用的新版本）
unluac_path = os.path.join(BASE_DIR, "unluac.jar")

# OpCode 映射表路径
opmap_path = os.path.join(BASE_DIR, "opmap")

# 日志文件路径
LOG_FILE = os.path.join(os.path.dirname(BASE_DIR), "反编译日志.txt")

error_msg = []
invalid_files = []


def log_message(message: str):
    """向控制台和日志文件同时输出信息"""
    print(message)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(message + '\n')
    except Exception as e:
        print(f"    [警告] 写入日志文件失败: {e}")


def fixFile(src: str, dst: str) -> bool:
    '''
    修复luac文件的文件头。
    用 FIXED_HEAD 替换原文件中从开头到特征标记 \\x28\\x77\\x40\\x01 之间的内容。
    输出文件自动去除 .bank 后缀，改为 .lua 后缀。
    返回 True 表示成功，False 表示无效文件。
    '''
    try:
        with open(src, 'rb') as f:
            data = f.read()
    except Exception as e:
        error_msg.append(f'读取文件失败: {src} - {e}')
        return False

    end = data.find(b'\x28\x77\x40\x01')
    if end == -1:
        error_msg.append(f'无效的 luac 文件（未找到头部特征标记）: {src}')
        invalid_files.append(src)
        return False

    HEAD_LEN = end + 4
    data = FIXED_HEAD + data[HEAD_LEN:]

    # 去除 .bank 后缀，保证输出为 .lua 文件
    filename, _ = os.path.splitext(dst)
    dst = filename + '.lua'

    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        error_msg.append(f'写入修复文件失败: {dst} - {e}')
        return False


def decompile(src: str, dst: str) -> bool:
    '''
    使用 unluac.jar 反编译单个 luac 文件。
    '''
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    command = [
        'java', '-jar', unluac_path,
        src,
        '-o', dst,
        '--opmap', opmap_path
    ]
    cmd_str = ' '.join(command)
    print(f'    [反编译] {cmd_str}')

    try:
        result = subprocess.run(command, capture_output=True, timeout=120)
        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='replace')
            error_msg.append(f'反编译失败: {src} -> {dst} (返回码: {result.returncode})')
            if stderr.strip():
                print(f'    [错误详情] {stderr.strip()}')
            return False
        return True
    except subprocess.TimeoutExpired:
        error_msg.append(f'反编译超时 (120秒): {src}')
        return False
    except FileNotFoundError:
        error_msg.append(f'未找到 Java 运行时，请确保 Java 已安装并加入 PATH')
        return False
    except Exception as e:
        error_msg.append(f'反编译过程异常: {src} - {e}')
        return False


def is_lua_file(filename: str) -> bool:
    """判断是否为需要处理的 Lua 字节码文件"""
    # 排除上次处理遗留的 .lua.bank.lua 文件（它们是已处理过的输出，不是源文件）
    if filename.endswith('.lua.bank.lua'):
        return False
    return filename.endswith('.lua') or filename.endswith('.lua.bank')


def get_output_name(filename: str) -> str:
    """从文件名中提取输出文件名（去除 .bank 后缀）"""
    if filename.endswith('.lua.bank'):
        return filename[:-len('.bank')]
    return filename


def process_file(src_path: str, output_dir: str) -> bool:
    """
    对单个文件执行 头部修复 → 反编译 流程。
    返回 True 表示成功，False 表示失败。
    """
    filename = os.path.basename(src_path)
    output_name = get_output_name(filename)
    output_path = os.path.join(output_dir, output_name)

    # 临时文件（与输出文件同目录，确保在相同文件系统上）
    temp_file = os.path.join(output_dir, f'__tmp_{output_name}')

    print(f'\n[处理] {filename}')

    # Step 1: 修复头部
    print('  [步骤 1/2] 修复文件头部...')
    if not fixFile(src_path, temp_file):
        log_message(f'  [失败] 头部修复失败: {src_path}')
        return False

    # fixFile 输出路径 = os.path.splitext(temp_file)[0] + '.lua'
    # 由于 temp_file 本身已以 .lua 结尾（output_name 含 .lua），实际路径就是 temp_file
    actual_temp = temp_file
    if not os.path.isfile(actual_temp):
        error_msg.append(f'头部修复未生成文件: {actual_temp}')
        log_message(f'  [失败] 头部修复未生成文件: {src_path}')
        return False

    print('  [步骤 1/2] 头部修复完成')

    # Step 2: 反编译
    print('  [步骤 2/2] 反编译中...')
    if not decompile(actual_temp, output_path):
        log_message(f'  [失败] 反编译失败: {src_path}')
        # 清理临时文件
        if os.path.isfile(actual_temp):
            os.remove(actual_temp)
        return False

    print('  [步骤 2/2] 反编译完成')

    # 清理临时文件
    if os.path.isfile(actual_temp):
        os.remove(actual_temp)

    log_message(f'  [成功] 输出: {output_path}')
    return True


def main():
    print("=" * 60)
    print("  Lua 文件修复 + 反编译工具")
    print("  支持批量处理目录下的所有 .lua / .lua.bank 文件")
    print("=" * 60)

    src = input("\n请输入需要处理的 luac 文件或目录路径：").strip()
    dst = input("请输入输出目录路径：").strip()

    if not os.path.exists(src):
        print(f"输入路径不存在：{src}")
        return
    if not os.path.exists(dst):
        os.makedirs(dst)

    # 检查工具文件
    if not os.path.isfile(unluac_path):
        print(f"[错误] unluac.jar 不存在: {unluac_path}")
        return
    if not os.path.isfile(opmap_path):
        print(f"[错误] opmap 文件不存在: {opmap_path}")
        return

    # 记录开始
    start_time = time.time()
    log_message(f"\n{'='*60}")
    log_message(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_message(f"  源路径: {src}")
    log_message(f"  输出目录: {dst}")
    log_message(f"{'='*60}")

    success_count = 0
    fail_count = 0

    if os.path.isfile(src):
        # 单个文件处理
        if process_file(src, dst):
            success_count = 1
        else:
            fail_count = 1
    else:
        # 批量处理目录
        all_files = []
        for root, _, filenames in os.walk(src):
            for filename in filenames:
                if is_lua_file(filename):
                    all_files.append(os.path.join(root, filename))

        if not all_files:
            log_message(f"[警告] 源目录中未找到任何 .lua 或 .lua.bank 文件: {src}")
            return

        log_message(f"\n找到 {len(all_files)} 个待处理文件:\n")

        for file_path in all_files:
            if process_file(file_path, dst):
                success_count += 1
            else:
                fail_count += 1

    # 复制无效文件（保留原始未修复的 .lua.bank 文件到输出目录）
    handle_invalid_files(src, dst)

    # 汇总
    end_time = time.time()
    duration = end_time - start_time

    summary = (
        f"\n{'='*60}\n"
        f"  处理完成!\n"
        f"  总文件: {success_count + fail_count}\n"
        f"  成功: {success_count}\n"
        f"  失败: {fail_count}\n"
        f"  用时: {duration:.2f} 秒\n"
        f"  输出目录: {dst}\n"
        f"{'='*60}"
    )
    log_message(summary)

    if error_msg:
        print(f"\n遇到 {len(error_msg)} 个错误：")
        for msg in error_msg[-20:]:  # 只显示最近20个错误
            print(f"  - {msg}")


def handle_invalid_files(src: str, dst: str):
    """将无效的原始文件复制到输出目录（保留原始数据）"""
    for file in invalid_files:
        rel_path = os.path.relpath(file, src) if os.path.isdir(src) else os.path.basename(file)
        target_path = os.path.join(dst, rel_path)
        target_dir = os.path.dirname(target_path)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
        shutil.copy(file, target_path)
        print(f"  [已复制无效文件] {file} -> {target_path}")


if __name__ == "__main__":
    main()