"""pet.pyw 里安装 Claude Code 插件的部分：装上、更新、删掉改名前的旧插件。

全在临时文件夹里做，不碰真的 ~/.claude。pet.pyw 要导入 PyQt5，没装时这组测试跳过。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

try:
    import PyQt5  # noqa: F401
except ImportError:
    pet = None
else:
    # pet.pyw 不是 .py，按文件导入；不是 __main__，所以不会启动桌宠。
    loader = SourceFileLoader("pet", str(HERE / "pet.pyw"))
    pet = importlib.util.module_from_spec(importlib.util.spec_from_loader("pet", loader))
    loader.exec_module(pet)


@unittest.skipIf(pet is None, "没装 PyQt5")
class PluginInstall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.skills = Path(self.dir.name) / "skills"
        self.src = HERE / "claude-plugin"
        self.dst = self.skills / "musume-bridge"
        self.old = self.skills / pet.OLD_PLUGIN

    def tearDown(self):
        self.dir.cleanup()

    def status(self) -> str:
        return pet.plugin_status(self.src, self.dst)

    def install(self) -> str:
        return pet.install_plugin(self.src, self.dst)

    def make_old(self, name: str | None = None) -> None:
        """照 0.1 版的样子摆一份旧插件：三个文件，plugin.json 里写着它的名字。"""
        for rel in pet.PLUGIN_FILES:
            (self.old / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.old / rel).write_text("旧", encoding="utf-8")
        (self.old / pet.PLUGIN_FILES[0]).write_text(json.dumps({"name": name or pet.OLD_PLUGIN, "version": "0.1.0"}), encoding="utf-8")

    def test_bundled_plugin_matches_the_install_folder_name(self):
        data = json.loads((self.src / pet.PLUGIN_FILES[0]).read_text(encoding="utf-8"))
        self.assertEqual(data["name"], pet.PLUGIN_DST.name)
        self.assertNotEqual(data["name"], pet.OLD_PLUGIN)

    def test_install_then_update(self):
        self.assertEqual(self.status(), "missing")
        self.assertTrue(self.install().startswith("插件装好了"))
        self.assertEqual(self.status(), "current")
        for rel in pet.PLUGIN_FILES:
            self.assertEqual((self.dst / rel).read_bytes(), (self.src / rel).read_bytes())
        self.assertEqual(self.install(), "插件已经是最新的了。")

        (self.dst / "hooks" / "register.ts").write_text("// 旧版", encoding="utf-8")
        self.assertEqual(self.status(), "outdated")
        self.assertTrue(self.install().startswith("插件更新好了"))
        self.assertEqual(self.status(), "current")

    def test_install_removes_the_renamed_old_plugin(self):
        self.make_old()
        self.install()
        self.assertFalse(self.old.exists())
        self.assertEqual(self.status(), "current")

    def test_old_plugin_stays_until_the_new_one_is_current(self):
        self.make_old()
        self.assertFalse(pet.remove_old_plugin(self.src, self.dst))  # 新的还没装
        self.assertTrue(self.old.exists())
        self.install()
        (self.dst / "hooks" / "register.ts").write_text("// 改过", encoding="utf-8")
        self.make_old()
        self.assertFalse(pet.remove_old_plugin(self.src, self.dst))  # 新的不是最新的
        self.assertTrue(self.old.exists())

    def test_leaves_folders_that_are_not_the_old_plugin(self):
        self.install()
        self.make_old(name="someone-else")
        self.assertFalse(pet.remove_old_plugin(self.src, self.dst))
        self.assertTrue(self.old.exists())
        shutil.rmtree(self.old)
        self.old.mkdir()  # 没有 plugin.json
        self.assertFalse(pet.remove_old_plugin(self.src, self.dst))
        self.assertTrue(self.old.exists())

    def test_does_not_follow_a_link(self):
        self.install()
        target = Path(self.dir.name) / "elsewhere"
        self.make_old()
        self.old.rename(target)
        try:
            os.symlink(target, self.old, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("这台电脑上建不了符号链接")
        self.assertFalse(pet.remove_old_plugin(self.src, self.dst))
        self.assertTrue((target / pet.PLUGIN_FILES[0]).exists())

    def test_does_not_install_into_a_link(self):
        target = Path(self.dir.name) / "elsewhere"
        target.mkdir()
        self.skills.mkdir()
        try:
            os.symlink(target, self.dst, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("这台电脑上建不了符号链接")
        self.assertEqual(self.status(), "blocked")
        self.assertIn("是一个链接", self.install())
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
