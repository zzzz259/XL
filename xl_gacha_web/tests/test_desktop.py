import tempfile
import unittest
from pathlib import Path

from desktop import make_server


class DesktopTests(unittest.TestCase):
    def test_occupied_port_gets_a_different_port_instead_of_sharing_listener(self):
        with tempfile.TemporaryDirectory() as folder:
            first = make_server(Path(folder) / "first", 0)
            try:
                second = make_server(Path(folder) / "second", first.server_port)
                try:
                    self.assertNotEqual(first.server_port, second.server_port)
                finally:
                    second.server_close()
            finally:
                first.server_close()


if __name__ == "__main__":
    unittest.main()
