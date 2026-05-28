import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request
from datetime import datetime


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "thinkcopy_config.json")
HISTORY_PATH = os.path.join(APP_DIR, "thinkcopy_history.json")
HISTORY_LIMIT = 20
MODEL_OPTIONS = ("deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner")

DEFAULT_CONFIG = {
    "api_url": "https://api.deepseek.com/chat/completions",
    "api_key": "",
    "model": "deepseek-v4-flash",
    "window_width": 420,
    "window_alpha": 1.0,
    "topmost": False,
    "poll_interval_ms": 3000,
    "empty_poll_interval_ms": 500,
    "max_completion_tokens": 300,
    "temperature": 0.3,
}

FACT_CHECK_PROMPT = (
    "你是一个事实核查助手。对用户提供的文本进行事实核查，判断其内容是否正确。"
    "务必通过联网搜索来核实每一条信息，不要凭记忆判断。直接给出简明的评估结论，"
    "指出正确或错误之处，但也不要吹毛求疵，过度反驳。尽量精炼，不要铺垫和客套话。"
    "请使用 Markdown 格式返回结果。"
)

FOLLOWUP_PROMPT = (
    "你是一个事实核查追问助手。用户会基于一条已经核查过的文本继续提问。"
    "请结合原文、已有分析和既往追问回答，继续通过联网搜索核实，给出简明、准确的回答。"
    "不要客套，不要重复无关背景。请使用 Markdown 格式返回结果。"
)


def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                config.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    return normalize_config(config)


def normalize_config(config):
    normalized = DEFAULT_CONFIG.copy()
    normalized.update(config)
    normalized["window_width"] = clamp_int(normalized.get("window_width"), 320, 900)
    normalized["window_alpha"] = clamp_float(normalized.get("window_alpha"), 0.35, 1.0)
    normalized["poll_interval_ms"] = clamp_int(normalized.get("poll_interval_ms"), 500, 60000)
    normalized["empty_poll_interval_ms"] = clamp_int(
        normalized.get("empty_poll_interval_ms"), 200, 60000
    )
    normalized["max_completion_tokens"] = clamp_int(
        normalized.get("max_completion_tokens"), 100, 8000
    )
    normalized["temperature"] = clamp_float(normalized.get("temperature"), 0.0, 2.0)
    normalized["topmost"] = to_bool(normalized.get("topmost"))
    for key in ("api_url", "api_key", "model"):
        normalized[key] = str(normalized.get(key, "")).strip()
    return normalized


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(normalize_config(config), f, ensure_ascii=False, indent=2)


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [normalize_history_item(item) for item in data[:HISTORY_LIMIT]]


def normalize_history_item(item):
    if not isinstance(item, dict):
        item = {}
    return {
        "created_at": item.get("created_at", ""),
        "content": item.get("content", ""),
        "result": item.get("result", ""),
        "model": item.get("model", ""),
        "followups": item.get("followups", [])
        if isinstance(item.get("followups", []), list)
        else [],
    }


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history[:HISTORY_LIMIT], f, ensure_ascii=False, indent=2)


def clamp_int(value, min_value, max_value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = min_value
    return max(min_value, min(value, max_value))


def clamp_float(value, min_value, max_value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = min_value
    return max(min_value, min(value, max_value))


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def get_clipboard():
    if sys.platform == "win32":
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
        )
        return result.stdout.rstrip("\n")

    root = tk.Tk()
    root.withdraw()
    try:
        content = root.clipboard_get()
    except tk.TclError:
        content = ""
    root.destroy()
    return content


def call_deepseek(text, config):
    api_url = config.get("api_url", "").strip()
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "").strip()
    if not api_url:
        return False, "API 地址未配置，请先在设置中填写。"
    if not api_key:
        return False, "API Key 未配置，请先在设置中填写。"
    if not model:
        return False, "模型未配置，请先在设置中填写。"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": FACT_CHECK_PROMPT},
            {"role": "user", "content": f"请评估以下文本：\n{text}"},
        ],
        "search": True,
        "max_completion_tokens": config["max_completion_tokens"],
        "temperature": config["temperature"],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return True, result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        return False, f"API 请求失败：HTTP {e.code}\n{detail or e.reason}"
    except urllib.error.URLError as e:
        return False, f"网络连接失败：{e.reason}"
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return False, f"API 返回格式异常：{e}"
    except Exception as e:
        return False, f"API 调用失败：{e}"


