from __future__ import annotations
import json
import re
import webbrowser
from .config import load
from .store import Store


def ai_question(scan_id=None, plan_id=None, anonymize=True):
    content = {"purpose": "Đánh giá dung lượng và đề xuất dọn Windows. Không đưa lệnh xóa mù; phân loại cần giữ/có thể dọn/cần kiểm tra thêm.",
               "constraints": "Không đề xuất xóa thủ công WinSxS, DriverStore, Windows Installer; giữ dữ liệu cá nhân, project và .venv. Không đọc Chrome/Edge."}
    if scan_id:
        s = Store()
        content["snapshot"] = s.scan(scan_id)
        content["top_changes"] = s.compare(s.previous(scan_id), scan_id, limit=30)
        s.close()
    if plan_id:
        from .cleanup import get_plan
        from .cleanup import plan_page
        p = get_plan(plan_id,compact=True)
        examples=plan_page(plan_id,limit=30)['entries'] if p.get('entry_archive') else p['entries'][:30]
        content["cleanup_plan"] = {"bytes": p["bytes"], "rules": p["rules"], "blocked": p["blocked"], "examples": examples}
    text = "Xin xem metadata dưới đây và tư vấn mục có thể dọn, tác động, cách dọn chính thức, và các điều kiện cần kiểm tra. Đây chỉ là câu hỏi tham khảo, không cho phép thực thi.\n\n" + json.dumps(content, ensure_ascii=False, indent=2)
    if anonymize:
        from .log_reader import redact
        text=redact(text)
    return text


def open_provider(name):
    url = load()["ai_providers"].get(name)
    if not url or not url.startswith("https://"):
        raise ValueError("Provider/URL không hợp lệ")
    webbrowser.open(url)
    return {"provider": name, "url": url, "note": "Mở trang; dán câu hỏi đã sao chép. Login tùy dịch vụ; không gửi tự động."}
