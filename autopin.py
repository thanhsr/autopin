#!/usr/bin/env python3
"""
autopin.py - Tạo file CSV để bulk-upload Pin lên Pinterest.

Quy trình (chạy trong thư mục ~/Desktop/autopin):
    python3 autopin.py push      # đẩy ảnh lên GitHub (repo public "autopin")
    python3 autopin.py csv       # xuất CSV (tối đa 200 pin/file) kèm lịch đăng
    python3 autopin.py all       # push + csv

Nội dung pin (title/description/keywords) nằm trong content/<thư mục ảnh>.json.
Không có API: nhờ Claude trong chat viết file này cho ảnh mới.
Có API key: python3 autopin.py content  (tự viết cho các ảnh chưa có nội dung)

Sau đó vào Pinterest > Settings > Import content > Upload .csv và chọn file trong thư mục csv/.
"""

import base64
import csv
import json
import os
import random
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================ CẤU HÌNH ============================
GITHUB_USER = "thanhsr"   # <-- sửa thành username GitHub của bạn
GITHUB_REPO = "autopin"
BRANCH = "main"

BOARDS = {
    # thư mục ảnh : (tên board trên Pinterest, link đích)
    "love": ("Love, Slowly", "https://www.amazon.com/dp/B0HSJFWBPC"),
    "bloom": ("A Road in Bloom", "https://www.amazon.com/dp/B0HQW9N3FN"),
}

CLAUDE_MODEL = "claude-sonnet-5-5"     # có thể đổi sang "claude-haiku-5-5" cho rẻ hơn

PINS_PER_DAY = 10                      # số pin mỗi ngày
START_DATE = None                      # None = bắt đầu từ ngày mai, hoặc "2026-11-26"
# Giờ đăng theo UTC. Mặc định rải từ 8h sáng tới 11h tối giờ miền Đông nước Mỹ
# (khách Amazon.com). Số phần tử nên >= PINS_PER_DAY.
POST_TIMES_UTC = ["13:00", "14:30", "16:00", "17:30", "19:00",
                  "20:30", "22:00", "23:30", "01:00", "02:30"]
JITTER_MINUTES = 20                    # lệch ngẫu nhiên +/- vài phút cho tự nhiên
MAX_ROWS_PER_CSV = 10                  # Pinterest chỉ cho tối đa 10 pin hẹn giờ cùng lúc -> 1 file/ngày
PUSH_BATCH = 25                        # số ảnh mỗi lần git push
# ===================================================================

ROOT = Path(__file__).resolve().parent
CONTENT_DIR = ROOT / "content"
CSV_DIR = ROOT / "csv"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}

ANGLES = [
    "a vocabulary tip built around one Spanish word or phrase from the page",
    "a short grammar insight from the Spanish note on the page",
    "the romantic or emotional message of the quote, then the Spanish behind it",
    "motivation for adult beginners who feel too busy to learn Spanish",
    "learning Spanish through reading instead of drills or apps",
    "a 'Spanish phrase of the day' style hook",
    "learning Spanish as a couple or for someone you love",
    "a cozy reading-habit angle: one page a day, slowly",
    "a gift idea for a Spanish learner or book lover",
    "a 'say it in Spanish' angle: how to express this feeling in Spanish",
    "why bilingual English-Spanish side-by-side text makes learning easier",
    "a self-reflection question inspired by the page, plus the Spanish words",
]

PROMPT = """This image is a page from "Love, Slowly / Amor, despacio", a bilingual English-Spanish book of short quotes about love and life, with Spanish translations in brackets and a "Spanish note" at the bottom.

Write ONE Pinterest pin about learning Spanish through this bilingual English-Spanish book, based on what this specific page actually says.

Angle for this pin: {angle}

Rules:
- Language: English (Spanish words/phrases from the page are welcome).
- title: max 90 characters, catchy, searchable, no hashtags, no emojis, no quotation marks around the whole title.
- description: 250-450 characters. Mention one concrete Spanish word or phrase from this page and what it means. Naturally mention it is a bilingual English-Spanish book. End with a soft call to action (e.g. tap to get the book). No hashtags. No emojis.
- keywords: 5-8 comma-separated search terms (e.g. learn spanish, bilingual book, spanish for beginners).
- Must be clearly different from these recent titles:
{recent}

Return ONLY valid JSON: {{"title": "...", "description": "...", "keywords": "..."}}"""


