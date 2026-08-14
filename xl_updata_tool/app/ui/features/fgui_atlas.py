# -*- coding: utf-8 -*-
"""FGUI 图集切割核心模块：解析 FairyGUI 二进制图集并切割为独立 PNG 图片"""

import os
import json
import struct
from enum import Enum
from PIL import Image


class PackageItemType(Enum):
    Image = 0
    MovieClip = 1
    Sound = 2
    Component = 3
    Atlas = 4
    Font = 5
    Swf = 6
    Misc = 7
    Unknown = 8
    Spine = 9
    DragoneBones = 10


class Rect:
    def __init__(self, x=0.0, y=0.0, width=0.0, height=0.0):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class Vector2:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


class AtlasSprite:
    def __init__(self):
        self.atlas = None
        self.rect = Rect()
        self.offset = Vector2()
        self.originalSize = Vector2()
        self.rotated = False


class PackageItem:
    def __init__(self):
        self.owner = None
        self.type = PackageItemType.Unknown
        self.id = ""
        self.name = ""
        self.width = 0
        self.height = 0
        self.path = ""
        self.file = ""
        self.exported = False
        self.rawData = None
        self.branches = None
        self.highResolution = None
        self.scale9Grid = None
        self.scaleByTile = False
        self.tileGridIndice = 0
        self.pixelHitTestData = None


class ByteBuffer:
    def __init__(self, data: bytes, offset: int = 0, length: int = -1):
        self._data = data
        self._pointer = 0
        self._offset = offset
        if length < 0:
            self._length = len(data) - offset
        else:
            self._length = length
        self.little_endian = False
        self.string_table = []
        self.version = 0

    @property
    def position(self) -> int:
        return self._pointer

    @position.setter
    def position(self, value: int):
        self._pointer = value

    @property
    def length(self) -> int:
        return self._length

    @property
    def bytes_available(self) -> bool:
        return self._pointer < self._length

    def skip(self, count: int) -> int:
        self._pointer += count
        return self._pointer

    def read_byte(self) -> int:
        if self._pointer >= self._length:
            raise IndexError("Buffer out of range")
        result = self._data[self._offset + self._pointer]
        self._pointer += 1
        return result

    def read_bytes(self, count: int) -> bytes:
        if self._pointer + count > self._length:
            raise IndexError("Buffer out of range")
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + count]
        self._pointer += count
        return result

    def read_bool(self) -> bool:
        return self.read_byte() == 1

    def read_short(self) -> int:
        start_index = self._offset + self._pointer
        self._pointer += 2
        if self.little_endian:
            return self._data[start_index] | (self._data[start_index + 1] << 8)
        else:
            return (self._data[start_index] << 8) | self._data[start_index + 1]

    def read_ushort(self) -> int:
        return self.read_short() & 0xFFFF

    def read_int(self) -> int:
        start_index = self._offset + self._pointer
        self._pointer += 4
        if self.little_endian:
            return (self._data[start_index] |
                    (self._data[start_index + 1] << 8) |
                    (self._data[start_index + 2] << 16) |
                    (self._data[start_index + 3] << 24))
        else:
            return ((self._data[start_index] << 24) |
                    (self._data[start_index + 1] << 16) |
                    (self._data[start_index + 2] << 8) |
                    self._data[start_index + 3])

    def read_uint(self) -> int:
        return self.read_int() & 0xFFFFFFFF

    def read_float(self) -> float:
        int_val = self.read_int()
        return struct.unpack('f', struct.pack('I', int_val))[0]

    def read_string(self) -> str:
        length = self.read_ushort()
        if length == 0:
            return ""
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + length].decode('utf-8')
        self._pointer += length
        return result

    def read_string_with_length(self, length: int) -> str:
        if length == 0:
            return ""
        result = self._data[self._offset + self._pointer:self._offset + self._pointer + length].decode('utf-8')
        self._pointer += length
        return result

    def read_s(self) -> str:
        index = self.read_ushort()
        if index == 65534:
            return None
        elif index == 65533:
            return ""
        elif index < len(self.string_table):
            return self.string_table[index]
        else:
            return ""

    def read_s_array(self, count: int) -> list:
        result = []
        for i in range(count):
            result.append(self.read_s())
        return result

    def read_color(self) -> tuple:
        r = self.read_byte()
        g = self.read_byte()
        b = self.read_byte()
        a = self.read_byte()
        return (r, g, b, a)

    def seek(self, index_table_pos: int, block_index: int) -> bool:
        tmp = self.position
        self.position = index_table_pos
        seg_count = self.read_byte()
        if block_index < seg_count:
            use_short = self.read_byte() == 1
            if use_short:
                self.position += 2 * block_index
                new_pos = self.read_short()
            else:
                self.position += 4 * block_index
                new_pos = self.read_int()
            if new_pos > 0:
                self.position = index_table_pos + new_pos
                return True
            else:
                self.position = tmp
                return False
        else:
            self.position = tmp
            return False

    def read_buffer(self) -> 'ByteBuffer':
        count = self.read_int()
        ba = ByteBuffer(self._data, self.position, count)
        ba.string_table = self.string_table
        ba.version = self.version
        self.position += count
        return ba


