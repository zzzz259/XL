"""Identity paths for selected Unity assets whose export names collide."""
import re
from pathlib import PurePosixPath


def identity_output_path(asset):
    name = PurePosixPath(asset.name.replace('\\', '/'))
    if (not re.fullmatch(r'[A-Za-z0-9_-]+', asset.bundle_hash)
            or name.is_absolute() or '..' in name.parts or len(name.parts) != 1
            or ':' in str(name) or str(name) in {'', '.'}):
        raise ValueError('unsafe exact Unity asset identity path')
    if asset.asset_type.casefold() == 'texture2d' and name.suffix.casefold() != '.png':
        name = PurePosixPath(str(name) + '.png')
    return PurePosixPath('_portrait_inputs') / asset.bundle_hash / str(asset.path_id) / name