def die(msg):
    print(f"\n❌ {msg}")
    sys.exit(1)


def list_images(folder):
    d = ROOT / folder
    if not d.is_dir():
        die(f"Không thấy thư mục ảnh: {d}")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT)


def media_url(folder, filename):
    return (f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/"
            f"{BRANCH}/{folder}/{filename}")


# ----------------------------- PUSH -----------------------------
def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def cmd_push():
    if GITHUB_USER == "YOUR_GITHUB_USERNAME":
        die("Hãy sửa GITHUB_USER ở đầu file autopin.py trước.")
    if not (ROOT / ".git").exists():
        git("init")
        git("checkout", "-b", BRANCH)
        (ROOT / ".gitignore").write_text("csv/\n.env\n__pycache__/\n.DS_Store\n")
    remote = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    if git("remote", check=False) == "":
        git("remote", "add", "origin", remote)
    git("config", "http.postBuffer", "524288000")

    files = []
    for folder in BOARDS:
        files += [str(p.relative_to(ROOT)) for p in list_images(folder)]
    tracked = set(git("ls-files").splitlines())
    status = git("status", "--porcelain").splitlines()
    modified = {line[3:].strip('"') for line in status}
    todo = [f for f in files if f not in tracked or f in modified]

    extra = [f for f in [".gitignore", "autopin.py"] if (ROOT / f).exists()]
    print(f"Có {len(todo)} ảnh cần đẩy lên GitHub.")
    batches = [todo[i:i + PUSH_BATCH] for i in range(0, len(todo), PUSH_BATCH)] or [[]]
    for n, batch in enumerate(batches, 1):
        git("add", *(batch + extra))
        if git("status", "--porcelain") == "":
            continue
        git("commit", "-m", f"Add images batch {n}/{len(batches)}")
        print(f"  Đang push đợt {n}/{len(batches)} ({len(batch)} ảnh)...")
        r = subprocess.run(["git", "push", "-u", "origin", BRANCH], cwd=ROOT)
        if r.returncode != 0:
            die("git push lỗi. Kiểm tra repo đã tạo (Public) và bạn đã đăng nhập GitHub.")
    print("✅ Đã đẩy xong ảnh lên GitHub.")


# ---------------------------- CONTENT ----------------------------
def load_content(folder):
    f = CONTENT_DIR / f"{folder}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_content(folder, data):
    CONTENT_DIR.mkdir(exist_ok=True)
    f = CONTENT_DIR / f"{folder}.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ask_claude(client, img_path, angle, recent):
    media = {".png": "image/png", ".jpg": "image/jpeg",
             ".jpeg": "image/jpeg", ".webp": "image/webp"}[img_path.suffix.lower()]
    b64 = base64.standard_b64encode(img_path.read_bytes()).decode()
    recent_txt = "\n".join(f"- {t}" for t in recent) or "- (none yet)"
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
            {"type": "text", "text": PROMPT.format(angle=angle, recent=recent_txt)},
        ]}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(text)
    return {
        "title": data["title"].strip()[:100],
        "description": data["description"].strip()[:500],
        "keywords": data.get("keywords", "").strip(),
    }


def cmd_content():
    pending = sum(1 for f in BOARDS for p in list_images(f) if p.name not in load_content(f))
    if pending == 0:
        print("✅ Tất cả ảnh đã có nội dung, không cần viết thêm.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        die(f"Có {pending} ảnh chưa có nội dung. Không có API key thì nhờ Claude trong chat "
            "viết thêm vào content/*.json, rồi chạy lại 'csv'.")
    try:
        import anthropic
    except ImportError:
        die("Thiếu thư viện. Chạy: pip3 install anthropic")
    client = anthropic.Anthropic()

    for folder in BOARDS:
        data = load_content(folder)
        images = list_images(folder)
        todo = [p for p in images if p.name not in data]
        print(f"[{folder}] {len(data)} pin đã có nội dung, cần viết thêm {len(todo)}.")
        for i, img in enumerate(todo, 1):
            titles = {v["title"].lower() for v in data.values()}
            recent = [v["title"] for v in list(data.values())[-25:]]
            for attempt in range(3):
                try:
                    item = ask_claude(client, img, random.choice(ANGLES), recent)
                    if item["title"].lower() in titles:
                        raise ValueError("trùng tiêu đề")
                    break
                except Exception as e:
                    print(f"   thử lại {img.name}: {e}")
                    item = None
            if not item:
                print(f"   ⚠️ bỏ qua {img.name}")
                continue
            data[img.name] = item
            save_content(folder, data)  # lưu sau mỗi ảnh, dừng giữa chừng không mất
            print(f"  [{i}/{len(todo)}] {img.name}: {item['title']}")
    print(f"✅ Nội dung đã lưu trong {CONTENT_DIR}/ (có thể mở ra sửa tay).")


