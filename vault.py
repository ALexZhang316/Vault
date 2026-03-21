#!/usr/bin/env python3
"""
Vault — 单盘多副本归档维护工具
========================================
纯 Python stdlib 实现，零外部依赖。
单文件即完整工具，拷贝即可使用。

用法:
    python vault.py init <归档根目录>
    python vault.py sync <归档根目录> [--dry-run]
    python vault.py verify <归档根目录>
    python vault.py snapshot <归档根目录> [--dry-run]
    python vault.py repair <归档根目录> [--dry-run]
    python vault.py maintain <归档根目录> [--dry-run]
    python vault.py status <归档根目录>
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Optional

# ============================================================
# 版本与常量
# ============================================================
VERSION = "1.0.0"
PROGRAM_NAME = "Vault"
SYSTEM_DIR = ".archive"
CONFIG_FILE = "config.json"
BASELINE_FILE = "baseline.json"
MASTER_DIR = "master"
MIRRORS_DIR = "mirrors"
SNAPSHOTS_DIR = "snapshots"
LOGS_DIR = "logs"
REPORTS_DIR = "reports"
FIXED_MIRROR_COUNT = 2

# 状态码
STATUS_OK = "通过"
STATUS_WARN = "警告"
STATUS_ERROR = "需要处理"

# ============================================================
# 哈希抽象层 — 如需替换算法只改这里
# ============================================================
HASH_ALGORITHM = "sha256"
HASH_BLOCK_SIZE = 1024 * 1024  # 1MB


def validate_hash_algorithm(algorithm: str) -> str:
    """验证哈希算法是否受 hashlib 支持。"""
    try:
        hashlib.new(algorithm)
    except (TypeError, ValueError) as e:
        raise ValueError(f"不支持的 hash_algorithm: {algorithm}") from e
    return algorithm


def get_config_hash_algorithm(config: dict) -> str:
    """从配置中读取并验证当前使用的哈希算法。"""
    return validate_hash_algorithm(config.get("hash_algorithm", HASH_ALGORITHM))


def compute_file_hash(filepath: Path, algorithm: str = HASH_ALGORITHM) -> Optional[str]:
    """计算文件哈希。返回十六进制摘要，失败返回 None。"""
    try:
        h = hashlib.new(algorithm)
        with open(filepath, "rb") as f:
            while True:
                block = f.read(HASH_BLOCK_SIZE)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()
    except (OSError, PermissionError) as e:
        return None


# ============================================================
# 配置管理
# ============================================================
DEFAULT_CONFIG = {
    "version": VERSION,
    "hash_algorithm": HASH_ALGORITHM,
    "max_snapshots": 10,
    "ignore_patterns": [
        ".archive",
        "Thumbs.db",
        "desktop.ini",
        ".DS_Store",
        "~$*",
    ],
    "delete_protection": True,
    "snapshot_readonly": True,
}


def load_config(archive_root: Path) -> dict:
    config_path = archive_root / SYSTEM_DIR / CONFIG_FILE
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}\n请先运行 init 命令。")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(archive_root: Path, config: dict):
    config_path = archive_root / SYSTEM_DIR / CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ============================================================
# 日志系统
# ============================================================
class ArchiveLogger:
    """双通道日志：机器日志(详细) + 人类报告(摘要)"""

    def __init__(self, archive_root: Path, command: str):
        self.archive_root = archive_root
        self.command = command
        self.timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.messages = []  # 人类可读消息
        self.stats = {}

        # 机器日志
        logs_dir = archive_root / SYSTEM_DIR / LOGS_DIR
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"{self.timestamp}_{command}.log"

        self.logger = logging.getLogger(f"archive_{self.timestamp}")
        self.logger.setLevel(logging.DEBUG)
        # 清除旧 handler
        self.logger.handlers.clear()
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        ))
        self.logger.addHandler(fh)

        # 控制台
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(ch)

        self.log_file = log_file

    def debug(self, msg):
        self.logger.debug(msg)

    def info(self, msg):
        self.logger.info(msg)
        self.messages.append(("信息", msg))

    def warn(self, msg):
        self.logger.warning(msg)
        self.messages.append(("警告", msg))

    def error(self, msg):
        self.logger.error(msg)
        self.messages.append(("错误", msg))

    def write_report(self, overall_status: str):
        """写出人类可读报告"""
        reports_dir = self.archive_root / SYSTEM_DIR / REPORTS_DIR
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_file = reports_dir / f"{self.timestamp}_{self.command}.txt"

        lines = []
        lines.append(f"{'=' * 60}")
        lines.append(f"  {PROGRAM_NAME} — {self.command} 报告")
        lines.append(f"  时间: {self.timestamp}")
        lines.append(f"  总体状态: {overall_status}")
        lines.append(f"{'=' * 60}")
        lines.append("")

        if self.stats:
            lines.append("【统计】")
            for k, v in self.stats.items():
                lines.append(f"  {k}: {v}")
            lines.append("")

        has_warn = False
        has_error = False
        for level, msg in self.messages:
            if level == "警告":
                has_warn = True
            if level == "错误":
                has_error = True

        if has_error:
            lines.append("【需要处理的问题】")
            for level, msg in self.messages:
                if level == "错误":
                    lines.append(f"  ✗ {msg}")
            lines.append("")

        if has_warn:
            lines.append("【警告】")
            for level, msg in self.messages:
                if level == "警告":
                    lines.append(f"  ! {msg}")
            lines.append("")

        lines.append(f"详细日志: {self.log_file}")
        lines.append("")

        report_text = "\n".join(lines)
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_text)

        return report_file, report_text


# ============================================================
# 文件扫描工具
# ============================================================
def should_ignore(rel_path: str, ignore_patterns: list) -> bool:
    """检查相对路径是否匹配忽略规则"""
    parts = rel_path.replace("\\", "/").split("/")
    for pattern in ignore_patterns:
        for part in parts:
            if pattern.startswith("~$") and part.startswith("~$"):
                return True
            if part == pattern:
                return True
    return False


def scan_directory(base_dir: Path, ignore_patterns: list, algorithm: str = HASH_ALGORITHM) -> dict:
    """
    扫描目录，返回 {相对路径: {"size": int, "mtime": float, "hash": str}}
    """
    result = {}
    if not base_dir.exists():
        return result
    for root, dirs, files in os.walk(base_dir):
        # 过滤忽略目录
        dirs[:] = [d for d in dirs
                   if not should_ignore(
                       os.path.relpath(os.path.join(root, d), base_dir),
                       ignore_patterns)]
        for fname in files:
            fpath = Path(root) / fname
            rel = os.path.relpath(fpath, base_dir).replace("\\", "/")
            if should_ignore(rel, ignore_patterns):
                continue
            try:
                st = fpath.stat()
                file_hash = compute_file_hash(fpath, algorithm)
                result[rel] = {
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "hash": file_hash,
                }
            except (OSError, PermissionError):
                result[rel] = {
                    "size": -1,
                    "mtime": 0,
                    "hash": None,
                }
    return result


def scan_directory_fast(base_dir: Path, ignore_patterns: list) -> set:
    """只返回文件相对路径集合，不算哈希（用于快速对比文件列表）"""
    result = set()
    if not base_dir.exists():
        return result
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs
                   if not should_ignore(
                       os.path.relpath(os.path.join(root, d), base_dir),
                       ignore_patterns)]
        for fname in files:
            fpath = Path(root) / fname
            rel = os.path.relpath(fpath, base_dir).replace("\\", "/")
            if should_ignore(rel, ignore_patterns):
                continue
            result.add(rel)
    return result


def get_mirror_names() -> list[str]:
    """返回固定的镜像目录名列表。"""
    return [f"mirror_{i}" for i in range(1, FIXED_MIRROR_COUNT + 1)]


def get_current_copy_path(archive_root: Path, master_dir: Path, source_name: str, rel: str) -> Path:
    """返回主副本或镜像副本的实际路径。"""
    if source_name == "主副本":
        return master_dir / rel
    return archive_root / MIRRORS_DIR / source_name / rel


def choose_current_source_name(source_names: list[str]) -> Optional[str]:
    """在当前链中选择优先使用的恢复源。"""
    if "主副本" in source_names:
        return "主副本"
    for name in get_mirror_names():
        if name in source_names:
            return name
    if source_names:
        return source_names[0]
    return None


# ============================================================
# 基准管理
# ============================================================
def load_baseline(archive_root: Path) -> dict:
    bp = archive_root / SYSTEM_DIR / BASELINE_FILE
    if not bp.exists():
        return {}
    with open(bp, "r", encoding="utf-8") as f:
        return json.load(f)


def save_baseline(archive_root: Path, baseline: dict):
    bp = archive_root / SYSTEM_DIR / BASELINE_FILE
    # 先写临时文件再替换，防止写到一半断电
    tmp = bp.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)
    if bp.exists():
        bp.unlink()
    tmp.rename(bp)


# ============================================================
# 核心命令实现
# ============================================================

def cmd_init(archive_root: Path, log: ArchiveLogger):
    """初始化归档目录结构"""
    log.info(f"初始化归档目录: {archive_root}")
    algorithm = get_config_hash_algorithm(DEFAULT_CONFIG)

    # 检查是否已初始化
    if (archive_root / SYSTEM_DIR / CONFIG_FILE).exists():
        log.warn("该目录已初始化过。如需重新初始化，请先手动删除 .archive 目录。")
        return STATUS_WARN

    # 创建目录结构
    dirs_to_create = [
        archive_root / MASTER_DIR,
        archive_root / SNAPSHOTS_DIR,
        archive_root / SYSTEM_DIR / LOGS_DIR,
        archive_root / SYSTEM_DIR / REPORTS_DIR,
    ]
    dirs_to_create.extend(archive_root / MIRRORS_DIR / name for name in get_mirror_names())
    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        log.debug(f"创建目录: {d}")

    # 写入默认配置
    save_config(archive_root, DEFAULT_CONFIG)
    log.info("默认配置已生成。")

    # 初始化空基准
    save_baseline(archive_root, {
        "created": datetime.datetime.now().isoformat(),
        "algorithm": algorithm,
        "files": {},
    })
    log.info("校验基准已初始化。")

    # 健康自检
    ok = True
    for d in dirs_to_create:
        if not d.exists():
            log.error(f"目录创建失败: {d}")
            ok = False
    if (archive_root / SYSTEM_DIR / CONFIG_FILE).exists():
        log.debug("配置文件存在。")
    else:
        log.error("配置文件创建失败。")
        ok = False

    if ok:
        log.info("初始化完成，健康自检通过。")
        log.info(f"请将归档文件放入: {archive_root / MASTER_DIR}")
        return STATUS_OK
    else:
        return STATUS_ERROR


def cmd_sync(archive_root: Path, log: ArchiveLogger, dry_run: bool = False):
    """将主副本同步到镜像"""
    config = load_config(archive_root)
    algorithm = get_config_hash_algorithm(config)
    master_dir = archive_root / MASTER_DIR
    ignore = config["ignore_patterns"]

    if not master_dir.exists():
        log.error(f"主副本目录不存在: {master_dir}")
        return STATUS_ERROR

    mode_label = "【预演模式】" if dry_run else ""
    log.info(f"{mode_label}开始同步主副本到镜像...")

    # 扫描主副本
    log.info("扫描主副本...")
    master_files = scan_directory(master_dir, ignore, algorithm)
    log.stats["主副本文件数"] = len(master_files)

    overall = STATUS_OK
    for mirror_name in get_mirror_names():
        mirror_dir = archive_root / MIRRORS_DIR / mirror_name
        mirror_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"同步到 {mirror_name}...")
        mirror_files = scan_directory(mirror_dir, ignore, algorithm)

        added = 0
        updated = 0
        deleted_pending = []
        errors = 0

        # 新增和更新
        for rel, info in master_files.items():
            src = master_dir / rel
            dst = mirror_dir / rel

            if rel not in mirror_files:
                # 新增
                log.debug(f"  新增: {rel}")
                if not dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(str(src), str(dst))
                        # 验证拷贝
                        dst_hash = compute_file_hash(dst, algorithm)
                        if dst_hash != info["hash"]:
                            log.error(f"  拷贝验证失败: {rel}")
                            errors += 1
                            continue
                    except (OSError, shutil.Error) as e:
                        log.error(f"  拷贝失败: {rel} — {e}")
                        errors += 1
                        continue
                added += 1

            elif mirror_files[rel]["hash"] != info["hash"]:
                # 内容不同，更新
                log.debug(f"  更新: {rel}")
                if not dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(str(src), str(dst))
                        dst_hash = compute_file_hash(dst, algorithm)
                        if dst_hash != info["hash"]:
                            log.error(f"  更新验证失败: {rel}")
                            errors += 1
                            continue
                    except (OSError, shutil.Error) as e:
                        log.error(f"  更新失败: {rel} — {e}")
                        errors += 1
                        continue
                updated += 1

        # 删除：镜像中有但主副本中没有的
        for rel in mirror_files:
            if rel not in master_files:
                deleted_pending.append(rel)

        if deleted_pending and config.get("delete_protection", True):
            log.warn(f"  {mirror_name} 中有 {len(deleted_pending)} 个文件在主副本中已不存在:")
            for rel in deleted_pending[:10]:
                log.warn(f"    — {rel}")
            if len(deleted_pending) > 10:
                log.warn(f"    ...及其他 {len(deleted_pending) - 10} 个文件")
            log.warn("  删除保护已启用，这些文件不会被自动删除。")
            log.warn("  如需清理，请使用 repair 命令，或手动处理。")
        elif deleted_pending:
            for rel in deleted_pending:
                dst = mirror_dir / rel
                if not dry_run:
                    try:
                        dst.unlink()
                        log.debug(f"  删除: {rel}")
                    except OSError as e:
                        log.error(f"  删除失败: {rel} — {e}")
                        errors += 1

        log.info(f"  {mirror_name}: 新增 {added}, 更新 {updated}, "
                 f"待清理 {len(deleted_pending)}, 错误 {errors}")
        log.stats[f"{mirror_name}_新增"] = added
        log.stats[f"{mirror_name}_更新"] = updated
        log.stats[f"{mirror_name}_待清理"] = len(deleted_pending)

        if errors > 0:
            overall = STATUS_ERROR

    # 同步后更新基准
    if not dry_run:
        log.info("更新校验基准...")
        baseline = load_baseline(archive_root)
        baseline["updated"] = datetime.datetime.now().isoformat()
        baseline["algorithm"] = algorithm
        baseline["files"] = {}
        for rel, info in master_files.items():
            baseline["files"][rel] = {
                "hash": info["hash"],
                "size": info["size"],
            }
        save_baseline(archive_root, baseline)
        log.info(f"基准已更新，包含 {len(master_files)} 个文件。")

    return overall


def cmd_verify(archive_root: Path, log: ArchiveLogger):
    """完整性校验"""
    config = load_config(archive_root)
    algorithm = get_config_hash_algorithm(config)
    ignore = config["ignore_patterns"]
    master_dir = archive_root / MASTER_DIR
    baseline = load_baseline(archive_root)
    baseline_files = baseline.get("files", {})
    baseline_algorithm = baseline.get("algorithm", HASH_ALGORITHM)

    log.info("开始完整性校验...")

    if baseline_files and baseline_algorithm != algorithm:
        log.error(
            f"当前配置使用 {algorithm}，但校验基准使用 {baseline_algorithm}。"
            "请先运行 sync 重新生成基准。"
        )
        return STATUS_ERROR

    overall = STATUS_OK
    issues = []

    # 1. 主副本 vs 基准
    log.info("校验主副本 vs 基准...")
    master_files = scan_directory(master_dir, ignore, algorithm)

    ok_count = 0
    for rel, binfo in baseline_files.items():
        if rel not in master_files:
            issues.append(("主副本", rel, "文件缺失", "基准中存在但主副本中找不到"))
            log.warn(f"  缺失: {rel} (主副本)")
        elif master_files[rel]["hash"] is None:
            issues.append(("主副本", rel, "无法读取", "文件无法计算哈希"))
            log.warn(f"  无法读取: {rel} (主副本)")
        elif master_files[rel]["hash"] != binfo["hash"]:
            issues.append(("主副本", rel, "内容不一致",
                           f"基准={binfo['hash'][:16]}... 实际={master_files[rel]['hash'][:16]}..."))
            log.warn(f"  不一致: {rel} (主副本与基准不符)")
        else:
            ok_count += 1

    # 主副本中有但基准中没有的（新文件）
    for rel in master_files:
        if rel not in baseline_files:
            issues.append(("主副本", rel, "未纳入基准", "主副本中存在但基准中没有记录"))
            log.warn(f"  未纳入基准: {rel}")

    log.info(f"  主副本: {ok_count} 个文件校验通过")
    log.stats["主副本_通过"] = ok_count
    log.stats["主副本_文件总数"] = len(master_files)

    # 2. 各镜像 vs 基准 & vs 主副本
    for mirror_name in get_mirror_names():
        mirror_dir = archive_root / MIRRORS_DIR / mirror_name
        if not mirror_dir.exists():
            log.warn(f"  镜像目录不存在: {mirror_name}")
            issues.append((mirror_name, "*", "目录缺失", "整个镜像目录不存在"))
            continue

        log.info(f"校验 {mirror_name}...")
        mirror_files = scan_directory(mirror_dir, ignore, algorithm)
        m_ok = 0

        for rel, binfo in baseline_files.items():
            if rel not in mirror_files:
                issues.append((mirror_name, rel, "文件缺失", "基准中存在但镜像中找不到"))
                log.warn(f"  缺失: {rel} ({mirror_name})")
            elif mirror_files[rel]["hash"] is None:
                issues.append((mirror_name, rel, "无法读取", "文件无法计算哈希"))
            elif mirror_files[rel]["hash"] != binfo["hash"]:
                # 进一步区分：是镜像损坏还是主副本已更新
                master_hash = master_files.get(rel, {}).get("hash")
                if master_hash and mirror_files[rel]["hash"] == master_hash:
                    # 镜像和主副本一致但与基准不同 — 可能基准过时
                    issues.append((mirror_name, rel, "基准过时",
                                   "镜像与主副本一致，但与基准不符"))
                else:
                    issues.append((mirror_name, rel, "内容不一致",
                                   f"基准={binfo['hash'][:16]}... 实际={mirror_files[rel]['hash'][:16]}..."))
                    log.warn(f"  不一致: {rel} ({mirror_name})")
            else:
                m_ok += 1

        # 镜像中多出的文件
        for rel in mirror_files:
            if rel not in baseline_files and rel not in master_files:
                issues.append((mirror_name, rel, "多余文件", "镜像中存在但主副本和基准中都没有"))

        log.info(f"  {mirror_name}: {m_ok} 个文件校验通过")
        log.stats[f"{mirror_name}_通过"] = m_ok

    # 3. 汇总
    if not issues:
        log.info("所有校验通过，无异常。")
        return STATUS_OK

    # 分类统计
    by_type = {}
    for loc, rel, issue_type, detail in issues:
        by_type.setdefault(issue_type, []).append((loc, rel, detail))

    log.info("")
    log.info(f"发现 {len(issues)} 个问题:")
    for itype, items in by_type.items():
        log.info(f"  [{itype}] {len(items)} 个")

    log.stats["问题总数"] = len(issues)

    # 判断严重程度
    serious_types = {"文件缺失", "内容不一致", "无法读取", "疑似损坏"}
    has_serious = any(t in serious_types for t in by_type)

    return STATUS_ERROR if has_serious else STATUS_WARN


def cmd_snapshot(archive_root: Path, log: ArchiveLogger, dry_run: bool = False):
    """创建主副本的冻结快照"""
    config = load_config(archive_root)
    algorithm = get_config_hash_algorithm(config)
    ignore = config["ignore_patterns"]
    master_dir = archive_root / MASTER_DIR
    max_snapshots = config.get("max_snapshots", 10)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_dir = archive_root / SNAPSHOTS_DIR / ts
    # 避免同秒冲突
    if snap_dir.exists():
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        snap_dir = archive_root / SNAPSHOTS_DIR / ts

    mode_label = "【预演模式】" if dry_run else ""
    log.info(f"{mode_label}创建快照: {ts}")

    if not master_dir.exists() or not any(master_dir.iterdir()):
        log.warn("主副本为空，跳过快照。")
        return STATUS_WARN

    # 扫描主副本
    master_files = scan_directory(master_dir, ignore, algorithm)
    log.stats["快照文件数"] = len(master_files)

    if dry_run:
        log.info(f"  将创建快照: {snap_dir}")
        log.info(f"  包含 {len(master_files)} 个文件")
        return STATUS_OK

    # 复制
    snap_dir.mkdir(parents=True, exist_ok=True)
    errors = 0
    for rel in master_files:
        src = master_dir / rel
        dst = snap_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dst))
        except (OSError, shutil.Error) as e:
            log.error(f"  快照拷贝失败: {rel} — {e}")
            errors += 1

    # 保存快照自己的校验基准
    snap_baseline = {
        "created": datetime.datetime.now().isoformat(),
        "algorithm": algorithm,
        "source": "snapshot",
        "snapshot_id": ts,
        "files": {},
    }
    for rel, info in master_files.items():
        snap_baseline["files"][rel] = {
            "hash": info["hash"],
            "size": info["size"],
        }
    snap_baseline_path = snap_dir / "_snapshot_baseline.json"
    with open(snap_baseline_path, "w", encoding="utf-8") as f:
        json.dump(snap_baseline, f, ensure_ascii=False, indent=2)

    # 尝试设为只读
    if config.get("snapshot_readonly", True):
        try:
            for root, dirs, files in os.walk(snap_dir):
                for fname in files:
                    fpath = Path(root) / fname
                    os.chmod(str(fpath), stat.S_IREAD)
        except OSError:
            log.warn("无法将快照设为只读（部分文件权限修改失败）。")

    log.info(f"快照已创建: {snap_dir}")

    # 清理旧快照
    snapshots = sorted([
        d for d in (archive_root / SNAPSHOTS_DIR).iterdir()
        if d.is_dir() and d.name != ".archive"
    ])
    if len(snapshots) > max_snapshots:
        to_remove = snapshots[:len(snapshots) - max_snapshots]
        for old_snap in to_remove:
            log.info(f"清理旧快照: {old_snap.name}")
            # 先去掉只读
            for root, dirs, files in os.walk(old_snap):
                for fname in files:
                    fpath = Path(root) / fname
                    try:
                        os.chmod(str(fpath), stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
            shutil.rmtree(str(old_snap), ignore_errors=True)

    if errors > 0:
        return STATUS_ERROR
    return STATUS_OK


def cmd_repair(archive_root: Path, log: ArchiveLogger, dry_run: bool = False):
    """修复异常：基于主副本、镜像、快照、基准进行判定和修复"""
    config = load_config(archive_root)
    algorithm = get_config_hash_algorithm(config)
    ignore = config["ignore_patterns"]
    master_dir = archive_root / MASTER_DIR
    baseline = load_baseline(archive_root)
    baseline_files = baseline.get("files", {})
    baseline_algorithm = baseline.get("algorithm", HASH_ALGORITHM)

    mode_label = "【预演模式】" if dry_run else ""
    log.info(f"{mode_label}开始修复检查...")

    if baseline_files and baseline_algorithm != algorithm:
        log.error(
            f"当前配置使用 {algorithm}，但校验基准使用 {baseline_algorithm}。"
            "请先运行 sync 重新生成基准。"
        )
        return STATUS_ERROR

    # 收集所有副本的状态
    master_files = scan_directory(master_dir, ignore, algorithm)

    mirrors = {}
    for name in get_mirror_names():
        mdir = archive_root / MIRRORS_DIR / name
        if mdir.exists():
            mirrors[name] = scan_directory(mdir, ignore, algorithm)
        else:
            mirrors[name] = {}

    # 找最新快照
    latest_snapshot = None
    latest_snap_files = {}
    snap_dir = archive_root / SNAPSHOTS_DIR
    if snap_dir.exists():
        snaps = sorted([d for d in snap_dir.iterdir() if d.is_dir()])
        if snaps:
            latest_snapshot = snaps[-1]
            # 读快照基准
            snap_bl_path = latest_snapshot / "_snapshot_baseline.json"
            if snap_bl_path.exists():
                with open(snap_bl_path, "r", encoding="utf-8") as f:
                    snap_bl = json.load(f)
                snap_algorithm = snap_bl.get("algorithm", HASH_ALGORITHM)
                if snap_algorithm == algorithm:
                    latest_snap_files = snap_bl.get("files", {})
                else:
                    log.warn(
                        f"最新快照使用 {snap_algorithm}，与当前配置 {algorithm} 不一致，"
                        "已跳过该快照作为修复依据。"
                    )

    repaired = 0
    skipped = 0
    manual_needed = []
    errors = 0

    all_files = set(baseline_files.keys()) | set(master_files.keys())
    for name, mfiles in mirrors.items():
        all_files |= set(mfiles.keys())

    for rel in sorted(all_files):
        b_hash = baseline_files.get(rel, {}).get("hash")
        m_hash = master_files.get(rel, {}).get("hash")
        snap_hash = latest_snap_files.get(rel, {}).get("hash")

        current_entries = {
            "主副本": {
                "hash": m_hash,
                "path": master_dir / rel,
            }
        }
        for name in get_mirror_names():
            current_entries[name] = {
                "hash": mirrors[name].get(rel, {}).get("hash"),
                "path": archive_root / MIRRORS_DIR / name / rel,
            }

        # 检查镜像缺失情况
        missing_mirrors = []
        for name in get_mirror_names():
            if current_entries[name]["hash"] is None and (rel in baseline_files or rel in master_files):
                missing_mirrors.append(name)

        # 主副本和基准都已不存在，但镜像里还残留的文件：显式清理
        orphan_mirrors = [
            name for name in get_mirror_names()
            if current_entries[name]["hash"] is not None
            and rel not in baseline_files
            and rel not in master_files
        ]
        if orphan_mirrors:
            log.info(f"  发现镜像残留文件: {rel}")
            for name in orphan_mirrors:
                target_path = current_entries[name]["path"]
                log.info(f"    → 清理 {name}: {rel}")
                if not dry_run:
                    try:
                        if target_path.exists():
                            try:
                                os.chmod(str(target_path), stat.S_IWRITE | stat.S_IREAD)
                            except OSError:
                                pass
                            target_path.unlink()
                        repaired += 1
                    except OSError as e:
                        log.error(f"    清理失败: {rel} ({name}) — {e}")
                        errors += 1
                else:
                    repaired += 1
            continue

        current_hashes = {
            name: entry["hash"]
            for name, entry in current_entries.items()
            if entry["hash"] is not None
        }
        if not current_hashes and not b_hash and not snap_hash:
            log.debug(f"  跳过 {rel}: 无任何有效数据源")
            continue

        sources = {**current_hashes}
        if snap_hash:
            sources["快照"] = snap_hash
        if b_hash:
            sources["基准"] = b_hash

        current_unique_hashes = set(current_hashes.values())
        history_hashes = [h for h in (b_hash, snap_hash) if h is not None]

        if missing_mirrors:
            log.info(f"  发现缺失: {rel} (在 {', '.join(missing_mirrors)} 中)")
        if len(current_unique_hashes) > 1:
            log.info(f"  发现当前链不一致: {rel}")
        elif history_hashes and current_unique_hashes:
            only_current_hash = next(iter(current_unique_hashes))
            if any(h != only_current_hash for h in history_hashes):
                log.info(f"  发现当前链与历史记录冲突: {rel}")
        for src, h in sources.items():
            log.debug(f"    {src}: {h[:16]}...")

        auto_repair_reason = None
        anchor_hash = None
        source_name = None

        # 当前链完全一致且无缺失，且与历史记录无冲突：无需处理
        if current_unique_hashes:
            current_only_hash = next(iter(current_unique_hashes)) if len(current_unique_hashes) == 1 else None
            current_targets = [
                name for name, entry in current_entries.items()
                if entry["hash"] != current_only_hash
            ] if current_only_hash else []
            history_conflict = current_only_hash is not None and any(
                h != current_only_hash for h in history_hashes
            )
            if current_only_hash is not None and not current_targets and not history_conflict:
                continue

        # 规则 1：当前链现存副本本来就一致，只是镜像缺失/不可读 —— 直接补镜像
        if m_hash is not None and len(current_unique_hashes) == 1:
            mirror_targets = [
                name for name in get_mirror_names()
                if current_entries[name]["hash"] != m_hash
            ]
            if mirror_targets:
                anchor_hash = m_hash
                source_name = "主副本"
                auto_repair_reason = "当前链一致，可直接恢复镜像"

        # 规则 2：历史证据支持某一个当前版本 —— 用该版本修复其他副本
        if anchor_hash is None and current_hashes:
            history_supported_hashes = {}
            if b_hash and b_hash in current_unique_hashes:
                history_supported_hashes.setdefault(b_hash, []).append("基准")
            if snap_hash and snap_hash in current_unique_hashes:
                history_supported_hashes.setdefault(snap_hash, []).append("快照")

            if len(history_supported_hashes) == 1:
                anchor_hash, supported_by = next(iter(history_supported_hashes.items()))
                supporters = [
                    name for name, h in current_hashes.items()
                    if h == anchor_hash
                ]
                source_name = choose_current_source_name(supporters)
                auto_repair_reason = f"{'、'.join(supported_by)}支持当前版本"
            elif len(history_supported_hashes) > 1:
                auto_repair_reason = None

        if anchor_hash is None or source_name is None:
            manual_reason = "当前副本之间存在冲突，且历史证据不足以支持唯一版本"
            if current_unique_hashes and len(current_unique_hashes) == 1 and history_hashes:
                manual_reason = "当前链虽然一致，但与基准或快照冲突，需要人工确认"
            elif not current_hashes and (b_hash or snap_hash):
                manual_reason = "缺少可用的现存副本，无法安全恢复"
            elif b_hash and snap_hash and b_hash != snap_hash and b_hash in current_unique_hashes and snap_hash in current_unique_hashes:
                manual_reason = "基准和快照分别支持不同的当前版本，需要人工确认"

            log.warn(f"  无法自动判定 {rel} 的正确版本，需要人工介入。")
            log.warn(f"    原因: {manual_reason}")
            log.warn(f"    各源哈希:")
            for src, h in sources.items():
                log.warn(f"      {src}: {h[:16]}...")
            manual_needed.append(rel)
            skipped += 1
            continue

        src_path = get_current_copy_path(archive_root, master_dir, source_name, rel)
        log.info(f"  修复依据: {source_name} ({auto_repair_reason})")

        targets_to_fix = [
            (name, entry["path"])
            for name, entry in current_entries.items()
            if entry["hash"] != anchor_hash
        ]

        for target_name, target_path in targets_to_fix:
            log.info(f"    → 修复 {target_name}: {rel}")
            if not dry_run:
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    # 去掉只读（如果有）
                    if target_path.exists():
                        try:
                            os.chmod(str(target_path), stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
                    shutil.copy2(str(src_path), str(target_path))
                    # 验证
                    verify_hash = compute_file_hash(target_path, algorithm)
                    if verify_hash != anchor_hash:
                        log.error(f"    修复后验证失败: {rel} ({target_name})")
                        errors += 1
                    else:
                        repaired += 1
                except (OSError, shutil.Error) as e:
                    log.error(f"    修复失败: {rel} ({target_name}) — {e}")
                    errors += 1
            else:
                repaired += 1

    log.stats["已修复"] = repaired
    log.stats["需人工处理"] = skipped
    log.stats["修复错误"] = errors

    if manual_needed:
        log.info("")
        log.info("以下文件需要人工判断:")
        for rel in manual_needed:
            log.info(f"  — {rel}")

    if not dry_run and repaired > 0:
        # 修复后复检
        log.info("")
        log.info("修复后复检...")
        post_master = scan_directory(master_dir, ignore, algorithm)
        recheck_ok = True
        for name in get_mirror_names():
            mdir = archive_root / MIRRORS_DIR / name
            if not mdir.exists():
                continue
            post_mirror = scan_directory(mdir, ignore, algorithm)
            for rel in post_master:
                if rel in post_mirror:
                    if post_master[rel]["hash"] != post_mirror[rel]["hash"]:
                        log.warn(f"  复检不一致: {rel}")
                        recheck_ok = False
        if recheck_ok:
            log.info("复检通过。")
        else:
            log.warn("复检发现残余不一致。")

    if errors > 0:
        return STATUS_ERROR
    if skipped > 0:
        return STATUS_WARN
    return STATUS_OK


def cmd_status(archive_root: Path, log: ArchiveLogger):
    """快速状态查看"""
    config = load_config(archive_root)
    algorithm = get_config_hash_algorithm(config)
    ignore = config["ignore_patterns"]
    master_dir = archive_root / MASTER_DIR

    log.info(f"{PROGRAM_NAME} v{VERSION}")
    log.info(f"归档目录: {archive_root}")
    log.info(f"哈希算法: {algorithm}")
    log.info("")

    # 主副本
    if master_dir.exists():
        master_count = len(scan_directory_fast(master_dir, ignore))
        log.info(f"主副本: {master_count} 个文件")
    else:
        log.info("主副本: 目录不存在")

    # 镜像
    for name in get_mirror_names():
        mdir = archive_root / MIRRORS_DIR / name
        if mdir.exists():
            mc = len(scan_directory_fast(mdir, ignore))
            log.info(f"{name}: {mc} 个文件")
        else:
            log.info(f"{name}: 不存在")

    # 快照
    snap_dir = archive_root / SNAPSHOTS_DIR
    if snap_dir.exists():
        snaps = sorted([d.name for d in snap_dir.iterdir() if d.is_dir()])
        log.info(f"快照: {len(snaps)} 个")
        if snaps:
            log.info(f"  最新: {snaps[-1]}")
            log.info(f"  最旧: {snaps[0]}")
    else:
        log.info("快照: 无")

    # 基准
    baseline = load_baseline(archive_root)
    bl_files = baseline.get("files", {})
    bl_updated = baseline.get("updated", baseline.get("created", "未知"))
    log.info(f"基准: {len(bl_files)} 个文件, 更新于 {bl_updated}")

    # 日志
    logs_dir = archive_root / SYSTEM_DIR / LOGS_DIR
    if logs_dir.exists():
        log_files = sorted(logs_dir.glob("*.log"))
        log.info(f"日志: {len(log_files)} 个")
        if log_files:
            log.info(f"  最新: {log_files[-1].name}")

    return STATUS_OK


def cmd_maintain(archive_root: Path, log: ArchiveLogger, dry_run: bool = False):
    """统一维护流程：巡检 → 快照 → 同步 → 校验 → 修复 → 报告"""
    mode_label = "【预演模式】" if dry_run else ""
    log.info(f"{mode_label}开始统一维护流程...")
    log.info("=" * 50)

    results = {}

    # 步骤1: 快照（先保存当前状态）
    log.info("")
    log.info("【步骤 1/4】创建快照...")
    log.info("-" * 40)
    r = cmd_snapshot(archive_root, log, dry_run)
    results["快照"] = r

    # 步骤2: 同步
    log.info("")
    log.info("【步骤 2/4】同步主副本到镜像...")
    log.info("-" * 40)
    r = cmd_sync(archive_root, log, dry_run)
    results["同步"] = r

    # 步骤3: 校验
    log.info("")
    log.info("【步骤 3/4】完整性校验...")
    log.info("-" * 40)
    r = cmd_verify(archive_root, log)
    results["校验"] = r

    # 步骤4: 如有问题则修复
    if results["校验"] != STATUS_OK:
        log.info("")
        log.info("【步骤 4/4】尝试修复...")
        log.info("-" * 40)
        r = cmd_repair(archive_root, log, dry_run)
        results["修复"] = r
    else:
        log.info("")
        log.info("【步骤 4/4】校验通过，无需修复。")
        results["修复"] = STATUS_OK

    # 汇总
    log.info("")
    log.info("=" * 50)
    log.info("维护结果汇总:")
    overall = STATUS_OK
    for step, status in results.items():
        symbol = "✓" if status == STATUS_OK else ("!" if status == STATUS_WARN else "✗")
        log.info(f"  {symbol} {step}: {status}")
        if status == STATUS_ERROR:
            overall = STATUS_ERROR
        elif status == STATUS_WARN and overall == STATUS_OK:
            overall = STATUS_WARN

    log.info("")
    log.info(f"总体状态: {overall}")

    return overall


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        prog="vault",
        description=f"{PROGRAM_NAME} v{VERSION} — 单盘多副本归档维护工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令说明:
  init      初始化归档目录
  sync      同步主副本到镜像
  verify    完整性校验
  snapshot  创建快照
  repair    修复异常
  maintain  统一维护（推荐日常使用）
  status    查看状态

示例:
  python vault.py init D:\\我的归档
  python vault.py maintain D:\\我的归档
  python vault.py maintain D:\\我的归档 --dry-run
""")
    parser.add_argument("command",
                        choices=["init", "sync", "verify", "snapshot",
                                 "repair", "maintain", "status"],
                        help="要执行的命令")
    parser.add_argument("archive_root", help="归档根目录路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="预演模式：只检查，不实际修改")

    args = parser.parse_args()
    archive_root = Path(args.archive_root).resolve()
    command = args.command
    dry_run = args.dry_run

    # init 不需要预先存在的配置
    if command != "init" and not (archive_root / SYSTEM_DIR / CONFIG_FILE).exists():
        print(f"错误: {archive_root} 尚未初始化。请先运行: python vault.py init {archive_root}")
        sys.exit(1)

    log = ArchiveLogger(archive_root, command)

    try:
        if command == "init":
            result = cmd_init(archive_root, log)
        elif command == "sync":
            result = cmd_sync(archive_root, log, dry_run)
        elif command == "verify":
            result = cmd_verify(archive_root, log)
        elif command == "snapshot":
            result = cmd_snapshot(archive_root, log, dry_run)
        elif command == "repair":
            result = cmd_repair(archive_root, log, dry_run)
        elif command == "maintain":
            result = cmd_maintain(archive_root, log, dry_run)
        elif command == "status":
            result = cmd_status(archive_root, log)
        else:
            result = STATUS_ERROR

        # 写报告
        report_file, report_text = log.write_report(result)
        print("")
        print(report_text)

        if result == STATUS_ERROR:
            sys.exit(1)
        elif result == STATUS_WARN:
            sys.exit(0)  # 警告不算失败
        else:
            sys.exit(0)

    except Exception as e:
        log.error(f"未预期的错误: {e}")
        import traceback
        log.debug(traceback.format_exc())
        log.write_report(STATUS_ERROR)
        sys.exit(2)


if __name__ == "__main__":
    main()
