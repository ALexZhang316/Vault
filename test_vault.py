#!/usr/bin/env python3
"""
Vault 自动化测试
========================
构造测试数据，覆盖所有关键场景。
"""

import os
import sys
import json
import hashlib
import shutil
import subprocess
import tempfile
from unittest import mock
from pathlib import Path

import vault

SCRIPT = str(Path(__file__).parent / "vault.py")
PYTHON = sys.executable

# 测试根目录
TEST_ROOT = Path(tempfile.mkdtemp(prefix="ak_test_"))

passed = 0
failed = 0
test_num = 0


def run_cmd(command, archive_root=None, dry_run=False, expect_fail=False):
    """运行 vault 命令"""
    if archive_root is None:
        archive_root = TEST_ROOT
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    args = [PYTHON, SCRIPT, command, str(archive_root)]
    if dry_run:
        args.append("--dry-run")
    result = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", env=env)
    output = (result.stdout or "") + (result.stderr or "")
    if expect_fail and result.returncode != 0:
        return True, output
    if not expect_fail and result.returncode != 0:
        return False, output
    if expect_fail and result.returncode == 0:
        return False, output
    return True, output


def test(name, condition, detail=""):
    global passed, failed, test_num
    test_num += 1
    if condition:
        passed += 1
        print(f"  [通过] 测试 {test_num}: {name}")
    else:
        failed += 1
        print(f"  [失败] 测试 {test_num}: {name}")
        if detail:
            try:
                print(f"         {detail}")
            except UnicodeEncodeError:
                print("         (detail contains unrepresentable chars)")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main():
    global TEST_ROOT

    print(f"测试目录: {TEST_ROOT}")
    print("=" * 60)

    # ==========================================
    # 测试 1: 初始化
    # ==========================================
    print("\n--- 测试组 1: 初始化 ---")
    ok, out = run_cmd("init")
    test("init 命令成功", ok, out)
    test("master 目录存在", (TEST_ROOT / "master").is_dir())
    test("mirrors/mirror_1 目录存在", (TEST_ROOT / "mirrors" / "mirror_1").is_dir())
    test("mirrors/mirror_2 目录存在", (TEST_ROOT / "mirrors" / "mirror_2").is_dir())
    test("config.json 存在", (TEST_ROOT / ".archive" / "config.json").is_file())
    test("baseline.json 存在", (TEST_ROOT / ".archive" / "baseline.json").is_file())

    # ==========================================
    # 测试 2: 新建文件后同步
    # ==========================================
    print("\n--- 测试组 2: 新建文件 + 同步 ---")
    write_file(TEST_ROOT / "master" / "文档" / "readme.txt", "这是一份测试文档。\n版本1。")
    write_file(TEST_ROOT / "master" / "图片" / "photo.dat", "fake image data 12345")
    write_file(TEST_ROOT / "master" / "notes.txt", "笔记内容ABC")

    ok, out = run_cmd("sync")
    test("sync 命令成功", ok, out)
    test("镜像文件存在 readme.txt",
         (TEST_ROOT / "mirrors" / "mirror_1" / "文档" / "readme.txt").is_file())
    test("镜像文件存在 photo.dat",
         (TEST_ROOT / "mirrors" / "mirror_1" / "图片" / "photo.dat").is_file())
    test("镜像内容一致 notes.txt",
         read_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt") == "笔记内容ABC")
    test("第二份镜像内容一致 notes.txt",
         read_file(TEST_ROOT / "mirrors" / "mirror_2" / "notes.txt") == "笔记内容ABC")

    # 校验
    ok, out = run_cmd("verify")
    test("同步后校验通过", ok, out)

    # ==========================================
    # 测试 3: 修改文件后识别并更新
    # ==========================================
    print("\n--- 测试组 3: 修改文件 + 同步 ---")
    write_file(TEST_ROOT / "master" / "notes.txt", "笔记内容ABC — 已修改版本2")

    ok, out = run_cmd("sync")
    test("修改后 sync 成功", ok, out)
    test("镜像已更新",
         read_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt") == "笔记内容ABC — 已修改版本2")

    # ==========================================
    # 测试 4: 删除文件后流程安全
    # ==========================================
    print("\n--- 测试组 4: 删除保护 ---")
    os.unlink(TEST_ROOT / "master" / "图片" / "photo.dat")

    ok, out = run_cmd("sync")
    test("删除后 sync 成功（带警告）", ok)
    # 删除保护下，镜像中文件应仍然存在
    test("镜像中被删文件仍保留（删除保护）",
         (TEST_ROOT / "mirrors" / "mirror_1" / "图片" / "photo.dat").is_file())
    test("第二份镜像中被删文件仍保留（删除保护）",
         (TEST_ROOT / "mirrors" / "mirror_2" / "图片" / "photo.dat").is_file())
    ok, out = run_cmd("repair", dry_run=True)
    test("repair --dry-run 可预演清理镜像遗留文件", ok, out)
    test("预演不会删除镜像遗留文件",
         (TEST_ROOT / "mirrors" / "mirror_1" / "图片" / "photo.dat").is_file())
    ok, out = run_cmd("repair")
    test("repair 会清理镜像遗留文件", ok, out)
    test("镜像遗留文件已删除",
         not (TEST_ROOT / "mirrors" / "mirror_1" / "图片" / "photo.dat").exists())
    test("第二份镜像遗留文件已删除",
         not (TEST_ROOT / "mirrors" / "mirror_2" / "图片" / "photo.dat").exists())
    ok, out = run_cmd("verify")
    test("清理遗留文件后校验通过", ok, out)

    # ==========================================
    # 测试 5: 创建快照
    # ==========================================
    print("\n--- 测试组 5: 快照 ---")
    ok, out = run_cmd("snapshot")
    test("snapshot 命令成功", ok, out)
    snap_dirs = [d for d in (TEST_ROOT / "snapshots").iterdir() if d.is_dir()]
    test("快照目录已创建", len(snap_dirs) >= 1)
    if snap_dirs:
        snap = snap_dirs[-1]
        test("快照包含文件",
             (snap / "notes.txt").is_file())
        test("快照有独立基准",
             (snap / "_snapshot_baseline.json").is_file())

    # ==========================================
    # 测试 6: 破坏镜像文件内容 → 发现并修复
    # ==========================================
    print("\n--- 测试组 6: 单份损坏 → 发现 + 修复 ---")
    # 先确保同步干净
    ok, _ = run_cmd("sync")

    corrupted_file = TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt"
    write_file(corrupted_file, "CORRUPTED DATA !!!")

    ok, out = run_cmd("verify")
    test("损坏被发现（verify 报告异常）", not ok or "不一致" in out, out)

    # 预演修复
    ok, out = run_cmd("repair", dry_run=True)
    test("repair --dry-run 不报错", ok, out)
    # 预演后文件应仍是损坏的
    test("预演不修改文件",
         read_file(corrupted_file) == "CORRUPTED DATA !!!")

    # 实际修复
    ok, out = run_cmd("repair")
    test("repair 成功", ok, out)
    test("修复后内容正确",
         read_file(corrupted_file) == "笔记内容ABC — 已修改版本2")

    # ==========================================
    # 测试 7: 删除镜像文件 → 发现并修复
    # ==========================================
    print("\n--- 测试组 7: 镜像文件缺失 → 修复 ---")
    missing_file = TEST_ROOT / "mirrors" / "mirror_1" / "文档" / "readme.txt"
    if missing_file.exists():
        os.unlink(missing_file)

    ok, out = run_cmd("verify")
    test("缺失被发现", not ok or "缺失" in out, out)

    ok, out = run_cmd("repair")
    test("缺失修复成功", ok, out)
    test("文件已恢复", missing_file.is_file())

    # ==========================================
    # 测试 8: 冲突 → 无法自动修复
    # ==========================================
    print("\n--- 测试组 8: 冲突 → 停止并要求人工 ---")
    # 制造场景：创建一个新文件，只存在于主副本和镜像中（无基准无快照）
    # 然后让两个版本不同 → 只有2个源且不一致 → 无法判定
    write_file(TEST_ROOT / "master" / "conflict_test.txt", "版本A")
    write_file(TEST_ROOT / "mirrors" / "mirror_1" / "conflict_test.txt", "版本B")
    # 不做 sync 所以基准中没有这个文件

    ok, out = run_cmd("repair")
    # 此时应该有 "人工" 相关提示 或 warn
    has_manual_hint = ("人工" in out or "无法自动" in out or "警告" in out)
    test("冲突时提示人工介入", has_manual_hint, out[:500])

    # 清理冲突测试文件
    for p in [TEST_ROOT / "master" / "conflict_test.txt",
              TEST_ROOT / "mirrors" / "mirror_1" / "conflict_test.txt"]:
        if p.exists():
            p.unlink()

    # ==========================================
    # 测试 9: 快照可用于恢复判定
    # ==========================================
    print("\n--- 测试组 9: 快照辅助恢复 ---")
    # 恢复到干净状态
    write_file(TEST_ROOT / "master" / "notes.txt", "笔记内容ABC — 已修改版本2")
    run_cmd("sync")

    # 再次快照
    run_cmd("snapshot")

    # 现在破坏主副本和一个镜像，保留另一份镜像正确
    write_file(TEST_ROOT / "master" / "notes.txt", "错误内容Z")
    write_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt", "错误内容Z")

    ok, out = run_cmd("repair")
    test("历史证据支持时 repair 成功", ok, out)
    test("主副本已恢复到历史支持版本",
         read_file(TEST_ROOT / "master" / "notes.txt") == "笔记内容ABC — 已修改版本2")
    test("损坏镜像已恢复到历史支持版本",
         read_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt") == "笔记内容ABC — 已修改版本2")
    test("未损坏镜像保持正确版本",
         read_file(TEST_ROOT / "mirrors" / "mirror_2" / "notes.txt") == "笔记内容ABC — 已修改版本2")

    # ==========================================
    # 测试 10: 当前链整体一致但与历史冲突 → 必须人工确认
    # ==========================================
    print("\n--- 测试组 10: 当前链冲突需人工确认 ---")
    write_file(TEST_ROOT / "master" / "notes.txt", "整体错误版本Q")
    write_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt", "整体错误版本Q")
    write_file(TEST_ROOT / "mirrors" / "mirror_2" / "notes.txt", "整体错误版本Q")

    ok, out = run_cmd("repair")
    has_manual_hint = ("人工" in out or "无法自动" in out or "警告" in out)
    test("当前链整体冲突时提示人工介入", has_manual_hint, out[:500])
    test("人工介入场景不会自动改写当前链",
         read_file(TEST_ROOT / "master" / "notes.txt") == "整体错误版本Q")
    test("人工介入场景不会自动改写镜像",
         read_file(TEST_ROOT / "mirrors" / "mirror_1" / "notes.txt") == "整体错误版本Q")

    # ==========================================
    # 测试 11: 统一维护流程
    # ==========================================
    print("\n--- 测试组 11: 统一维护 maintain ---")
    # 先恢复干净
    write_file(TEST_ROOT / "master" / "notes.txt", "维护测试最终版本")
    ok, out = run_cmd("maintain")
    test("maintain 命令完成", ok, out)
    test("maintain 输出包含汇总", "汇总" in out or "总体状态" in out, out[:500])

    # ==========================================
    # 测试 12: 幂等性 — 重复运行稳定
    # ==========================================
    print("\n--- 测试组 12: 幂等性 ---")
    # 先清理：删除镜像中多余的 photo.dat（删除保护遗留）
    leftover = TEST_ROOT / "mirrors" / "mirror_1" / "图片" / "photo.dat"
    if leftover.exists():
        leftover.unlink()
    # 确保干净状态
    run_cmd("sync")
    ok1, out1 = run_cmd("maintain")
    # Debug: dump output to file
    Path(TEST_ROOT / "debug_maintain1.txt").write_text(out1, encoding="utf-8")
    ok2, out2 = run_cmd("maintain")
    test("重复 maintain 第一次成功", ok1, f"exit_code issue, see {TEST_ROOT}/debug_maintain1.txt")
    test("重复 maintain 第二次成功", ok2)
    # 第二次运行后文件内容没变
    test("重复运行后内容不变",
         read_file(TEST_ROOT / "master" / "notes.txt") == "维护测试最终版本")

    # ==========================================
    # 测试 13: 预演模式
    # ==========================================
    print("\n--- 测试组 13: 预演模式 ---")
    ok, out = run_cmd("maintain", dry_run=True)
    test("maintain --dry-run 成功", ok, out)
    test("预演输出包含标识", "预演" in out, out[:300])

    # ==========================================
    # 测试 14: status
    # ==========================================
    print("\n--- 测试组 14: status ---")
    ok, out = run_cmd("status")
    test("status 命令成功", ok, out)
    test("status 显示文件数", "个文件" in out, out[:300])

    # ==========================================
    # 测试 15: hash_algorithm 配置生效
    # ==========================================
    print("\n--- 测试组 15: hash_algorithm 配置 ---")
    algo_root = TEST_ROOT / "_algo_case"
    ok, out = run_cmd("init", archive_root=algo_root)
    test("独立归档根初始化成功", ok, out)

    config_path = algo_root / ".archive" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["hash_algorithm"] = "blake2b"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    algo_text = "hash algorithm test"
    write_file(algo_root / "master" / "algo.txt", algo_text)
    ok, out = run_cmd("sync", archive_root=algo_root)
    test("切换到 blake2b 后 sync 成功", ok, out)

    baseline = json.loads((algo_root / ".archive" / "baseline.json").read_text(encoding="utf-8"))
    expected_hash = hashlib.new("blake2b", algo_text.encode("utf-8")).hexdigest()
    test("baseline 记录的算法是 blake2b", baseline.get("algorithm") == "blake2b")
    test("baseline 中的哈希值按 blake2b 生成",
         baseline["files"]["algo.txt"]["hash"] == expected_hash)

    ok, out = run_cmd("verify", archive_root=algo_root)
    test("blake2b 配置下 verify 通过", ok, out)

    # ==========================================
    # 测试 16: baseline 保存采用原子替换
    # ==========================================
    print("\n--- 测试组 16: baseline 原子替换 ---")
    atomic_root = TEST_ROOT / "_atomic_case"
    ok, out = run_cmd("init", archive_root=atomic_root)
    test("原子替换用例初始化成功", ok, out)

    atomic_baseline = {
        "created": "2026-03-26T14:00:00",
        "algorithm": "sha256",
        "files": {
            "atomic.txt": {
                "hash": "abc123",
                "size": 7,
            }
        },
    }
    with mock.patch("pathlib.Path.unlink", side_effect=AssertionError("save_baseline should not unlink the existing file")):
        vault.save_baseline(atomic_root, atomic_baseline)

    saved_baseline = json.loads((atomic_root / ".archive" / "baseline.json").read_text(encoding="utf-8"))
    test("save_baseline 不依赖先删除旧文件", saved_baseline == atomic_baseline)

    # ==========================================
    # 汇总
    # ==========================================
    print("\n" + "=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败, 共 {test_num} 项")
    print(f"测试目录: {TEST_ROOT}")

    if failed > 0:
        print("\n! 存在失败测试，请检查。")
        sys.exit(1)
    else:
        print("\n* 全部测试通过。")

    # 清理
    try:
        # 去掉只读属性再删
        for root, dirs, files in os.walk(TEST_ROOT):
            for f in files:
                fp = Path(root) / f
                try:
                    import stat
                    os.chmod(str(fp), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        shutil.rmtree(str(TEST_ROOT), ignore_errors=True)
        print("测试目录已清理。")
    except Exception:
        print(f"请手动清理测试目录: {TEST_ROOT}")


if __name__ == "__main__":
    main()
