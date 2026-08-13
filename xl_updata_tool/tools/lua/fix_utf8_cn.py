import re
import os
import shutil

def tran(input_path, output_path):
    pattern = re.compile(r'(\\(\d{3}))+')

    with open(input_path, 'r', encoding='utf-8') as input_file:
        content = input_file.read()

    def replace_long_match(match):
        codes = match.group().split('\\')[1:]
        byte_values = [int(code) for code in codes]
        byte_sequence = bytes(byte_values)
        try:
            result = byte_sequence.decode('utf-8')
        except UnicodeDecodeError:
            result = match.group()
        return result

    content = pattern.sub(replace_long_match, content)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as output_file:
        output_file.write(content)

def process_path(input_path, output_root):
    if os.path.isfile(input_path):
        filename = os.path.basename(input_path)
        output_path = os.path.join(output_root, filename)
        tran(input_path, output_path)
        print(f"Processed file: {input_path} -> {output_path}")
    elif os.path.isdir(input_path):
        for root, _, files in os.walk(input_path):
            for file in files:
                in_file = os.path.join(root, file)
                rel_path = os.path.relpath(in_file, input_path)
                out_file = os.path.join(output_root, rel_path)
                tran(in_file, out_file)
                print(f"Processed file: {in_file} -> {out_file}")
    else:
        print(f"路径不存在或不是文件/文件夹: {input_path}")

if __name__ == '__main__':
    import sys

    input_path = r"C:\Users\Administrator\Desktop\测试\输出\BaseWord_cn.lua"   # 你的输入文件
    output_root = r"C:\Users\Administrator\Desktop\测试\输出"   # 输出到同一目录

    print(f"输入路径: {input_path}")
    print(f"输出路径: {output_root}")
    process_path(input_path, output_root)
    print("处理完成！")
