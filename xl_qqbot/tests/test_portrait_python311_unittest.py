"""Supported-interpreter local multipart intake and link refusal."""

import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if __package__:
    from . import test_portrait_multipart_unittest as fixtures
else:
    import test_portrait_multipart_unittest as fixtures


class Python311MultipartTests(fixtures.PortraitMultipartFixture):
    def test_valid_intake_without_path_junction_api(self):
        # Python 3.11 has no callable pathlib junction predicate.
        with patch.object(Path, "is_junction", None, create=True):
            fixtures.PortraitMultipartTests.test_explicit_root_and_hash_wire(self)

    def test_mount_point_reparse_tag_rejected_without_path_api(self):
        from bot_app.local_image import resolve_artifact

        with (
            patch.object(Path, "is_junction", None, create=True),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(
                Path,
                "lstat",
                return_value=SimpleNamespace(
                    st_reparse_tag=getattr(
                        stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
                    )
                ),
            ),
        ):
            try:
                with self.assertRaisesRegex(ValueError, "cannot contain links"):
                    resolve_artifact(
                        {"game": self.root},
                        "game",
                        f"artifacts/{self.sha}.png",
                        self.sha,
                    )
            except TypeError as error:
                self.fail(f"unsupported pathlib API escaped link refusal: {error}")


if __name__ == "__main__":
    unittest.main()