class UIPackage:
    URL_PREFIX = "ui://"

    def __init__(self):
        self.id = ""
        self.name = ""
        self._items = []
        self._items_by_id = {}
        self._items_by_name = {}
        self._sprites = {}
        self._dependencies = []
        self._asset_path = ""
        self._branches = []
        self._branch_index = -1
        self.string_table = []

    def load_package(self, buffer: ByteBuffer, asset_name_prefix: str) -> bool:
        if buffer.read_uint() != 0x46475549:
            raise Exception(f"Invalid package format in '{asset_name_prefix}'")
        buffer.version = buffer.read_int()
        ver2 = buffer.version >= 2
        compressed = buffer.read_bool()
        self.id = buffer.read_string()
        self.name = buffer.read_string()
        buffer.skip(20)
        index_table_pos = buffer.position
        if buffer.seek(index_table_pos, 4):
            count = buffer.read_int()
            self.string_table = []
            for i in range(count):
                self.string_table.append(buffer.read_string())
            buffer.string_table = self.string_table
        if buffer.seek(index_table_pos, 0):
            count = buffer.read_short()
            self._dependencies = []
            for i in range(count):
                dep_id = buffer.read_s()
                dep_name = buffer.read_s()
                self._dependencies.append({"id": dep_id, "name": dep_name})
        branch_included = False
        if ver2 and buffer.seek(index_table_pos, 5):
            count = buffer.read_short()
            if count > 0:
                self._branches = buffer.read_s_array(count)
                branch_included = count > 0
        if buffer.seek(index_table_pos, 1):
            count = buffer.read_short()
            asset_path = os.path.dirname(asset_name_prefix)
            if asset_path:
                asset_path += "/"
            for i in range(count):
                next_pos = buffer.read_int() + buffer.position
                item = PackageItem()
                item.owner = self
                item.type = PackageItemType(buffer.read_byte())
                item.id = buffer.read_s()
                item.name = buffer.read_s()
                item.path = buffer.read_s()
                item.file = buffer.read_s()
                item.exported = buffer.read_bool()
                item.width = buffer.read_int()
                item.height = buffer.read_int()
                if item.type == PackageItemType.Image:
                    scale_option = buffer.read_byte()
                    if scale_option == 1:
                        rect = Rect()
                        rect.x = buffer.read_int()
                        rect.y = buffer.read_int()
                        rect.width = buffer.read_int()
                        rect.height = buffer.read_int()
                        item.scale9Grid = rect
                        item.tileGridIndice = buffer.read_int()
                    elif scale_option == 2:
                        item.scaleByTile = True
                    buffer.read_bool()
                elif item.type == PackageItemType.MovieClip:
                    buffer.read_bool()
                    item.rawData = buffer.read_buffer()
                elif item.type == PackageItemType.Font:
                    item.rawData = buffer.read_buffer()
                elif item.type in [PackageItemType.Atlas, PackageItemType.Sound, PackageItemType.Misc]:
                    item.file = asset_name_prefix + "_" + item.file
                if ver2:
                    branch_str = buffer.read_s()
                    if branch_str:
                        item.name = branch_str + "/" + item.name
                    branch_count = buffer.read_byte()
                    if branch_count > 0:
                        if branch_included:
                            item.branches = buffer.read_s_array(branch_count)
                    high_res_count = buffer.read_byte()
                    if high_res_count > 0:
                        item.highResolution = buffer.read_s_array(high_res_count)
                self._items.append(item)
                self._items_by_id[item.id] = item
                if item.name:
                    self._items_by_name[item.name] = item
                buffer.position = next_pos
        if buffer.seek(index_table_pos, 2):
            count = buffer.read_short()
            for i in range(count):
                next_pos = buffer.read_ushort() + buffer.position
                item_id = buffer.read_s()
                atlas_item_id = buffer.read_s()
                atlas_item = self._items_by_id.get(atlas_item_id)
                if atlas_item:
                    sprite = AtlasSprite()
                    sprite.atlas = atlas_item
                    sprite.rect.x = buffer.read_int()
                    sprite.rect.y = buffer.read_int()
                    sprite.rect.width = buffer.read_int()
                    sprite.rect.height = buffer.read_int()
                    sprite.rotated = buffer.read_bool()
                    if ver2 and buffer.read_bool():
                        sprite.offset.x = buffer.read_int()
                        sprite.offset.y = buffer.read_int()
                        sprite.originalSize.x = buffer.read_int()
                        sprite.originalSize.y = buffer.read_int()
                    elif sprite.rotated:
                        sprite.originalSize.x = sprite.rect.height
                        sprite.originalSize.y = sprite.rect.width
                    else:
                        sprite.originalSize.x = sprite.rect.width
                        sprite.originalSize.y = sprite.rect.height
                    self._sprites[item_id] = sprite
                buffer.position = next_pos
        return True

    def get_items(self) -> list:
        return self._items

    def get_item(self, item_id: str):
        return self._items_by_id.get(item_id)

    def get_item_by_name(self, item_name: str):
        return self._items_by_name.get(item_name)

    @property
    def sprites(self) -> dict:
        return self._sprites