def call_followup(question, item, config):
    api_url = config.get("api_url", "").strip()
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "").strip()
    if not api_url:
        return False, "API 地址未配置，请先在设置中填写。"
    if not api_key:
        return False, "API Key 未配置，请先在设置中填写。"
    if not model:
        return False, "模型未配置，请先在设置中填写。"

    followup_text = []
    for followup in item.get("followups", []):
        followup_text.append(f"问：{followup.get('question', '')}")
        followup_text.append(f"答：{followup.get('answer', '')}")

    context = (
        f"原文：\n{item.get('content', '')}\n\n"
        f"初次事实核查结果：\n{item.get('result', '')}\n\n"
        f"已有追问：\n{chr(10).join(followup_text) if followup_text else '无'}\n\n"
        f"本次追问：\n{question}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": FOLLOWUP_PROMPT},
            {"role": "user", "content": context},
        ],
        "search": True,
        "max_completion_tokens": config["max_completion_tokens"],
        "temperature": config["temperature"],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return True, result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        return False, f"API 请求失败：HTTP {e.code}\n{detail or e.reason}"
    except urllib.error.URLError as e:
        return False, f"网络连接失败：{e.reason}"
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return False, f"API 返回格式异常：{e}"
    except Exception as e:
        return False, f"API 调用失败：{e}"


