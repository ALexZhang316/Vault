#!/usr/bin/env python3
"""
Vault user assistant.

This GUI keeps day-to-day use away from command lines while still using
vault.py as the single source of backup behavior.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Optional
import tkinter as tk


APP_TITLE = "Vault 文件备份助手"
REPO_DIR = Path(__file__).resolve().parent
VAULT_SCRIPT = REPO_DIR / "vault.py"
SYSTEM_DIR = ".archive"
CONFIG_FILE = "config.json"
MASTER_DIR = "master"
REPORTS_DIR = "reports"

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
STATE_FILE = APPDATA / "VaultAssistant" / "settings.json"


def is_initialized(archive_root: Path) -> bool:
    return (archive_root / SYSTEM_DIR / CONFIG_FILE).is_file()


def load_saved_archive_root() -> str:
    if not STATE_FILE.is_file():
        return ""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get("archive_root", "")
    return value if isinstance(value, str) else ""


def save_archive_root(path: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"archive_root": path}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class VaultAssistant(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(760, 560)
        self.archive_root = tk.StringVar(value=load_saved_archive_root())
        self.status_text = tk.StringVar()
        self.running = False
        self.buttons = []

        self._build_ui()
        self._refresh_status()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        header = tk.Label(
            self,
            text="Vault 文件备份助手",
            font=("Microsoft YaHei UI", 18, "bold"),
            anchor="w",
        )
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))

        intro = tk.Label(
            self,
            text="添加新文件后运行维护；确认已有文件的修改后，先同步更改再运行维护。",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        )
        intro.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        path_frame = tk.Frame(self)
        path_frame.grid(row=2, column=0, sticky="ew", padx=18)
        path_frame.columnconfigure(1, weight=1)

        tk.Label(path_frame, text="归档文件夹").grid(row=0, column=0, sticky="w")
        entry = tk.Entry(path_frame, textvariable=self.archive_root)
        entry.grid(row=0, column=1, sticky="ew", padx=8)
        self._add_button(path_frame, "选择...", self.choose_archive).grid(row=0, column=2)

        action_frame = tk.Frame(self)
        action_frame.grid(row=3, column=0, sticky="new", padx=18, pady=12)
        for i in range(4):
            action_frame.columnconfigure(i, weight=1)

        actions = [
            ("初始化归档", self.init_archive),
            ("打开放文件的文件夹", self.open_master_folder),
            ("同步已确认更改", self.sync_confirmed_changes),
            ("预演维护", self.dry_run_maintain),
            ("正式维护", self.maintain),
            ("查看状态", self.show_status),
            ("打开最近报告", self.open_latest_report),
            ("打开报告文件夹", self.open_reports_folder),
        ]
        for index, (label, command) in enumerate(actions):
            button = self._add_button(action_frame, label, command)
            button.grid(
                row=index // 4,
                column=index % 4,
                sticky="ew",
                padx=4,
                pady=4,
                ipady=5,
            )

        self.output = scrolledtext.ScrolledText(
            self,
            wrap="word",
            height=16,
            font=("Consolas", 10),
        )
        self.output.grid(row=4, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.rowconfigure(4, weight=1)

        status = tk.Label(
            self,
            textvariable=self.status_text,
            anchor="w",
            relief="sunken",
            padx=8,
        )
        status.grid(row=5, column=0, sticky="ew")

    def _add_button(self, parent: tk.Widget, label: str, command) -> tk.Button:
        button = tk.Button(parent, text=label, command=command)
        self.buttons.append(button)
        return button

    def _selected_root(self) -> Optional[Path]:
        raw = self.archive_root.get().strip().strip('"')
        if not raw:
            messagebox.showinfo(APP_TITLE, "请先选择一个归档文件夹。")
            return None
        return Path(raw).expanduser().resolve()

    def _initialized_root(self) -> Optional[Path]:
        archive_root = self._selected_root()
        if archive_root is None:
            return None
        if not is_initialized(archive_root):
            messagebox.showinfo(APP_TITLE, "这个文件夹还没有初始化。请先点击“初始化归档”。")
            return None
        return archive_root

    def _refresh_status(self) -> None:
        raw = self.archive_root.get().strip()
        if not raw:
            self.status_text.set("未选择归档文件夹")
            return
        archive_root = Path(raw).expanduser()
        state = "已初始化" if is_initialized(archive_root) else "未初始化"
        self.status_text.set(f"当前归档文件夹：{archive_root}（{state}）")

    def _append_output(self, text: str) -> None:
        self.output.insert("end", text)
        self.output.see("end")

    def choose_archive(self) -> None:
        selected = filedialog.askdirectory(
            title="选择或新建一个归档文件夹",
            initialdir=self.archive_root.get().strip() or str(Path.home()),
        )
        if not selected:
            return
        archive_root = str(Path(selected).resolve())
        self.archive_root.set(archive_root)
        save_archive_root(archive_root)
        self._refresh_status()
        self._append_output(f"\n已选择归档文件夹：{archive_root}\n")

    def init_archive(self) -> None:
        archive_root = self._selected_root()
        if archive_root is None:
            return
        self._run_vault(["init", str(archive_root)], "初始化归档")

    def open_master_folder(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        master = archive_root / MASTER_DIR
        master.mkdir(parents=True, exist_ok=True)
        os.startfile(master)

    def dry_run_maintain(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        self._run_vault(["maintain", str(archive_root), "--dry-run"], "预演维护")

    def sync_confirmed_changes(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        self._run_vault(["sync", str(archive_root)], "同步已确认更改")

    def maintain(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        self._run_vault(["maintain", str(archive_root)], "正式维护")

    def show_status(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        self._run_vault(["status", str(archive_root)], "查看状态")

    def open_reports_folder(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        reports = archive_root / SYSTEM_DIR / REPORTS_DIR
        if not reports.is_dir():
            messagebox.showinfo(APP_TITLE, "还没有报告。运行一次维护或状态查看后会生成报告。")
            return
        os.startfile(reports)

    def open_latest_report(self) -> None:
        archive_root = self._initialized_root()
        if archive_root is None:
            return
        reports = archive_root / SYSTEM_DIR / REPORTS_DIR
        if not reports.is_dir():
            messagebox.showinfo(APP_TITLE, "还没有报告。运行一次维护或状态查看后会生成报告。")
            return
        files = sorted(
            [path for path in reports.glob("*.txt") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not files:
            messagebox.showinfo(APP_TITLE, "还没有报告。运行一次维护或状态查看后会生成报告。")
            return
        os.startfile(files[0])

    def _set_running(self, running: bool) -> None:
        self.running = running
        state = "disabled" if running else "normal"
        for button in self.buttons:
            button.configure(state=state)

    def _run_vault(self, args: list[str], title: str) -> None:
        if self.running:
            return
        if not VAULT_SCRIPT.is_file():
            messagebox.showerror(APP_TITLE, f"找不到 vault.py：{VAULT_SCRIPT}")
            return

        command = [sys.executable, str(VAULT_SCRIPT), *args]
        self._set_running(True)
        self._append_output(f"\n=== {title} ===\n")
        self._append_output("正在执行，请稍候...\n")
        self.status_text.set(f"{title} 正在执行...")

        thread = threading.Thread(
            target=self._worker,
            args=(command, title),
            daemon=True,
        )
        thread.start()

    def _worker(self, command: list[str], title: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                cwd=str(REPO_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )
            output = (result.stdout or "") + (result.stderr or "")
            self.after(0, self._finish_run, title, result.returncode, output)
        except OSError as exc:
            self.after(0, self._finish_run, title, 2, f"执行失败：{exc}\n")

    def _finish_run(self, title: str, returncode: int, output: str) -> None:
        self._append_output(output if output else "(没有输出)\n")
        summary = self._extract_overall_status(output)
        self._refresh_status()
        if summary:
            self.status_text.set(f"{title} 完成：{summary}")
        else:
            self.status_text.set(f"{title} 完成，退出码 {returncode}")
        self._append_output(f"=== 完成：退出码 {returncode} ===\n")
        self._set_running(False)

    @staticmethod
    def _extract_overall_status(output: str) -> str:
        for line in reversed(output.splitlines()):
            if "总体状态:" in line:
                return line.split("总体状态:", 1)[1].strip()
        return ""


def main() -> None:
    app = VaultAssistant()
    app.mainloop()


if __name__ == "__main__":
    main()
