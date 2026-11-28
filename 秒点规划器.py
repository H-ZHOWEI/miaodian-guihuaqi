# -*- coding: utf-8 -*-
"""
秒点规划器 v1.16.0
根据每个账号当前秒点余额，模拟并生成「点赞 / 登录领奖 / 复制转账 / 打包」的执行方案。
用 Python 3.12 + tkinter，可打包为单文件 exe。
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import csv
import os
import sys
import calendar
import unicodedata
import re
import shutil
import time
import hashlib
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error
import datetime
import calendar

APP_NAME = "秒点规划器"
VERSION = "1.16.0"
APP_VERSION = VERSION   # 版本更新模块使用

# ===== 内置「版本更新」配置 =====
GITHUB_OWNER = "H-ZHOWEI"
GITHUB_REPO = "miaodian-guihuaqi"
UPDATE_MANIFEST_PRIMARY = ("https://raw.githubusercontent.com/%s/%s/main/version.json"
                           % (GITHUB_OWNER, GITHUB_REPO))
# 下载镜像轮换：直链 / ghproxy.net / gh-proxy.com / github.com/raw
_EXE_MIRROR_PREFIX = ["https://ghproxy.net/", "https://gh-proxy.com/"]
_UPDATE_SKIP_NETWORK = False   # 自测可置 True 跳过网络

WEEK_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


# ===== 内置「版本更新」核心（无 GUI，纯函数）=====
def _app_dir():
    """程序所在目录：打包后为 exe 所在目录，源码运行为 py 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def parse_version(v):
    """'1.10' -> (1,10,0)；'v1.9.2' -> (1,9,2)。按数字段比较，1.10 > 1.9。"""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for seg in v.split("."):
        m = re.findall(r"\d+", seg)
        parts.append(int(m[0]) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def cmp_version(a, b):
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def update_config_path():
    return os.path.join(_app_dir(), "update_config.json")


def load_update_config():
    cfg = {"source": None, "skip_version": None}
    p = update_config_path()
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_update_config(cfg):
    try:
        with open(update_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _mirror_urls(primary, nocache=True):
    """清单/下载地址 -> 多镜像列表（直链 + 代理镜像）。不使用 jsDelivr。"""
    def stamp(u):
        if not nocache:
            return u
        sep = "&" if "?" in u else "?"
        return u + sep + "_t=" + str(int(time.time() * 1000))

    urls = [stamp(primary)]
    for p in _EXE_MIRROR_PREFIX:
        urls.append(stamp(p.rstrip("/") + "/" + primary))
    if "raw.githubusercontent.com" in primary:
        alt = primary.replace("raw.githubusercontent.com", "github.com", 1)
        alt = re.sub(r"github\.com/([^/]+)/([^/]+)/main/",
                     r"github.com/\1/\2/raw/main/", alt, count=1)
        if alt != primary:
            urls.append(stamp(alt))
    return urls


def _http_get_bytes(url, start=None, end=None, timeout=30):
    headers = {"User-Agent": "Mozilla/5.0"}
    if start is not None:
        headers["Range"] = "bytes=%d-%s" % (start, end if end is not None else "")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_manifest(urls):
    """并发请求多个清单地址，返回 (best_dict, hit_source) 或 (None, None)。"""
    results = {}

    def worker(u):
        try:
            raw = _http_get_bytes(u, timeout=10)
            txt = raw.decode("utf-8", "replace")
            if not txt.lstrip().startswith("{"):
                return
            d = json.loads(txt)
            if isinstance(d, dict) and d.get("version") and d.get("download_url"):
                results[u] = d
        except Exception:
            pass

    ths = [threading.Thread(target=worker, args=(u,), daemon=True) for u in urls]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=12)
    if not results:
        return None, None
    best = max(results, key=lambda u: parse_version(results[u].get("version", "0")))
    return results[best], best


def _probe_size(mirrors):
    for base in mirrors:
        try:
            req = urllib.request.Request(base, headers={"User-Agent": "Mozilla/5.0",
                                                        "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                cr = r.headers.get("Content-Range", "")
                m = re.search(r"/\s*(\d+)", cr)
                if m:
                    return int(m.group(1))
                cl = r.headers.get("Content-Length")
                if cl:
                    return int(cl)
        except Exception:
            continue
    return 0


def verify_exe(path, sha256=None, size=None):
    """三重校验：存在 / MZ 头 / 大小 / SHA256。"""
    if not os.path.isfile(path):
        return False, "文件不存在"
    sz = os.path.getsize(path)
    if size and sz != size:
        return False, "文件大小不符（%d != %d）" % (sz, size)
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            return False, "文件头不是 MZ（不是有效 exe）"
    if sha256:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest().lower() != sha256.lower():
            return False, "SHA256 校验失败"
    return True, "ok"


def download_update(download_url, dest, sha256=None, size=None,
                    on_progress=None, on_stage=None):
    """分段并发下载 + 断点续传 + 镜像轮换 + 校验。返回 (ok, msg)。"""
    mirrors = _mirror_urls(download_url)
    total = size or _probe_size(mirrors)
    if not total:
        return False, "无法获取文件大小（网络或地址错误）"
    if on_stage:
        on_stage("总大小 %.1f MB，开始下载…" % (total / 1024.0 / 1024.0))
    tmpdir = dest + ".parts"
    try:
        os.makedirs(tmpdir, exist_ok=True)
        seg = max(1, min(5, total // (1024 * 1024)))   # 每片 >=1MB，最多 5 片
        seg_size = total // seg
        segs = []
        for i in range(seg):
            s = i * seg_size
            e = total - 1 if i == seg - 1 else s + seg_size - 1
            segs.append((i, s, e))
        done = [0]
        lock = threading.Lock()
        errs = []
        part_paths = [os.path.join(tmpdir, "seg_%d" % i) for i in range(seg)]

        def try_seg(idx, s, e):
            part = part_paths[idx]
            existing = os.path.getsize(part) if os.path.isfile(part) else 0
            cur_s = s + existing
            if cur_s > e:
                return
            for mi, base in enumerate(mirrors):
                for attempt in range(3):
                    try:
                        data = _http_get_bytes(base, cur_s, e)
                        if not data:
                            continue
                        with open(part, "ab") as f:
                            f.write(data)
                        with lock:
                            done[0] += len(data)
                        return
                    except Exception as ex:
                        if mi == len(mirrors) - 1 and attempt == 2:
                            errs.append(str(ex)[:120])

        ths = [threading.Thread(target=try_seg, args=(i, s, e), daemon=True)
               for (i, s, e) in segs]
        for t in ths:
            t.start()
        while any(t.is_alive() for t in ths):
            if on_progress:
                on_progress(done[0], total)
            time.sleep(0.2)
        for t in ths:
            t.join(timeout=1)
        if on_progress:
            on_progress(done[0], total)
        for i in range(seg):
            p = part_paths[i]
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                return False, "分片 %d 下载失败：%s" % (i, errs[-1] if errs else "未知")
        with open(dest, "wb") as out:
            for i in range(seg):
                with open(part_paths[i], "rb") as pf:
                    shutil.copyfileobj(pf, out)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    ok, msg = verify_exe(dest, sha256, size)
    return (ok, msg) if ok else (False, msg)


def install_update(new_exe):
    """把下载好的 new_exe 就地替换为当前 exe。失败必还原，绝不退出。"""
    current = os.path.abspath(sys.executable)
    bak = current + ".bak"
    try:
        if os.path.isfile(bak):
            os.remove(bak)
    except Exception:
        pass
    try:
        base = os.path.splitext(os.path.basename(current))[0]
        neighbor = os.path.join(os.path.dirname(current), base + "_新版.exe")
        shutil.copy2(new_exe, neighbor)
    except Exception:
        pass
    try:
        os.rename(current, bak)            # Windows 允许重命名运行中的 exe
        shutil.copy2(new_exe, current)
        ok, msg = verify_exe(current)
        if not ok:
            try:
                os.remove(current)
            except Exception:
                pass
            os.rename(bak, current)
            return False, "安装校验失败，已还原原程序：" + msg
        return True, "ok"
    except Exception as ex:
        try:
            if os.path.isfile(bak):
                os.rename(bak, current)
        except Exception:
            pass
        return False, "安装失败，已尽量还原：" + str(ex)[:120]


def restart_app(root=None):
    """PyInstaller 单文件重启：剥离临时环境变量，拉起新实例后退出旧实例。"""
    env = dict(os.environ)
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE"):
        env.pop(k, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        subprocess.Popen([sys.executable], env=env)
    except Exception:
        pass
    try:
        if root is not None:
            root.quit()
    except Exception:
        pass
    threading.Timer(3, lambda: os._exit(0)).start()
    try:
        if root is not None:
            root.destroy()
    except Exception:
        pass
    os._exit(0)


def cleanup_mei():
    """启动时清理历史残留的 _MEI 临时目录（跳过当前进程自己的解压目录）。"""
    try:
        tmp = tempfile.gettempdir()
        me = getattr(sys, "_MEIPASS", None)
        me_abs = os.path.abspath(me).lower() if me else None
        for name in os.listdir(tmp):
            if name.startswith("_MEI") and os.path.isdir(os.path.join(tmp, name)):
                p = os.path.abspath(os.path.join(tmp, name))
                if me_abs and p.lower() == me_abs:
                    continue
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def month_context(now=None, today_done=False):
    """返回日期上下文。
    today_done=False → 方案从今天排到月末；
    today_done=True  → 今天已操作过，从明天排到月末（若跨月则顺延到下月整月）。
    所有 day 字段均为「起始日所在月」的日历日。
    """
    now = now or datetime.date.today()
    start = now + datetime.timedelta(days=1) if today_done else now
    days = calendar.monthrange(start.year, start.month)[1]
    plan_days = max(1, days - start.day + 1)
    # 规划区间内的周日（weekday()==6）：这些天零收入、后端照常扣
    rest_days = set()
    for d in range(start.day, start.day + plan_days):
        try:
            if datetime.date(start.year, start.month, d).weekday() == 6:
                rest_days.add(d)
        except ValueError:
            pass
    return {
        "year": start.year,
        "month": start.month,
        "start_day": start.day,
        "days_in_month": days,
        "plan_days": plan_days,
        "weekday": WEEK_CN[start.weekday()],
        "rest_days": rest_days,      # 周日"休息日"：零收入日
        "today": now.day,            # 真实今天（用于日期栏显示）
        "today_year": now.year,
        "today_month": now.month,
        "today_days_in_month": calendar.monthrange(now.year, now.month)[1],
        "today_weekday": WEEK_CN[now.weekday()],
        "today_done": today_done,
        "cross_month": (start.month != now.month or start.year != now.year),
    }

# ---------- 默认参数 ----------
DEFAULTS = {
    "work_count": 4,        # 大号作品数量（轮换用）
    "work_price": 130,      # 每个作品复制定价
    "like_gain": 1,         # 每次点赞获得秒点
    "like_per_day": 20,     # 每天点赞次数上限
    "login_bonus": 100,     # 每日登录奖励
    "login_days_cap": 7,    # 每月可领登录奖励天数
    "expiry_days": 7,       # 免费秒点有效期（天）·按用户确认以7天为准，可在参数里调整
    "copy_fee_pct": 20,     # 复制手续费百分比（作者实得 = 定价 * (1 - fee%)）
    "backend_projects": 3,  # 大号后端项目数
    "backend_per_day": 20,  # 每个后端项目每天扣费
    "pack_cost": 300,       # 打包一次消耗
    "plan_days": 30,        # 规划天数（自动 = 今天到月末，运行时覆盖）
}

PARAM_LABELS = [
    ("work_count", "作品数量"), ("work_price", "作品定价(点)"),
    ("like_gain", "每次点赞(点)"), ("like_per_day", "每日点赞次数"),
    ("login_bonus", "登录奖励(点)"), ("login_days_cap", "登录月上限(天)"),
    ("expiry_days", "有效期(天)"), ("copy_fee_pct", "复制手续费(%)"),
    ("backend_projects", "后端项目数"), ("backend_per_day", "每项目每天扣(点)"),
    ("pack_cost", "打包消耗(点)"), ("plan_days", "规划天数(自动)"),
]


# ---------- 核心算法（纯函数，便于测试）----------
def _consume(queue, need):
    """从最老批次开始扣 need 点，返回剩余队列。"""
    if need <= 0:
        return queue
    rest = need
    out = []
    for s, a in queue:
        if rest > 0:
            take = min(a, rest)
            rest -= take
            if a - take > 0:
                out.append((s, a - take))
        else:
            out.append((s, a))
    return out


def _total(queue):
    return sum(a for _, a in queue)


def _expire(queue, day, expiry):
    return [(s, a) for s, a in queue if day - s < expiry]


def _disp_w(s):
    """字符串显示宽度：中文等全角字符按 2 列算，供纯文本表格对齐。"""
    return sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in str(s))


def _pad(s, width, align="left"):
    """按显示宽度补空格到 width 列，中文不再歪歪扭扭。"""
    s = str(s)
    gap = width - _disp_w(s)
    if gap <= 0:
        return s
    return (s + " " * gap) if align == "left" else (" " * gap + s)


def _day_text(year, month, day, days_in_month=None):
    """把「当月第 day 天」转成真实日期文本；超过当月天数自动跨到下个月显示。"""
    if days_in_month is None:
        days_in_month = calendar.monthrange(year, month)[1]
    if day <= days_in_month:
        return "%d月%d日" % (month, day)
    y2, m2 = (year + 1, 1) if month == 12 else (year, month + 1)
    return "%d月%d日" % (m2, day - days_in_month)


def _date_stamp(year, month, day, days_in_month=None):
    """平台口径的日期戳 YYYY-MM-DD；超过当月天数按真实日历跨月、跨年进位。"""
    if days_in_month is None:
        days_in_month = calendar.monthrange(year, month)[1]
    if day <= days_in_month:
        y, m, d = year, month, day
    else:
        base = datetime.date(year, month, days_in_month)
        t = base + datetime.timedelta(days=day - days_in_month)
        y, m, d = t.year, t.month, t.day
    return "%04d-%02d-%02d" % (y, m, d)


def _expire_stamp(year, month, day, days_in_month=None):
    """平台口径的到期时间戳：到期日当天 00:00 失效（截图：发生 11-27 → 到期 12-04 00:00）。"""
    return _date_stamp(year, month, day, days_in_month) + " 00:00"


def simulate_small(ac, p, start_day=1, rest_days=None, cal=None):
    """模拟一个小号。返回 (copies, daily_events, author_income, end_balance, login_used, queue)
    start_day: 方案起始的真实日历日（今天几号），所有 day 均为日历日。
    rest_days: 周日集合，这些天零收入（不点赞、不登录、不复制），但秒点照常过期。
    cal: {"year","month","days"} 日历信息，供过期标注跨月显示真实日期；缺省按「N日」标注。
    """
    rest_days = rest_days or set()
    like_gain = p["like_gain"] * p["like_per_day"]
    login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
    login_granted = 0  # 方案窗口内实际领到的登录奖励次数
    # 负余额=欠费：如实记为债务，后续收益须先偿还，不能用「从0开始」抹掉
    debt = max(0.0, -float(ac.get("balance", 0)))
    queue = [(start_day, float(ac["balance"]))] if ac["balance"] > 0 else []
    copies = []       # (day, work_index_1based)
    events = []       # 每日事件 list of (day, action, amount, balance)
    ptr = 0
    work_count = max(1, p["work_count"])
    price = p["work_price"]

    def _bal():
        return round(_total(queue) - debt, 2)

    for d in range(start_day, start_day + p["plan_days"]):
        # 秒点过期是真实发生的余额减少，显式记一笔，保证逐笔账目能跟平台对上
        before_expire = _total(queue) - debt
        queue = _expire(queue, d, p["expiry_days"])
        expired = round(before_expire - (_total(queue) - debt), 2)
        if expired > 0.005:
            events.append((d, "秒点过期", -expired, _bal()))
        if d in rest_days:
            # 周日：不点赞、不领登录奖、不复制；只记录零收入
            events.append((d, "周日休息(无收入)", 0, _bal()))
        else:
            gain = like_gain
            login_flag = login_left > 0
            if login_flag:
                gain += p["login_bonus"]
                login_left -= 1
                login_granted += 1
            # 有欠费时，收益先冲抵债务，剩余才入队（队列=可用秒点）
            pay = 0
            if debt > 0 and gain > 0:
                pay = min(debt, gain)
                debt -= pay
                gain -= pay
            if gain > 0:
                queue.append((d, float(gain)))
            # 每笔都记录「这笔操作之后」的真实余额，不再全部填成日终值。
            # 还债是收入的内部去向：合并进文案说明，不单列一笔金额对不上的账。
            now = _bal()
            label = "登录领奖+点赞" if login_flag else "点赞"
            if p["expiry_days"] > 0:
                if cal:
                    label += "(本批%s到期)" % _expire_stamp(
                        cal["year"], cal["month"], d + p["expiry_days"], cal["days"])
                else:
                    label += "(本批第%d天到期)" % (d + p["expiry_days"])
            if pay > 0:
                label += "(其中%g还欠费)" % round(pay, 2)
            events.append((d, label, round(gain + pay, 2), now))

            made = 0
            while price > 0 and _total(queue) >= price:
                queue = _consume(queue, price)
                wi = ptr % work_count
                copies.append((d, wi + 1))
                events.append((d, "复制→作品%d" % (wi + 1), -price, _bal()))
                ptr += 1
                made += 1
                if made > 1000:  # 保险
                    break

    author_income = len(copies) * price * (1 - p["copy_fee_pct"] / 100.0)
    # 剩余批次 [(获得日, 剩余点)]：获得日+有效期=过期日，供到期预警
    return copies, events, author_income, _total(queue) - debt, login_granted, queue


def simulate_big(ac, p, income_by_day, start_day=1, rest_days=None, pack_on_start=False, cal=None):
    """模拟大号。income_by_day: dict day(日历日)->小号转账金额。
    rest_days: 周日集合，这些天零收入，但后端照常扣费。
    pack_on_start: 用户勾选「起始日打包」→ 仅在起始日打包一次并扣点；平时不自动打包。
    返回 (packs, events, end_balance, login_used, packable_days)
      packs: 实际打包的日期列表；packable_days: 余额达到打包线、可随时手动打包的日期列表。
    """
    rest_days = rest_days or set()
    like_gain = p["like_gain"] * p["like_per_day"]
    login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
    login_granted = 0  # 方案窗口内实际领到的登录奖励次数
    # 负余额=欠费：后端照扣造成的欠费如实保留，收款先还债
    debt = max(0.0, -float(ac.get("balance", 0)))
    queue = [(start_day, float(ac["balance"]))] if ac["balance"] > 0 else []
    packs = []
    events = []
    packable_days = []
    backend = p["backend_projects"] * p["backend_per_day"]
    pack_cost = p["pack_cost"]

    def _bal():
        return round(_total(queue) - debt, 2)

    for d in range(start_day, start_day + p["plan_days"]):
        # 秒点过期：真实减少，显式记一笔
        before_expire = _total(queue) - debt
        queue = _expire(queue, d, p["expiry_days"])
        expired = round(before_expire - (_total(queue) - debt), 2)
        if expired > 0.005:
            events.append((d, "秒点过期", -expired, _bal()))
        is_rest = d in rest_days
        is_first = (d == start_day)
        inc = 0.0 if is_rest else income_by_day.get(d, 0.0)
        day_gain = 0.0
        if not is_rest:
            day_gain = like_gain
            if login_left > 0:
                day_gain += p["login_bonus"]
                login_left -= 1
                login_granted += 1
        # 收款/点赞先冲抵欠费，剩余才成为可用秒点
        income = day_gain + inc
        pay = 0.0
        if debt > 0 and income > 0:
            pay = min(debt, income)
            debt -= pay
            income -= pay
        if income > 0:
            queue.append((d, float(income)))
        # 收入事件记原始金额，余额快照为扣债入队后的真实值
        if is_rest:
            events.append((d, "周日休息(无收款)", 0, _bal()))
        else:
            label = "收款+活跃"
            if p["expiry_days"] > 0:
                if cal:
                    label += "(本批%s到期)" % _expire_stamp(
                        cal["year"], cal["month"], d + p["expiry_days"], cal["days"])
                else:
                    label += "(本批第%d天到期)" % (d + p["expiry_days"])
            if pay > 0:
                label += "(其中%g还欠费)" % round(pay, 2)
            events.append((d, label, round(day_gain + inc, 2), _bal()))

        # 后端扣费：周日照常扣。余额不足时不再停在0，而是形成欠费（负余额）
        avail = _total(queue)
        if backend > avail:
            short = backend - avail
            queue = []
            debt += short
            events.append((d, "后端扣费(含欠费)", -backend, _bal()))
        else:
            queue = _consume(queue, backend)
            events.append((d, "后端扣费", -backend, _bal()))

        # 打包：只按用户勾选在起始日执行一次；之后余额够仅提示、不自动扣
        cur_bal = _total(queue) - debt
        if pack_on_start and is_first and not is_rest and pack_cost > 0 and cur_bal >= pack_cost:
            queue = _consume(queue, pack_cost)
            packs.append(d)
            events.append((d, "打包(按你勾选)", -pack_cost, _bal()))
        elif pack_cost > 0 and cur_bal >= pack_cost:
            packable_days.append(d)

    # 剩余批次 [(获得日, 剩余点)]：获得日+有效期=过期日，供到期预警
    return packs, events, _total(queue) - debt, login_granted, packable_days, queue


def build_plan(accounts, p, ctx=None, pack_on_start=False):
    """生成 (完整文本, csv行列表, 分板块列表, 每日动作表, 每日余额曲线, 每日登录曲线, 到期信息)。
    每日动作表: {日: {账号备注: [动作, ...]}}，供「确认采用」后主页面每日展示。
    余额/登录曲线: {日: {账号备注: 当天结束值}}，供采用后按日期自动推进当前秒点。
    到期信息: {账号备注: [(获得日, 过期日, 剩余点), ...]}，供到期预警与今日面板提醒。
    """
    ctx = ctx or month_context()
    start_day = ctx["start_day"]
    rest_days = ctx.get("rest_days", set())
    # 规划天数强制与「今天到月末」一致，避免残留旧值
    p = dict(p)
    p["plan_days"] = ctx["plan_days"]

    bigs = [a for a in accounts if a["role"] == "big"]
    smalls = [a for a in accounts if a["role"] == "small"]
    if not bigs:
        raise ValueError("缺少大号，请保留一个大号账号。")
    big = bigs[0]

    # 先跑小号
    cal = {"year": ctx["year"], "month": ctx["month"], "days": ctx["days_in_month"]}
    small_results = []
    income_by_day = {}
    for ac in smalls:
        copies, ev, author_income, endbal, login_used, q = simulate_small(
            ac, p, start_day, rest_days, cal=cal)
        small_results.append((ac, copies, ev, author_income, endbal, login_used, q))
        per = p["work_price"] * (1 - p["copy_fee_pct"] / 100.0)
        for d, wi in copies:
            income_by_day[d] = income_by_day.get(d, 0.0) + per

    packs, bev, bend, big_login_used, packable_days, bqueue = simulate_big(
        big, p, income_by_day, start_day, rest_days, pack_on_start, cal=cal)

    # ---- 到期预警：平台优先消耗「即将到期」的秒点；到期没用掉的批次会自然流失 ----
    # 每个批次记录 (获得日, 过期日=获得日+有效期, 剩余点)。当前余额近似按「起始日获得」
    # 计算有效期，实际到期日以平台【秒点详情】里最近一笔即将到期的秒点为准。
    # 同日获得的批次合并为一行，避免重复显示。
    def _merge_batches(q):
        merged = {}
        for s, a in q:
            if a > 0.005:
                key = (s, s + p["expiry_days"])
                merged[key] = round(merged.get(key, 0.0) + a, 2)
        return [(s, e, a) for (s, e), a in sorted(merged.items())]

    expiry_info = {}   # note -> [(获得日, 过期日, 剩余点)]
    for ac, copies, ev, ai, eb, lu, q in small_results:
        batches = _merge_batches(q)
        if batches:
            expiry_info[ac["note"]] = batches
    bb = _merge_batches(bqueue)
    if bb:
        expiry_info[big["note"]] = bb

    # ---- 文本方案：分板块构建，便于 GUI 分页展示 ----
    sec_overview, sec_today, sec_daily, sec_copy, sec_big = [], [], [], [], []

    # 板块1：总览与规则
    sec_overview.append("%s 方案（%d月%d日 %s 起，至%d月末共 %d 天）" %
                        (APP_NAME, ctx["month"], ctx["start_day"], ctx["weekday"],
                         ctx["month"], ctx["plan_days"]))
    if ctx.get("today_done"):
        sec_overview.append("※ 你已勾选「今天(%d月%d日)全部操作过」，本方案从明天开始排。" %
                            (ctx["today_month"], ctx["today"]))
    per = p["work_price"] * (1 - p["copy_fee_pct"] / 100.0)
    sec_overview.append("规则：作品定价 %d 点，手续费 %d%% → 大号每得 %g 点/次" %
                        (p["work_price"], p["copy_fee_pct"], per))
    sec_overview.append("      点赞 +%d/天（%d次×%d点）；登录前 %d 天 +%d/天；有效期 %d 天" %
                        (p["like_gain"] * p["like_per_day"], p["like_per_day"], p["like_gain"],
                         p["login_days_cap"], p["login_bonus"], p["expiry_days"]))
    sec_overview.append("      后端 %d 项×%d = %d/天；打包 %d 点/次" %
                        (p["backend_projects"], p["backend_per_day"],
                         p["backend_projects"] * p["backend_per_day"], p["pack_cost"]))
    if rest_days:
        rest_txt = "、".join("%d日" % d for d in sorted(rest_days))
        sec_overview.append("★ 周日（%s）为休息日：全天无点赞/登录/复制/打包收入，后端照常扣费。" % rest_txt)

    # 到期提醒：官方规则——平台优先消耗「即将到期」的秒点；到期没用掉就自然流失
    warn_lines = []
    for note in [ac["note"] for ac in smalls] + [big["note"]]:
        for s, e, a in expiry_info.get(note, []):
            if e <= start_day + p["plan_days"]:
                warn_lines.append("  ⚠ %s：%g 点（%s 发生）到期时间 %s，没用掉就流失"
                                  % (note, a,
                                     _date_stamp(ctx["year"], ctx["month"], s, ctx["days_in_month"]),
                                     _expire_stamp(ctx["year"], ctx["month"], e, ctx["days_in_month"])))
    if warn_lines:
        sec_overview.append("到期提醒（平台优先消耗快到期的秒点，先到期的先扣）：")
        sec_overview += warn_lines
    else:
        sec_overview.append("到期提醒：规划期内没有秒点会到期流失。")

    # 板块2：首日各号该做什么
    day_label = "明天" if ctx.get("today_done") else "今天"
    sec_today.append("【%s（%d月%d日 %s）各号该做什么】" %
                     (day_label, ctx["month"], ctx["start_day"], ctx["weekday"]))
    notes = [ac["note"] for ac in smalls] + [big["note"]]
    if start_day in rest_days:
        sec_today.append("  ★ 起始日是周日：所有号都不登录、不点赞、不复制、不打包，全部静默；")
        sec_today.append("    只有大号后端照扣 %d 点。等周一再恢复操作。" %
                         (p["backend_projects"] * p["backend_per_day"]))
    else:
        for ac, copies, ev, ai, eb, lu, _q in small_results:
            login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
            today_copies = [wi for d, wi in copies if d == start_day]
            login_txt = "登录领奖+点赞" if login_left > 0 else "点赞(已领满登录)"
            if today_copies:
                cp = "，复制：" + "、".join("作品%d" % w for w in today_copies)
            else:
                cp = "（余额不足，先攒点）"
            sec_today.append("  · %s：%s%d 次%s" %
                             (ac["note"], login_txt, p["like_per_day"], cp))
        big_login_left = max(0, p["login_days_cap"] - int(big.get("login_claimed", 0)))
        big_login_txt = "登录领奖+点赞" if big_login_left > 0 else "点赞(已领满登录)"
        pack_today = "；【按你勾选】今天打包一次 -%d 点" % p["pack_cost"] if (
            pack_on_start and packs) else "；今天不打包"
        sec_today.append("  · %s：%s%d 次（后端今天照扣 %d 点）%s" %
                         (big["note"], big_login_txt, p["like_per_day"],
                          p["backend_projects"] * p["backend_per_day"], pack_today))

    # 板块3：逐日行动预览
    sec_daily.append("每个号每天具体做什么，照着做即可")
    actions_by_day = {}   # day -> {note: [动作文本]}
    def _short_action(a):
        if "登录领奖+点赞" in a:
            return "登录+点赞"
        if a == "点赞":
            return "点赞"
        if "周日休息" in a:
            return "静默(周日)"
        if "复制→" in a:
            return a.replace("复制→", "复制")
        if "打包" in a:
            return "★打包"
        if "后端扣费" in a:
            return "后端照扣"
        if "收款+活跃" in a:
            return "收款"
        return a
    for ac, copies, ev, ai, eb, lu, _q in small_results:
        for d, action, amt, bal in ev:
            acts = actions_by_day.setdefault(d, {}).setdefault(ac["note"], [])
            s = _short_action(action)
            if s not in acts:
                acts.append(s)
    for d, action, amt, bal in bev:
        acts = actions_by_day.setdefault(d, {}).setdefault(big["note"], [])
        s = _short_action(action)
        if s not in acts:
            acts.append(s)
    big_login_left_daily = max(0, p["login_days_cap"] - int(big.get("login_claimed", 0)))
    for d in range(start_day, start_day + p["plan_days"]):
        week = WEEK_CN[datetime.date(ctx["year"], ctx["month"], d).weekday()]
        tag = "  ★周日休息日" if d in rest_days else ""
        sec_daily.append("  ── %d日(%s)%s" % (d, week, tag))
        day_map = actions_by_day.setdefault(d, {})
        # 大号的点赞/登录领奖不单独记事件，这里补上，方便逐日执行时逐号照做
        if d not in rest_days:
            base = "登录+点赞" if big_login_left_daily > 0 else "点赞"
            if big_login_left_daily > 0:
                big_login_left_daily -= 1
            big_acts = day_map.setdefault(big["note"], [])
            if base not in big_acts:
                big_acts.insert(0, base)
        for note in notes:
            acts = day_map.setdefault(note, [])  # 静默号也占位，方便主页面逐号展示
            sec_daily.append("      %s：%s" % (note, "、".join(acts) if acts else "静默"))

    # 板块4：小号复制排期
    for ac, copies, ev, ai, eb, lu, _q in small_results:
        sec_copy.append("【%s】当前 %g 点｜本月已领登录 %d 天（方案内再领 %d 天）" %
                        (ac["note"], ac["balance"], int(ac.get("login_claimed", 0)), lu))
        sec_copy.append("  复制 %d 次 → 为大号贡献 %g 点；月末余额约 %g 点" %
                        (len(copies), ai, eb))
        if copies:
            txt = "  "
            for i, (d, wi) in enumerate(copies):
                txt += "%d日→作品%d  " % (d, wi)
                if (i + 1) % 5 == 0:
                    sec_copy.append(txt)
                    txt = "  "
            if txt.strip():
                sec_copy.append(txt)
            sec_copy.append("  （作品按 1→2→3→4 轮换，同一账号不会连续复制同一个作品）")
        else:
            sec_copy.append("  当前余额不足定价 %d，需先攒点。" % p["work_price"])
        sec_copy.append("")

    # 板块5：大号与打包 + 汇总
    total_ai = sum(r[3] for r in small_results)
    total_copies = sum(len(r[1]) for r in small_results)
    sec_big.append("【%s】当前 %g 点｜本月已领登录 %d 天（方案内再领 %d 天）" %
                   (big["note"], big["balance"], int(big.get("login_claimed", 0)), big_login_used))
    sec_big.append("  小号合计复制 %d 次 → 转账收入 %g 点" % (total_copies, total_ai))
    sec_big.append("  后端每天 -%d，%d 天共 -%d" %
                   (p["backend_projects"] * p["backend_per_day"], p["plan_days"],
                    p["backend_projects"] * p["backend_per_day"] * p["plan_days"]))
    if packs:
        sec_big.append("  打包：按你的勾选，在 %s 打包 1 次，扣 %d 点。" %
                       ("、".join("%d日" % d for d in packs), p["pack_cost"]))
    else:
        sec_big.append("  本次未安排打包（平时不打包，余额自然累积/过期）。")
    if packable_days:
        sec_big.append("  可打包时机：%d日 起余额达到 %d 点，届时你可自行决定要不要打包（软件不自动扣）。" %
                       (packable_days[0], p["pack_cost"]))
    else:
        sec_big.append("  规划期内余额未再达到打包线 %d。" % p["pack_cost"])
    sec_big.append("  月末预计余额约 %g 点" % bend)
    sec_big.append("")
    sec_big.append("【汇总】小号复制 %d 次｜大号打包 %d 次｜大号月末余额约 %g 点" %
                   (total_copies, len(packs), bend))
    if p["work_count"] < 2:
        sec_big.append("提示：作品数量建议≥2，否则同一账号可能连续复制同一作品。")
    sec_big.append("说明：方案按真实日历日排布（%d月%d日～%d日）；当前余额按起始日可用计入并从起始日起算有效期；" %
                   (ctx["month"], ctx["start_day"], ctx["days_in_month"]))
    sec_big.append("      平台优先消耗快到期的秒点（先发生的先扣），到期没用掉会自然流失；到期时间按平台口径")
    sec_big.append("      「发生日+%d天 = 到期日当天 00:00 失效」显示。当前余额以起始日发生近似，真实时间以平台明细为准。" % p["expiry_days"])
    sec_big.append("      余额请填「调整好作品之后」的实际数（调整消耗的秒点已含在内，软件不再另扣）。")
    sec_big.append("      打包平时不自动做；要打包就在界面勾选，生成时只扣那一次。改了余额记得重新生成。")

    # 完整文本（兼容导出/外部调用）
    L = []
    L.append("=" * 58); L += sec_overview; L.append(""); L.append("=" * 58)
    L += sec_today; L.append("")
    L.append("【逐日行动预览】"); L += sec_daily; L.append("")
    L += sec_copy
    L.append("-" * 58); L += sec_big
    full_text = "\n".join(L)

    sections = [
        ("今日行动", "\n".join(sec_overview) + "\n\n" + "\n".join(sec_today)),
        ("逐日行动", "\n".join(sec_daily)),
        ("复制排期", "\n".join(sec_copy)),
        ("大号与打包", "\n".join(sec_big)),
        ("全部", full_text),
    ]

    # ---- CSV 明细 ----
    rows = [["日期(号)", "账号", "备注", "动作", "变动(点)", "操作后余额"]]
    for ac, copies, ev, ai, eb, lu, _q in small_results:
        for d, action, amt, bal in ev:
            rows.append([d, "小号", ac["note"], action, round(amt, 2), round(bal, 2)])
    for d, action, amt, bal in bev:
        rows.append([d, "大号", big["note"], action, round(amt, 2), round(bal, 2)])

    # ---- 每日余额/登录曲线：按每个号每天「最后一条事件的余额」取整日终值 ----
    balance_curve = {}   # day -> {note: 日终余额}
    login_curve = {}     # day -> {note: 日终已领登录天数}
    for ac, copies, ev, ai, eb, lu, _q in small_results:
        for d, action, amt, bal in ev:
            balance_curve.setdefault(d, {})[ac["note"]] = round(bal, 2)
    for d, action, amt, bal in bev:
        balance_curve.setdefault(d, {})[big["note"]] = round(bal, 2)
    # 登录天数：按方案模拟实际领取（起始日已领 + 方案期内领的天数，封顶 login_days_cap）
    big_login_start = int(big.get("login_claimed", 0))
    for idx, ac in enumerate(small_results):
        start_lc = int(ac[0].get("login_claimed", 0))
        _fill_login_curve(login_curve, ac[0]["note"], start_lc, p, start_day, rest_days)
    _fill_login_curve(login_curve, big["note"], big_login_start, p, start_day, rest_days)

    return full_text, rows, sections, actions_by_day, balance_curve, login_curve, expiry_info


def _fill_login_curve(curve, note, start_claimed, p, start_day, rest_days):
    """按方案规则填充某账号逐日「累计已领登录天数」。"""
    cap = int(p["login_days_cap"])
    left = max(0, cap - start_claimed)
    claimed = start_claimed
    for d in range(start_day, start_day + int(p["plan_days"])):
        if d not in rest_days and left > 0:
            claimed += 1
            left -= 1
        curve.setdefault(d, {})[note] = claimed


def _eval_price(accounts, p, ctx, price):
    """在给定统一定价下，用现有 simulate_small 评估各小号：返回过期浪费/复制次数/大号实收。"""
    cal = {"year": ctx["year"], "month": ctx["month"], "days": ctx["days_in_month"]}
    rest_days = ctx.get("rest_days", set())
    start_day = ctx["start_day"]
    pp = dict(p)
    pp["work_price"] = price
    waste = 0.0
    copies = 0
    income = 0.0
    for ac in accounts:
        if ac["role"] != "small":
            continue
        cp, ev, ai, _eb, _lu, _q = simulate_small(ac, pp, start_day, rest_days, cal=cal)
        copies += len(cp)
        income += ai
        for _d, act, amt, _bal in ev:
            if act == "秒点过期" and amt < 0:
                waste += -amt
    return {"waste": round(waste, 2), "copies": int(copies), "income": round(income, 2)}


def recommend_price(accounts, p, ctx=None):
    """为大号作品推荐一个统一上架秒点（方案期内固定，不中途改价）。

    综合每个小号当前秒点与即将失效批次：枚举候选定价，复用同一套 simulate_small 模拟，
    按「过期浪费最少 → 复制次数最少（不浪费前提下定价尽量高、操作尽量省）」选价。
    观察窗口覆盖有效期（至少 2 倍有效期），让「攒不够而过期」能被识别。

    返回 dict：price/waste/copies/income/current/candidates/note/ok；无小号时返回 None。
    """
    ctx = ctx or month_context()
    smalls = [a for a in accounts if a["role"] == "small"]
    if not smalls:
        return None
    expiry = max(1, int(p["expiry_days"]))
    observe = max(int(p["plan_days"]), expiry * 2, expiry + 1)
    pp = dict(p)
    pp["plan_days"] = observe

    # 候选价上限：覆盖当前余额、有效期内可得秒点、当前定价的 2 倍
    gain_cap = (p["like_gain"] * p["like_per_day"] + p["login_bonus"]) * expiry
    top = max([float(a["balance"]) for a in smalls] +
              [gain_cap, float(p["work_price"]) * 2, 200.0])
    top = int((top + 9) // 10 * 10)
    candidates = list(range(10, top + 1, 10))

    results = []
    for cand in candidates:
        r = _eval_price(accounts, pp, ctx, cand)
        r["price"] = cand
        results.append(r)

    best = min(results, key=lambda r: (r["waste"], r["copies"]))
    current_price = int(p["work_price"])
    current = _eval_price(accounts, pp, ctx, current_price)
    current["price"] = current_price

    if best["waste"] <= 0.005:
        note = ("按 %d 点上架：方案期内各号秒点可在到期前复制消耗完，无过期浪费；"
                "这是不浪费前提下复制次数最少的一档，方案期内保持不变。" % best["price"])
    else:
        note = ("所有候选定价都会有部分秒点过期；%d 点过期最少（约 %g 点），"
                "大号实收约 %g 点。方案期内保持不变。" %
                (best["price"], best["waste"], best["income"]))
    return {
        "ok": True, "price": best["price"], "waste": best["waste"],
        "copies": best["copies"], "income": best["income"],
        "current": current, "candidates": results, "note": note,
    }


# ---------- GUI ----------
# ---------- 视觉规范（仅外观，不影响任何计算）----------
C_BG = "#F4F6FA"          # 页面底色
C_SURFACE = "#FFFFFF"     # 卡片底色
C_BORDER = "#E3E8EF"      # 卡片描边
C_INK = "#172033"         # 主文字
C_MUTED = "#667085"       # 次要文字
C_PRIMARY = "#2F6BED"     # 主操作蓝
C_PRIMARY_DARK = "#2457CC"
C_NAVY = "#1B263B"        # 顶部导航
C_SUCCESS = "#0E9F6E"
C_WARN = "#B54708"
C_DANGER = "#D92D20"
FONT = "Microsoft YaHei"


class AccountRow:
    def __init__(self, parent, role, idx, note="", balance=0, login_claimed=0, on_delete=None):
        self.role = role
        self.frame = tk.Frame(parent, bg=C_SURFACE, highlightthickness=1,
                              highlightbackground=C_BORDER)
        self.role_var = tk.StringVar(value="大号" if role == "big" else "小号%d" % idx)
        self.note_var = tk.StringVar(value=note)
        self.bal_var = tk.StringVar(value=str(balance))
        self.login_var = tk.StringVar(value=str(login_claimed))

        badge_bg, badge_fg = (("#EEF2FF", "#4338CA") if role == "big"
                              else ("#EFF6FF", "#1D4ED8"))
        tk.Label(self.frame, textvariable=self.role_var, width=7, bg=badge_bg, fg=badge_fg,
                 font=(FONT, 10, "bold"), pady=2).grid(row=0, column=0, padx=(10, 8), pady=2)
        tk.Label(self.frame, text="备注", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9)).grid(row=0, column=1, padx=(0, 4))
        ttk.Entry(self.frame, textvariable=self.note_var, width=13).grid(
            row=0, column=2, padx=(0, 14), sticky="w")
        tk.Label(self.frame, text="当前秒点", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9)).grid(row=0, column=3, padx=(0, 4))
        ttk.Entry(self.frame, textvariable=self.bal_var, width=8).grid(
            row=0, column=4, padx=(0, 14), sticky="w")
        # 大号和小号都需要「本月已领登录天数」（两者都涉及登录奖励）
        tk.Label(self.frame, text="本月已领登录(天)", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9)).grid(row=0, column=5, padx=(0, 4))
        ttk.Spinbox(self.frame, from_=0, to=7, textvariable=self.login_var,
                    width=4).grid(row=0, column=6, sticky="w")
        if role == "small":
            ttk.Button(self.frame, text="删除", width=6, style="Ghost.TButton",
                       command=on_delete).grid(row=0, column=7, padx=(12, 10))

    def to_dict(self):
        bal = float(self.bal_var.get() or 0)
        lc = int(self.login_var.get() or 0)
        return {"role": self.role, "note": self.note_var.get().strip() or self.role_var.get(),
                "balance": bal, "login_claimed": lc}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s v%s" % (APP_NAME, VERSION))
        self.geometry("1000x920")
        self.minsize(940, 860)
        self.rows = []
        self.small_counter = 0
        self.last_rows = None
        self.pending_plan = None   # 最近生成、尚未确认采用的方案快照
        self.adopted_plan = None   # 已确认采用的方案（持久化，主页面每日展示）
        self.change_log = []       # 实际加减记录（每次推进/回退逐条记，供核对）
        self.detail_group_var = tk.StringVar(value="day")   # 逐笔核对：按日期 / 按账号
        self.detail_scope_var = tk.StringVar(value="done")  # 逐笔核对：只看已发生 / 全部
        self.today_done_var = tk.BooleanVar(value=False)  # 今天是否已全部操作过
        self.pack_today_var = tk.BooleanVar(value=False)  # 起始日是否打包
        self.save_status_var = tk.StringVar(value="")
        self._save_job = None          # 防抖定时器
        self._binding_saves = False    # 灌数据期间暂不触发 trace 保存
        self.ctx = month_context(today_done=False)
        self._build_style()
        self._build_ui()
        self._auto_load()   # 启动自动恢复上次配置（无则用默认）
        # 今日执行面板：每 30 秒与窗口重新获得焦点时按系统日期自动刷新
        self._today_key = datetime.date.today().isoformat()
        self._refresh_today_panel()
        # 旧版已采用的方案没有全文，启动即按快照起始数据补一份，点「查看当前方案」可立即回看
        ap = getattr(self, "adopted_plan", None)
        if ap and not ap.get("text"):
            self._ensure_plan_text(ap)
        self._advance_balances()   # 启动时按方案把当前秒点推进到今天
        self.after(30000, self._tick_today)
        self.bind("<FocusIn>", lambda e: self._tick_today(check_only=True))
        # 关闭窗口时自动保存，用户无需手动点
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 每次启动后台静默检查更新（不打断使用）
        self.after(300, lambda: self._check_update(manual=False))

    def _card(self, parent, title, subtitle=""):
        """统一的白色卡片容器：标题 + 可选副标题，调用方自行 pack。"""
        f = tk.Frame(parent, bg=C_SURFACE, highlightthickness=1,
                     highlightbackground=C_BORDER)
        h = tk.Frame(f, bg=C_SURFACE)
        h.pack(fill="x", padx=16, pady=(8, 4))
        tk.Label(h, text=title, bg=C_SURFACE, fg=C_INK,
                 font=(FONT, 11, "bold")).pack(side="left")
        if subtitle:
            tk.Label(h, text=subtitle, bg=C_SURFACE, fg=C_MUTED,
                     font=(FONT, 9)).pack(side="left", padx=10)
        return f

    def _build_style(self):
        self.configure(bg=C_BG)
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TFrame", background=C_BG)
        st.configure("Bg.TFrame", background=C_BG)
        st.configure("TLabel", background=C_SURFACE, foreground=C_INK, font=(FONT, 10))
        st.configure("Bg.TLabel", background=C_BG, foreground=C_MUTED, font=(FONT, 9))
        st.configure("Muted.TLabel", background=C_SURFACE, foreground=C_MUTED, font=(FONT, 9))
        st.configure("Section.TLabel", background=C_SURFACE, foreground=C_INK,
                     font=(FONT, 10, "bold"))
        st.configure("Info.TLabel", background=C_SURFACE, foreground=C_PRIMARY,
                     font=(FONT, 10, "bold"))
        st.configure("Ok.TLabel", background=C_SURFACE, foreground=C_SUCCESS, font=(FONT, 9))
        st.configure("Warn.TLabel", background=C_SURFACE, foreground=C_WARN, font=(FONT, 9))
        st.configure("Danger.TLabel", background=C_SURFACE, foreground=C_DANGER, font=(FONT, 9))
        st.configure("TButton", font=(FONT, 10), padding=(10, 5), background=C_SURFACE,
                     foreground=C_INK, bordercolor=C_BORDER, relief="flat")
        st.map("TButton", background=[("active", "#F1F5F9"), ("pressed", "#E2E8F0")])
        st.configure("Primary.TButton", font=(FONT, 10, "bold"), padding=(16, 7),
                     background=C_PRIMARY, foreground="#FFFFFF", bordercolor=C_PRIMARY,
                     relief="flat")
        st.map("Primary.TButton", background=[("active", C_PRIMARY_DARK),
                                              ("pressed", C_PRIMARY_DARK)])
        st.configure("Ghost.TButton", font=(FONT, 9), padding=(6, 3), background="#FFF1F0",
                     foreground=C_DANGER, bordercolor="#FECACA", relief="flat")
        st.map("Ghost.TButton", background=[("active", "#FFE0DE")])
        st.configure("TEntry", fieldbackground="#FFFFFF", background="#FFFFFF",
                     bordercolor=C_BORDER, foreground=C_INK, padding=(6, 4),
                     font=(FONT, 10), insertcolor=C_INK)
        st.configure("TSpinbox", fieldbackground="#FFFFFF", background="#FFFFFF",
                     bordercolor=C_BORDER, foreground=C_INK, padding=(4, 3),
                     font=(FONT, 10), arrowsize=12)
        st.configure("TCheckbutton", background=C_SURFACE, foreground=C_INK,
                     font=(FONT, 10), indicatorcolor="#FFFFFF", bordercolor=C_BORDER)
        st.map("TCheckbutton", background=[("active", C_SURFACE)])
        st.configure("TRadiobutton", background=C_SURFACE, foreground=C_INK, font=(FONT, 10))

    def _header_button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg="#334155", fg="#E2E8F0",
                         activebackground="#475569", activeforeground="#FFFFFF",
                         relief="flat", bd=0, padx=10, pady=4, font=(FONT, 9), cursor="hand2")

    def _build_ui(self):
        # ===== 顶部导航：品牌 / 更新状态 =====
        head = tk.Frame(self, bg=C_NAVY, height=58)
        head.pack(fill="x")
        head.pack_propagate(False)
        left = tk.Frame(head, bg=C_NAVY)
        left.pack(side="left", padx=(18, 0))
        tk.Label(left, text="秒点规划器", bg=C_NAVY, fg="#FFFFFF",
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(left, text="点赞 · 复制 · 打包，一眼看清每天怎么操作", bg=C_NAVY,
                 fg="#9FB3CC", font=(FONT, 9)).pack(anchor="w")
        right = tk.Frame(head, bg=C_NAVY)
        right.pack(side="right", padx=(0, 14))
        self.ver_label = tk.Label(right, text="v" + APP_VERSION, bg="#334155", fg="#E2E8F0",
                                  font=(FONT, 9, "bold"), padx=8, pady=3, cursor="hand2")
        self.ver_label.pack(side="left", padx=(0, 10))
        self.ver_label.bind("<Button-1>", lambda e: self._check_update(manual=True))
        self.update_btn = self._header_button(right, "检查更新",
                                              lambda: self._check_update(manual=True))
        self.update_btn.pack(side="left", padx=(0, 10))
        self.update_status = tk.Label(right, text="", bg=C_NAVY, fg="#94A3B8",
                                      font=(FONT, 9))
        self.update_status.pack(side="left", padx=(0, 8))
        self.restore_src_btn = self._header_button(right, "恢复默认更新地址",
                                                   self._restore_default_source)
        if load_update_config().get("source"):
            self.restore_src_btn.pack(side="left", padx=(0, 6))

        # ===== 非模态更新横幅（默认隐藏，发现新版时弹出）=====
        b = tk.Frame(self, bg="#FFFAEB", highlightthickness=1, highlightbackground="#FEDF89")
        b.pack(fill="x", pady=(10, 0))
        b.pack_forget()
        self.banner = b
        inner = tk.Frame(b, bg="#FFFAEB")
        inner.pack(fill="x", padx=12, pady=8)
        self.banner_text = tk.Label(inner, text="", bg="#FFFAEB", fg="#93370D",
                                    font=(FONT, 10, "bold"))
        self.banner_text.pack(side="left")
        self.banner_notes = tk.Label(inner, text="", bg="#FFFAEB", fg="#B54708",
                                     font=(FONT, 9))
        self.banner_notes.pack(side="left", padx=8)
        self.banner_stage = tk.Label(inner, text="", bg="#FFFAEB", fg="#B54708",
                                     font=(FONT, 9))
        self.banner_stage.pack(side="left", padx=8)
        self.banner_pb = ttk.Progressbar(inner, orient="horizontal",
                                         mode="determinate", maximum=1)
        bf = tk.Frame(inner, bg="#FFFAEB")
        bf.pack(side="right")
        self.btn_now = ttk.Button(bf, text="立即更新", style="Primary.TButton",
                                  command=self._start_download)
        self.btn_now.pack(side="left", padx=3)
        self.btn_skip = ttk.Button(bf, text="跳过此版本", command=self._on_skip)
        self.btn_skip.pack(side="left", padx=3)
        self.btn_later = ttk.Button(bf, text="稍后再说", command=self._on_later)
        self.btn_later.pack(side="left", padx=3)

        # ===== 可滚动主区域：内容再多也不会被窗口截断 =====
        wrap = tk.Frame(self, bg=C_BG)
        self._main_wrap = wrap
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=C_BG, highlightthickness=0)
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        content = tk.Frame(canvas, bg=C_BG)
        win_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfig(win_id, width=e.width),
                                             canvas.configure(scrollregion=canvas.bbox("all"))))
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

        # ===== 本月安排 =====
        info = self._card(content, "本月安排", "按系统日期自动计算")
        info.pack(fill="x", pady=(8, 6))
        info_row = tk.Frame(info, bg=C_SURFACE)
        info_row.pack(fill="x", padx=16, pady=(0, 8))
        self.date_var = tk.StringVar()
        self.rest_var = tk.StringVar()
        ttk.Label(info_row, textvariable=self.date_var, style="Info.TLabel",
                  anchor="w").pack(fill="x")
        ttk.Label(info_row, textvariable=self.rest_var, style="Warn.TLabel",
                  anchor="w").pack(fill="x", pady=(4, 0))

        # ===== 一眼速览：关键数据主动呈现，不用点按钮 =====
        ov = self._card(content, "一眼速览", "自动更新")
        ov.pack(fill="x", pady=(0, 8))
        ov_row = tk.Frame(ov, bg=C_SURFACE)
        ov_row.pack(fill="x", padx=16, pady=(0, 10))
        self.overview_var = tk.StringVar(value="")
        ttk.Label(ov_row, textvariable=self.overview_var, style="Info.TLabel",
                  wraplength=780, justify="left").pack(anchor="w")

        # ===== 今日执行 + 执行状态（并排）=====
        duo = tk.Frame(content, bg=C_BG)
        duo.pack(fill="x")
        duo.columnconfigure(0, weight=3)
        duo.columnconfigure(1, weight=2)
        self.today_frame = self._card(duo, "今日执行", "采用方案后每天自动更新")
        self.today_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        self.today_date_var = tk.StringVar()
        inner = tk.Frame(self.today_frame, bg=C_SURFACE)
        inner.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Label(inner, textvariable=self.today_date_var,
                  style="Section.TLabel").pack(anchor="w")
        self.today_lines = tk.Frame(inner, bg=C_SURFACE)
        self.today_lines.pack(fill="x", pady=(6, 0))
        self.balance_sync_var = tk.StringVar()
        ttk.Label(inner, textvariable=self.balance_sync_var,
                  style="Ok.TLabel", wraplength=520).pack(anchor="w", pady=(6, 0))

        sw = self._card(duo, "执行状态", "生成前确认")
        sw.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        srow = tk.Frame(sw, bg=C_SURFACE)
        srow.pack(fill="x", padx=16, pady=(0, 8))
        tk.Checkbutton(srow, text="今天已全部操作过（方案从明天排起）",
                       variable=self.today_done_var, wraplength=330, justify="left",
                       bg=C_SURFACE, fg=C_INK, activebackground=C_SURFACE,
                       selectcolor="#FFFFFF", font=(FONT, 10), anchor="w",
                       command=self._on_switch_changed).pack(anchor="w", pady=2)
        tk.Checkbutton(srow, text="今天我要打包（仅起始日打包一次）",
                       variable=self.pack_today_var, wraplength=330, justify="left",
                       bg=C_SURFACE, fg=C_INK, activebackground=C_SURFACE,
                       selectcolor="#FFFFFF", font=(FONT, 10), anchor="w",
                       command=self._on_switch_changed).pack(anchor="w", pady=2)

        # ===== 账号 =====
        acc_box = self._card(content, "账号", "可增删小号、改备注与当前秒点")
        acc_box.pack(fill="x", pady=(0, 8))
        holder = tk.Frame(acc_box, bg=C_SURFACE)
        holder.pack(fill="x", padx=16, pady=(0, 8))
        self.acc_list = tk.Frame(holder, bg=C_SURFACE)
        self.acc_list.pack(fill="x")
        btn_bar = tk.Frame(holder, bg=C_SURFACE)
        btn_bar.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_bar, text="＋ 添加小号", command=self.add_small).pack(side="left")
        ttk.Button(btn_bar, text="重置为 1大3小",
                   command=self._load_defaults).pack(side="left", padx=8)

        # ===== 规则参数 =====
        par_box = self._card(content, "规则参数", "按实际使用情况调整，改动自动保存")
        par_box.pack(fill="x", pady=(0, 8))
        grid = tk.Frame(par_box, bg=C_SURFACE)
        grid.pack(fill="x", padx=16, pady=(0, 10))
        self.param_vars = {}
        for i, (key, label) in enumerate(PARAM_LABELS):
            r, c = divmod(i, 3)
            cell = tk.Frame(grid, bg=C_SURFACE)
            cell.grid(row=r, column=c, padx=(0, 16), pady=3, sticky="w")
            ttk.Label(cell, text=label, width=16, style="Muted.TLabel").pack(side="left")
            v = tk.StringVar()
            ent = ttk.Entry(cell, textvariable=v, width=7)
            if key == "plan_days":
                ent.configure(state="readonly")  # 规划天数自动取当月，禁止手改
            else:
                v.trace_add("write", self._schedule_save)  # 参数改动也即时保存
            ent.pack(side="left")
            self.param_vars[key] = v
        for c in range(3):
            grid.columnconfigure(c, weight=1)

        # ===== 主操作栏 =====
        op = tk.Frame(content, bg=C_BG)
        op.pack(fill="x", pady=(4, 4))
        ttk.Button(op, text="一键生成并采用", style="Primary.TButton",
                   command=self.on_quick_adopt).pack(side="left")
        ttk.Button(op, text="生成方案（先看再采用）",
                   command=self.on_generate).pack(side="left", padx=8)
        ttk.Button(op, text="查看当前方案",
                   command=self.on_view_current).pack(side="left", padx=8)
        ttk.Button(op, text="查看加减明细",
                   command=self.on_view_changes).pack(side="left")
        ttk.Button(op, text="作品上架推荐",
                   command=self.on_price_recommend).pack(side="left", padx=8)
        ttk.Button(op, text="导出明细CSV",
                   command=self.on_export).pack(side="left", padx=8)
        ttk.Label(op, textvariable=self.save_status_var,
                  style="Bg.TLabel").pack(side="right")

        # 方案引导条：生成后使用，采用方案后自动隐藏
        self.plan_tip = tk.Frame(content, bg="#EEF4FF", highlightthickness=1,
                                 highlightbackground="#D5E3FF")
        self.plan_tip.pack(fill="x")
        tk.Label(self.plan_tip,
                 text="最省事：点「一键生成并采用」。想看细项不用重新生成——点「查看当前方案」回看全文，点「查看加减明细」逐笔核对。",
                 bg="#EEF4FF", fg="#475467", font=(FONT, 9), wraplength=940,
                 justify="left").pack(anchor="w", padx=12, pady=6)

    # ---- 日期栏刷新 ----
    def _refresh_date_bar(self):
        self.ctx = month_context(today_done=self.today_done_var.get())
        c = self.ctx
        start_txt = "明天" if c["today_done"] else "今天"
        self.date_var.set(
            "当前日期：%d年%d月%d日 %s ｜ 本月共 %d 天 ｜ 方案从%s(%d月%d日)排到月末，共 %d 天"
            % (c["today_year"], c["today_month"], c["today"], c["today_weekday"],
               c["today_days_in_month"], start_txt, c["month"], c["start_day"], c["plan_days"]))
        if c["rest_days"]:
            rest_txt = "、".join("%d日" % d for d in sorted(c["rest_days"]))
            self.rest_var.set("★ 周日（%s）零收入、后端照扣；方案已自动扣除这些天" % rest_txt)
        else:
            self.rest_var.set("")
        # 规划天数同步显示
        if hasattr(self, "param_vars") and "plan_days" in self.param_vars:
            self.param_vars["plan_days"].set(str(c["plan_days"]))

    def _refresh_overview(self):
        """一眼速览：自动汇总今日变动、近期到期、打包机会、负余额等，省去反复点窗口。"""
        if not hasattr(self, "overview_var"):
            return
        plan = getattr(self, "adopted_plan", None)
        if not plan:
            self.overview_var.set(
                "尚未采用方案：填好各号当前秒点后点「一键生成并采用」，"
                "主界面就会自动显示今日该做什么、何时到期、何时能打包。")
            return
        try:
            y, m = int(plan["year"]), int(plan["month"])
            ps, pe = int(plan["start_day"]), int(plan["end_day"])
            dim = calendar.monthrange(y, m)[1]
        except Exception:
            self.overview_var.set("方案数据异常，请点「一键生成并采用」重新生成。")
            return

        parts = []
        curday = self._current_plan_day(plan)
        # 方案进度
        if curday is None or curday < ps:
            parts.append("方案：%d月%d日～%d日（尚未开始，共 %d 天）" % (m, ps, pe, pe - ps + 1))
        elif curday > pe:
            parts.append("方案：%d月%d日～%d日（已结束，请重新生成）" % (m, ps, pe))
        else:
            parts.append("方案：%d月%d日～%d日，今日第 %d/%d 天，剩 %d 天"
                         % (m, ps, pe, curday, pe, max(0, pe - curday + 1)))

        # 今日按方案净变动（余额曲线今日值 - 前一日值；首日与起始余额比）
        bmap = plan.get("balance_curve") or {}
        if curday is not None:
            prev = str(int(curday) - 1)
            delta_sum = 0.0
            got = False
            for note, v in (bmap.get(str(curday)) or {}).items():
                base = (bmap.get(prev) or {}).get(note,
                        (plan.get("start_bal") or {}).get(note, v))
                try:
                    delta_sum += float(v) - float(base)
                    got = True
                except Exception:
                    pass
            if got:
                parts.append("今日按方案净变动 %+g 点" % round(delta_sum, 2))

        # 7 天内到期：按真实日期分组，跨月自动显示下个月
        today = datetime.date.today()
        due = {}
        for note, batches in (plan.get("expiry_info") or {}).items():
            for b in batches:
                try:
                    e, a = int(b[1]), float(b[2])
                    if e <= dim:
                        edate = datetime.date(y, m, e)
                    else:
                        y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
                        edate = datetime.date(y2, m2, e - dim)
                    left = (edate - today).days
                    if 0 <= left <= 7:
                        due.setdefault(edate, []).append((note, a))
                except Exception:
                    continue
        if due:
            chunks = []
            for edate in sorted(due)[:2]:
                items = "、".join("%s %g点" % (n, a) for n, a in due[edate])
                chunks.append("%s %s" % (edate.strftime("%Y-%m-%d 00:00"), items))
            parts.append("⚠ 7天内到期：" + "；".join(chunks) + "（没用掉会流失）")
        else:
            parts.append("7天内无秒点到期")

        # 打包机会：大号日终余额首次达到打包线的日期
        big_note = None
        for r in getattr(self, "rows", []):
            if getattr(r, "role", "") == "big":
                big_note = r.note_var.get().strip() or r.role_var.get()
                break
        pack_cost = 0.0
        for key in ("pack_cost",):
            try:
                pack_cost = float((self.param_vars[key].get() or 0) if hasattr(self, "param_vars")
                                  and key in self.param_vars else 0)
            except Exception:
                pass
        pack_day = None
        if big_note and pack_cost > 0:
            for d in sorted(bmap, key=lambda x: int(x)):
                if curday is not None and int(d) < int(curday):
                    continue
                try:
                    if float((bmap[d] or {})[big_note]) >= pack_cost:
                        pack_day = (int(d), float(bmap[d][big_note]))
                        break
                except Exception:
                    continue
        if pack_day:
            parts.append("打包机会：%d月%d日起 %s 可达 %g 点（线 %g，软件不自动扣）"
                         % (m, pack_day[0], big_note, pack_day[1], pack_cost))
        elif big_note and pack_cost > 0:
            peak = (-1.0, None)
            for d in sorted(bmap, key=lambda x: int(x)):
                if curday is not None and int(d) < int(curday):
                    continue
                try:
                    v = float((bmap[d] or {})[big_note])
                    if v > peak[0]:
                        peak = (v, int(d))
                except Exception:
                    continue
            if peak[1] is not None:
                parts.append("打包：%s 规划期内最高约 %g 点，距打包线 %g 还差 %g 点"
                             % (big_note, peak[0], pack_cost, round(max(0, pack_cost - peak[0]), 2)))

        # 负余额告警
        negs = []
        for r in getattr(self, "rows", []):
            try:
                bal = float(r.bal_var.get() or 0)
            except ValueError:
                bal = 0.0
            if bal < 0:
                negs.append("%s %g" % (r.note_var.get().strip() or r.role_var.get(), bal))
        if negs:
            parts.append("⚠ 负余额（后端照扣）：" + "、".join(negs))

        self.overview_var.set("  ｜  ".join(parts))

    # ---- 今日执行面板：按系统日期每天自动更新 ----
    def _tick_today(self, check_only=False):
        self._refresh_overview()   # 参数/余额手改后回到窗口即可看到最新速览
        key = datetime.date.today().isoformat()
        if key != getattr(self, "_today_key", None):
            self._today_key = key
            self._refresh_date_bar()   # 跨天同步刷新日期栏，避免一直显示昨天
            self._refresh_today_panel()
            self._advance_balances()   # 跨天自动推进当前秒点
        if not check_only:
            self.after(30000, self._tick_today)

    def _refresh_today_panel(self):
        self._refresh_overview()
        if not hasattr(self, "today_lines"):
            return
        for c in list(self.today_lines.winfo_children()):
            c.destroy()
        plan = getattr(self, "adopted_plan", None)
        now = datetime.date.today()
        if not plan:
            self.today_date_var.set("尚未采用方案")
            ttk.Label(self.today_lines,
                      text="生成方案后点「确认采用此方案」，这里会每天自动显示各号该做什么。",
                      style="Muted.TLabel").pack(anchor="w")
            return
        try:
            pstart = datetime.date(int(plan["year"]), int(plan["month"]), int(plan["start_day"]))
            pend = datetime.date(int(plan["year"]), int(plan["month"]), int(plan["end_day"]))
            actions = plan.get("actions", {})
        except Exception:
            self.today_date_var.set("采用的方案数据异常")
            ttk.Label(self.today_lines, text="请重新生成方案并点「确认采用此方案」。",
                      style="Warn.TLabel", wraplength=520).pack(anchor="w")
            return
        self.today_date_var.set("今天是 %d年%d月%d日 %s ｜ 按已采用方案执行" %
                                (now.year, now.month, now.day, WEEK_CN[now.weekday()]))
        if now < pstart:
            ttk.Label(self.today_lines,
                      text="今天不用操作，采用的方案从 %d月%d日 开始。" % (pstart.month, pstart.day),
                      style="Muted.TLabel", wraplength=520).pack(anchor="w")
            return
        if now > pend:
            ttk.Label(self.today_lines,
                      text="方案已到 %d月%d日 结束，请重新生成并采用新方案。" % (pend.month, pend.day),
                      style="Warn.TLabel", wraplength=520).pack(anchor="w")
            return
        # 到期预警：平台优先消耗快到期的秒点（先获得的先扣），到期没用掉会自然流失
        def _expiry_warnings(day_obj):
            lines = []
            try:
                y_, m_ = int(plan["year"]), int(plan["month"])
                days_ = int(plan.get("end_day") or calendar.monthrange(y_, m_)[1])
            except Exception:
                return lines
            for note, batches in (plan.get("expiry_info") or {}).items():
                for b in batches:
                    try:
                        e, a = int(b[1]), float(b[2])
                        if e <= days_:
                            edate = datetime.date(y_, m_, e)
                        else:
                            y2, m2 = (y_ + 1, 1) if m_ == 12 else (y_, m_ + 1)
                            edate = datetime.date(y2, m2, e - days_)
                    except Exception:
                        continue
                    if edate == day_obj:
                        lines.append("⚠ %s：%g 点今天到期（%s），没用掉今天就流失！" %
                                     (note, a, edate.strftime("%Y-%m-%d 00:00")))
                    elif edate == day_obj + datetime.timedelta(days=1):
                        lines.append("⚠ %s：%g 点明天到期（%s），尽快用掉" %
                                     (note, a, edate.strftime("%Y-%m-%d 00:00")))
            return lines

        day_actions = actions.get(str(now.day), {})
        warns = _expiry_warnings(now)
        if not day_actions and not warns:
            ttk.Label(self.today_lines, text="今日无安排（休息日）。", style="Muted.TLabel").pack(anchor="w")
            return
        bmap = plan.get("balance_curve") or {}
        prev = str(int(now.day) - 1)
        for note, acts in day_actions.items():
            extra = ""
            try:
                v = float((bmap.get(str(now.day)) or {})[note])
                base = (bmap.get(prev) or {}).get(note,
                        (plan.get("start_bal") or {}).get(note, v))
                extra = "（今日净 %+g）" % round(v - float(base), 2)
            except Exception:
                pass
            txt = "· %s：%s%s" % (note, "、".join(acts) if acts else "静默（不登录、不操作）", extra)
            ttk.Label(self.today_lines, text=txt, wraplength=520).pack(anchor="w")
        for w in warns:
            ttk.Label(self.today_lines, text=w, style="Danger.TLabel", wraplength=520).pack(anchor="w")

    # ---- 查看当前方案：确保快照里有完整全文（兼容旧方案）----
    def _ensure_plan_text(self, plan):
        """返回方案全文；旧版方案没存 text 时，按快照的起始数据重建一份并回填。

        重建用快照里的 start_bal/start_login（采用那一刻的余额），而不是界面上
        已自动推进过的当前余额，保证回看的是「当初采用的那份方案」。
        """
        if not plan:
            return None
        if plan.get("text"):
            return plan["text"]
        try:
            accounts, p = self.collect()   # 参数、账号结构取当前界面
            # 用采用时的起始余额/登录天数覆盖，还原当初生成方案的输入
            start_bal = plan.get("start_bal", {})
            start_login = plan.get("start_login", {})
            restored = []
            for a in accounts:
                if a["note"] in start_bal:
                    b = dict(a)
                    b["balance"] = start_bal[a["note"]]
                    b["login_claimed"] = int(start_login.get(a["note"], 0))
                    restored.append(b)
            py, pm = int(plan["year"]), int(plan["month"])
            ps, pe = int(plan["start_day"]), int(plan["end_day"])
            rest = set()
            for d in range(ps, pe + 1):
                if datetime.date(py, pm, d).weekday() == 6:
                    rest.add(d)
            ctx = {
                "year": py, "month": pm, "start_day": ps, "days_in_month": pe,
                "plan_days": max(1, pe - ps + 1), "rest_days": rest,
                "weekday": WEEK_CN[datetime.date(py, pm, ps).weekday()],
                "today_done": False,
            }
            text, rows, _secs, _acts, _bal, _login, _exp = build_plan(
                restored, p, ctx, pack_on_start=bool(plan.get("packed_on_start", False)))
            plan["text"] = text
            plan["rows"] = rows
            self._auto_save()
            return text
        except Exception as e:
            messagebox.showerror("无法还原旧方案", "这份方案是旧版生成的，重建失败：%s\n请重新生成并采用一次。" % e)
            return None

    # ---- 当前秒点按方案自动推进 ----
    def _rebuild_plan_curves(self, plan):
        """兼容旧版方案：只有 actions 没有余额曲线时，用当前账号/参数重算一份。
        仅在用户没有手改余额时结果准确；给出提示让用户知情。
        """
        try:
            accounts, p = self.collect()   # 会重建 self.ctx，但方案日期用 plan 里的
            py, pm = int(plan["year"]), int(plan["month"])
            ps, pe = int(plan["start_day"]), int(plan["end_day"])
            plan_start = datetime.date(py, pm, ps)
            rest = set()
            for d in range(ps, pe + 1):
                if datetime.date(py, pm, d).weekday() == 6:
                    rest.add(d)
            ctx = {
                "year": py, "month": pm, "start_day": ps, "days_in_month": pe,
                "plan_days": max(1, pe - ps + 1), "rest_days": rest,
                "weekday": WEEK_CN[plan_start.weekday()], "today_done": False,
            }
            _text, _rows, _secs, _acts, bal_curve, login_curve, _exp = build_plan(
                accounts, p, ctx, pack_on_start=bool(plan.get("packed_on_start", False)))
            plan["balance_curve"] = {str(d): dict(v) for d, v in bal_curve.items()}
            plan["login_curve"] = {str(d): dict(v) for d, v in login_curve.items()}
            plan["start_bal"] = {a["note"]: float(a["balance"]) for a in accounts}
            plan["start_login"] = {a["note"]: int(a.get("login_claimed", 0)) for a in accounts}
            plan["login_cap"] = int(p["login_days_cap"])
            plan.pop("last_advance_day", None)
            self._auto_save()
            self.balance_sync_var.set(
                "✓ 已按当前填写的余额/参数为方案补出每日曲线；从现在起当前秒点会自动推进。")
            return True
        except Exception:
            self.balance_sync_var.set("当前方案缺少余额曲线，请重新生成并采用一次以启用自动扣点。")
            return False

    def _curve_value(self, curve, day, note, fallback):
        """取某账号某日的日终值。
        方案起始日之前 → 返回起始余额（fallback）；
        方案结束之后 → 取最后一天；缺失 → 回退起始值。
        """
        if not curve:
            return fallback
        keys = sorted(int(k) for k in curve.keys())
        if not keys:
            return fallback
        if day < keys[0]:
            return fallback
        d = min(day, keys[-1])
        v = curve.get(str(d), {}).get(note)
        return fallback if v is None else v

    def _advance_balances(self):
        """把各号「当前秒点 / 已领登录天数」按方案推进到今天。

        用「每日增量」推进而非直接套方案绝对值：这样你在中途手动改过的余额
        会作为新基准保留，之后的扣/加继续在你的数值上叠加，不会被方案冲掉。
        """
        plan = getattr(self, "adopted_plan", None)
        if not plan:
            return False
        if not plan.get("balance_curve"):
            if not self._rebuild_plan_curves(plan):
                return False
        try:
            py, pm = int(plan["year"]), int(plan["month"])
            ps, pe = int(plan["start_day"]), int(plan["end_day"])
        except Exception:
            return False
        today = datetime.date.today()
        done = bool(self.today_done_var.get())

        # 目标日：方案当月内取今天/昨天；早于方案月取起始日前一天；晚于方案月取月末
        if (today.year, today.month) < (py, pm):
            target = ps - 1
        elif (today.year, today.month) > (py, pm):
            target = pe
        else:
            target = today.day if done else today.day - 1
            target = min(max(target, ps - 1), pe)

        last = plan.get("last_advance_day")
        if last is None:
            # 新方案以「起始日」为基准：用户填的当前秒点是起始日操作前的真实
            # 余额，起始日当天的收益不补算；勾选「今天已操作」才推进到收盘。
            plan["last_advance_day"] = ps
            last = ps
        last = int(last)
        # 标记语义 eod：True=标记日 d 表示「第 d 天收盘」余额（含当天动作）；
        # False=起始日标记表示「操作前余额」（刚采用方案、还没勾选已操作）。
        # 旧版方案没有该标记：标记日大于起始日的按收盘理解。
        eod = bool(plan.get("eod_marker", last > ps))
        if last == target and (eod or done or target != ps):
            self._set_balance_sync_note(plan, target, unchanged=True)
            return False

        bal_curve = plan.get("balance_curve", {})
        login_curve = plan.get("login_curve", {})
        start_bal = plan.get("start_bal", {})
        start_login = plan.get("start_login", {})
        cap = int(plan.get("login_cap", 7))

        def _eod(d, flag):
            # 标记日 d 是否按「当日收盘」取曲线值
            return d > ps or (d == ps and flag)

        prev_eod = eod
        tgt_eod = eod or done or target > ps

        def bval(d, flag, note):
            fb = float(start_bal.get(note, 0))
            if d < ps or not _eod(d, flag):
                return fb
            return self._curve_value(bal_curve, d, note, fb)

        def lval(d, flag, note):
            fb = int(start_login.get(note, 0))
            if d < ps or not _eod(d, flag):
                return fb
            return int(round(self._curve_value(login_curve, d, note, fb)))

        changed = False
        log_entries = []
        direction = "回退" if target < last else "推进"
        self._binding_saves = True
        try:
            for r in self.rows:
                note = r.note_var.get().strip()
                if not note or note not in start_bal:
                    continue  # 新增/改名的号不在方案内，跳过
                delta = bval(target, tgt_eod, note) - bval(last, prev_eod, note)
                try:
                    cur = float(r.bal_var.get() or 0)
                except ValueError:
                    cur = 0.0
                # 登录天数按同样增量推进/回退
                ldelta = lval(target, tgt_eod, note) - lval(last, prev_eod, note)
                try:
                    lcur = int(r.login_var.get() or 0)
                except ValueError:
                    lcur = 0
                mdelta = 0.0
                mlogin = 0
                if abs(delta) > 0.005:
                    # 允许余额为负并如实显示：平台后端照扣，扣超了就是欠费，
                    # 旧版「扣成负数就跳过该号」会让余额卡住不动，反而对不上账。
                    newv = round(cur + delta, 2)
                    r.bal_var.set("%g" % newv)
                    mdelta = round(delta, 2)
                    changed = True
                if ldelta:
                    lnew = max(0, min(cap, lcur + ldelta))
                    if lnew != lcur:
                        r.login_var.set(str(lnew))
                        mlogin = lnew - lcur
                        changed = True
                if mdelta or mlogin:
                    acts = (plan.get("actions", {}).get(str(target), {}) or {}).get(note, [])
                    log_entries.append({
                        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "day": int(target), "note": note,
                        "delta": mdelta, "before": round(cur, 2),
                        "after": round(cur + mdelta, 2),
                        "login_delta": mlogin,
                        "reason": "、".join(acts) if acts else direction,
                        "direction": direction,
                    })
        finally:
            self._binding_saves = False

        plan["last_advance_day"] = target
        if target < ps:
            plan["eod_marker"] = False
        elif tgt_eod or last > ps:
            plan["eod_marker"] = True
        if log_entries:
            self._append_change_log(log_entries)
        self._auto_save()   # 推进日期必须落盘，避免重启后重复叠加同一段增量
        self._set_balance_sync_note(plan, target, unchanged=not changed,
                                    rolled_back=target < last)
        return changed

    def _append_change_log(self, entries):
        """把本次实际加减追加到记录（保留最近 500 条，避免无限增长）。"""
        log = getattr(self, "change_log", None)
        if not isinstance(log, list):
            log = []
        log.extend(entries)
        self.change_log = log[-500:]

    def _negative_notes(self):
        """返回当前余额为负的账号备注列表。"""
        out = []
        for r in getattr(self, "rows", []):
            try:
                if float(r.bal_var.get() or 0) < 0:
                    out.append(r.note_var.get().strip())
            except ValueError:
                pass
        return out

    def _set_balance_sync_note(self, plan, target, unchanged=False, rolled_back=False):
        try:
            py, pm = int(plan["year"]), int(plan["month"])
            if target < int(plan["start_day"]):
                self.balance_sync_var.set("当前秒点：方案尚未开始，保持你填写的起始余额。")
            elif target > int(plan["end_day"]):
                self.balance_sync_var.set("✓ 当前秒点已按方案推进到 %d月%d日（月末）；本月方案已结束。" % (pm, int(plan["end_day"])))
            else:
                if rolled_back:
                    word = "已回退"
                elif unchanged:
                    word = "已同步"
                else:
                    word = "已自动推进"
                msg = ("✓ 当前秒点%s %d月%d日收盘（含该日操作后的余额）：按方案扣/加"
                       "（点赞、复制、后端等）；手改过的余额会保留并继续叠加。")
                today = datetime.date.today()
                if (not bool(self.today_done_var.get())
                        and (today.year, today.month) == (py, pm)
                        and today.day == target + 1):
                    msg += "今天(%d月%d日)还没勾「今天各号已全部操作过」，操作完勾上即推进到今天。"
                    text = msg % (word, pm, target, pm, today.day)
                else:
                    text = msg % (word, pm, target)
                neg = self._negative_notes()
                if neg:
                    text += " ⚠ %s 已为负数（后端照扣所致），负数会如实显示；点「查看加减明细」核对每笔。" % "、".join(neg)
                self.balance_sync_var.set(text)
        except Exception:
            pass

    # ---- 字段改动即时保存（防抖）----
    def _schedule_save(self, *args):
        """任一输入变化后 0.6 秒自动保存；灌配置期间跳过。"""
        if getattr(self, "_binding_saves", False):
            return
        if self._save_job:
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.after(600, self._do_scheduled_save)

    def _do_scheduled_save(self):
        self._save_job = None
        self._auto_save()
        try:
            self.save_status_var.set("✓ 已自动保存（下次打开自动恢复）")
        except Exception:
            pass

    def _bind_row_saves(self, row):
        for var in (row.note_var, row.bal_var, row.login_var):
            var.trace_add("write", self._schedule_save)

    def _on_switch_changed(self):
        self._refresh_date_bar()
        self._advance_balances()   # 勾选今天已操作 → 推进到今天的余额
        self._auto_save()
        try:
            self.save_status_var.set("✓ 已自动保存")
        except Exception:
            pass

    # ---- 账号行管理 ----
    def add_row(self, role, idx, note, balance, login_claimed):
        row = AccountRow(self.acc_list, role, idx, note, balance, login_claimed,
                         on_delete=lambda r=None: None)
        row.frame.pack(fill="x", pady=4)
        if role == "small":
            row.frame.configure  # noqa
            # 重新绑定删除命令，带上自身引用
            for w in row.frame.winfo_children():
                pass
        self.rows.append(row)
        self._bind_row_saves(row)
        return row

    def add_small(self):
        self.small_counter += 1
        row = self.add_row("small", self.small_counter, "小号%d" % self.small_counter, 0, 0)
        # 找到删除按钮并绑定
        btn = row.frame.grid_slaves(row=0, column=7)
        if btn:
            btn[0].configure(command=lambda rr=row: self.delete_row(rr))
        self._schedule_save()

    def delete_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            row.frame.destroy()
            self._schedule_save()

    def _load_defaults(self):
        self._binding_saves = True
        try:
            self._load_defaults_inner()
        finally:
            self._binding_saves = False

    def _load_defaults_inner(self):
        for r in list(self.rows):
            r.frame.destroy()
        self.rows = []
        self.small_counter = 0
        self.adopted_plan = None   # 重置账号=放弃已采用方案，避免主页面显示旧安排
        self.add_row("big", 0, "大号", 0, 0)
        for i in range(3):
            self.add_small()
        for k, v in DEFAULTS.items():
            self.param_vars[k].set(str(v))
        self.today_done_var.set(False)
        self.pack_today_var.set(False)
        self._refresh_date_bar()

    # ---- 数据收集 ----
    def collect(self):
        accounts = [r.to_dict() for r in self.rows]
        p = {}
        for k, _ in PARAM_LABELS:
            raw = self.param_vars[k].get().strip()
            try:
                val = float(raw)
            except ValueError:
                raise ValueError("参数「%s」必须是数字。" % dict(PARAM_LABELS)[k])
            p[k] = int(val) if val == int(val) else val
        # 日期上下文按「今天是否已操作」实时重建，规划天数忽略配置旧值
        self.ctx = month_context(today_done=self.today_done_var.get())
        p["plan_days"] = self.ctx["plan_days"]
        return accounts, p

    # ---- 事件 ----
    def _make_plan_snapshot(self):
        """按当前界面生成一份完整方案快照（不弹窗、不采用）。"""
        accounts, p = self.collect()
        text, rows, _sections, actions, balance_curve, login_curve, expiry = build_plan(
            accounts, p, self.ctx, pack_on_start=self.pack_today_var.get())
        self.last_rows = rows
        c = self.ctx
        return {
            "year": c["year"], "month": c["month"],
            "start_day": c["start_day"], "end_day": c["days_in_month"],
            "text": text,                       # 完整方案全文，供「查看当前方案」随时回看
            "rows": rows,                       # 明细行，供查看时导出 CSV
            "actions": {str(d): dict(v) for d, v in actions.items()},
            "balance_curve": {str(d): dict(v) for d, v in balance_curve.items()},
            "login_curve": {str(d): dict(v) for d, v in login_curve.items()},
            "expiry_info": {k: [list(b) for b in v] for k, v in expiry.items()},
            "start_bal": {a["note"]: float(a["balance"]) for a in accounts},
            "start_login": {a["note"]: int(a.get("login_claimed", 0)) for a in accounts},
            "login_cap": int(p["login_days_cap"]),
            "packed_on_start": bool(self.pack_today_var.get()),
        }

    def on_generate(self):
        try:
            self.pending_plan = self._make_plan_snapshot()
            self._auto_save()  # 生成时顺手保存，改动即时落盘
            self._show_plan(self.pending_plan["text"])  # 独立窗口单页完整展示
        except Exception as e:
            messagebox.showerror("无法生成方案", str(e))

    def on_quick_adopt(self):
        """最少操作：一次点击生成并采用，主页面立即显示今日安排与速览。"""
        if getattr(self, "adopted_plan", None) and not messagebox.askyesno(
                "提示", "已有采用中的方案，一键生成并采用会替换它。继续吗？", parent=self):
            return
        try:
            self.pending_plan = self._make_plan_snapshot()
            self._apply_plan(self.pending_plan)
            messagebox.showinfo(
                "已一键采用",
                "方案已生成并采用。主页面今天就能看到每个号该做什么、净变动、到期时间和打包机会。\n"
                "想看完整方案点「查看当前方案」，想逐笔核对点「查看加减明细」，都不用重新生成。",
                parent=self)
        except Exception as e:
            messagebox.showerror("无法生成并采用", str(e))

    # ---- 方案独立页面：一页看完整方案，不拆分页签 ----
    def _show_plan(self, text, adopted=False):
        """adopted=True 表示回看已采用的方案：只读，不显示「确认采用」避免误重置进度。"""
        if hasattr(self, "plan_tip") and not self.plan_tip.winfo_ismapped():
            self.plan_tip.pack(fill="x")
        win = getattr(self, "plan_win", None)
        if win is not None and win.winfo_exists():
            # 已打开则刷新同一个窗口，避免堆一堆窗口
            txt = self.plan_text
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", text)
            txt.configure(state="disabled")
            txt.yview_moveto(0)
            self._after_plan_shown(text, adopted)
            win.lift()
            win.focus_set()
            return

        win = tk.Toplevel(self)
        self.plan_win = win
        win.configure(bg=C_BG)
        win.geometry("1040x860")
        win.minsize(760, 560)

        bar = tk.Frame(win, bg=C_SURFACE, highlightthickness=1,
                       highlightbackground=C_BORDER)
        bar.pack(fill="x")
        brow = tk.Frame(bar, bg=C_SURFACE)
        brow.pack(fill="x", padx=16, pady=10)
        tk.Label(brow, text="方案", bg=C_SURFACE, fg=C_INK,
                 font=(FONT, 12, "bold")).pack(side="left", padx=(0, 14))
        self.plan_status_var = tk.StringVar()
        ttk.Label(brow, textvariable=self.plan_status_var,
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(brow, text="关闭", command=self._close_plan).pack(side="right", padx=3)
        ttk.Button(brow, text="复制全文", command=self._copy_plan).pack(side="right", padx=3)
        ttk.Button(brow, text="导出明细CSV",
                   command=self.on_export).pack(side="right", padx=3)
        self._adopt_btn = ttk.Button(brow, text="确认采用此方案",
                                     style="Primary.TButton", command=self._adopt_plan)

        body = tk.Frame(win, bg=C_BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        txt = scrolledtext.ScrolledText(body, wrap="word", font=(FONT, 10),
                                        bg=C_SURFACE, fg=C_INK,
                                        insertbackground=C_PRIMARY,
                                        padx=18, pady=14, spacing1=2, spacing3=3,
                                        highlightthickness=1,
                                        highlightbackground=C_BORDER, relief="flat")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")  # 只读，防止误改方案
        self.plan_text = txt
        win.protocol("WM_DELETE_WINDOW", self._close_plan)
        win.after(80, lambda: (win.lift(), win.focus_set()))
        self._after_plan_shown(text, adopted)

    def _after_plan_shown(self, text, adopted):
        """根据生成/查看两种模式，调整标题、状态栏、按钮与导出明细来源。"""
        if adopted:
            plan = getattr(self, "adopted_plan", None)
            self._plan_export_rows = (plan or {}).get("rows") or self.last_rows
            btn = getattr(self, "_adopt_btn", None)
            if btn is not None:
                btn.pack_forget()   # 查看模式不提供「确认采用」，防止重置推进进度
            self.plan_win.title("当前方案 - %s" % APP_NAME)
            stamp = (plan or {}).get("adopted_at", "")
            self.plan_status_var.set("当前已采用的方案%s ｜ 只读回看，不影响方案执行" %
                                     ("（采用于 %s）" % stamp if stamp else ""))
        else:
            self._plan_export_rows = self.last_rows
            btn = getattr(self, "_adopt_btn", None)
            if btn is not None:
                btn.pack(side="right", padx=3)
            self.plan_win.title("方案 - %s" % APP_NAME)
            self.plan_status_var.set("方案生成时间：%s ｜ 确认采用后主页面每日显示各号操作" %
                                     datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

    def _detail_view_mode(self):
        """当前逐笔视图设置：(group, scope) = (按日期/按账号, 全部/只看已发生)。"""
        g = getattr(self, "detail_group_var", None)
        s = getattr(self, "detail_scope_var", None)
        return (g.get() if g else "day", s.get() if s else "done")

    def _current_plan_day(self, plan):
        """方案推进到哪一天（今天/昨天收盘），用于「只看已发生」过滤。"""
        if not plan:
            return None
        try:
            last = plan.get("last_advance_day")
            if last is not None:
                return int(last)
        except Exception:
            pass
        return None

    def _build_change_detail_text(self):
        """组装「加减明细」全文：当前余额 → 预期对照 → 逐笔核对 → 实际记录。"""
        lines = []
        W = 76
        lines.append("=" * W)
        lines.append("秒点加减明细（与平台余额逐笔核对）")
        lines.append("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("=" * W)

        plan = getattr(self, "adopted_plan", None) or getattr(self, "pending_plan", None)

        # ---- 一、当前各号秒点（界面现值，核对基准） ----
        lines.append("")
        lines.append("【一】当前各号秒点（界面现值 —— 核对基准）")
        lines.append("-" * W)
        cur = {}
        exp_info = (plan or {}).get("expiry_info") or {}
        if plan:
            py_, pm_ = int(plan["year"]), int(plan["month"])
            pdays_ = int(plan.get("end_day") or calendar.monthrange(py_, pm_)[1])
        else:
            py_ = pm_ = pdays_ = None
        for r in getattr(self, "rows", []):
            try:
                bal = float(r.bal_var.get() or 0)
            except ValueError:
                bal = 0.0
            note = r.note_var.get().strip() or r.role_var.get()
            cur[note] = bal
            mark = "  ⚠负数" if bal < 0 else ""
            lines.append("  %s%s 点%s" % (_pad(note, 14), _pad("%g" % bal, 10, "right"), mark))
            # 附到期批次：平台口径「发生时间 → 到期时间(00:00 失效)」，可直接与平台秒点明细对照
            # 跨月、跨年按真实日历进位（如 2026-11-28 发生 → 2026-12-05 00:00 到期）
            for b in exp_info.get(note, []):
                try:
                    if py_ is None:
                        continue
                    lines.append("      ↳ %s点（发生 %s → 到期 %s）" % (
                        _pad("%g" % float(b[2]), 5),
                        _date_stamp(py_, pm_, int(b[0]), pdays_),
                        _expire_stamp(py_, pm_, int(b[1]), pdays_)))
                except Exception:
                    continue

        # ---- 二、方案预期 vs 当前（解释为什么对不上） ----
        lines.append("")
        lines.append("【二】方案预期 vs 当前（为什么数字会不一样）")
        lines.append("-" * W)
        if not plan:
            lines.append("  （尚未采用方案，无可对照的预期值。）")
        else:
            py, pm = int(plan["year"]), int(plan["month"])
            ps, pe = int(plan["start_day"]), int(plan["end_day"])
            curday = self._current_plan_day(plan)
            start_bal = plan.get("start_bal", {}) or {}
            bal_curve = plan.get("balance_curve", {}) or {}
            lines.append("  方案区间：%d月%d日～%d日 ｜ 采用时间：%s ｜ 推进到：%s" % (
                pm, ps, pe, plan.get("adopted_at", "—"),
                ("%d日收盘" % curday) if curday is not None else "起始前"))
            lines.append("  %s  %s  %s  %s" % (
                _pad("账号", 14), _pad("方案起始", 12, "right"),
                _pad("方案该日预期", 14, "right"), _pad("当前实际", 10, "right")))
            for r in getattr(self, "rows", []):
                note = r.note_var.get().strip() or r.role_var.get()
                if note not in start_bal:
                    continue
                sb = start_bal.get(note)
                expect = None
                if curday is not None:
                    expect = self._curve_value(bal_curve, curday, note, sb)
                lines.append("  %s  %s  %s  %s" % (
                    _pad(note, 14),
                    _pad(("%g" % float(sb)) if sb is not None else "—", 12, "right"),
                    _pad(("%g" % float(expect)) if expect is not None else "—", 14, "right"),
                    _pad("%g" % cur.get(note, 0), 10, "right")))
            lines.append("  说明：软件按「每日增量」推进，你手工改过的余额会作为新基准继续叠加，")
            lines.append("        所以当前值常与方案模拟值不同；这是正常的，以【一】当前值为准。")
            if curday is None:
                lines.append("  ⚠ 方案推进标记为「起始前」：当前值就是你填的起始余额，方案当天动作尚未计入。")
        rows = (plan.get("rows") if plan else None) or getattr(self, "last_rows", None)
        data = []
        if rows:
            header = rows[0] if rows and isinstance(rows[0], list) else []
            data = rows[1:] if header and str(header[0]).startswith("日期") else rows

        # ---- 三、逐笔核对表（可切换按日期/账号分组、只看已发生/全部） ----
        lines.append("")
        group, scope = self._detail_view_mode()
        scope_txt = "只看已发生" if scope == "done" else "全部（含未发生）"
        lines.append("【三】方案逐笔核对（%s / %s）" %
                     ("按日期分组" if group == "day" else "按账号分组", scope_txt))
        lines.append("-" * W)
        if not data:
            lines.append("  （暂无方案明细，请先生成方案。）")
        else:
            curday = self._current_plan_day(plan)
            if scope == "done" and curday is not None:
                shown = [r for r in data if int(r[0]) <= curday]
            elif scope == "done":
                shown = []
            else:
                shown = data
            if not shown:
                lines.append("  （方案当天动作尚未计入；点「全部(含未发生)」可查看完整计划。）")
            else:
                # 从方案起始余额逐笔重算余额链：旧快照里存的余额是「日终回填」的坏值，
                # 直接用每笔「变动」累加才能给出「上笔→变动→本笔」自洽的一串数。
                start_bal = (plan.get("start_bal", {}) or {}) if plan else {}
                running = {}
                def _base(note, fallback):
                    if note in running:
                        return running[note]
                    return float(start_bal.get(note, fallback))
                if group == "day":
                    by_day = {}
                    for row in shown:
                        by_day.setdefault(int(row[0]), []).append(row)
                    for d in sorted(by_day):
                        acts = (plan.get("actions", {}) or {}).get(str(d), {}) if plan else {}
                        tag = "  ★周日休息日" if any("静默" in a for a in acts.values()) else ""
                        lines.append("  ── %d日%s" % (d, tag))
                        for row in by_day[d]:
                            role, note, action, amt = row[1], row[2], row[3], float(row[4])
                            base = _base(note, float(row[5]) - amt)
                            bal = round(base + amt, 2)
                            running[note] = bal
                            mark = "  ⚠负" if bal < 0 else ""
                            lines.append("     %s %s %s %s%s" %
                                         (_pad(role, 5), _pad(note, 12), _pad(action, 30),
                                          _pad("%s → %+g → %s" % ("%g" % base, amt, "%g" % bal), 26),
                                          mark))
                else:
                    by_note = {}
                    for row in shown:
                        by_note.setdefault(row[2], []).append(row)
                    for note in sorted(by_note):
                        lines.append("  ── %s" % note)
                        base = float(start_bal.get(note, 0)) if plan else 0.0
                        for row in sorted(by_note[note], key=lambda x: int(x[0])):
                            day, amt = int(row[0]), float(row[4])
                            bal = round(base + amt, 2)
                            mark = "  ⚠负" if bal < 0 else ""
                            lines.append("     %s %s %s%s" %
                                         (_pad("%d日" % day, 5), _pad(row[3], 30),
                                          _pad("%s → %+g → %s" % ("%g" % base, amt, "%g" % bal), 26),
                                          mark))
                            base = bal

        # ---- 四、实际加减记录（推进/回退时真实改了多少） ----
        lines.append("")
        lines.append("【四】软件实际加减记录（每日推进 / 取消勾选回退时真实改到余额上的数）")
        lines.append("-" * W)
        log = getattr(self, "change_log", None)
        if not isinstance(log, list) or not log:
            lines.append("  （暂无记录。采用方案后跨天，或勾选/取消「今天已操作」，这里会逐条记录。）")
            lines.append("  提示：旧版本采用或手工填写的余额没有逐笔记录，属正常现象。")
        else:
            for e in log[-80:]:
                try:
                    lines.append("  %s  %s月%s日  %-10s %+8s 点  %s → %s  [%s]  %s%s" % (
                        e.get("ts", ""), plan.get("month", "") if plan else "",
                        e.get("day", ""), e.get("note", ""),
                        ("%g" % float(e.get("delta", 0))),
                        ("%g" % float(e.get("before", 0))),
                        ("%g" % float(e.get("after", 0))),
                        e.get("direction", ""), e.get("reason", ""),
                        "  ⚠负" if float(e.get("after", 0)) < 0 else ""))
                except Exception:
                    continue
            lines.append("  （仅显示最近 80 条，共 %d 条存档。）" % len(log))
        lines.append("")
        lines.append("核对方法：先看【一】当前值是否与平台一致；不一致时对照【二】找原因，")
        lines.append("再用【三】逐笔「上笔→变动→本笔」定位哪一笔，最后用【四】看软件实际改了多少。")
        return "\n".join(lines)

    def _refresh_detail_text(self, *args):
        """视图切换后重绘明细全文。"""
        txt = getattr(self, "detail_text", None)
        if txt is None:
            return
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("1.0", self._build_change_detail_text())
        txt.configure(state="disabled")
        txt.yview_moveto(0)

    # ---- 作品上架推荐：综合各号秒点与即将失效批次，给出一个统一的推荐上架秒点 ----
    def _build_recommend_text(self, r):
        """组装推荐说明全文：结论 → 各候选对比 → 计算依据与约束。"""
        W = 72
        L = []
        L.append("=" * W)
        L.append("大号作品上架秒点推荐（方案期内固定，不中途改价）")
        L.append("生成时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        L.append("=" * W)
        L.append("")
        L.append("【推荐结论】")
        L.append("  每个作品统一上架：%d 点" % r["price"])
        L.append("  %s" % r["note"])
        cur = r["current"]
        if cur["price"] != r["price"]:
            L.append("  当前定价 %d 点：过期浪费约 %g 点、复制 %d 次、大号实收约 %g 点。" %
                     (cur["price"], cur["waste"], cur["copies"], cur["income"]))
            L.append("  推荐定价 %d 点：过期浪费约 %g 点、复制 %d 次、大号实收约 %g 点。" %
                     (r["price"], r["waste"], r["copies"], r["income"]))
        L.append("")
        L.append("【候选定价对比】（过期浪费越少越好；同样不浪费时复制次数越少越省事）")
        L.append("  %s  %s  %s  %s" % (
            _pad("上架秒点", 10, "right"), _pad("过期浪费", 10, "right"),
            _pad("复制次数", 10, "right"), _pad("大号实收", 12, "right")))
        for c in r["candidates"]:
            mark = "  ← 推荐" if c["price"] == r["price"] else ""
            L.append("  %s  %s  %s  %s%s" % (
                _pad("%d" % c["price"], 10, "right"),
                _pad("%g" % c["waste"], 10, "right"),
                _pad("%d" % c["copies"], 10, "right"),
                _pad("%g" % c["income"], 12, "right"), mark))
        L.append("")
        L.append("【计算依据与约束】")
        L.append("  · 综合每个小号「当前秒点 + 方案期内每日点赞/登录新增」与秒点有效期，")
        L.append("    按先进先出模拟哪些批次会在到期前来不及被复制而流失。")
        L.append("  · 目标：先把过期浪费降到最低，再在不浪费的前提下选复制次数最少（定价尽量高）的一档。")
        L.append("  · 一个方案内每个作品的上架秒点保持不变，不按日调价；改价需到平台手动操作并会消耗秒点。")
        L.append("  · 本推荐只给建议值，不会自动改平台定价；点「应用此价到作品定价」后需重新生成方案才会按新价排期。")
        return "\n".join(L)

    def on_price_recommend(self):
        """计算并展示作品上架秒点推荐。"""
        try:
            accounts, p = self.collect()
            r = recommend_price(accounts, p, self.ctx)
            if not r or not r.get("ok"):
                messagebox.showinfo("无法推荐",
                                    "请保留至少一个小号，再计算作品上架推荐。", parent=self)
                return
            self._recommend_result = r
            text = self._build_recommend_text(r)
            win = getattr(self, "recommend_win", None)
            if win is not None and win.winfo_exists():
                w = getattr(self, "recommend_text", None)
                if w is not None:
                    w.configure(state="normal")
                    w.delete("1.0", "end")
                    w.insert("1.0", text)
                    w.configure(state="disabled")
                win.lift()
                win.focus_set()
                return

            win = tk.Toplevel(self)
            self.recommend_win = win
            win.title("作品上架推荐 - %s" % APP_NAME)
            win.configure(bg=C_BG)
            win.geometry("960x760")
            win.minsize(760, 560)

            bar = tk.Frame(win, bg=C_SURFACE, highlightthickness=1,
                           highlightbackground=C_BORDER)
            bar.pack(fill="x")
            brow = tk.Frame(bar, bg=C_SURFACE)
            brow.pack(fill="x", padx=16, pady=10)
            tk.Label(brow, text="作品上架推荐", bg=C_SURFACE, fg=C_INK,
                     font=(FONT, 12, "bold")).pack(side="left", padx=(0, 14))
            ttk.Label(brow, text="方案期内固定不调价", style="Muted.TLabel").pack(side="left")
            ttk.Button(brow, text="关闭", command=self._close_recommend).pack(side="right", padx=3)
            ttk.Button(brow, text="复制全文", command=self._copy_recommend).pack(side="right", padx=3)
            ttk.Button(brow, text="应用此价到作品定价", style="Primary.TButton",
                       command=self._apply_recommend_price).pack(side="right", padx=3)

            body = tk.Frame(win, bg=C_BG)
            body.pack(fill="both", expand=True, padx=12, pady=12)
            txt = scrolledtext.ScrolledText(body, wrap="none", font=("Consolas", 10),
                                            bg=C_SURFACE, fg=C_INK,
                                            insertbackground=C_PRIMARY,
                                            padx=16, pady=14, highlightthickness=1,
                                            highlightbackground=C_BORDER, relief="flat")
            txt.pack(fill="both", expand=True)
            txt.insert("1.0", text)
            txt.configure(state="disabled")
            self.recommend_text = txt
            win.protocol("WM_DELETE_WINDOW", self._close_recommend)
            win.after(80, lambda: (win.lift(), win.focus_set()))
        except Exception as e:
            messagebox.showerror("推荐失败", str(e), parent=self)

    def _apply_recommend_price(self):
        """把推荐价写回「作品定价」参数框并保存。"""
        r = getattr(self, "_recommend_result", None)
        if not r:
            return
        try:
            self.param_vars["work_price"].set(str(r["price"]))
            self._auto_save()
            messagebox.showinfo(
                "已应用", "作品定价已设为 %d 点并保存。\n"
                "请点「一键生成并采用」或「生成方案」重新排期，方案才会按新价执行。" % r["price"],
                parent=getattr(self, "recommend_win", self))
        except Exception as e:
            messagebox.showerror("应用失败", str(e), parent=self)

    def _copy_recommend(self):
        w = getattr(self, "recommend_text", None)
        if w is None:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(w.get("1.0", "end-1c"))
            messagebox.showinfo("已复制", "推荐说明已复制到剪贴板。",
                                parent=getattr(self, "recommend_win", self))
        except Exception:
            pass

    def _close_recommend(self):
        win = getattr(self, "recommend_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self.recommend_win = None
        self.recommend_text = None

    def on_view_changes(self):
        """独立窗口展示每笔扣除/增加明细，供用户核对是否有加减错误。"""
        text = self._build_change_detail_text()
        win = getattr(self, "detail_win", None)
        if win is not None and win.winfo_exists():
            self._refresh_detail_text()
            win.lift()
            win.focus_set()
            return

        win = tk.Toplevel(self)
        self.detail_win = win
        win.title("加减明细 - %s" % APP_NAME)
        win.configure(bg=C_BG)
        win.geometry("1080x860")
        win.minsize(780, 580)

        bar = tk.Frame(win, bg=C_SURFACE, highlightthickness=1,
                       highlightbackground=C_BORDER)
        bar.pack(fill="x")
        brow = tk.Frame(bar, bg=C_SURFACE)
        brow.pack(fill="x", padx=16, pady=10)
        tk.Label(brow, text="加减明细", bg=C_SURFACE, fg=C_INK,
                 font=(FONT, 12, "bold")).pack(side="left", padx=(0, 14))
        ttk.Label(brow, text="与平台余额逐笔核对",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(brow, text="关闭", command=self._close_detail).pack(side="right", padx=3)
        ttk.Button(brow, text="复制全文", command=self._copy_detail).pack(side="right", padx=3)
        ttk.Button(brow, text="导出明细CSV",
                   command=self.on_export).pack(side="right", padx=3)

        # 视图切换工具条：分组方式 / 是否已发生，切换即重绘
        tools = tk.Frame(win, bg=C_SURFACE, highlightthickness=1,
                         highlightbackground=C_BORDER)
        tools.pack(fill="x", padx=12, pady=(10, 0))
        trow = tk.Frame(tools, bg=C_SURFACE)
        trow.pack(fill="x", padx=12, pady=8)
        ttk.Label(trow, text="逐笔视图：", style="Muted.TLabel").pack(side="left")
        ttk.Radiobutton(trow, text="按日期", value="day", variable=self.detail_group_var,
                        command=self._refresh_detail_text).pack(side="left")
        ttk.Radiobutton(trow, text="按账号", value="note", variable=self.detail_group_var,
                        command=self._refresh_detail_text).pack(side="left", padx=(2, 12))
        ttk.Radiobutton(trow, text="只看已发生", value="done", variable=self.detail_scope_var,
                        command=self._refresh_detail_text).pack(side="left")
        ttk.Radiobutton(trow, text="全部(含未发生)", value="all", variable=self.detail_scope_var,
                        command=self._refresh_detail_text).pack(side="left")

        body = tk.Frame(win, bg=C_BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        txt = scrolledtext.ScrolledText(body, wrap="none", font=("Consolas", 10),
                                        bg=C_SURFACE, fg=C_INK,
                                        insertbackground=C_PRIMARY,
                                        padx=16, pady=14, highlightthickness=1,
                                        highlightbackground=C_BORDER, relief="flat")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        self.detail_text = txt
        win.protocol("WM_DELETE_WINDOW", self._close_detail)
        win.after(80, lambda: (win.lift(), win.focus_set()))

    def _copy_detail(self):
        w = getattr(self, "detail_text", None)
        if w is None:
            return
        txt = w.get("1.0", "end-1c")
        try:
            self.clipboard_clear()
            self.clipboard_append(txt)
            messagebox.showinfo("已复制", "加减明细全文已复制到剪贴板。", parent=self.detail_win)
        except Exception:
            pass

    def _close_detail(self):
        win = getattr(self, "detail_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self.detail_win = None
        self.detail_text = None

    def on_view_current(self):
        """随时回看已采用的完整方案，不必重新生成（重新生成会基于当前余额，是另一份方案）。"""
        plan = getattr(self, "adopted_plan", None)
        if not plan:
            messagebox.showinfo("提示",
                                "还没有已采用的方案。请先点「生成方案」，核对后点「确认采用此方案」。")
            return
        text = self._ensure_plan_text(plan)
        if text is None:
            return
        self._show_plan(text, adopted=True)

    def _apply_plan(self, plan):
        """采用一份方案快照：持久化 + 刷新主页面 + 立即推进当天余额。"""
        self.adopted_plan = dict(plan)
        self.adopted_plan["adopted_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.adopted_plan.pop("last_advance_day", None)  # 新方案从头推进
        self._auto_save()
        self.plan_tip.pack_forget()   # 采用后不再需要生成引导，留出今日执行空间
        self._refresh_today_panel()   # 内含一眼速览刷新
        self._advance_balances()      # 采用即按方案推进当天余额
        self._refresh_overview()      # 推进后再刷一次，保证净变动/余额信息是最新

    def _adopt_plan(self):
        """确认采用当前生成的方案：持久化快照，主页面从此每天自动显示各号操作。"""
        plan = getattr(self, "pending_plan", None)
        if not plan:
            parent = getattr(self, "plan_win", None)
            if parent is None or not parent.winfo_exists():
                parent = self
            messagebox.showwarning("提示", "请先生成方案。", parent=parent)
            return
        self._apply_plan(plan)
        self._close_plan()
        messagebox.showinfo("已采用",
                            "方案已采用。主页面会每天自动显示当天各号该做什么，当前秒点也会按方案自动扣/加。\n想看完整方案时，点「查看当前方案」即可回看，不用重新生成。",
                            parent=self)

    def _close_plan(self):
        win = getattr(self, "plan_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
        self.plan_win = None
        self.plan_text = None
        self._adopt_btn = None
        self._plan_export_rows = None

    def _copy_plan(self):
        txt = getattr(self, "plan_text", None)
        if txt is None:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(txt.get("1.0", "end-1c"))
            messagebox.showinfo("已复制", "方案全文已复制到剪贴板。", parent=self.plan_win)
        except Exception as e:
            messagebox.showerror("复制失败", str(e), parent=self.plan_win)

    def on_export(self):
        rows = getattr(self, "_plan_export_rows", None)
        if rows is None:
            rows = self.last_rows
        if not rows:
            messagebox.showinfo("提示", "请先点击「生成方案」，或采用方案后点「查看当前方案」。")
            return
        path = filedialog.asksaveasfilename(
            title="导出明细CSV", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")], initialfile="秒点方案明细.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(rows)
            parent = getattr(self, "plan_win", None)
            if parent is not None and not parent.winfo_exists():
                parent = None
            messagebox.showinfo("成功", "已导出：%s" % path, parent=parent)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def config_path(self):
        """配置统一放在「用户目录」，exe 换位置/更新后仍能读到同一份，不被覆盖丢失。"""
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        folder = os.path.join(base, APP_NAME)
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
        return os.path.join(folder, "秒点规划器配置.json")

    def _legacy_config_path(self):
        """旧版配置位置：exe 同目录。仅用于一次性迁移。"""
        return os.path.join(_app_dir(), "秒点规划器配置.json")

    # ---- 配置：开机自动恢复 + 自动保存（无需手动点）----
    def _auto_save(self):
        """账号与参数分别容错保存：某一项输入非法也不影响其他项落盘。"""
        data = {"version": VERSION}
        # 账号：逐行容错
        accounts = []
        for r in self.rows:
            try:
                accounts.append(r.to_dict())
            except Exception:
                pass
        data["accounts"] = accounts
        # 参数：逐项容错，跳过非法输入
        p = {}
        for k, _ in PARAM_LABELS:
            try:
                raw = self.param_vars[k].get().strip()
                val = float(raw)
                p[k] = int(val) if val == int(val) else val
            except Exception:
                pass
        data["params"] = p
        data["today_done"] = bool(self.today_done_var.get())
        data["pack_today"] = bool(self.pack_today_var.get())
        data["adopted_plan"] = getattr(self, "adopted_plan", None)
        data["change_log"] = getattr(self, "change_log", [])
        try:
            with open(self.config_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 保存到状态栏给出可见提示，不再静默失败
            try:
                self.save_status_var.set("⚠ 配置保存失败：%s" % str(e)[:40])
            except Exception:
                pass

    def _apply_data(self, data):
        """把配置数据灌回界面（供自动恢复与手动重置共用）。"""
        self._binding_saves = True
        try:
            self._apply_data_inner(data)
        finally:
            self._binding_saves = False

    def _apply_data_inner(self, data):
        for r in list(self.rows):
            r.frame.destroy()
        self.rows = []
        self.small_counter = 0
        has_big = False
        for a in data["accounts"]:
            if a["role"] == "big":
                self.add_row("big", 0, a["note"], a["balance"], a.get("login_claimed", 0))
                has_big = True
            else:
                self.small_counter += 1
                nr = self.add_row("small", self.small_counter,
                                  a["note"], a["balance"], a.get("login_claimed", 0))
                btn = nr.frame.grid_slaves(row=0, column=7)
                if btn:
                    btn[0].configure(command=lambda rr=nr: self.delete_row(rr))
        if not has_big:
            self.add_row("big", 0, "大号", 0, 0)
        # 先用默认值填满，再用配置里的值覆盖，避免残缺配置导致参数框为空
        for k, v in DEFAULTS.items():
            self.param_vars[k].set(str(v))
        for k, v in data.get("params", {}).items():
            if k in self.param_vars and str(v).strip() != "":
                self.param_vars[k].set(str(v))
        # 恢复两个执行状态开关
        self.today_done_var.set(bool(data.get("today_done", False)))
        self.pack_today_var.set(bool(data.get("pack_today", False)))
        # 恢复实际加减记录（供「查看加减明细」核对，兼容旧配置缺字段）
        log = data.get("change_log", [])
        self.change_log = log if isinstance(log, list) else []
        self._refresh_date_bar()

    def _auto_load(self):
        """启动时读取上次配置；兼容旧 exe 目录配置并迁到用户目录；无则用默认值。"""
        path = self.config_path()
        data = None
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None
        # 若用户目录没有，尝试从旧 exe 目录读取并迁移（换位置/更新后数据不丢）
        if not data or not data.get("accounts"):
            old = self._legacy_config_path()
            if os.path.exists(old):
                try:
                    with open(old, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                    if old_data.get("accounts"):
                        data = old_data
                        # 直接把旧配置落到新路径（不要经过界面，避免写入默认值）
                        with open(path, "w", encoding="utf-8") as nf:
                            json.dump(old_data, nf, ensure_ascii=False, indent=2)
                except Exception:
                    pass
        if data and data.get("accounts"):
            self._apply_data(data)
            self.adopted_plan = data.get("adopted_plan")
            try:
                self.save_status_var.set("✓ 已自动恢复上次填写的数据")
            except Exception:
                pass
        else:
            self._load_defaults()

    # ===== 内置版本更新：检查 / 横幅 / 下载 / 安装 / 重启 =====
    def _check_update(self, manual=False):
        cfg = load_update_config()
        source = cfg.get("source") or UPDATE_MANIFEST_PRIMARY
        urls = _mirror_urls(source)
        self.update_status.config(text="检查更新中…", fg="#94A3B8")
        self.update_btn.config(state=tk.DISABLED)

        def worker():
            if _UPDATE_SKIP_NETWORK:
                self.after(0, self._on_manifest, None, None, manual)
                return
            d, hit = _fetch_manifest(urls)
            self.after(0, self._on_manifest, d, hit, manual)

        threading.Thread(target=worker, daemon=True).start()

    def _on_manifest(self, manifest, hit_source, manual):
        self.update_btn.config(state=tk.NORMAL)
        if not manifest:
            self.update_status.config(text="检查失败（网络/地址）", fg="#94A3B8")
            if manual:
                messagebox.showwarning("检查更新", "暂时无法连接更新服务器，请稍后再试。")
            return
        latest = manifest.get("version", "0")
        self.update_status.config(text="线上 v%s · %s" % (latest, self._short_source(hit_source)),
                                  fg="#94A3B8")
        if cmp_version(latest, APP_VERSION) > 0:
            self.update_status.config(fg="#FBBF24")   # 有新版本 → 橙色提示
            cfg = load_update_config()
            # 跳过此版本：仅启动检查生效，且只对该版本
            if (not manual) and cfg.get("skip_version") == latest:
                return
            self._show_banner(manifest, hit_source, manual)
        elif manual:
            messagebox.showinfo("检查更新", "当前已是最新版本（v%s）。" % APP_VERSION)

    @staticmethod
    def _short_source(url):
        if not url:
            return ""
        if "ghproxy.net" in url:
            return "ghproxy"
        if "gh-proxy.com" in url:
            return "gh-proxy"
        if "github.com" in url and "/raw/" in url:
            return "github/raw"
        return "raw"

    def _show_banner(self, manifest, hit_source, manual):
        self.banner_version = manifest.get("version")
        self.banner_manifest = manifest
        notes = manifest.get("notes", "")
        self.banner_text.config(text="发现新版本 v%s%s" % (
            self.banner_version, "（重要更新）" if manifest.get("mandatory") else ""))
        self.banner_notes.config(text=(notes[:120] + ("…" if len(notes) > 120 else ""))
                                 if notes else "")
        self.banner_stage.config(text="")
        self.banner_pb.pack_forget()
        self.banner.pack(fill="x", before=self._main_wrap)
        # 跳过此版本只对启动检查生效
        self.btn_skip.config(state=tk.DISABLED if manual else tk.NORMAL)
        self.btn_now.config(state=tk.NORMAL)
        self.btn_later.config(state=tk.NORMAL)

    def _start_download(self):
        m = getattr(self, "banner_manifest", None)
        if not m:
            return
        self.banner_pb.pack(fill="x", padx=10, pady=(2, 6))
        self.banner_pb["value"] = 0
        self.banner_pb["maximum"] = 1
        self.banner_stage.config(text="准备下载…")
        self.btn_now.config(state=tk.DISABLED)
        self.btn_skip.config(state=tk.DISABLED)
        self.btn_later.config(state=tk.DISABLED)

        def on_progress(done, total):
            self.after(0, lambda: [
                self.banner_pb.config(maximum=total or 1, value=done),
                self.banner_stage.config(text="下载中 %.0f%%" % (done / (total or 1) * 100))])

        def on_stage(t):
            self.after(0, lambda: self.banner_stage.config(text=t))

        def worker():
            dest = os.path.join(_app_dir(), "_update_tmp.exe")
            try:
                if os.path.isfile(dest):
                    os.remove(dest)
            except Exception:
                pass
            ok, msg = download_update(
                m.get("download_url"), dest,
                sha256=m.get("sha256"), size=m.get("size"),
                on_progress=on_progress, on_stage=on_stage)
            self.after(0, self._on_download_done, ok, msg, dest)

        threading.Thread(target=worker, daemon=True).start()

    def _on_download_done(self, ok, msg, dest):
        if not ok:
            self.banner_stage.config(text="下载失败：" + msg)
            self.btn_now.config(state=tk.NORMAL)
            self.btn_later.config(state=tk.NORMAL)
            self.btn_skip.config(state=tk.NORMAL)
            messagebox.showerror("更新失败", "下载未成功：" + msg + "\n可点「立即更新」重试。")
            return
        self.banner_stage.config(text="下载完成，正在安装替换…")
        ok2, msg2 = install_update(dest)
        if not ok2:
            self.banner_stage.config(text="安装失败：" + msg2)
            self.btn_now.config(state=tk.NORMAL)
            messagebox.showerror("安装失败", msg2)
            return
        self.banner.pack_forget()
        self._ask_restart()

    def _ask_restart(self):
        win = tk.Toplevel(self)
        win.title("更新完成")
        win.configure(bg=C_SURFACE)
        win.geometry("400x170")
        win.transient(self)
        tk.Label(win, text="新版本已安装完成！", bg=C_SURFACE, fg=C_INK,
                 font=(FONT, 13, "bold")).pack(pady=(22, 6))
        tk.Label(win, text="是否立即重启以使用新版本？", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 10)).pack(pady=4)
        bf = tk.Frame(win, bg=C_SURFACE)
        bf.pack(pady=12)
        ttk.Button(bf, text="立即重启", style="Primary.TButton",
                   command=lambda: (win.destroy(), restart_app(self))).pack(side="left", padx=8)
        ttk.Button(bf, text="稍后重启",
                   command=win.destroy).pack(side="left", padx=8)

    def _on_skip(self):
        v = getattr(self, "banner_version", None)
        if v:
            cfg = load_update_config()
            cfg["skip_version"] = v
            save_update_config(cfg)
        self.banner.pack_forget()

    def _on_later(self):
        self.banner.pack_forget()

    def _restore_default_source(self):
        cfg = load_update_config()
        cfg["source"] = None
        save_update_config(cfg)
        self.restore_src_btn.pack_forget()
        self.update_status.config(text="已恢复默认更新地址", fg="#94A3B8")

    def _on_close(self):
        # 取消尚未触发的防抖保存，避免销毁后回调报错
        if getattr(self, "_save_job", None):
            try:
                self.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        self._auto_save()
        self.destroy()


def main():
    cleanup_mei()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
