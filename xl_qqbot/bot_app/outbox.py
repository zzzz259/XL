import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CharacterImage:
    id: str
    name: str
    file_name: str
    file_path: str
    version_dir: str


@dataclass(frozen=True)
class VersionBatch:
    version: str
    version_dir: str
    generated_at: str
    images: List[CharacterImage]


def list_version_batches(outbox_dir: str) -> List[VersionBatch]:
    if not os.path.isdir(outbox_dir):
        return []
    batches: List[VersionBatch] = []
    for name in sorted(os.listdir(outbox_dir)):
        path = os.path.join(outbox_dir, name)
        if not os.path.isdir(path):
            continue
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.exists(manifest_path):
            continue
        batch = _parse_version_batch(path, manifest_path)
        if batch and batch.images:
            batches.append(batch)
    return batches


def _parse_version_batch(version_dir: str, manifest_path: str) -> Optional[VersionBatch]:
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    version = str(manifest.get("version") or os.path.basename(version_dir))
    generated_at = str(manifest.get("generated_at", ""))
    images: List[CharacterImage] = []

    entries = manifest.get("characters", manifest.get("images", []))
    for item in entries:
        file_name = item.get("file_name") or item.get("filename")
        if not file_name:
            continue
        file_path = os.path.join(version_dir, file_name)
        if not os.path.exists(file_path):
            continue
        images.append(
            CharacterImage(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                file_name=file_name,
                file_path=file_path,
                version_dir=version_dir,
            )
        )
    return VersionBatch(
        version=version,
        version_dir=version_dir,
        generated_at=generated_at,
        images=images,
    )


def is_image_done(version_dir: str, file_name: str) -> bool:
    return os.path.exists(os.path.join(version_dir, f"{file_name}.done"))


def mark_image_done(version_dir: str, file_name: str) -> None:
    done_path = os.path.join(version_dir, f"{file_name}.done")
    open(done_path, "w", encoding="utf-8").close()


def is_batch_complete(batch: VersionBatch) -> bool:
    if not batch.images:
        return True
    return all(is_image_done(batch.version_dir, img.file_name) for img in batch.images)


class SentRecordStore:
    def __init__(self, data_dir: str):
        self.data_dir = os.path.join(data_dir, "sent")
        os.makedirs(self.data_dir, exist_ok=True)

    def version_dir(self, version: str) -> str:
        return os.path.join(self.data_dir, version)

    def ensure_version_dir(self, version: str) -> str:
        path = self.version_dir(version)
        os.makedirs(path, exist_ok=True)
        return path

    def save_manifest(self, version: str, manifest: Dict) -> None:
        path = self.ensure_version_dir(version)
        with open(os.path.join(path, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def load_status(self, version: str) -> Dict:
        path = os.path.join(self.version_dir(version), "status.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def save_status(self, version: str, status: Dict) -> None:
        path = self.ensure_version_dir(version)
        with open(os.path.join(path, "status.json"), "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    def is_sent_to_group(self, version: str, file_name: str, group_openid: str) -> bool:
        status = self.load_status(version)
        return bool(status.get(file_name, {}).get(group_openid, False))

    def mark_sent_to_group(self, version: str, file_name: str, group_openid: str) -> None:
        status = self.load_status(version)
        file_status = status.setdefault(file_name, {})
        file_status[group_openid] = True
        self.save_status(version, status)
