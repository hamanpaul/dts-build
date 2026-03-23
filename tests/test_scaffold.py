from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dtsbuild.manifest import load_manifest
from dtsbuild.scaffold import init_folder


class ScaffoldTest(unittest.TestCase):
    def test_init_folder_creates_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            folder = init_folder(
                root=root,
                project="MyBoard",
                profile="968375GWO_WL25DX_WLMLO",
                refboard="968375GO",
                family="bcm68575",
            )

            self.assertTrue((folder / "manifest.yaml").exists())
            self.assertTrue((folder / "tables" / "blockdiag.csv").exists())
            self.assertTrue((folder / "tables" / "ddr.csv").exists())
            self.assertTrue((folder / "tables" / "gpio_led.csv").exists())
            self.assertTrue((folder / "tables" / "network.csv").exists())
            self.assertTrue((root / "dtsout_MyBoard").exists())

            manifest = load_manifest(folder)
            self.assertEqual(manifest.project, "MyBoard")
            self.assertEqual(manifest.family, "bcm68575")
            self.assertEqual(manifest.profile, "968375GWO_WL25DX_WLMLO")
            self.assertEqual(manifest.output_dir, "dtsout_MyBoard")


if __name__ == "__main__":
    unittest.main()