class UIPackageTool:
    @staticmethod
    def split_atlas(byte_file: str, export_dir: str, is_override_exists: bool = True):
        base_name = os.path.splitext(os.path.basename(byte_file))[0]
        out_path = os.path.join(export_dir, base_name)
        os.makedirs(out_path, exist_ok=True)
        info_output_file = os.path.join(out_path, f"{base_name}_cut_info.json")
        cut_info = []
        with open(byte_file, 'rb') as f:
            source_data = f.read()
        buffer = ByteBuffer(source_data)
        file_dir = os.path.dirname(byte_file)
        pkg = UIPackage()
        main_asset_name = base_name
        pkg.load_package(buffer, main_asset_name)
        sprites = pkg.sprites
        atlas_map = {}
        for item in pkg.get_items():
            if item.type == PackageItemType.Atlas:
                atlas_file = item.file.replace("_fui", "")
                atlas_path = os.path.join(file_dir, atlas_file)
                if os.path.exists(atlas_path):
                    atlas_map[item.file] = Image.open(atlas_path).convert("RGBA")
        sprite_name_count = {}
        for sprite_id, sprite in sprites.items():
            item = pkg.get_item(sprite_id)
            if not item:
                continue
            name = item.name
            rect = sprite.rect
            rotated = sprite.rotated
            atlas_file = sprite.atlas.file if sprite.atlas else "unknown_atlas"
            output_file_name = f"{name}_{atlas_file}.png"
            if output_file_name in sprite_name_count:
                sprite_name_count[output_file_name] += 1
                output_file_name = f"{name}_{atlas_file}_{sprite_name_count[output_file_name]}.png"
            else:
                sprite_name_count[output_file_name] = 0
            output_path = os.path.join(out_path, output_file_name)
            if not is_override_exists and os.path.exists(output_path):
                continue
            atlas_key = sprite.atlas.file if sprite.atlas else None
            if not atlas_key or atlas_key not in atlas_map:
                continue
            atlas_img = atlas_map[atlas_key]
            x = int(rect.x)
            y = int(rect.y)
            width = int(rect.width)
            height = int(rect.height)
            atlas_width, atlas_height = atlas_img.size
            if x < 0 or y < 0 or x + width > atlas_width or y + height > atlas_height:
                print(f"Warning: Sprite {name} out of atlas bounds")
                continue
            sub_image = atlas_img.crop((x, y, x + width, y + height))
            if rotated:
                sub_image = sub_image.transpose(Image.ROTATE_90)
                sub_image = sub_image.transpose(Image.ROTATE_180)
            sub_image.save(output_path, "PNG")
            cut_info.append({
                "sprite_name": name,
                "atlas_file": atlas_key,
                "x": x, "y": y,
                "width": width, "height": height,
                "rotated": rotated,
                "output_file": os.path.basename(output_path)
            })
        with open(info_output_file, 'w', encoding='utf-8') as f:
            json.dump(cut_info, f, indent=4, ensure_ascii=False)