class ClipboardOverlay:
    def __init__(self):
        self.config = load_config()
        self.history = load_history()
        self.result_queue = queue.Queue()
        self.last_content = ""
        self.current_source = ""
        self.current_history_index = None
        self.evaluating = False
        self.followup_evaluating = False
        self.refresh_job = None
        self.settings_save_job = None
        self.polling_started = False
        self.closed = False
        self.field_vars = {}
        self.field_entries = {}
        self.current_source_prefix = "当前原文"
        self.source_expanded = False

        self.root = tk.Tk()
        self.root.title("剪贴板评估")
        self.root.configure(bg="#ffffff")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._setup_window()
        self._build_layout()
        self._setup_tags()

        if self._has_required_config():
            self.show_result_view()
            self.start_polling()
        else:
            self.show_settings_view(first_run=True)

        self.root.after(100, self._drain_result_queue)

    def _setup_window(self):
        self.sidebar_width = self.config["window_width"]
        self.root.attributes("-topmost", self.config["topmost"])
        self.root.wm_attributes("-alpha", self.config["window_alpha"])

    def _build_layout(self):
        self.toolbar = tk.Frame(self.root, bg="#f7f7f7", height=42)
        self.toolbar.pack(fill="x")
        self.toolbar.pack_propagate(False)

        self.title_label = tk.Label(
            self.toolbar,
            text="剪贴板事实核查",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#222222",
            bg="#f7f7f7",
        )
        self.title_label.pack(side="left", padx=(12, 8))

        self.pin_btn = self._toolbar_button(self._pin_text(), self.toggle_topmost)
        self.pin_btn.pack(side="right", padx=(0, 8), pady=7)
        self.settings_btn = self._toolbar_button("设置", self.show_settings_view)
        self.settings_btn.pack(side="right", padx=(0, 8), pady=7)
        self.history_btn = self._toolbar_button("历史", self.show_history_view)
        self.history_btn.pack(side="right", padx=(0, 8), pady=7)
        self.result_btn = self._toolbar_button("AI分析", self.show_result_view)
        self.result_btn.pack(side="right", padx=(0, 8), pady=7)

        self.content = tk.Frame(self.root, bg="#ffffff")
        self.content.pack(fill="both", expand=True)

        self.result_frame = tk.Frame(self.content, bg="#ffffff")
        self.source_label = tk.Label(
            self.result_frame,
            text="复制一段文本后，这里会显示事实核查结果。",
            font=("Microsoft YaHei UI", 9),
            fg="#666666",
            bg="#ffffff",
            anchor="w",
            justify="left",
            wraplength=max(260, self.sidebar_width - 32),
            cursor="hand2",
        )
        self.source_label.bind("<Button-1>", self.toggle_source)
        self.source_label.pack(fill="x", padx=16, pady=(10, 0))
        self.text_widget = tk.Text(
            self.result_frame,
            font=("Microsoft YaHei UI", 11),
            fg="#333333",
            bg="#ffffff",
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=14,
            state="disabled",
        )
        self.text_widget.pack(fill="both", expand=True)

        self.followup_frame = tk.Frame(self.result_frame, bg="#f7f7f7")
        self.followup_frame.pack(fill="x", padx=12, pady=(0, 12))
        self.followup_entry = tk.Entry(
            self.followup_frame,
            font=("Microsoft YaHei UI", 10),
            fg="#222222",
            bg="#ffffff",
            relief="flat",
            insertbackground="#222222",
        )
        self.followup_entry.pack(side="left", fill="x", expand=True, padx=8, pady=8, ipady=6)
        self.followup_entry.bind("<Return>", self.send_followup)
        self.followup_btn = self._button(
            self.followup_frame,
            "追问",
            self.send_followup,
            bg="#e5e7eb",
            active_bg="#d1d5db",
            padx=12,
            pady=6,
        )
        self.followup_btn.pack(side="right", padx=(0, 8), pady=8)
        self.followup_status = tk.Label(
            self.result_frame,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg="#666666",
            bg="#ffffff",
            anchor="w",
        )
        self.followup_status.pack(fill="x", padx=16, pady=(0, 8))

        self.settings_frame = tk.Frame(self.content, bg="#ffffff")
        self.history_frame = tk.Frame(self.content, bg="#ffffff")

        self._build_settings_panel()
        self._build_history_panel()
        self._position_right()

    def _toolbar_button(self, text, command):
        return self._button(
            self.toolbar,
            text=text,
            command=command,
            bg="#eeeeee",
            active_bg="#d1d5db",
            padx=8,
            pady=3,
        )

    def _button(
        self,
        parent,
        text,
        command,
        bg="#eeeeee",
        active_bg="#d1d5db",
        fg="#333333",
        active_fg=None,
        font=("Microsoft YaHei UI", 9),
        padx=8,
        pady=3,
    ):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            font=font,
            fg=fg,
            bg=bg,
            activebackground=active_bg,
            activeforeground=active_fg or fg,
            relief="flat",
            borderwidth=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
        )
        button.bind("<Enter>", lambda e: button.config(bg=active_bg))
        button.bind("<Leave>", lambda e: button.config(bg=bg))
        return button

    def _build_settings_panel(self):
        header = tk.Label(
            self.settings_frame,
            text="设置",
            font=("Microsoft YaHei UI", 14, "bold"),
            fg="#1a1a1a",
            bg="#ffffff",
            anchor="w",
        )
        header.pack(fill="x", padx=16, pady=(16, 8))

        form = tk.Frame(self.settings_frame, bg="#ffffff")
        form.pack(fill="both", expand=True, padx=16)

        self._add_entry(form, "API 地址", "api_url")
        self._add_entry(form, "API Key", "api_key", show="*")
        self._add_entry(form, "模型", "model", values=MODEL_OPTIONS)
        self._add_entry(form, "窗口宽度", "window_width")
        self._add_entry(form, "透明度 0.35-1", "window_alpha")
        self._add_entry(form, "轮询间隔 ms", "poll_interval_ms")
        self._add_entry(form, "空剪贴板间隔 ms", "empty_poll_interval_ms")
        self._add_entry(form, "最大输出 token", "max_completion_tokens")
        self._add_entry(form, "temperature 0-2", "temperature")

        self.field_vars["topmost"] = tk.BooleanVar(value=self.config["topmost"])
        topmost_check = tk.Checkbutton(
            form,
            text="窗口置顶",
            variable=self.field_vars["topmost"],
            command=self.schedule_settings_save,
            font=("Microsoft YaHei UI", 10),
            fg="#333333",
            bg="#ffffff",
            activebackground="#ffffff",
            anchor="w",
        )
        topmost_check.pack(fill="x", pady=(6, 12))

        self.settings_status = tk.Label(
            form,
            text="",
            font=("Microsoft YaHei UI", 9),
            fg="#666666",
            bg="#ffffff",
            anchor="w",
            justify="left",
            wraplength=max(260, self.sidebar_width - 32),
        )
        self.settings_status.pack(fill="x", pady=(0, 8))

        save_btn = self._button(
            form,
            text="保存设置",
            command=self.save_settings_now,
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#ffffff",
            bg="#2563eb",
            active_bg="#1d4ed8",
            active_fg="#ffffff",
            padx=12,
            pady=8,
        )
        save_btn.pack(fill="x", pady=(0, 12))

    def _add_entry(self, parent, label, key, show=None, values=None):
        tk.Label(
            parent,
            text=label,
            font=("Microsoft YaHei UI", 9),
            fg="#555555",
            bg="#ffffff",
            anchor="w",
        ).pack(fill="x", pady=(8, 2))
        var = tk.StringVar(value=str(self.config.get(key, "")))
        self.field_vars[key] = var
        if values:
            entry = ttk.Combobox(
                parent,
                textvariable=var,
                values=values,
                font=("Microsoft YaHei UI", 10),
            )
        else:
            entry = tk.Entry(
                parent,
                textvariable=var,
                show=show,
                font=("Microsoft YaHei UI", 10),
                fg="#222222",
                bg="#f7f7f7",
                relief="flat",
                insertbackground="#222222",
            )
        self.field_entries[key] = entry
        entry.bind("<KeyRelease>", self.schedule_settings_save)
        entry.bind("<FocusOut>", self.schedule_settings_save)
        entry.bind("<Return>", self.save_settings_now)
        if values:
            entry.bind("<<ComboboxSelected>>", self.schedule_settings_save)
        entry.pack(fill="x", ipady=6)

    def _build_history_panel(self):
        top = tk.Frame(self.history_frame, bg="#ffffff")
        top.pack(fill="x", padx=16, pady=(16, 8))

        tk.Label(
            top,
            text="历史记录",
            font=("Microsoft YaHei UI", 14, "bold"),
            fg="#1a1a1a",
            bg="#ffffff",
        ).pack(side="left")
        self._button(
            top,
            text="清空",
            command=self.clear_history,
            font=("Microsoft YaHei UI", 9),
            fg="#b91c1c",
            bg="#fee2e2",
            active_bg="#fecaca",
            padx=8,
            pady=3,
        ).pack(side="right")

        history_body = tk.Frame(self.history_frame, bg="#ffffff")
        history_body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.history_canvas = tk.Canvas(
            history_body,
            bg="#ffffff",
            highlightthickness=0,
            borderwidth=0,
        )
        history_scrollbar = tk.Scrollbar(
            history_body,
            orient="vertical",
            command=self.history_canvas.yview,
        )
        self.history_list = tk.Frame(self.history_canvas, bg="#ffffff")
        self.history_canvas_window = self.history_canvas.create_window(
            (0, 0), window=self.history_list, anchor="nw"
        )
        self.history_canvas.configure(yscrollcommand=history_scrollbar.set)
        self.history_canvas.pack(side="left", fill="both", expand=True)
        history_scrollbar.pack(side="right", fill="y")
        self.history_list.bind("<Configure>", self._update_history_scrollregion)
        self.history_canvas.bind("<Configure>", self._resize_history_canvas)
        self.refresh_history_panel()

    def _setup_tags(self):
        base_font = ("Microsoft YaHei UI", 11)
        bold_font = ("Microsoft YaHei UI", 11, "bold")
        italic_font = ("Microsoft YaHei UI", 11, "italic")
        code_font = ("Consolas", 11)
        h1_font = ("Microsoft YaHei UI", 15, "bold")
        h2_font = ("Microsoft YaHei UI", 13, "bold")
        h3_font = ("Microsoft YaHei UI", 11, "bold")

        self.text_widget.tag_configure("bold", font=bold_font)
        self.text_widget.tag_configure("italic", font=italic_font)
        self.text_widget.tag_configure(
            "code", font=code_font, background="#f0f0f0", foreground="#c7254e"
        )
        self.text_widget.tag_configure(
            "h1", font=h1_font, foreground="#1a1a1a", spacing3=6, spacing1=12
        )
        self.text_widget.tag_configure(
            "h2", font=h2_font, foreground="#1a1a1a", spacing3=4, spacing1=10
        )
        self.text_widget.tag_configure(
            "h3", font=h3_font, foreground="#333333", spacing3=2, spacing1=8
        )
        self.text_widget.tag_configure("bullet", lmargin1=6, lmargin2=14)
        self.text_widget.tag_configure("normal", font=base_font)

    def _has_required_config(self):
        return bool(self.config.get("api_url") and self.config.get("api_key"))

    def _pin_text(self):
        return "已置顶" if self.config["topmost"] else "置顶"

    def _show_frame(self, frame):
        for child in self.content.winfo_children():
            child.pack_forget()
        frame.pack(fill="both", expand=True)

    def show_result_view(self):
        self._show_frame(self.result_frame)

    def show_settings_view(self, first_run=False):
        self._sync_settings_fields()
        if first_run:
            self.settings_status.config(text="首次使用请填写 API 地址和 API Key。")
        self._show_frame(self.settings_frame)

    def show_history_view(self):
        self.refresh_history_panel()
        self._show_frame(self.history_frame)

    def _sync_settings_fields(self):
        for key, var in self.field_vars.items():
            if key == "topmost":
                var.set(self.config["topmost"])
            else:
                var.set(str(self.config.get(key, "")))

    def schedule_settings_save(self, event=None):
        if self.settings_save_job is not None:
            self.root.after_cancel(self.settings_save_job)
        self.settings_save_job = self.root.after(500, self.auto_save_settings)

    def auto_save_settings(self):
        self.settings_save_job = None
        self.apply_settings(manual=False)

    def save_settings_now(self, event=None):
        if self.settings_save_job is not None:
            self.root.after_cancel(self.settings_save_job)
            self.settings_save_job = None
        if self.apply_settings(manual=True):
            self.show_result_view()
        return "break"

    def apply_settings(self, manual=False):
        old_width = self.config["window_width"]
        old_config = self.config.copy()
        raw = {}
        for key, var in self.field_vars.items():
            raw[key] = var.get() if key != "topmost" else var.get()
        new_config = normalize_config(raw)

        self.config = new_config
        save_config(self.config)
        self.sidebar_width = self.config["window_width"]
        self.root.attributes("-topmost", self.config["topmost"])
        self.root.wm_attributes("-alpha", self.config["window_alpha"])
        self.pin_btn.config(text=self._pin_text())
        self.source_label.config(wraplength=max(260, self.sidebar_width - 32))
        self.settings_status.config(wraplength=max(260, self.sidebar_width - 32))
        if not self.config["api_url"] or not self.config["api_key"]:
            self.settings_status.config(text="设置不完整：API 地址和 API Key 不能为空。", fg="#b91c1c")
        elif self._settings_were_corrected(raw, self.config):
            self.settings_status.config(text="数值已自动修正并保存。", fg="#92400e")
            self._sync_settings_fields()
        else:
            status = "设置已保存。" if manual else "已自动保存。"
            self.settings_status.config(text=status, fg="#166534")

        if old_width != self.sidebar_width:
            self._position_right()
            self.refresh_history_panel()

        if not self.polling_started and self._has_required_config():
            self.show_result_view()
            self.start_polling()

        if old_config["topmost"] != self.config["topmost"] and "topmost" in self.field_vars:
            self.field_vars["topmost"].set(self.config["topmost"])
        return self._has_required_config()

    def _settings_were_corrected(self, raw, config):
        numeric_keys = (
            "window_width",
            "window_alpha",
            "poll_interval_ms",
            "empty_poll_interval_ms",
            "max_completion_tokens",
            "temperature",
        )
        for key in numeric_keys:
            raw_value = str(raw.get(key, "")).strip()
            normalized_value = str(config.get(key, "")).strip()
            try:
                if isinstance(config.get(key), float):
                    if float(raw_value) != float(normalized_value):
                        return True
                elif int(raw_value) != int(float(normalized_value)):
                    return True
            except (TypeError, ValueError):
                return True
        return False

    def toggle_topmost(self):
        self.config["topmost"] = not self.config["topmost"]
        self.root.attributes("-topmost", self.config["topmost"])
        self.pin_btn.config(text=self._pin_text())
        if "topmost" in self.field_vars:
            self.field_vars["topmost"].set(self.config["topmost"])
        save_config(self.config)

    def render_md(self, md_text):
        self.text_widget.config(state="normal")
        self.text_widget.delete("1.0", "end")

        inline_re = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`")

        for line in md_text.split("\n"):
            stripped = line.rstrip()
            block_tag, block_text = self._parse_block(stripped)

            if block_tag is None:
                self.text_widget.insert("end", "\n")
                continue

            if block_tag.startswith("h"):
                self.text_widget.insert("end", block_text + "\n", block_tag)
            elif block_tag == "bullet":
                self.text_widget.insert("end", "• ", "bullet")
                self._insert_inline(block_text, inline_re)
                self.text_widget.insert("end", "\n")
            else:
                self._insert_inline(block_text, inline_re)
                self.text_widget.insert("end", "\n")

        self.text_widget.config(state="disabled")

    def render_history_item(self, item):
        parts = [item.get("result", "")]
        followups = item.get("followups", [])
        if followups:
            parts.append("\n### 追问")
            for followup in followups:
                question = followup.get("question", "")
                answer = followup.get("answer", "")
                parts.append(f"\n**问：** {question}\n\n**答：**\n{answer}")
        self.render_md("\n".join(parts))

    def _parse_block(self, line):
        if not line:
            return None, None

        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            return f"h{level}", m.group(2)

        m = re.match(r"^[\-\*]\s+(.*)", line)
        if m:
            return "bullet", m.group(1)

        return "normal", line

    def _insert_inline(self, text, pattern):
        idx = 0
        for m in pattern.finditer(text):
            if m.start() > idx:
                self.text_widget.insert("end", text[idx : m.start()], "normal")
            if m.group(1):
                self.text_widget.insert("end", m.group(1), "bold")
            elif m.group(2):
                self.text_widget.insert("end", m.group(2), "italic")
            elif m.group(3):
                self.text_widget.insert("end", m.group(3), "code")
            idx = m.end()
        if idx < len(text):
            self.text_widget.insert("end", text[idx:], "normal")

    def _set_loading(self, text):
        self.text_widget.config(state="normal")
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", text, "normal")
        self.text_widget.config(state="disabled")

    def _set_source(self, content, prefix="当前原文"):
        self.current_source = content
        self.current_source_prefix = prefix
        self.source_expanded = False
        self._render_source_label()

    def toggle_source(self, event=None):
        if not self.current_source:
            return
        self.source_expanded = not self.source_expanded
        self._render_source_label()

    def _render_source_label(self):
        if not self.current_source:
            self.source_label.config(text="复制一段文本后，这里会显示事实核查结果。")
            return
        if self.source_expanded:
            text = self.current_source
            hint = "点击收起"
        else:
            text = self._summary(self.current_source, 120)
            hint = "点击展开"
        self.source_label.config(text=f"{self.current_source_prefix}：{text}  ({hint})")

    def _summary(self, text, limit):
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _position_right(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        h = sh - 80
        x = sw - self.sidebar_width - 10
        y = 40
        self.root.geometry(f"{self.sidebar_width}x{h}+{x}+{y}")

    def start_polling(self):
        if self.polling_started:
            return
        self.polling_started = True
        self.refresh()

    def refresh(self):
        if self.closed:
            return
        try:
            content = get_clipboard()
        except Exception as e:
            self.show_result_view()
            self.render_md(f"读取剪贴板失败：{e}")
            content = ""

        if (
            content
            and content != self.last_content
            and not self.evaluating
            and not self.followup_evaluating
        ):
            self.last_content = content
            self.evaluating = True
            self._set_source(content)
            self.show_result_view()
            self._set_loading("AI 评估中...")
            config_snapshot = self.config.copy()
            worker = threading.Thread(
                target=self._evaluate_worker,
                args=(content, config_snapshot),
                daemon=True,
            )
            worker.start()

        next_check = (
            self.config["poll_interval_ms"]
            if content
            else self.config["empty_poll_interval_ms"]
        )
        self.refresh_job = self.root.after(next_check, self.refresh)

    def _evaluate_worker(self, content, config_snapshot):
        ok, result = call_deepseek(content, config_snapshot)
        self.result_queue.put(("analysis", ok, content, result, config_snapshot.get("model", "")))

    def send_followup(self, event=None):
        question = self.followup_entry.get().strip()
        if not question:
            self.followup_status.config(text="请输入追问内容。", fg="#b91c1c")
            return "break"
        if self.current_history_index is None or self.current_history_index >= len(self.history):
            self.followup_status.config(text="请先复制文本完成一次 AI 分析。", fg="#b91c1c")
            return "break"
        if self.followup_evaluating:
            return "break"

        self.followup_evaluating = True
        self.followup_status.config(text="AI 追问中...", fg="#666666")
        self.followup_entry.delete(0, "end")
        index = self.current_history_index
        history_snapshot = json.loads(json.dumps(self.history[index], ensure_ascii=False))
        config_snapshot = self.config.copy()
        worker = threading.Thread(
            target=self._followup_worker,
            args=(index, question, history_snapshot, config_snapshot),
            daemon=True,
        )
        worker.start()
        return "break"

    def _followup_worker(self, index, question, history_snapshot, config_snapshot):
        ok, answer = call_followup(question, history_snapshot, config_snapshot)
        self.result_queue.put(
            ("followup", ok, index, question, answer, config_snapshot.get("model", ""))
        )

    def _drain_result_queue(self):
        if self.closed:
            return
        try:
            while True:
                item = self.result_queue.get_nowait()
                if item[0] == "analysis":
                    _, ok, content, result, model = item
                    self.evaluating = False
                    self.show_result_view()
                    if ok:
                        self.current_history_index = self.add_history(content, result, model)
                        self.render_history_item(self.history[self.current_history_index])
                    else:
                        self.current_history_index = None
                        self.render_md(result)
                elif item[0] == "followup":
                    _, ok, index, question, answer, model = item
                    self.followup_evaluating = False
                    self.followup_status.config(text="")
                    if ok and 0 <= index < len(self.history):
                        followup = {
                            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "question": question,
                            "answer": answer,
                            "model": model,
                        }
                        self.history[index].setdefault("followups", []).append(followup)
                        save_history(self.history)
                        self.current_history_index = index
                        self.render_history_item(self.history[index])
                        self.refresh_history_panel()
                    else:
                        self.followup_status.config(text=answer, fg="#b91c1c")
                        if (
                            self.current_history_index is not None
                            and self.current_history_index < len(self.history)
                        ):
                            self.render_history_item(self.history[self.current_history_index])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_result_queue)

    def add_history(self, content, result, model):
        item = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content": content,
            "result": result,
            "model": model,
            "followups": [],
        }
        self.history.insert(0, item)
        self.history = self.history[:HISTORY_LIMIT]
        save_history(self.history)
        self.refresh_history_panel()
        return 0

    def refresh_history_panel(self):
        for child in self.history_list.winfo_children():
            child.destroy()

        if not self.history:
            tk.Label(
                self.history_list,
                text="暂无历史记录。",
                font=("Microsoft YaHei UI", 10),
                fg="#777777",
                bg="#ffffff",
                anchor="w",
            ).pack(fill="x", padx=4, pady=8)
            return

        for index, item in enumerate(self.history):
            text = f"{item.get('created_at', '')}\n{self._summary(item.get('content', ''), 58)}"
            btn = tk.Button(
                self.history_list,
                text=text,
                command=lambda i=index: self.open_history_item(i),
                font=("Microsoft YaHei UI", 9),
                fg="#222222",
                bg="#f5f5f5",
                activebackground="#e5e7eb",
                relief="flat",
                borderwidth=0,
                justify="left",
                anchor="w",
                padx=8,
                pady=8,
                cursor="hand2",
                wraplength=max(240, self.sidebar_width - 44),
            )
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#e5e7eb"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg="#f5f5f5"))
            btn.pack(fill="x", padx=4, pady=4)
        self._update_history_scrollregion()

    def _update_history_scrollregion(self, event=None):
        self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))

    def _resize_history_canvas(self, event):
        self.history_canvas.itemconfig(self.history_canvas_window, width=event.width)

    def open_history_item(self, index):
        if index < 0 or index >= len(self.history):
            return
        item = self.history[index]
        self.current_history_index = index
        self._set_source(item.get("content", ""), prefix="历史原文")
        self.show_result_view()
        self.render_history_item(item)

    def clear_history(self):
        self.history = []
        self.current_history_index = None
        save_history(self.history)
        self.refresh_history_panel()

    def close(self):
        self.closed = True
        if self.refresh_job is not None:
            try:
                self.root.after_cancel(self.refresh_job)
            except tk.TclError:
                pass
        if self.settings_save_job is not None:
            try:
                self.root.after_cancel(self.settings_save_job)
            except tk.TclError:
                pass
        self.root.destroy()

    def run(self):
        self._position_right()
        self.root.mainloop()


if __name__ == "__main__":
    overlay = ClipboardOverlay()
    overlay.run()
