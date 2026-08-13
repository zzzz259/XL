import os, pathlib, codecs, shutil, subprocess, argparse, sys
import psutil

'''
  Epic7 Bank File Decryptor (v2.1)
  支持命令行参数指定输入/输出目录，递归扫描 .bank 文件。
  支持作为模块导入调用（run 函数），避免打包后递归启动 EXE。

  用法:
    python epic7_debank.py                          # 使用默认 ./input → ./result
    python epic7_debank.py -i /path/to/input -o /path/to/output

  作为模块导入:
    from epic7_debank import run
    run(input_dir="/path/to/input", output_dir="/path/to/output")

  工作流程:
    1) 递归扫描输入目录，收集所有 .bank 文件
    2) 对每个 .bank 文件，使用 quickbms.exe 解包到临时目录
    3) quickbms 通过 -S 参数回调 _epic7_defsb.py 提取音频
    4) 将提取的音频文件收集到输出目录
    5) 清理临时目录
'''


def parse_args():
    parser = argparse.ArgumentParser(description="Epic7 Bank File Decryptor")
    parser.add_argument("--input-dir", "-i", default=None,
                        help="输入目录（递归扫描 .bank 文件），默认为脚本目录下的 input/")
    parser.add_argument("--output-dir", "-o", default=None,
                        help="输出目录（提取的音频文件），默认为脚本目录下的 result/")
    return parser.parse_args()


def _should_process_file(filepath):
    """判断文件是否应作为音频处理

    筛选规则（两者需同时满足）：
    1. 文件名（不含扩展名）完全由数字组成
    2. 路径中包含 fmodassets 子目录
    """
    fname = os.path.basename(filepath)
    base = os.path.splitext(fname)[0]
    if not base.isdigit():
        return False
    if "fmodassets" not in filepath.split(os.sep):
        return False
    return True


def collect_bank_files(input_root):
    """递归扫描输入目录，收集所有符合筛选条件的 .bank 文件路径"""
    bank_files = []
    for root, dirs, files in os.walk(input_root):
        for f in files:
            if f.lower().endswith(".bank"):
                filepath = os.path.join(root, f)
                if _should_process_file(filepath):
                    bank_files.append(filepath)
    return bank_files


