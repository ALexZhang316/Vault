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
    # 显式同步已确认的正常修改，再执行日常维护
    write_file(TEST_ROOT / "master" / "notes.txt", "维护测试最终版本")
    run_cmd("sync")
    maintain_snapshots_before = len(vault.list_published_snapshots(TEST_ROOT))
    ok, out = run_cmd("maintain")
    test("maintain 命令完成", ok, out)
    test("maintain 输出包含汇总", "汇总" in out or "总体状态" in out, out[:500])
    test("maintain 完整执行安全门和四个步骤",
         all(marker in out for marker in [
             "【安全门】", "【步骤 1/4】", "【步骤 2/4】",
             "【步骤 3/4】", "【步骤 4/4】",
         ]), out[:1000])
    test("maintain 发布一个完整快照",
         len(vault.list_published_snapshots(TEST_ROOT)) == maintain_snapshots_before + 1)
    maintained_baseline = json.loads(
        (TEST_ROOT / ".archive" / "baseline.json").read_text(encoding="utf-8")
    )
    maintained_hash = hashlib.sha256("维护测试最终版本".encode("utf-8")).hexdigest()
    test("maintain 后 master、镜像和 baseline 一致",
         maintained_baseline["files"]["notes.txt"]["hash"] == maintained_hash
         and all(read_file(TEST_ROOT / "mirrors" / name / "notes.txt") == "维护测试最终版本"
                 for name in ["mirror_1", "mirror_2"]))

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
    real_replace = os.replace
    with mock.patch(
        "pathlib.Path.unlink",
        side_effect=AssertionError("save_baseline should not unlink the existing file"),
    ), mock.patch("vault.os.replace", wraps=real_replace) as replace_mock:
        vault.save_baseline(atomic_root, atomic_baseline)

    saved_baseline = json.loads((atomic_root / ".archive" / "baseline.json").read_text(encoding="utf-8"))
    replace_source, replace_target = map(Path, replace_mock.call_args.args)
    replacement_baseline = {**atomic_baseline, "updated": "2026-03-26T14:01:00"}
    replace_failed = False
    try:
        with mock.patch("vault.os.replace", side_effect=OSError("injected baseline replace failure")):
            vault.save_baseline(atomic_root, replacement_baseline)
    except OSError:
        replace_failed = True

    test("save_baseline 不依赖先删除旧文件",
         saved_baseline == atomic_baseline
         and replace_mock.call_count == 1
         and replace_source.parent == replace_target.parent
         and replace_source == replace_target.with_suffix(".tmp")
         and replace_target.name == "baseline.json"
         and replace_failed
         and json.loads(replace_target.read_text(encoding="utf-8")) == atomic_baseline
         and not replace_source.exists())

    # ==========================================
    # 测试 17: maintain 不传播 master 删除
    # ==========================================
    print("\n--- 测试组 17: maintain 删除安全门 ---")
    delete_root = TEST_ROOT / "_maintain_delete_case"
    run_cmd("init", archive_root=delete_root)
    write_file(delete_root / "master" / "protected.txt", "GOOD DATA")
    write_file(delete_root / "master" / "stable.txt", "STABLE DATA")
    run_cmd("sync", archive_root=delete_root)
    baseline_before = (delete_root / ".archive" / "baseline.json").read_bytes()
    snapshots_before = {d.name for d in (delete_root / "snapshots").iterdir() if d.is_dir()}

    (delete_root / "master" / "protected.txt").unlink()
    ok, out = run_cmd("maintain", archive_root=delete_root, expect_fail=True)
    test("master 删除后 maintain 会停止", ok, out)
    test("删除安全门给出停止说明", "维护已停止" in out, out[:500])
    test("maintain 不删除两份正常镜像",
         all(read_file(delete_root / "mirrors" / name / "protected.txt") == "GOOD DATA"
             for name in ["mirror_1", "mirror_2"]))
    test("maintain 不移除删除文件的 baseline 记录",
         (delete_root / ".archive" / "baseline.json").read_bytes() == baseline_before)
    snapshots_after = {d.name for d in (delete_root / "snapshots").iterdir() if d.is_dir()}
    test("删除安全门前不创建快照", snapshots_after == snapshots_before)

    run_cmd("sync", archive_root=delete_root)
    ok, out = run_cmd("maintain", archive_root=delete_root)
    test("显式 sync 后 maintain 仍不自动清理镜像", ok, out)
    test("maintain 的警告流程继续保留两份镜像",
         all(read_file(delete_root / "mirrors" / name / "protected.txt") == "GOOD DATA"
             for name in ["mirror_1", "mirror_2"]))
    test("镜像清理要求显式 repair", "显式运行 repair --dry-run" in out, out[:500])
    test("删除待确认流程汇总为警告", "总体状态: 警告" in out, out[:1000])

    real_cmd_sync = vault.cmd_sync

    def sync_then_inject_mirror_error(archive_root, log, dry_run=False):
        status = real_cmd_sync(archive_root, log, dry_run)
        write_file(archive_root / "mirrors" / "mirror_1" / "stable.txt", "BROKEN MIRROR")
        return status

    conditional_log = vault.ArchiveLogger(delete_root, "maintain_conditional_error")
    with mock.patch("vault.cmd_sync", side_effect=sync_then_inject_mirror_error):
        conditional_status = vault.cmd_maintain(delete_root, conditional_log)

    test("其他严重问题触发内部 repair 时仍不清理 orphan",
         conditional_status == vault.STATUS_ERROR
         and all(read_file(delete_root / "mirrors" / name / "protected.txt") == "GOOD DATA"
                 for name in ["mirror_1", "mirror_2"]))
    test("内部 repair 仍会修复非删除类问题",
         read_file(delete_root / "mirrors" / "mirror_1" / "stable.txt") == "STABLE DATA")

    # ==========================================
    # 测试 18: maintain 不传播 master 静默损坏
    # ==========================================
    print("\n--- 测试组 18: maintain 损坏安全门 ---")
    corrupt_root = TEST_ROOT / "_maintain_corrupt_case"
    run_cmd("init", archive_root=corrupt_root)
    write_file(corrupt_root / "master" / "tracked.txt", "KNOWN GOOD")
    run_cmd("sync", archive_root=corrupt_root)
    corrupt_baseline_before = (corrupt_root / ".archive" / "baseline.json").read_bytes()
    corrupt_snapshots_before = {
        d.name for d in (corrupt_root / "snapshots").iterdir() if d.is_dir()
    }

    write_file(corrupt_root / "master" / "tracked.txt", "SILENT CORRUPTION")
    ok, out = run_cmd("maintain", archive_root=corrupt_root, expect_fail=True)
    test("master 内容异常后 maintain 会停止", ok, out)
    test("损坏不会覆盖两份正常镜像",
         all(read_file(corrupt_root / "mirrors" / name / "tracked.txt") == "KNOWN GOOD"
             for name in ["mirror_1", "mirror_2"]))
    test("损坏不会更新 baseline",
         (corrupt_root / ".archive" / "baseline.json").read_bytes() == corrupt_baseline_before)
    corrupt_snapshots_after = {
        d.name for d in (corrupt_root / "snapshots").iterdir() if d.is_dir()
    }
    test("损坏内容不会生成新快照", corrupt_snapshots_after == corrupt_snapshots_before)
    test("损坏场景不会报告全部通过", "总体状态: 通过" not in out, out[:500])

    # ==========================================
    # 测试 19: 失败快照不发布、不淘汰
    # ==========================================
    print("\n--- 测试组 19: 快照原子发布 ---")
    snapshot_root = TEST_ROOT / "_snapshot_atomic_case"
    run_cmd("init", archive_root=snapshot_root)
    snapshot_config_path = snapshot_root / ".archive" / "config.json"
    snapshot_config = json.loads(snapshot_config_path.read_text(encoding="utf-8"))
    snapshot_config["max_snapshots"] = 1
    snapshot_config["snapshot_readonly"] = False
    snapshot_config_path.write_text(
        json.dumps(snapshot_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_file(snapshot_root / "master" / "good.txt", "GOOD")
    write_file(snapshot_root / "master" / "fail.txt", "COMPLETE CONTENT")

    initial_log = vault.ArchiveLogger(snapshot_root, "snapshot_initial")
    initial_status = vault.cmd_snapshot(snapshot_root, initial_log)
    original_snapshots = vault.list_published_snapshots(snapshot_root)
    test("初始正常快照创建成功",
         initial_status == vault.STATUS_OK and len(original_snapshots) == 1)
    original_snapshot = original_snapshots[0]
    original_copy2 = vault.shutil.copy2

    def visible_snapshot_entries():
        return {
            path.name for path in (snapshot_root / "snapshots").iterdir()
            if not path.name.startswith(".")
        }

    original_visible_entries = visible_snapshot_entries()

    def fail_one_snapshot_copy(src, dst):
        if Path(src).name == "fail.txt":
            raise OSError("injected snapshot copy failure")
        return original_copy2(src, dst)

    failed_log = vault.ArchiveLogger(snapshot_root, "snapshot_copy_failure")
    with mock.patch("vault.shutil.copy2", side_effect=fail_one_snapshot_copy):
        failed_status = vault.cmd_snapshot(snapshot_root, failed_log)

    test("快照复制异常返回需要处理", failed_status == vault.STATUS_ERROR)
    test("复制异常不发布半快照",
         visible_snapshot_entries() == original_visible_entries
         and vault.list_published_snapshots(snapshot_root) == [original_snapshot])
    test("复制异常不淘汰旧正常快照", original_snapshot.is_dir())
    test("复制异常会清理暂存目录",
         not any(d.name.startswith(vault.SNAPSHOT_STAGING_PREFIX)
                 for d in (snapshot_root / "snapshots").iterdir() if d.is_dir()))

    def truncate_one_snapshot_copy(src, dst):
        if Path(src).name == "fail.txt":
            Path(dst).write_bytes(b"truncated")
            return str(dst)
        return original_copy2(src, dst)

    truncated_log = vault.ArchiveLogger(snapshot_root, "snapshot_truncated_copy")
    with mock.patch("vault.shutil.copy2", side_effect=truncate_one_snapshot_copy):
        truncated_status = vault.cmd_snapshot(snapshot_root, truncated_log)

    test("快照静默截断会被哈希校验阻止", truncated_status == vault.STATUS_ERROR)
    test("静默截断后旧正常快照仍保留",
         visible_snapshot_entries() == original_visible_entries
         and vault.list_published_snapshots(snapshot_root) == [original_snapshot])

    manifest_log = vault.ArchiveLogger(snapshot_root, "snapshot_manifest_failure")
    with mock.patch("vault.os.fsync", side_effect=OSError("injected manifest fsync failure")):
        manifest_status = vault.cmd_snapshot(snapshot_root, manifest_log)

    test("快照清单落盘失败会阻止发布", manifest_status == vault.STATUS_ERROR)
    test("清单落盘失败不新增公开目录且不淘汰旧快照",
         visible_snapshot_entries() == original_visible_entries
         and original_snapshot.is_dir()
         and not any(path.name.startswith(vault.SNAPSHOT_STAGING_PREFIX)
                     for path in (snapshot_root / "snapshots").iterdir()))

    publish_log = vault.ArchiveLogger(snapshot_root, "snapshot_publish_failure")
    with mock.patch("vault.os.replace", side_effect=OSError("injected snapshot publish failure")):
        publish_status = vault.cmd_snapshot(snapshot_root, publish_log)

    test("快照原子发布失败会返回需要处理", publish_status == vault.STATUS_ERROR)
    test("原子发布失败不新增公开目录且不淘汰旧快照",
         visible_snapshot_entries() == original_visible_entries
         and original_snapshot.is_dir()
         and not any(path.name.startswith(vault.SNAPSHOT_STAGING_PREFIX)
                     for path in (snapshot_root / "snapshots").iterdir()))

    def walk_with_access_error(top, *args, **kwargs):
        kwargs["onerror"](PermissionError("injected directory enumeration failure"))
        return iter(())

    scan_log = vault.ArchiveLogger(snapshot_root, "snapshot_scan_failure")
    with mock.patch("vault.os.walk", side_effect=walk_with_access_error):
        scan_status = vault.cmd_snapshot(snapshot_root, scan_log)

    test("主副本目录枚举失败会阻止快照", scan_status == vault.STATUS_ERROR)
    test("目录枚举失败不发布快照也不淘汰旧快照",
         visible_snapshot_entries() == original_visible_entries
         and original_snapshot.is_dir())

    replacement_log = vault.ArchiveLogger(snapshot_root, "snapshot_replacement")
    replacement_status = vault.cmd_snapshot(snapshot_root, replacement_log)
    replacement_snapshots = vault.list_published_snapshots(snapshot_root)
    test("完整新快照成功后才执行淘汰",
         replacement_status == vault.STATUS_OK
         and len(replacement_snapshots) == 1
         and replacement_snapshots[0] != original_snapshot
         and not original_snapshot.exists())
    test("发布快照的 baseline 与实际文件完整匹配",
         vault.load_valid_snapshot_baseline(
             replacement_snapshots[0],
             expected_algorithm="sha256",
             verify_hashes=True,
         ) is not None)

    # ==========================================
    # 测试 20: 同步失败不推进 baseline
    # ==========================================
    print("\n--- 测试组 20: 同步提交边界 ---")
    sync_failure_root = TEST_ROOT / "_sync_failure_case"
    run_cmd("init", archive_root=sync_failure_root)
    write_file(sync_failure_root / "master" / "tracked.txt", "VERSION 1")
    run_cmd("sync", archive_root=sync_failure_root)
    sync_baseline_before = (sync_failure_root / ".archive" / "baseline.json").read_bytes()
    write_file(sync_failure_root / "master" / "tracked.txt", "VERSION 2")

    def fail_second_mirror(src, dst):
        if "mirror_2" in Path(dst).parts:
            raise OSError("injected mirror copy failure")
        return original_copy2(src, dst)

    sync_failure_log = vault.ArchiveLogger(sync_failure_root, "sync_failure")
    with mock.patch("vault.shutil.copy2", side_effect=fail_second_mirror):
        sync_failure_status = vault.cmd_sync(sync_failure_root, sync_failure_log)

    test("任一镜像同步失败会返回需要处理", sync_failure_status == vault.STATUS_ERROR)
    test("任一镜像同步失败时 baseline 保持不变",
         (sync_failure_root / ".archive" / "baseline.json").read_bytes() == sync_baseline_before)

    # ==========================================
    # 测试 21: baseline 缺失或损坏时安全停止
    # ==========================================
    print("\n--- 测试组 21: baseline 完整性门禁 ---")
    missing_baseline_root = TEST_ROOT / "_missing_baseline_case"
    run_cmd("init", archive_root=missing_baseline_root)
    write_file(missing_baseline_root / "master" / "tracked.txt", "KNOWN GOOD")
    run_cmd("sync", archive_root=missing_baseline_root)
    missing_baseline_path = missing_baseline_root / ".archive" / "baseline.json"
    missing_snapshots_before = {
        path.name for path in (missing_baseline_root / "snapshots").iterdir()
    }
    missing_baseline_path.unlink()
    write_file(missing_baseline_root / "master" / "tracked.txt", "CORRUPTED")

    ok, out = run_cmd("maintain", archive_root=missing_baseline_root, expect_fail=True)
    test("baseline 缺失时 maintain 会停止", ok and "校验基准不可用" in out, out[:1000])
    test("baseline 缺失时损坏不会传播到镜像",
         all(read_file(missing_baseline_root / "mirrors" / name / "tracked.txt") == "KNOWN GOOD"
             for name in ["mirror_1", "mirror_2"]))
    test("baseline 缺失时不创建快照也不重建基准",
         not missing_baseline_path.exists()
         and {path.name for path in (missing_baseline_root / "snapshots").iterdir()}
         == missing_snapshots_before)

    (missing_baseline_root / "master" / "tracked.txt").unlink()
    ok, out = run_cmd("repair", archive_root=missing_baseline_root, expect_fail=True)
    test("baseline 缺失时 repair 不会把镜像误判为 orphan",
         ok and all((missing_baseline_root / "mirrors" / name / "tracked.txt").is_file()
                    for name in ["mirror_1", "mirror_2"]), out[:1000])

    missing_baseline_path.write_text(
        json.dumps({"algorithm": "sha256"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ok, out = run_cmd("maintain", archive_root=missing_baseline_root, expect_fail=True)
    test("baseline schema 不完整时 maintain 同样停止",
         ok and "缺少 files" in out
         and all((missing_baseline_root / "mirrors" / name / "tracked.txt").is_file()
                 for name in ["mirror_1", "mirror_2"]), out[:1000])

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
