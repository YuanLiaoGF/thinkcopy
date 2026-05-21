import tkinter as tk
import tkinter.font as tkfont
import subprocess
import sys
import json
import urllib.request
import re

API_KEY = ""
API_URL = "https://api.deepseek.com/chat/completions"


def get_clipboard():
    if sys.platform == "win32":
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
        )
        return result.stdout.rstrip("\n")
    else:
        root = tk.Tk()
        root.withdraw()
        try:
            content = root.clipboard_get()
        except tk.TclError:
            content = ""
        root.destroy()
        return content


def call_deepseek(text):
    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {
                "role": "system",
                "content": "你是一个事实核查助手。对用户提供的文本进行事实核查，判断其内容是否正确。务必通过联网搜索来核实每一条信息，不要凭记忆判断。直接给出简明的评估结论，指出正确或错误之处，但也不要吹毛求疵，过度反驳。尽量精炼，不要铺垫和客套话。请使用 Markdown 格式返回结果。",
            },
            {
                "role": "user",
                "content": f"请评估以下文本：\n{text}",
            },
        ],
        "search": True,
        "max_completion_tokens": 300,
        "temperature": 0.3,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"API 调用失败: {e}"


class ClipboardOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("剪贴板评估")
        self.root.attributes("-topmost", False)
        self.root.configure(bg="#ffffff")

        self.sidebar_width = 360

        self.text_widget = tk.Text(
            self.root,
            font=("Microsoft YaHei UI", 11),
            fg="#333333",
            bg="#ffffff",
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=16,
            state="disabled",
        )
        self.text_widget.pack(fill="both", expand=True)

        self._setup_tags()

        self.close_btn = tk.Label(
            self.root,
            text="✕",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#999999",
            bg="#ffffff",
            cursor="hand2",
        )
        self.close_btn.bind("<Button-1>", lambda e: self.root.destroy())

        self.last_content = ""
        self.evaluating = False

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
        self.text_widget.tag_configure("code", font=code_font, background="#f0f0f0",
                                        foreground="#c7254e")
        self.text_widget.tag_configure("h1", font=h1_font, foreground="#1a1a1a",
                                        spacing3=6, spacing1=12)
        self.text_widget.tag_configure("h2", font=h2_font, foreground="#1a1a1a",
                                        spacing3=4, spacing1=10)
        self.text_widget.tag_configure("h3", font=h3_font, foreground="#333333",
                                        spacing3=2, spacing1=8)
        self.text_widget.tag_configure("bullet", lmargin1=6, lmargin2=14)
        self.text_widget.tag_configure("normal", font=base_font)

    def render_md(self, md_text):
        self.text_widget.config(state="normal")
        self.text_widget.delete("1.0", "end")

        inline_re = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`')

        for line in md_text.split('\n'):
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

    def _parse_block(self, line):
        if not line:
            return None, None

        m = re.match(r'^(#{1,3})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            return f"h{level}", m.group(2)

        m = re.match(r'^[\-\*]\s+(.*)', line)
        if m:
            return "bullet", m.group(1)

        return "normal", line

    def _insert_inline(self, text, pattern):
        idx = 0
        for m in pattern.finditer(text):
            if m.start() > idx:
                self.text_widget.insert("end", text[idx:m.start()], "normal")
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

    def _position_right(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        h = sh - 80
        x = sw - self.sidebar_width - 10
        y = 40
        self.root.geometry(f"{self.sidebar_width}x{h}+{x}+{y}")
        self.close_btn.place(x=self.sidebar_width - 10, y=6, anchor="ne")

    def refresh(self):
        content = get_clipboard()
        if content and content != self.last_content and not self.evaluating:
            self.last_content = content
            self.evaluating = True
            self._set_loading("AI 评估中...")
            self.root.after(100, lambda: self._do_evaluate(content))

        next_check = 3000 if content else 500
        self.root.after(next_check, self.refresh)

    def _do_evaluate(self, content):
        result = call_deepseek(content)
        self.render_md(result)
        self.evaluating = False

    def run(self):
        self._position_right()
        self.refresh()
        self.root.mainloop()


if __name__ == "__main__":
    overlay = ClipboardOverlay()
    overlay.run()