# ------------------------------ CSV ------------------------------
def schedule(n):
    if START_DATE:
        day = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    else:
        day = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    times = POST_TIMES_UTC[:PINS_PER_DAY]
    out = []
    i = 0
    while len(out) < n:
        for t in times:
            if len(out) >= n:
                break
            hh, mm = map(int, t.split(":"))
            base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            # giờ < 12 UTC coi như thuộc buổi tối hôm trước ở Mỹ -> sang ngày UTC kế tiếp
            if hh < 12:
                base += timedelta(days=1)
            dt = base + timedelta(hours=hh, minutes=mm + random.randint(-JITTER_MINUTES, JITTER_MINUTES))
            out.append(dt.strftime("%Y-%m-%dT%H:%M:%S"))
        day += timedelta(days=1)
        i += 1
    return out


def url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


def cmd_csv():
    if GITHUB_USER == "YOUR_GITHUB_USERNAME":
        die("Hãy sửa GITHUB_USER ở đầu file autopin.py trước.")
    CSV_DIR.mkdir(exist_ok=True)
    rows = []
    for folder, (board, link) in BOARDS.items():
        data = load_content(folder)
        images = list_images(folder)
        missing = [p.name for p in images if p.name not in data]
        if missing:
            print(f"⚠️ [{folder}] {len(missing)} ảnh chưa có nội dung, sẽ bỏ qua (chạy 'content' trước).")
        for p in images:
            if p.name in data:
                c = data[p.name]
                rows.append({
                    "Title": c["title"],
                    "Media URL": media_url(folder, p.name),
                    "Pinterest board": board,
                    "Thumbnail": "",
                    "Description": c["description"],
                    "Link": link,
                    "Publish date": "",
                    "Keywords": c.get("keywords", ""),
                })
    if not rows:
        die("Chưa có pin nào để xuất.")

    print("Kiểm tra link ảnh trên GitHub...")
    if not url_ok(rows[0]["Media URL"]):
        die(f"Không mở được {rows[0]['Media URL']}\n"
            "Repo đã Public chưa? Đã chạy 'push' chưa?")

    random.shuffle(rows)  # trộn để các trang liền nhau không đăng sát nhau
    for r, d in zip(rows, schedule(len(rows))):
        r["Publish date"] = d

    fields = ["Title", "Media URL", "Pinterest board", "Thumbnail",
              "Description", "Link", "Publish date", "Keywords"]
    parts = [rows[i:i + MAX_ROWS_PER_CSV] for i in range(0, len(rows), MAX_ROWS_PER_CSV)]
    for n, part in enumerate(parts, 1):
        part.sort(key=lambda r: r["Publish date"])
        f = CSV_DIR / f"day{n:02d}_upload_{part[0]['Publish date'][:10]}.csv"
        with f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(part)
        print(f"  {f.name}: {len(part)} pin, {part[0]['Publish date'][:10]} → "
              f"{part[-1]['Publish date'][:10]}")
    print(f"✅ Xong. Mỗi ngày upload 1 file theo thứ tự (dayNN_upload_<ngày>.csv), "
          "trước giờ đăng đầu tiên trong file (khoảng 19h giờ Việt Nam).")


if __name__ == "__main__":
    cmds = {"push": [cmd_push], "content": [cmd_content], "csv": [cmd_csv],
            "all": [cmd_push, cmd_csv]}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(0)
    for fn in cmds[sys.argv[1]]:
        fn()
