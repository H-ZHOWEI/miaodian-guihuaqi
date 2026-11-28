# -*- coding: utf-8 -*-
"""
秒点规划器 v1.20.0
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
VERSION = "1.20.0"
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
    "copy_limit": 40,       # 单个作品累计复制上限：满40次后下线换新，超过后大号收不到点
    "publish_cost": 20,     # 发布/上架一个新作品消耗的大号秒点
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


def _real_date(year, month, day):
    """把「当月第 day 天」换成真实日期：day=1 为当月1号，day<=0 往前跨月、
    超过月末往后跨月，跨年自动进位。供过期批次（获得日可为负）统一换算。"""
    return datetime.date(year, month, 1) + datetime.timedelta(days=int(day) - 1)


def _date_stamp(year, month, day, days_in_month=None):
    """平台口径的日期戳 YYYY-MM-DD；day<=0 往前跨月、超月末往后跨月、跨年自动进位。"""
    t = _real_date(year, month, day)
    return "%04d-%02d-%02d" % (t.year, t.month, t.day)


def _expire_stamp(year, month, day, days_in_month=None):
    """平台口径的到期时间戳：到期日当天 00:00 失效（截图：发生 11-27 → 到期 12-04 00:00）。"""
    return _date_stamp(year, month, day, days_in_month) + " 00:00"


def _initial_queue(ac, p, start_day):
    """构造账号初始秒点队列。

    普通情况：当前余额整批视为「起始日获得」，有效期后过期。
    勾选「今天有今晚12点过期的秒点」（ac['expire_today']=True）：
      把当前余额均分成 expiry 份，获得日依次为 start_day-expiry ... start_day-1，
      于是从今晚(start_day)起每天过期一份——对应平台「每天过期7天前获得的那批」。
    负余额不走队列（记为欠费 debt），保持原逻辑。
    """
    bal = float(ac.get("balance", 0))
    if bal <= 0:
        return []
    expiry = max(1, int(p.get("expiry_days", 7)))
    if ac.get("expire_today"):
        per = bal / float(expiry)
        # 获得日 = start_day-expiry ... start_day-1（今晚 start_day 过期的正是最早那份）
        return [(start_day - expiry + i, round(per, 6)) for i in range(expiry)]
    return [(start_day, bal)]


def _init_work_state(p, works=None):
    """构造可变的作品状态副本：[{name, copies, active}, ...]。

    works 为用户在界面维护的大号作品列表；缺省时按 work_count 生成默认作品。
    copies 是累计被复制次数（用户手填 + 系统自动累计）；满 copy_limit 的作品
    自动视为下线（active=False），不再分派复制，避免超过40次后大号白干。
    """
    limit = int(p.get("copy_limit", DEFAULTS["copy_limit"]))
    if works:
        out = []
        for w in works:
            cp = int(w.get("copies", 0) or 0)
            out.append({"name": str(w.get("name", "作品")),
                        "copies": cp, "start_copies": cp,
                        "active": bool(w.get("active", True)) and cp < limit})
        return out
    return [{"name": "作品%d" % (i + 1), "copies": 0, "start_copies": 0, "active": True}
            for i in range(max(1, int(p["work_count"])))]


def simulate_small(ac, p, start_day=1, rest_days=None, cal=None, work_state=None, today_ops=None):
    """模拟一个小号。返回 (copies, daily_events, author_income, end_balance, login_used, queue)
    start_day: 方案起始的真实日历日（今天几号），所有 day 均为日历日。
    rest_days: 周日集合，这些天零收入（不点赞、不登录、不复制），但秒点照常过期。
    cal: {"year","month","days"} 日历信息，供过期标注跨月显示真实日期；缺省按「N日」标注。
    work_state: 大号作品状态（跨小号共享、原地累计复制次数）；缺省按作品数生成。
    today_ops: 今日已手动做过的操作 {"like": bool, "copy": int}；首日跳过相应安排，
               避免与用户已经点过的快捷操作重复加/扣秒点（余额本身已含手动变动）。
    """
    rest_days = rest_days or set()
    today_ops = today_ops or {}
    like_gain = p["like_gain"] * p["like_per_day"]
    login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
    login_granted = 0  # 方案窗口内实际领到的登录奖励次数
    # 负余额=欠费：如实记为债务，后续收益须先偿还，不能用「从0开始」抹掉
    debt = max(0.0, -float(ac.get("balance", 0)))
    queue = _initial_queue(ac, p, start_day)  # 勾选今晚过期则余额均分7份、每天过期一份
    copies = []       # (day, work_index_1based)
    events = []       # 每日事件 list of (day, action, amount, balance)
    ptr = 0
    work_count = max(1, p["work_count"])
    work_state = work_state if work_state is not None else _init_work_state(p)
    limit = int(p.get("copy_limit", DEFAULTS["copy_limit"]))
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
            is_first = (d == start_day)
            # 今日已手动点赞 → 首日不再重复安排点赞收益（登录奖仍按规则发）
            gain = 0 if (is_first and today_ops.get("like")) else like_gain
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
            if is_first and today_ops.get("like") and not login_flag:
                label = "今天已点赞(跳过)"
            else:
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
            # 说明：手动复制时余额已实时扣减，方案从扣后余额出发，不会重复扣点；
            # 今日已复制次数只在方案文本里「体现」，不在这里再减一次（否则重复计算）。
            while price > 0 and _total(queue) >= price:
                # 只复制「在售且未达40次」的作品；满40次的下线，避免大号白干
                avail = [i for i, w in enumerate(work_state)
                         if w["active"] and w["copies"] < limit]
                if not avail:
                    break  # 全部作品已满，停止复制（提示需发布新作品）
                wi = avail[ptr % len(avail)]
                queue = _consume(queue, price)
                copies.append((d, wi + 1))
                wname = work_state[wi]["name"]
                work_state[wi]["copies"] += 1
                work_state[wi].setdefault("_log", []).append(int(d))  # 供方案每日推进作品次数
                if work_state[wi]["copies"] >= limit:
                    work_state[wi]["active"] = False
                events.append((d, "复制→%s" % wname, -price, _bal()))
                ptr += 1
                made += 1
                if made > 1000:  # 保险
                    break

    author_income = len(copies) * price * (1 - p["copy_fee_pct"] / 100.0)
    # 剩余批次 [(获得日, 剩余点)]：获得日+有效期=过期日，供到期预警
    return copies, events, author_income, _total(queue) - debt, login_granted, queue


def simulate_big(ac, p, income_by_day, start_day=1, rest_days=None, pack_on_start=False,
                 cal=None, today_ops=None):
    """模拟大号。income_by_day: dict day(日历日)->小号转账金额。
    rest_days: 周日集合，这些天零收入，但后端照常扣费。
    pack_on_start: 用户勾选「起始日打包」→ 仅在起始日打包一次并扣点；平时不自动打包。
    today_ops: 今日已手动操作 {"like": bool}；首日跳过已做过的点赞，避免重复安排。
    返回 (packs, events, end_balance, login_used, packable_days)
      packs: 实际打包的日期列表；packable_days: 余额达到打包线、可随时手动打包的日期列表。
    """
    rest_days = rest_days or set()
    today_ops = today_ops or {}
    like_gain = p["like_gain"] * p["like_per_day"]
    login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
    login_granted = 0  # 方案窗口内实际领到的登录奖励次数
    # 负余额=欠费：后端照扣造成的欠费如实保留，收款先还债
    debt = max(0.0, -float(ac.get("balance", 0)))
    queue = _initial_queue(ac, p, start_day)  # 勾选今晚过期则余额均分7份、每天过期一份
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
            # 今日已手动点赞 → 首日不重复安排点赞收益（登录奖仍按规则发）
            day_gain = 0 if (is_first and today_ops.get("like")) else like_gain
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


def build_plan(accounts, p, ctx=None, pack_on_start=False, works=None,
               today_ops=None, manual_batches=None):
    """生成 (完整文本, csv行列表, 分板块列表, 每日动作表, 每日余额曲线, 每日登录曲线, 到期信息, 作品曲线)。
    每日动作表: {日: {账号备注: [动作, ...]}}，供「确认采用」后主页面每日展示。
    余额/登录曲线: {日: {账号备注: 当天结束值}}，供采用后按日期自动推进当前秒点。
    到期信息: {账号备注: [(获得日, 过期日, 剩余点), ...]}，供到期预警与今日面板提醒。
    works: 大号作品列表 [{name, copies, active}]；缺省按作品数生成默认作品。
    today_ops: {账号备注: {"like": bool, "copy": int}} 今日已手动操作，首日跳过避免重复。
    manual_batches: 手动操作产生的大号收款批次，并入方案到期提醒与说明。
    """
    ctx = ctx or month_context()
    today_ops = today_ops or {}
    manual_batches = manual_batches or []
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

    # 先跑小号：作品状态跨所有小号共享、原地累计复制次数（满40次自动下线）
    cal = {"year": ctx["year"], "month": ctx["month"], "days": ctx["days_in_month"]}
    work_state = _init_work_state(p, works)
    copy_limit = int(p.get("copy_limit", DEFAULTS["copy_limit"]))
    small_results = []
    income_by_day = {}
    for ac in smalls:
        copies, ev, author_income, endbal, login_used, q = simulate_small(
            ac, p, start_day, rest_days, cal=cal, work_state=work_state,
            today_ops=today_ops.get(ac["note"], {}))
        small_results.append((ac, copies, ev, author_income, endbal, login_used, q))
        per = p["work_price"] * (1 - p["copy_fee_pct"] / 100.0)
        for d, wi in copies:
            income_by_day[d] = income_by_day.get(d, 0.0) + per

    packs, bev, bend, big_login_used, packable_days, bqueue = simulate_big(
        big, p, income_by_day, start_day, rest_days, pack_on_start, cal=cal,
        today_ops=today_ops.get(big["note"], {}))

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

    # 已记录的用户手动操作（永久保留，方案首日已跳过重复安排）
    if today_ops:
        done_bits = []
        for note, op in today_ops.items():
            bits = []
            if op.get("like"):
                bits.append("已点赞")
            if op.get("copy"):
                bits.append("已复制%d次" % int(op["copy"]))
            if op.get("publish"):
                bits.append("已发布应用")
            if bits:
                done_bits.append("%s：%s" % (note, "、".join(bits)))
        if done_bits:
            sec_overview.append("★ 你今天已记录的操作（方案首日不重复安排）：" + "；".join(done_bits))

    # 大号快捷收款批次到期：并入方案，方便一眼核对（如3个小号同日复制共240点7天后到期）
    if manual_batches:
        # 只显示尚未到期的批次（可能落在下月，不能按方案月末过滤掉）
        today_date = datetime.date(ctx.get("today_year", ctx["year"]),
                                   ctx.get("today_month", ctx["month"]),
                                   ctx.get("today", start_day))
        mb_by_exp = {}
        for b in manual_batches:
            try:
                exp = datetime.date.fromisoformat(b["expire"])
                if exp >= today_date:
                    mb_by_exp.setdefault(exp, []).append((b.get("name", "作品"), float(b["amount"])))
            except Exception:
                continue
        if mb_by_exp:
            sec_overview.append("★ 大号手动收款到期（已计入当前余额，到期没用会流失）：")
            for exp in sorted(mb_by_exp)[:4]:
                items = mb_by_exp[exp]
                total = round(sum(a for _, a in items), 2)
                sec_overview.append("  ⚠ %s 00:00：共 %g 点（%s）" %
                                    (exp.strftime("%Y-%m-%d"), total,
                                     "、".join("%s%g" % (n, a) for n, a in items[:3])))

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

    # 作品名安全取用（索引 1-based）
    def _wname(wi):
        idx = int(wi) - 1
        return work_state[idx]["name"] if 0 <= idx < len(work_state) else "作品%d" % wi

    # 板块4：小号复制排期
    for ac, copies, ev, ai, eb, lu, _q in small_results:
        sec_copy.append("【%s】当前 %g 点｜本月已领登录 %d 天（方案内再领 %d 天）" %
                        (ac["note"], ac["balance"], int(ac.get("login_claimed", 0)), lu))
        sec_copy.append("  复制 %d 次 → 为大号贡献 %g 点；月末余额约 %g 点" %
                        (len(copies), ai, eb))
        if copies:
            txt = "  "
            for i, (d, wi) in enumerate(copies):
                txt += "%d日→%s  " % (d, _wname(wi))
                if (i + 1) % 5 == 0:
                    sec_copy.append(txt)
                    txt = "  "
            if txt.strip():
                sec_copy.append(txt)
            sec_copy.append("  （在售作品轮换，同一账号不会连续复制同一个作品）")
        else:
            sec_copy.append("  当前余额不足定价 %d，需先攒点。" % p["work_price"])
        sec_copy.append("")

    # 作品复制进度：起始次数 + 方案内新增 = 月末累计；满40次的下线需换新
    work_progress = []
    for w in work_state:
        start_cp = int(w.get("start_copies", w["copies"]))
        end_cp = int(w["copies"])
        work_progress.append({"name": w["name"], "start": start_cp,
                              "added": end_cp - start_cp, "end": end_cp,
                              "offline": (not w["active"]) or end_cp >= copy_limit})
    offline_names = [w["name"] for w in work_progress if w["offline"]]
    sec_work = []
    sec_work.append("【大号作品复制进度】（单作品累计 %d 次后须下线换新，超过后大号收不到秒点）" % copy_limit)
    for w in work_progress:
        status = "⚠已满%d次·需下线换新" % copy_limit if w["offline"] else "在售"
        sec_work.append("  · %s：起始 %d 次 + 方案内 %d 次 = 月末 %d 次｜%s" %
                        (w["name"], w["start"], w["added"], w["end"], status))
    if offline_names:
        sec_work.append("  ★ %s 将满 %d 次，请在此之前发布/上架新作品接替（发布扣 %d 点）。" %
                        ("、".join(offline_names), copy_limit,
                         int(p.get("publish_cost", DEFAULTS["publish_cost"]))))
    if work_progress and all(w["offline"] for w in work_progress):
        sec_work.append("  ★ 全部作品已满：方案已停止复制安排，请先发布新作品再重新生成方案。")
    sec_work.append("")

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
    L += sec_work
    L += sec_copy
    L.append("-" * 58); L += sec_big
    full_text = "\n".join(L)

    sections = [
        ("今日行动", "\n".join(sec_overview) + "\n\n" + "\n".join(sec_today)),
        ("逐日行动", "\n".join(sec_daily)),
        ("作品进度", "\n".join(sec_work)),
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

    # 作品复制曲线：{日历日: {作品名: 当日结束的累计复制次数}}，供采用方案后自动累计作品次数
    work_copy_curve = {}
    for w in work_state:
        start_cp = int(w.get("start_copies", w["copies"]))
        running = start_cp
        by_day = {}
        for d in w.get("_log", []):
            by_day[d] = by_day.get(d, 0) + 1
        for d in range(start_day, start_day + int(p["plan_days"])):
            running += by_day.get(d, 0)
            work_copy_curve.setdefault(d, {})[w["name"]] = running

    return (full_text, rows, sections, actions_by_day, balance_curve,
            login_curve, expiry_info, work_copy_curve)


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


def _eval_price(accounts, p, ctx, price, works=None):
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
        # 每个小号用独立作品副本评估（互不影响），并计入40次上限造成的复制损失
        ws = _init_work_state(pp, works)
        cp, ev, ai, _eb, _lu, _q = simulate_small(ac, pp, start_day, rest_days, cal=cal, work_state=ws)
        copies += len(cp)
        income += ai
        for _d, act, amt, _bal in ev:
            if act == "秒点过期" and amt < 0:
                waste += -amt
    return {"waste": round(waste, 2), "copies": int(copies), "income": round(income, 2)}


def recommend_price(accounts, p, ctx=None, works=None):
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
        r = _eval_price(accounts, pp, ctx, cand, works=works)
        r["price"] = cand
        results.append(r)

    best = min(results, key=lambda r: (r["waste"], r["copies"]))
    current_price = int(p["work_price"])
    current = _eval_price(accounts, pp, ctx, current_price, works=works)
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


def _center_window(win, parent=None, width=None, height=None):
    """把窗口居中显示。

    parent=None → 相对屏幕正中（用于主窗口）；
    parent 给定 → 相对父窗口正中（用于软件内弹出的子窗口）。
    width/height 给定则以该尺寸计算位置，否则用窗口当前实际尺寸。
    """
    try:
        win.update_idletasks()
        w = width or win.winfo_width()
        h = height or win.winfo_height()
        if w < 10:
            w = 800
        if h < 10:
            h = 600
        if parent is not None:
            parent.update_idletasks()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
        else:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
        x = max(0, x)
        y = max(0, y)
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
    except Exception:
        pass


class AccountRow:
    def __init__(self, parent, role, idx, note="", balance=0, login_claimed=0,
                 on_delete=None, expire_today=False):
        self.role = role
        self.frame = tk.Frame(parent, bg=C_SURFACE, highlightthickness=1,
                              highlightbackground=C_BORDER)
        self.role_var = tk.StringVar(value="大号" if role == "big" else "小号%d" % idx)
        self.note_var = tk.StringVar(value=note)
        self.bal_var = tk.StringVar(value=str(balance))
        self.login_var = tk.StringVar(value=str(login_claimed))
        self.expire_today_var = tk.BooleanVar(value=bool(expire_today))

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
        # 勾选：今天是否有今晚12点就过期的秒点（用于把当前余额均分7份、推算每天到期）
        tk.Checkbutton(self.frame, text="今天有今晚12点过期的秒点（当前秒点均分7天、每天过期一份）",
                       variable=self.expire_today_var, bg=C_SURFACE, fg=C_MUTED,
                       activebackground=C_SURFACE, selectcolor="#FFFFFF",
                       font=(FONT, 9), anchor="w", justify="left").grid(
            row=1, column=0, columnspan=8, padx=(10, 8), pady=(0, 4), sticky="w")

    def to_dict(self):
        bal = float(self.bal_var.get() or 0)
        lc = int(self.login_var.get() or 0)
        return {"role": self.role, "note": self.note_var.get().strip() or self.role_var.get(),
                "balance": bal, "login_claimed": lc,
                "expire_today": bool(self.expire_today_var.get())}


class WorkRow:
    """大号作品一行：名称 + 累计复制次数（可手改，系统也会自动累计）。"""
    def __init__(self, parent, name="作品", copies=0, idx=1, on_delete=None):
        self.frame = tk.Frame(parent, bg=C_SURFACE, highlightthickness=1,
                              highlightbackground=C_BORDER)
        self.name_var = tk.StringVar(value=name)
        self.copies_var = tk.StringVar(value=str(copies))
        self.idx = idx
        tk.Label(self.frame, text="作品%d" % idx, bg="#EEF2FF", fg="#4338CA",
                 font=(FONT, 10, "bold"), pady=2).grid(row=0, column=0, padx=(10, 8), pady=2)
        tk.Label(self.frame, text="名称", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9)).grid(row=0, column=1, padx=(0, 4))
        ttk.Entry(self.frame, textvariable=self.name_var, width=16).grid(
            row=0, column=2, padx=(0, 14), sticky="w")
        tk.Label(self.frame, text="累计复制(次)", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9)).grid(row=0, column=3, padx=(0, 4))
        ttk.Spinbox(self.frame, from_=0, to=9999, textvariable=self.copies_var,
                    width=6).grid(row=0, column=4, sticky="w")
        self.status_var = tk.StringVar(value="")
        tk.Label(self.frame, textvariable=self.status_var, bg=C_SURFACE, fg=C_WARN,
                 font=(FONT, 9)).grid(row=0, column=5, padx=(12, 4), sticky="w")
        self.del_btn = ttk.Button(self.frame, text="删除", width=6, style="Ghost.TButton",
                                  command=on_delete)
        self.del_btn.grid(row=0, column=6, padx=(12, 10))

    def to_dict(self):
        try:
            cp = int(self.copies_var.get() or 0)
        except ValueError:
            cp = 0
        return {"name": self.name_var.get().strip() or "作品",
                "copies": cp, "active": cp < int(DEFAULTS["copy_limit"])}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s v%s" % (APP_NAME, VERSION))
        self.geometry("1000x920")
        self.minsize(940, 860)
        self.rows = []
        self.small_counter = 0
        self.work_rows = []        # 大号作品行 [{name_var, copies_var, frame}]
        self.last_rows = None
        self.pending_plan = None   # 最近生成、尚未确认采用的方案快照
        self.adopted_plan = None   # 已确认采用的方案（持久化，主页面每日展示）
        self.change_log = []       # 实际加减记录（每次推进/回退逐条记，供核对）
        self.manual_batches = []   # 快捷操作产生的大号收款批次 [{date, amount, name, note}]
        self.quick_note_var = tk.StringVar(value="")  # 快捷操作当前选中的账号备注
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
        _center_window(self, parent=None, width=1000, height=920)  # 首次打开居中屏幕
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
        # 顶部只保留一个入口：所有更新信息与操作都在独立窗口里
        self.update_entry = self._header_button(
            right, "v%s · 更新" % APP_VERSION, self._open_update_window)
        self.update_entry.pack(side="left", padx=(0, 6))
        self.update_win = None
        # 更新窗口内容先记在缓存里：启动检查可能在窗口创建前就拿到结果，
        # 等窗口一建好立即回填，避免自动弹出时显示「尚未检查」。
        self.update_view = {"status": "尚未检查", "color": C_MUTED, "latest": "尚未检查",
                            "source": "—", "notes": "", "check": True,
                            "now": False, "skip": False, "later": False}

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

        # ===== 快捷操作：选账号 → 一键记一笔实际发生的操作（带7天过期）=====
        qbox = self._card(content, "快捷操作", "实际做了什么就点一下，秒点与到期自动记")
        qbox.pack(fill="x", pady=(0, 8))
        qrow = tk.Frame(qbox, bg=C_SURFACE)
        qrow.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Label(qrow, text="选择账号", style="Muted.TLabel").pack(side="left")
        self.quick_combo = ttk.Combobox(qrow, textvariable=self.quick_note_var,
                                        state="readonly", width=14)
        self.quick_combo.pack(side="left", padx=(4, 14))
        self.quick_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_work_combo())
        self.publish_btn = ttk.Button(qrow, text="发布应用（-20，仅大号）",
                                      command=self.on_publish_app)
        self.publish_btn.pack(side="left")
        self.like_btn = ttk.Button(qrow, text="已点赞20次（+点）",
                                   command=self.on_quick_like)
        self.like_btn.pack(side="left", padx=8)
        self.copy_btn = ttk.Button(qrow, text="已复制大号作品",
                                   command=self.on_quick_copy)
        self.copy_btn.pack(side="left")
        self.undo_btn = ttk.Button(qrow, text="↩ 撤回今天的记录",
                                   command=self.on_undo_quick)
        self.undo_btn.pack(side="left", padx=8)
        qhint = tk.Frame(qbox, bg=C_SURFACE)
        qhint.pack(fill="x", padx=16, pady=(0, 8))
        self.quick_hint_var = tk.StringVar(value="")
        ttk.Label(qhint, textvariable=self.quick_hint_var,
                  style="Muted.TLabel", wraplength=920, justify="left").pack(anchor="w")

        # ===== 大号作品：名称 + 累计复制次数（满40需下线换新）=====
        wbox = self._card(content, "大号作品", "满40次须下线换新；系统按方案自动累计")
        wbox.pack(fill="x", pady=(0, 8))
        wholder = tk.Frame(wbox, bg=C_SURFACE)
        wholder.pack(fill="x", padx=16, pady=(0, 8))
        self.work_list = tk.Frame(wholder, bg=C_SURFACE)
        self.work_list.pack(fill="x")
        self.work_tip_var = tk.StringVar(value="")
        ttk.Label(wholder, textvariable=self.work_tip_var,
                  style="Warn.TLabel", wraplength=920, justify="left").pack(anchor="w", pady=(6, 0))
        wbar = tk.Frame(wholder, bg=C_SURFACE)
        wbar.pack(fill="x", pady=(8, 0))
        ttk.Button(wbar, text="＋ 添加作品", command=self.add_work).pack(side="left")
        self.work_auto_var = tk.StringVar(value="")
        ttk.Label(wbar, textvariable=self.work_auto_var,
                  style="Ok.TLabel", wraplength=680, justify="left").pack(side="left", padx=12)

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
                    edate = _real_date(y, m, e)  # 支持 e<=0 往前跨月（勾选今晚过期的批次）
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

        # 快捷操作产生的大号收款批次：按到期日展示（如3个小号同日复制→240点7天后到期）
        mb_due = {}
        for b in getattr(self, "manual_batches", []):
            if b.get("revoked"):
                continue
            try:
                exp = datetime.date.fromisoformat(b["expire"])
                left = (exp - today).days
                if 0 <= left <= 7:
                    mb_due.setdefault(exp, []).append((b.get("name", "作品"), float(b["amount"]),
                                                        b.get("from", "")))
            except Exception:
                continue
        if mb_due:
            chunks = []
            for exp in sorted(mb_due)[:2]:
                items = mb_due[exp]
                total = round(sum(a for _, a, _ in items), 2)
                names = "、".join("%s%g点" % (n, a) for n, a, _ in items[:3])
                if len(items) > 3:
                    names += "等%d笔" % len(items)
                chunks.append("%s 共%g点（%s）" % (exp.strftime("%Y-%m-%d 00:00"), total, names))
            parts.append("⚠ 大号快捷收款到期：" + "；".join(chunks) + "（没用掉会流失）")

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
                        edate = _real_date(y_, m_, e)  # 支持 e<=0 往前跨月
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
            start_exp = plan.get("expire_today", {})
            restored = []
            for a in accounts:
                if a["note"] in start_bal:
                    b = dict(a)
                    b["balance"] = start_bal[a["note"]]
                    b["login_claimed"] = int(start_login.get(a["note"], 0))
                    b["expire_today"] = bool(start_exp.get(a["note"], False))
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
            text, rows, _secs, _acts, _bal, _login, _exp, _wcc = build_plan(
                restored, p, ctx, pack_on_start=bool(plan.get("packed_on_start", False)),
                works=plan.get("works") or getattr(self, "works", None),
                today_ops=plan.get("today_ops") or {},
                manual_batches=plan.get("manual_batches") or [])
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
            _text, _rows, _secs, _acts, bal_curve, login_curve, _exp, _wcc = build_plan(
                accounts, p, ctx, pack_on_start=bool(plan.get("packed_on_start", False)),
                works=plan.get("works") or getattr(self, "works", None),
                today_ops=plan.get("today_ops") or {},
                manual_batches=plan.get("manual_batches") or [])
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
            # 作品累计复制次数按方案曲线推进（用增量，保留用户手改/快捷操作累加的值）
            wc_curve = plan.get("work_copy_curve") or {}
            plan_works = plan.get("works") or []
            if wc_curve and plan_works:
                plan_names = {str(w.get("name", "")) for w in plan_works}
                def _wbase(name):
                    for w in plan_works:
                        if str(w.get("name", "")) == name:
                            try:
                                return int(w.get("copies", 0))
                            except Exception:
                                return 0
                    return 0
                def wval(d, flag, name):
                    if d < ps or not _eod(d, flag):
                        return _wbase(name)
                    v = (wc_curve.get(str(d)) or {}).get(name)
                    return _wbase(name) if v is None else int(round(float(v)))
                for wrow in getattr(self, "work_rows", []):
                    wname = wrow.name_var.get().strip()
                    if not wname or wname not in plan_names:
                        continue
                    wdelta = wval(target, tgt_eod, wname) - wval(last, prev_eod, wname)
                    if wdelta:
                        try:
                            wcur = int(wrow.copies_var.get() or 0)
                        except ValueError:
                            wcur = 0
                        wrow.copies_var.set(str(max(0, wcur + wdelta)))
                        changed = True
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

    def _ensure_log_seq(self):
        """给每一笔记录分配稳定编号（含旧配置），撤回按编号关联，不删原记录。"""
        log = getattr(self, "change_log", None)
        if not isinstance(log, list):
            log = []
        top = 0
        for e in log:
            try:
                top = max(top, int(e.get("seq", 0) or 0))
            except Exception:
                pass
        for e in log:
            if not e.get("seq"):
                top += 1
                e["seq"] = top
        self.change_log = log

    def _append_change_log(self, entries):
        """追加实际加减记录；用户操作永久保留，撤回也另记一笔而不覆盖原记录。"""
        log = getattr(self, "change_log", None)
        if not isinstance(log, list):
            log = []
        self.change_log = log
        self._ensure_log_seq()
        top = max([int(e.get("seq", 0) or 0) for e in self.change_log] or [0])
        for e in entries:
            d = dict(e)
            top += 1
            d["seq"] = top
            self.change_log.append(d)

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
        for var in (row.note_var, row.bal_var, row.login_var,
                    getattr(row, "expire_today_var", None)):
            if var is not None:
                var.trace_add("write", self._schedule_save)

    def _on_switch_changed(self):
        self._refresh_date_bar()
        self._advance_balances()   # 勾选今天已操作 → 推进到今天的余额
        self._auto_save()
        try:
            self.save_status_var.set("✓ 已自动保存")
        except Exception:
            pass

    # ---- 大号作品管理 ----
    def _collect_works(self):
        return [w.to_dict() for w in getattr(self, "work_rows", [])]

    def add_work(self, name=None, copies=0):
        idx = len(self.work_rows) + 1
        name = name or ("作品%d" % idx)
        w = WorkRow(self.work_list, name=name, copies=copies, idx=idx,
                    on_delete=lambda rr=None: None)
        w.frame.pack(fill="x", pady=3)
        for _w in self.work_rows:
            _w.del_btn.configure(command=lambda rr=_w: self.delete_work(rr))
        w.del_btn.configure(command=lambda rr=w: self.delete_work(rr))
        for var in (w.name_var, w.copies_var):
            var.trace_add("write", self._on_work_changed)
        self.work_rows.append(w)
        self._refresh_work_status()
        self._schedule_save()
        return w

    def delete_work(self, w):
        if w in self.work_rows:
            self.work_rows.remove(w)
            w.frame.destroy()
            for i, rw in enumerate(self.work_rows):
                rw.idx = i + 1
                lbl = rw.frame.grid_slaves(row=0, column=0)
                if lbl:
                    lbl[0].configure(text="作品%d" % (i + 1))
                rw.del_btn.configure(command=lambda rr=rw: self.delete_work(rr))
        self._refresh_work_status()
        self._schedule_save()

    def _on_work_changed(self, *args):
        if getattr(self, "_binding_saves", False):
            return
        self._refresh_work_status()
        self._schedule_save()

    def _refresh_work_status(self):
        """作品行状态与顶部提示：满40次标红并提示换新。"""
        limit = int(DEFAULTS["copy_limit"])
        full = []
        try:
            for w in self.work_rows:
                try:
                    cp = int(w.copies_var.get() or 0)
                except ValueError:
                    cp = 0
                if cp >= limit:
                    w.status_var.set("⚠已满%d次·需换新" % limit)
                    full.append(w.name_var.get().strip() or "作品")
                else:
                    w.status_var.set("剩余 %d 次" % (limit - cp))
        except Exception:
            pass
        if hasattr(self, "work_tip_var"):
            if full:
                self.work_tip_var.set("⚠ %s 累计已达 %d 次，继续复制大号收不到秒点；请点「发布应用」上新作品接替。"
                                      % ("、".join(full), limit))
            else:
                self.work_tip_var.set("规则：单作品累计复制满 %d 次须下线换新（超过后大号收不到秒点）；"
                                      "生成方案时会自动跳过已满的作品。" % limit)

    def _refresh_account_combo(self):
        """刷新快捷操作的账号下拉（备注 + 角色）。"""
        if not hasattr(self, "quick_combo"):
            return
        vals = []
        for r in getattr(self, "rows", []):
            note = r.note_var.get().strip() or r.role_var.get()
            vals.append(note)
        old = self.quick_note_var.get()
        self.quick_combo["values"] = vals
        if vals:
            # 优先保留原选择，否则默认选第一个小号，没有小号则选大号
            if old in vals:
                self.quick_note_var.set(old)
            else:
                small = next((r.note_var.get().strip() or r.role_var.get()
                              for r in self.rows if r.role == "small"), None)
                self.quick_note_var.set(small or vals[0])
        else:
            self.quick_note_var.set("")
        self._refresh_quick_hint()

    def _find_row_by_note(self, note):
        for r in getattr(self, "rows", []):
            if (r.note_var.get().strip() or r.role_var.get()) == note:
                return r
        return None

    def _refresh_quick_hint(self, msg=None):
        if not hasattr(self, "quick_hint_var"):
            return
        note = self.quick_note_var.get()
        r = self._find_row_by_note(note) if note else None
        is_big = bool(r and r.role == "big")
        liked = bool(note and self._has_today_op(note, "like"))
        copied = bool(note and self._has_today_op(note, "copy"))
        can_undo = bool(note and self._active_manual_entries(note=note))
        # 发布仅大号；点赞/复制每个账号一天各一次；大号不能复制；有记录才允许撤回
        for btn, enabled in ((getattr(self, "publish_btn", None), is_big),
                             (getattr(self, "like_btn", None), bool(note) and not liked),
                             (getattr(self, "copy_btn", None), bool(note) and not is_big and not copied),
                             (getattr(self, "undo_btn", None), can_undo)):
            if btn is not None:
                btn.configure(state="normal" if enabled else "disabled")
        if msg:
            self.quick_hint_var.set(msg)
            return
        if not note or not r:
            self.quick_hint_var.set("先在上方下拉选择账号；实际在平台做了操作，就点对应按钮记一笔，"
                                    "秒点增减与7天到期自动记录。")
        elif r.role == "big":
            head = "当前：%s（大号）。可点「发布应用」扣 %d 点上新作品，也可记点赞；" \
                   "「已复制大号作品」仅小号操作，大号不能点；" % (note, int(DEFAULTS["publish_cost"]))
            if liked:
                head += "今天已记过点赞（如需更正请点撤回）；"
            head += "大号收到的小号复制款会记在【大号收款批次】并按7天到期。"
            self.quick_hint_var.set(head)
        else:
            head = "当前：%s（小号）。点赞按每次 %d 点记 %d 次；复制按每个作品 %d 点扣，" \
                   "大号实收 %g 点并按7天到期。" % (
                       note, int(DEFAULTS["like_gain"]), int(DEFAULTS["like_per_day"]),
                       int(self.param_vars["work_price"].get() or 0),
                       int(self.param_vars["work_price"].get() or 0)
                       * (1 - float(self.param_vars["copy_fee_pct"].get() or 0) / 100.0))
            if liked or copied:
                head += " 今天已记过%s（每个号一天只能记一次，如需更正请点撤回）。" % (
                    "、".join(x for x, y in (("点赞", liked), ("复制", copied)) if y))
            self.quick_hint_var.set(head)

    def _read_param(self, key, default):
        try:
            v = float(self.param_vars[key].get())
            return int(v) if v == int(v) else v
        except Exception:
            return default

    @staticmethod
    def _op_kind(e):
        """把一条快捷操作记录归类：like / publish / copy / income。"""
        reason = str(e.get("reason", ""))
        try:
            delta = float(e.get("delta", 0))
        except Exception:
            delta = 0.0
        if reason.startswith("已点赞"):
            return "like"
        if reason.startswith("发布应用"):
            return "publish"
        if reason.startswith("收款:"):
            return "income"
        if reason.startswith("复制") and delta < 0:
            return "copy"
        return ""

    def _undone_op_ids(self):
        """已被撤回的操作标记（操作组编号 / 旧记录的流水号）；原记录保留不动。"""
        return {str(e.get("undo_of")) for e in (getattr(self, "change_log", []) or [])
                if e.get("undo_of")}

    def _active_manual_entries(self, kind=None, note=None, include_income=False):
        """取今天仍然有效的快捷操作记录（已撤回的自动排除）。"""
        today = datetime.date.today().isoformat()
        undone = self._undone_op_ids()
        out = []
        for e in getattr(self, "change_log", []) or []:
            try:
                if e.get("source") != "manual" or not str(e.get("ts", "")).startswith(today):
                    continue
                if str(e.get("op_id") or "") in undone or ("seq%s" % e.get("seq")) in undone:
                    continue
                k = self._op_kind(e)
                if not k or (k == "income" and not include_income):
                    continue
                if kind and k != kind:
                    continue
                if note and e.get("note") != note:
                    continue
                out.append(e)
            except Exception:
                continue
        return out

    def _has_today_op(self, note, kind):
        return bool(self._active_manual_entries(kind=kind, note=note))

    def _today_manual_ops(self):
        """从操作记录提取「今天已手动做过什么」，供生成方案首日跳过、避免重复安排。
        返回 {账号备注: {"like": bool, "copy": int, "publish": bool}}；撤回的不算。
        记录永久保留在 change_log，本方法只读不改。"""
        out = {}
        for e in self._active_manual_entries():
            note = e.get("note", "")
            slot = out.setdefault(note, {"like": False, "copy": 0, "publish": False})
            k = self._op_kind(e)
            if k == "like":
                slot["like"] = True
            elif k == "publish":
                slot["publish"] = True
            elif k == "copy":
                slot["copy"] += 1
        return out

    def _log_manual(self, note, delta, reason, big_gain=0.0, work_name="",
                    from_note="", op_id=None, created_work=""):
        """记一笔快捷操作：改账号余额 + change_log + （大号收款）手动批次。
        返回操作组编号 op_id：同一笔复制的小号扣款与大号收款共用一个编号，撤回时一起回退。"""
        r = self._find_row_by_note(note)
        if r is None:
            return None
        op_id = op_id or ("op%012x" % time.time_ns())
        try:
            cur = float(r.bal_var.get() or 0)
        except ValueError:
            cur = 0.0
        newv = round(cur + delta, 2)
        self._binding_saves = True
        try:
            r.bal_var.set("%g" % newv)
        finally:
            self._binding_saves = False
        today = datetime.date.today()
        self._append_change_log([{
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "day": today.day, "note": note, "delta": round(delta, 2),
            "before": round(cur, 2), "after": newv, "login_delta": 0,
            "reason": reason, "direction": "快捷操作", "source": "manual",
            "op_id": op_id,
        }])
        if created_work:
            self.change_log[-1]["created_work"] = created_work
        if work_name:
            self.change_log[-1]["work_name"] = work_name
        if big_gain > 0:
            expire_days = int(self._read_param("expiry_days", DEFAULTS["expiry_days"]))
            self.manual_batches.append({
                "date": today.isoformat(),
                "amount": round(big_gain, 2),
                "name": work_name, "from": from_note or note,
                "expire": (today + datetime.timedelta(days=expire_days)).isoformat(),
                "op_id": op_id, "revoked": False,
            })
        return op_id

    def on_publish_app(self):
        """发布/上架新作品：仅大号可点，扣 publish_cost 点并新增一个空作品。"""
        note = self.quick_note_var.get()
        r = self._find_row_by_note(note) if note else None
        if r is None:
            messagebox.showwarning("提示", "请先在下拉框选择账号。", parent=self)
            return
        if r.role != "big":
            messagebox.showwarning("仅大号可用", "「发布应用」只有大号可以操作，请切换到大号。", parent=self)
            return
        cost = int(DEFAULTS["publish_cost"])
        try:
            cur = float(r.bal_var.get() or 0)
        except ValueError:
            cur = 0.0
        if cur < cost:
            if not messagebox.askyesno("余额不足",
                                       "大号当前 %g 点，发布需 %d 点，发布后会变成负数（欠费）。仍要记录吗？"
                                       % (cur, cost), parent=self):
                return
        name = "新作品%d" % (len(self.work_rows) + 1)
        self._log_manual(note, -cost, "发布应用-%d" % cost, created_work=name)
        self.add_work(name=name, copies=0)
        self._auto_save()
        self._refresh_quick_hint("已在平台发布「%s」并记录：%s -%d 点；新作品累计复制从 0 开始。"
                                 % (name, note, cost))

    def on_quick_like(self):
        """已点赞20次：选中账号按 like_gain×like_per_day 加秒点，记7天到期。每号每天一次。"""
        note = self.quick_note_var.get()
        r = self._find_row_by_note(note) if note else None
        if r is None:
            messagebox.showwarning("提示", "请先在下拉框选择账号。", parent=self)
            return
        if self._has_today_op(note, "like"):
            self._refresh_quick_hint("⚠ %s 今天已记过点赞，一个号一天只能记一次；"
                                     "如记错了请点「撤回今天的记录」后再重新记录。" % note)
            return
        per = self._read_param("like_gain", DEFAULTS["like_gain"])
        cnt = int(self._read_param("like_per_day", DEFAULTS["like_per_day"]))
        gain = per * cnt
        if gain <= 0:
            messagebox.showwarning("参数异常", "每次点赞与每日点赞次数需为正数。", parent=self)
            return
        self._log_manual(note, gain, "已点赞%d次+%g" % (cnt, gain))
        self._auto_save()
        expire_days = int(self._read_param("expiry_days", DEFAULTS["expiry_days"]))
        self._refresh_quick_hint("已记录：%s 点赞 %d 次 +%g 点，这批秒点将于 %s 到期（%d天）。"
                                 % (note, cnt, gain,
                                    (datetime.date.today() + datetime.timedelta(days=expire_days)).isoformat(),
                                    expire_days))

    def on_quick_copy(self):
        """已复制大号作品：仅小号可点，每号每天一次；小号扣定价，大号加实收并记7天到期。"""
        note = self.quick_note_var.get()
        r = self._find_row_by_note(note) if note else None
        if r is None:
            messagebox.showwarning("提示", "请先在下拉框选择账号。", parent=self)
            return
        if r.role == "big":
            self._refresh_quick_hint("⚠「已复制大号作品」只有小号能操作；请切换到对应的小号再记录。")
            return
        if self._has_today_op(note, "copy"):
            self._refresh_quick_hint("⚠ %s 今天已记过复制，一个号一天只能记一次；"
                                     "如记错了请点「撤回今天的记录」后再重新记录。" % note)
            return
        works = self._collect_works()
        if not works:
            messagebox.showwarning("没有作品", "请先在「大号作品」里添加作品。", parent=self)
            return
        limit = int(DEFAULTS["copy_limit"])
        avail = [w for w in works if w["copies"] < limit]
        if not avail:
            messagebox.showwarning("作品已满",
                                   "全部作品累计复制已达 %d 次，继续复制大号收不到秒点；请先点「发布应用」上新作品。"
                                   % limit, parent=self)
            return
        # 弹出选择作品
        choice = self._ask_copy_work(avail, limit)
        if not choice:
            return
        work = choice
        price = self._read_param("work_price", DEFAULTS["work_price"])
        fee = self._read_param("copy_fee_pct", DEFAULTS["copy_fee_pct"])
        gain = round(price * (1 - fee / 100.0), 2)
        # 小号复制：小号扣定价；大号加实收并记收款批次（同一 op_id 便于整笔撤回）
        op_id = self._log_manual(note, -price, "复制%s-%g" % (work["name"], price),
                                 work_name=work["name"])
        big_note = None
        for rr in self.rows:
            if rr.role == "big":
                big_note = rr.note_var.get().strip() or rr.role_var.get()
                break
        if big_note:
            self._log_manual(big_note, gain, "收款:%s(来自%s)" % (work["name"], note),
                             big_gain=gain, work_name=work["name"], from_note=note,
                             op_id=op_id)
        # 作品累计次数+1
        for w in self.work_rows:
            if (w.name_var.get().strip() or "作品") == work["name"]:
                try:
                    cp = int(w.copies_var.get() or 0)
                except ValueError:
                    cp = 0
                self._binding_saves = True
                try:
                    w.copies_var.set(str(cp + 1))
                finally:
                    self._binding_saves = False
                break
        self._refresh_work_status()
        self._auto_save()
        expire_days = int(self._read_param("expiry_days", DEFAULTS["expiry_days"]))
        self._refresh_quick_hint("已记录：%s 复制「%s」-%g 点；大号 +%g 点，将于 %s 到期（%d天）；"
                                 "作品累计 %d 次。"
                                 % (note, work["name"], price, gain,
                                    (datetime.date.today() + datetime.timedelta(days=expire_days)).isoformat(),
                                    expire_days, work["copies"] + 1))

    def on_undo_quick(self):
        """撤回当前账号今天记错的一笔快捷操作（点赞 / 复制 / 发布应用）。"""
        note = self.quick_note_var.get()
        if not note:
            messagebox.showwarning("提示", "请先在下拉框选择账号。", parent=self)
            return
        entries = self._active_manual_entries(note=note)
        groups = {}
        for e in entries:
            k = self._op_kind(e)
            if k == "income":  # 大号收款跟随复制那笔一起撤回，不单独列出
                continue
            key = str(e.get("op_id") or ("seq%s" % e.get("seq")))
            groups.setdefault(key, []).append(e)
        if not groups:
            self._refresh_quick_hint("当前账号今天没有可撤回的记录；只有点过快捷操作后才能撤回。")
            return
        picked = self._ask_undo_group(groups, note)
        if not picked:
            return
        title = str(picked[0].get("reason", ""))[:28]
        if not messagebox.askyesno(
                "确认撤回",
                "确定撤回「%s」这笔记录吗？\n\n"
                "对应账号的秒点会反向调整；复制会退回大号收款与作品累计次数；"
                "发布应用会删除尚未使用的新作品。原操作记录不会删除，明细里会保留两笔供核对。"
                % title, parent=self):
            return
        self._apply_undo_group(picked)

    def _ask_undo_group(self, groups, note):
        """列出当前账号今天可撤回的操作，返回用户选中的那一组记录。"""
        win = tk.Toplevel(self)
        win.title("撤回今天的快捷操作")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        tk.Label(win, text="%s 今天已记录的操作（选一笔撤回）" % note,
                 font=(FONT, 11, "bold"), bg=C_SURFACE, fg=C_INK).pack(padx=18, pady=(14, 6))
        tk.Label(win, text="撤回后原记录保留，另记一笔反向流水；已过期的秒点不会回来。",
                 font=(FONT, 9), bg=C_SURFACE, fg=C_MUTED).pack(padx=18, pady=(0, 8))
        lb = tk.Listbox(win, width=58, height=min(8, max(3, len(groups) + 1)),
                        font=(FONT, 10), bg="#FFFFFF", fg=C_INK,
                        highlightbackground=C_BORDER)
        labels = []
        for key, es in groups.items():
            head = es[0]
            ts = str(head.get("ts", ""))[11:16] or "—"
            kind = self._op_kind(head)
            kind_cn = {"like": "点赞", "copy": "复制", "publish": "发布"}.get(kind, kind)
            total = 0.0
            for e in es:
                try:
                    total += float(e.get("delta", 0))
                except Exception:
                    pass
            labels.append("%s  %-4s  %s  %+g 点" % (
                ts, kind_cn, str(head.get("reason", ""))[:26], total))
            lb.insert("end", labels[-1])
        lb.pack(padx=18, pady=4)
        if labels:
            lb.selection_set(0)
        bfr = tk.Frame(win)
        bfr.pack(pady=(8, 14))
        result = {"picked": None}

        def _ok():
            sel = lb.curselection()
            if not sel:
                return
            result["picked"] = groups[list(groups.keys())[int(sel[0])]]
            win.destroy()

        ttk.Button(bfr, text="撤回选中这笔", style="Primary.TButton", command=_ok).pack(side="left", padx=6)
        ttk.Button(bfr, text="取消", command=win.destroy).pack(side="left")
        _center_window(win, parent=self)
        self.wait_window(win)
        return result["picked"]

    @staticmethod
    def _undo_work_name(e):
        name = str(e.get("work_name") or "")
        if name:
            return name
        reason = str(e.get("reason", ""))
        if reason.startswith("复制"):
            return reason[2:].rsplit("-", 1)[0].replace("（大号自复制不计收款）", "")
        return ""

    def _apply_undo_group(self, picked):
        """按组反向改余额 / 批次 / 作品次数，逐笔追加撤回流水（不覆盖原记录）。"""
        op_ids = {str(e.get("op_id") or "") for e in picked if e.get("op_id")}
        related = []
        for e in list(getattr(self, "change_log", []) or []):
            if e in picked:
                related.append(e)
            elif str(e.get("op_id") or "") in op_ids and e.get("source") == "manual":
                related.append(e)  # 大号收款跟随复制那笔一起回退
        mark = str(picked[0].get("op_id") or ("seq%s" % picked[0].get("seq")))
        today = datetime.date.today()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        totals = {}
        for e in related:
            try:
                totals[e.get("note", "")] = totals.get(e.get("note", ""), 0.0) + float(e.get("delta", 0))
            except Exception:
                pass
        news = {}
        self._binding_saves = True
        try:
            for n, total in totals.items():
                r = self._find_row_by_note(n)
                if r is None:
                    continue
                try:
                    cur = float(r.bal_var.get() or 0)
                except ValueError:
                    cur = 0.0
                newv = round(cur - total, 2)
                r.bal_var.set("%g" % newv)
                news[n] = (cur, newv)
        finally:
            self._binding_saves = False
        undo_entries = []
        for e in related:
            n = e.get("note", "")
            before, after = news.get(n, (0.0, 0.0))
            try:
                delta = -float(e.get("delta", 0))
            except Exception:
                delta = 0.0
            undo_entries.append({
                "ts": now, "day": today.day, "note": n, "delta": round(delta, 2),
                "before": round(before, 2), "after": after, "login_delta": 0,
                "reason": "撤回：%s" % e.get("reason", ""), "direction": "撤回",
                "source": "undo", "undo_of": mark,
                "work_name": e.get("work_name", ""),
            })
        self._append_change_log(undo_entries)
        # 大号收款批次作废（明细里仍可见，标注已撤回）
        for b in getattr(self, "manual_batches", []):
            if str(b.get("op_id") or "") in op_ids:
                b["revoked"] = True
        # 作品累计次数 / 发布的新作品回退
        tips = []
        for w in list(getattr(self, "work_rows", [])):
            wname = w.name_var.get().strip()
            if not wname:
                continue
            if any(str(e.get("created_work") or "") == wname for e in picked):
                try:
                    cp = int(w.copies_var.get() or 0)
                except ValueError:
                    cp = 0
                if cp == 0:
                    w.frame.destroy()
                    self.work_rows.remove(w)
                    tips.append("已删除新作品「%s」" % wname)
                else:
                    tips.append("「%s」已产生复制记录，保留作品、仅回退秒点" % wname)
                continue
            minus = sum(1 for e in picked
                        if self._op_kind(e) == "copy" and self._undo_work_name(e) == wname)
            if minus:
                try:
                    cp = int(w.copies_var.get() or 0)
                except ValueError:
                    cp = 0
                w.copies_var.set(str(max(0, cp - minus)))
                tips.append("「%s」累计次数 -%d" % (wname, minus))
        self._refresh_work_status()
        self._refresh_account_combo()
        self._auto_save()
        extra = ("；" + "，".join(tips)) if tips else ""
        self._refresh_quick_hint("已撤回：%s（原记录保留，明细中可核对反向流水）%s。"
                                 % (str(picked[0].get("reason", ""))[:30], extra))

    def _ask_copy_work(self, avail, limit):
        """简易对话框：选择一个作品（含当前累计次数），返回该作品 dict。"""
        win = tk.Toplevel(self)
        win.title("选择复制的作品")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        tk.Label(win, text="选择小号刚复制的大号作品", font=(FONT, 11, "bold"),
                 bg=C_SURFACE, fg=C_INK).pack(padx=18, pady=(14, 6))
        tk.Label(win, text="累计已满 %d 次的作品不会出现在列表里（复制也收不到点）。" % limit,
                 font=(FONT, 9), bg=C_SURFACE, fg=C_MUTED).pack(padx=18, pady=(0, 8))
        opts = ["%s（已复制 %d 次）" % (w["name"], w["copies"]) for w in avail]
        name_to_opt = {w["name"]: o for w, o in zip(avail, opts)}
        cb = ttk.Combobox(win, values=opts, state="readonly", width=28)
        cb.set(opts[0])
        cb.pack(padx=18, pady=4)
        bfr = tk.Frame(win)
        bfr.pack(pady=(8, 14))
        result = {"work": None}

        def _ok():
            chosen = cb.get()
            for w in avail:
                if name_to_opt.get(w["name"]) == chosen:
                    result["work"] = w
                    break
            win.destroy()

        ttk.Button(bfr, text="确定", style="Primary.TButton", command=_ok).pack(side="left", padx=6)
        ttk.Button(bfr, text="取消", command=win.destroy).pack(side="left")
        _center_window(win, parent=self)  # 内容自适应后相对主窗口居中
        self.wait_window(win)
        return result["work"]

    def _refresh_work_combo(self):
        self._refresh_quick_hint()

    # ---- 账号行管理 ----
    def add_row(self, role, idx, note, balance, login_claimed, expire_today=False):
        row = AccountRow(self.acc_list, role, idx, note, balance, login_claimed,
                         on_delete=lambda r=None: None, expire_today=expire_today)
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
            if k in self.param_vars:
                self.param_vars[k].set(str(v))
        # 重建作品列表（默认4个）
        for w in list(getattr(self, "work_rows", [])):
            w.frame.destroy()
        self.work_rows = []
        for i in range(4):
            self.add_work(name="作品%d" % (i + 1), copies=0)
        self._refresh_account_combo()
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
        works = self._collect_works()
        today_ops = self._today_manual_ops()       # 今日已手动操作（记录只读，永久保留）
        # 大号手动收款批次：已撤回的不再参与方案到期提醒
        manual_batches = [b for b in (getattr(self, "manual_batches", []) or [])
                          if not b.get("revoked")]
        text, rows, _sections, actions, balance_curve, login_curve, expiry, work_copy_curve = build_plan(
            accounts, p, self.ctx, pack_on_start=self.pack_today_var.get(), works=works,
            today_ops=today_ops, manual_batches=manual_batches)
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
            "expire_today": {a["note"]: bool(a.get("expire_today", False)) for a in accounts},
            "login_cap": int(p["login_days_cap"]),
            "packed_on_start": bool(self.pack_today_var.get()),
            "works": works,                     # 方案采用时的作品快照（名称/累计次数）
            "work_copy_curve": {str(d): dict(v) for d, v in work_copy_curve.items()},
            "today_ops": today_ops,             # 今日已手动操作（生成时用于首日跳过）
            "manual_batches": [dict(b) for b in manual_batches],  # 大号收款批次快照
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
        _center_window(win, parent=self, width=1040, height=860)  # 相对主窗口居中

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
        lines.append("【四】软件实际加减记录（每日推进 / 快捷操作 / 撤回时真实改到余额上的数）")
        lines.append("  说明：撤回不删除原记录，而是另记一笔「撤回」反向流水；两笔净额为 0，便于逐笔核对。")
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

        # ---- 五、大号收款批次（快捷操作产生，带7天到期） ----
        lines.append("【五】大号收款批次（小号复制产生，按发生日+有效期到期）")
        lines.append("-" * W)
        mb = getattr(self, "manual_batches", [])
        if not isinstance(mb, list) or not mb:
            lines.append("  （暂无记录。点「已复制大号作品」后，大号收到的秒点会按笔记录发生日与到期时间。）")
        else:
            today = datetime.date.today()
            for b in mb[-80:]:
                try:
                    exp = datetime.date.fromisoformat(b["expire"])
                    left = (exp - today).days
                    if b.get("revoked"):
                        status = "已撤回（不计入）"
                    elif left < 0:
                        status = "已到期"
                    elif left == 0:
                        status = "⚠今天到期"
                    else:
                        status = "%d天后到期" % left
                    lines.append("  %s  复制「%s」由 %s 贡献  +%g 点  → 到期 %s 00:00（%s）" % (
                        b.get("date", ""), b.get("name", ""), b.get("from", ""),
                        float(b["amount"]), exp.isoformat(), status))
                except Exception:
                    continue
            lines.append("  （仅显示最近 80 条，共 %d 笔存档。）" % len(mb))
        lines.append("")
        lines.append("核对方法：先看【一】当前值是否与平台一致；不一致时对照【二】找原因，")
        lines.append("再用【三】逐笔「上笔→变动→本笔」定位哪一笔，最后用【四】看软件实际改了多少；")
        lines.append("【五】看快捷操作产生的大号收款什么时候到期（如三个小号同日复制共240点，7天后同时到期）。")
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
            r = recommend_price(accounts, p, self.ctx, works=self._collect_works())
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
            _center_window(win, parent=self, width=960, height=760)

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
        _center_window(win, parent=self, width=1080, height=860)

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
        data["works"] = self._collect_works()
        data["manual_batches"] = getattr(self, "manual_batches", [])
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
                self.add_row("big", 0, a["note"], a["balance"], a.get("login_claimed", 0),
                             expire_today=a.get("expire_today", False))
                has_big = True
            else:
                self.small_counter += 1
                nr = self.add_row("small", self.small_counter,
                                  a["note"], a["balance"], a.get("login_claimed", 0),
                                  expire_today=a.get("expire_today", False))
                btn = nr.frame.grid_slaves(row=0, column=7)
                if btn:
                    btn[0].configure(command=lambda rr=nr: self.delete_row(rr))
        if not has_big:
            self.add_row("big", 0, "大号", 0, 0)
        # 先用默认值填满，再用配置里的值覆盖，避免残缺配置导致参数框为空
        for k, v in DEFAULTS.items():
            if k in self.param_vars:
                self.param_vars[k].set(str(v))
        for k, v in data.get("params", {}).items():
            if k in self.param_vars and str(v).strip() != "":
                self.param_vars[k].set(str(v))
        # 恢复大号作品列表（兼容旧配置缺字段）
        for w in list(getattr(self, "work_rows", [])):
            w.frame.destroy()
        self.work_rows = []
        works_data = data.get("works")
        if isinstance(works_data, list) and works_data:
            for wd in works_data:
                self.add_work(name=wd.get("name", "作品"), copies=wd.get("copies", 0))
        else:
            for i in range(int(self.param_vars["work_count"].get() or 4)):
                self.add_work(name="作品%d" % (i + 1), copies=0)
        self._refresh_work_status()
        self._refresh_account_combo()
        # 恢复快捷操作产生的大号收款批次
        mb = data.get("manual_batches", [])
        self.manual_batches = mb if isinstance(mb, list) else []
        for b in self.manual_batches:
            b.setdefault("revoked", False)
        # 恢复两个执行状态开关
        self.today_done_var.set(bool(data.get("today_done", False)))
        self.pack_today_var.set(bool(data.get("pack_today", False)))
        # 恢复实际加减记录（供「查看加减明细」核对，兼容旧配置缺字段）
        log = data.get("change_log", [])
        self.change_log = log if isinstance(log, list) else []
        self._ensure_log_seq()   # 旧记录也编号，撤回时可精确定位
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

    # ===== 内置版本更新：独立窗口 / 检查 / 下载 / 安装 / 重启 =====
    def _open_update_window(self, focus=True):
        """打开/复用独立更新窗口：非模态、不 grab，随时可切回主窗口。"""
        win = getattr(self, "update_win", None)
        if win is None or not bool(win.winfo_exists()):
            win = self._build_update_window()
            self.update_win = win
        win.deiconify()
        if focus:
            win.lift()
            try:
                win.focus_force()
            except Exception:
                pass

    def _build_update_window(self):
        win = tk.Toplevel(self)
        win.title("检查更新")
        win.configure(bg=C_SURFACE)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)

        head = tk.Frame(win, bg=C_NAVY, height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="检查更新", bg=C_NAVY, fg="#FFFFFF",
                 font=(FONT, 13, "bold")).pack(side="left", padx=18)
        tk.Label(head, text="更新信息集中在这里，不遮挡主界面", bg=C_NAVY,
                 fg="#9FB3CC", font=(FONT, 9)).pack(side="left")

        box = tk.Frame(win, bg=C_SURFACE, highlightthickness=1,
                       highlightbackground=C_BORDER)
        box.pack(fill="both", expand=True, padx=16, pady=(14, 8))
        info = tk.Frame(box, bg=C_SURFACE)
        info.pack(fill="x", padx=16, pady=(14, 6))
        self.uw_current = tk.Label(info, text="当前版本：v%s" % APP_VERSION,
                                   bg=C_SURFACE, fg=C_INK, font=(FONT, 10, "bold"))
        self.uw_current.pack(anchor="w")
        self.uw_latest = tk.Label(info, text="线上版本：尚未检查", bg=C_SURFACE,
                                  fg=C_MUTED, font=(FONT, 10))
        self.uw_latest.pack(anchor="w", pady=(4, 0))
        self.uw_source = tk.Label(info, text="命中来源：—", bg=C_SURFACE,
                                  fg=C_MUTED, font=(FONT, 9))
        self.uw_source.pack(anchor="w", pady=(2, 0))
        self.uw_status = tk.Label(info, text="状态：尚未检查", bg=C_SURFACE,
                                  fg=C_MUTED, font=(FONT, 10))
        self.uw_status.pack(anchor="w", pady=(8, 0))

        tk.Label(box, text="版本说明", bg=C_SURFACE, fg=C_MUTED,
                 font=(FONT, 9, "bold")).pack(anchor="w", padx=16, pady=(8, 2))
        self.uw_notes = scrolledtext.ScrolledText(
            box, height=5, wrap="word", font=(FONT, 9), bg="#FFFFFF", fg=C_INK,
            relief="flat", borderwidth=1, highlightbackground=C_BORDER)
        self.uw_notes.pack(fill="both", expand=True, padx=16)
        self.uw_notes.configure(state="disabled")

        prog = tk.Frame(box, bg=C_SURFACE)
        prog.pack(fill="x", padx=16, pady=(10, 4))
        self.uw_stage = tk.Label(prog, text="", bg=C_SURFACE, fg=C_MUTED,
                                 font=(FONT, 9), anchor="w")
        self.uw_stage.pack(fill="x")
        self.uw_pb = ttk.Progressbar(prog, orient="horizontal",
                                     mode="determinate", maximum=1)
        self.uw_pb.pack(fill="x", pady=(4, 0))

        bar = tk.Frame(win, bg=C_SURFACE)
        bar.pack(fill="x", padx=16, pady=(4, 14))
        self.uw_check = ttk.Button(bar, text="检查更新",
                                   command=lambda: self._check_update(manual=True))
        self.uw_check.pack(side="left")
        self.uw_restore = ttk.Button(bar, text="恢复默认更新地址",
                                     command=self._restore_default_source)
        self.uw_now = ttk.Button(bar, text="立即更新", style="Primary.TButton",
                                 command=self._start_download, state="disabled")
        self.uw_skip = ttk.Button(bar, text="跳过此版本",
                                  command=self._on_skip, state="disabled")
        self.uw_later = ttk.Button(bar, text="稍后再说",
                                   command=self._on_later, state="disabled")
        self._uw_apply_state()
        _center_window(win, parent=self, width=640, height=520)
        win.withdraw()
        return win

    def _uw_apply_state(self):
        """把缓存的更新状态回填到独立窗口（窗口可能刚被自动创建）。"""
        v = getattr(self, "update_view", None)
        if not v:
            return
        w = getattr(self, "uw_latest", None)
        if w is not None:
            w.config(text="线上版本：" + str(v.get("latest") or "尚未检查"))
        w = getattr(self, "uw_source", None)
        if w is not None:
            w.config(text="命中来源：" + str(v.get("source") or "—"))
        w = getattr(self, "uw_status", None)
        if w is not None:
            w.config(text="状态：" + str(v.get("status") or ""), fg=v.get("color") or C_MUTED)
        self._uw_set_notes(v.get("notes") or "")
        self._uw_buttons(check=v.get("check", True), now=v.get("now", False),
                         skip=v.get("skip", False), later=v.get("later", False))
        rest = getattr(self, "uw_restore", None)
        if rest is not None:
            if load_update_config().get("source"):
                if not rest.winfo_manager():
                    rest.pack(side="left", padx=8)
            else:
                rest.pack_forget()

    def _uw_set_status(self, text, color=None):
        v = getattr(self, "update_view", None)
        if v is not None:
            v["status"] = text
            v["color"] = color or C_MUTED
        w = getattr(self, "uw_status", None)
        if w is not None:
            w.config(text="状态：" + text, fg=color or C_MUTED)

    def _uw_set_latest(self, text):
        v = getattr(self, "update_view", None)
        if v is not None:
            v["latest"] = text
        w = getattr(self, "uw_latest", None)
        if w is not None:
            w.config(text="线上版本：" + str(text))

    def _uw_set_source(self, text):
        v = getattr(self, "update_view", None)
        if v is not None:
            v["source"] = text
        w = getattr(self, "uw_source", None)
        if w is not None:
            w.config(text="命中来源：" + str(text))

    def _uw_set_notes(self, text):
        v = getattr(self, "update_view", None)
        if v is not None:
            v["notes"] = text or ""
        w = getattr(self, "uw_notes", None)
        if w is None:
            return
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.insert("1.0", text or "（本次更新没有说明）")
        w.configure(state="disabled")

    def _uw_buttons(self, check=None, now=False, skip=False, later=False):
        """统一控制更新窗口按钮状态；check=None 表示保持原样。"""
        v = getattr(self, "update_view", None)
        if v is not None:
            if check is not None:
                v["check"] = check
            v["now"], v["skip"], v["later"] = now, skip, later
        if check is not None:
            w = getattr(self, "uw_check", None)
            if w is not None:
                w.config(state="normal" if check else "disabled")
        for key, on in (("uw_now", now), ("uw_skip", skip), ("uw_later", later)):
            w = getattr(self, key, None)
            if w is not None:
                w.config(state="normal" if on else "disabled")

    def _set_update_entry_alert(self, on):
        e = getattr(self, "update_entry", None)
        if e is not None:
            e.config(fg="#FBBF24" if on else "#E2E8F0")

    def _check_update(self, manual=False):
        cfg = load_update_config()
        source = cfg.get("source") or UPDATE_MANIFEST_PRIMARY
        urls = _mirror_urls(source)
        if manual:
            self._open_update_window(focus=True)
        self._uw_set_status("检查更新中…", "#94A3B8")
        self._uw_buttons(check=False)
        self._uw_set_source("检查中…")

        def worker():
            if _UPDATE_SKIP_NETWORK:
                self.after(0, self._on_manifest, None, None, manual)
                return
            d, hit = _fetch_manifest(urls)
            self.after(0, self._on_manifest, d, hit, manual)

        threading.Thread(target=worker, daemon=True).start()

    def _on_manifest(self, manifest, hit_source, manual):
        self._uw_buttons(check=True)
        if not manifest:
            self._uw_set_status("检查失败（网络或地址），请稍后再试。", "#F87171")
            return
        latest = manifest.get("version", "0")
        self._uw_set_latest("v%s" % latest)
        self._uw_set_source(self._short_source(hit_source) or "—")
        self._uw_set_notes(manifest.get("notes", ""))
        if cmp_version(latest, APP_VERSION) > 0:
            cfg = load_update_config()
            # 跳过此版本：仅启动检查生效，且只对该版本
            if (not manual) and cfg.get("skip_version") == latest:
                self._uw_set_status("发现新版本 v%s（已跳过，不再提醒）" % latest, "#94A3B8")
                return
            self.update_manifest = manifest
            self.update_version = latest
            self._uw_set_status("发现新版本 v%s%s" % (
                latest, "（重要更新）" if manifest.get("mandatory") else ""), "#FBBF24")
            self._uw_buttons(now=True, skip=not manual, later=True)
            self._set_update_entry_alert(True)
            self._open_update_window(focus=manual)
        else:
            self._uw_set_status("当前已是最新版本（v%s）。" % APP_VERSION, "#94A3B8")
            self._uw_buttons(now=False, skip=False, later=False)
            self._set_update_entry_alert(False)

    @staticmethod
    def _short_source(url):
        if not url:
            return ""
        if "127.0.0.1" in url or "localhost" in url:
            return "本地测试"
        if "ghproxy.net" in url:
            return "ghproxy"
        if "gh-proxy.com" in url:
            return "gh-proxy"
        if "github.com" in url and "/raw/" in url:
            return "github/raw"
        return "raw"

    def _start_download(self):
        m = getattr(self, "update_manifest", None)
        if not m:
            return
        self._uw_set_status("准备下载…", "#94A3B8")
        pb = getattr(self, "uw_pb", None)
        if pb is not None:
            pb.pack(fill="x", pady=(4, 0))
            pb.config(maximum=1, value=0)
        self._uw_buttons(now=False, skip=False, later=False)

        def on_progress(done, total):
            self.after(0, lambda: [
                self.uw_pb.config(maximum=total or 1, value=done),
                self.uw_stage.config(text="下载中 %.0f%%" % (done / (total or 1) * 100))])

        def on_stage(t):
            self.after(0, lambda: self.uw_stage.config(text=t))

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
            self._uw_set_status("下载失败：%s（可点「立即更新」重试）" % msg, "#D92D20")
            self._uw_buttons(now=True, skip=True, later=True)
            return
        self._uw_set_status("下载完成，正在安装替换…", "#94A3B8")
        ok2, msg2 = install_update(dest)
        if not ok2:
            self._uw_set_status("安装失败：%s" % msg2, "#D92D20")
            self._uw_buttons(now=True)
            return
        win = getattr(self, "update_win", None)
        if win is not None:
            win.withdraw()
        self._ask_restart()

    def _ask_restart(self):
        win = tk.Toplevel(self)
        win.title("更新完成")
        win.configure(bg=C_SURFACE)
        win.geometry("400x170")
        win.transient(self)
        _center_window(win, parent=self, width=400, height=170)
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
        v = getattr(self, "update_version", None)
        if v:
            cfg = load_update_config()
            cfg["skip_version"] = v
            save_update_config(cfg)
        self._uw_set_status("已跳过此版本，下次启动不再提醒。", "#94A3B8")
        self._uw_buttons(now=False, skip=False, later=False)
        self._set_update_entry_alert(False)
        win = getattr(self, "update_win", None)
        if win is not None:
            win.withdraw()

    def _on_later(self):
        win = getattr(self, "update_win", None)
        if win is not None:
            win.withdraw()

    def _restore_default_source(self):
        cfg = load_update_config()
        cfg["source"] = None
        save_update_config(cfg)
        b = getattr(self, "uw_restore", None)
        if b is not None:
            b.pack_forget()
        self._uw_set_source("默认地址")
        self._uw_set_status("已恢复默认更新地址，可点「检查更新」重新检查。", "#94A3B8")

    def _on_close(self):
        # 独立更新窗口随主窗口一起关闭，避免留下孤立窗口
        w = getattr(self, "update_win", None)
        if w is not None:
            try:
                if bool(w.winfo_exists()):
                    w.destroy()
            except Exception:
                pass
            self.update_win = None
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