def collect_audio_files(result_dir):
    """递归收集结果目录中的所有音频文件"""
    audio_exts = {".wav", ".ogg", ".mp3"}
    audio_files = []
    if os.path.isdir(result_dir):
        for root, dirs, files in os.walk(result_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in audio_exts:
                    audio_files.append(os.path.join(root, f))
    return audio_files


def execute_quickbms_single(bank_path, folder4subcontractors_, bank_temp, folder_cur_, python_exe_):
    """对单个 .bank 文件执行 quickbms 解包"""
    bank_name = os.path.basename(bank_path)
    os.makedirs(bank_temp, exist_ok=True)

    try:
        subprocess.run([
            folder4subcontractors_ + "/quickbms.exe", "-Y", "-K", "-d",
            "-F", '{}.bank',
            '-S', python_exe_ + ' "' + folder_cur_ + '/_epic7_defsb.py" #INPUT# "' + folder_cur_ + '"',
            folder4subcontractors_ + '/bank-files.bms',
            bank_path,
            bank_temp
        ], check=False)
        return True
    except Exception as e:
        print(f"  [警告] 解包失败: {bank_name}: {e}")
        return False


def run(input_dir, output_dir, folder_cur=None):
    """核心逻辑：解密 .bank 文件（支持模块化调用）

    Args:
        input_dir: 输入目录（递归扫描 .bank 文件）
        output_dir: 输出目录（提取的音频文件）
        folder_cur: 脚本所在目录（自动检测，通常无需传入）
    """
    if folder_cur is None:
        folder_cur = os.path.dirname(os.path.abspath(__file__))

    # 打包后 sys.executable 指向 EXE 自身，无法执行 Python 脚本
    # 使用 "python" 回退到系统 PATH
    if getattr(sys, 'frozen', False):
        python_exe = "python"
    else:
        python_exe = psutil.Process(os.getpid()).name()

    os.environ["PYTHONIOENCODING"] = "utf-8"
    encoding = "utf-8"
    codecs.lookup(encoding)

    folder4subcontractors = os.path.join(folder_cur, "_subcontractors")
    folder4tempo = os.path.join(folder_cur, "_tempo")
    folder4result = os.path.join(folder_cur, "result")

    input_root = os.path.abspath(input_dir)
    output_root = os.path.abspath(output_dir)

    print(f"输入目录: {input_root}")
    print(f"输出目录: {output_root}")

    # 递归收集所有 .bank 文件
    bank_files = collect_bank_files(input_root)
    if not bank_files:
        print("未找到 .bank 文件")
        return

    print(f"共找到 {len(bank_files)} 个 .bank 文件")

    # 准备临时目录和结果目录
    shutil.rmtree(folder4tempo, ignore_errors=True)
    pathlib.Path(folder4tempo).mkdir(parents=True, exist_ok=True)
    os.makedirs(output_root, exist_ok=True)

    total_copied = 0
    total_skipped = 0
    total_failed = 0

    for i, bank_path in enumerate(bank_files):
        bank_name = os.path.basename(bank_path)
        print(f"[{i+1}/{len(bank_files)}] 处理: {bank_name}")

        # 获取相对于输入目录的路径，用于保留目录结构
        rel_path = os.path.relpath(bank_path, input_root)
        rel_dir = os.path.dirname(rel_path)

        # 构建输出子目录（保持与输入相同的目录结构）
        output_subdir = os.path.join(output_root, rel_dir) if rel_dir else output_root
        try:
            os.makedirs(output_subdir, exist_ok=True)
        except Exception as e:
            print(f"  [ERROR] 创建输出目录失败: {output_subdir}: {e}")
            continue

        # 为每个 bank 文件创建独立的临时子目录
        bank_stem = os.path.splitext(bank_name)[0]
        bank_temp = os.path.join(folder4tempo, bank_stem)

        # 解包当前 bank 文件
        success = execute_quickbms_single(bank_path, folder4subcontractors, bank_temp, folder_cur, python_exe)
        if not success:
            continue

        # 收集当前 bank 解包出的音频文件
        audio_files = collect_audio_files(folder4result)
        if not audio_files:
            print(f"  [DEBUG] 未提取到音频文件: {bank_name}")
            # 清理本次 result 中的残留文件，避免影响下一个 bank
            shutil.rmtree(folder4result, ignore_errors=True)
            pathlib.Path(folder4result).mkdir(parents=True, exist_ok=True)
            continue

        for src in audio_files:
            fname = os.path.basename(src)
            dst = os.path.join(output_subdir, fname)
            try:
                # 去重：文件存在且大小一致则跳过
                if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                    print(f"  跳过已存在: {os.path.relpath(dst, output_root)}")
                    total_skipped += 1
                    continue
                shutil.copy2(src, dst)
                total_copied += 1
                print(f"  [OK] {fname} → {os.path.relpath(dst, output_root)}")
            except Exception as e:
                print(f"  [ERROR] 复制失败: {fname}: {e}")
                total_failed += 1

        # 清理本次 result 目录，避免影响下一个 bank 解包
        shutil.rmtree(folder4result, ignore_errors=True)
        pathlib.Path(folder4result).mkdir(parents=True, exist_ok=True)

    print(f"\n处理完成: 已复制 {total_copied} 个, 跳过 {total_skipped} 个, 失败 {total_failed} 个")
    print(f"输出目录: {output_root}")

    # 清理临时目录
    shutil.rmtree(folder4tempo, ignore_errors=True)
    shutil.rmtree(folder4result, ignore_errors=True)

    print("完成!")


def main():
    args = parse_args()
    folder_cur = os.path.dirname(os.path.abspath(__file__))
    input_root = os.path.abspath(args.input_dir) if args.input_dir else os.path.join(folder_cur, "input")
    output_root = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(folder_cur, "result")
    run(input_root, output_root, folder_cur)


if __name__ == "__main__":
    main()
