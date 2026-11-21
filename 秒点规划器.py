# -*- coding: utf-8 -*-
"""
秒点规划器 v1.0.0
根据每个账号当前秒点余额，模拟并生成「点赞 / 登录领奖 / 复制转账 / 打包」的执行方案。
用 Python 3.12 + tkinter，可打包为单文件 exe。
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import json
import csv
import os
import sys
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
VERSION = "1.5.0"
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


def _mirror_urls(primary):
    """清单/下载地址 -> 多镜像列表（直链 + 代理镜像）。不使用 jsDelivr。"""
    urls = [primary]
    for p in _EXE_MIRROR_PREFIX:
        urls.append(p.rstrip("/") + "/" + primary)
    if "raw.githubusercontent.com" in primary:
        alt = primary.replace("raw.githubusercontent.com", "github.com", 1)
        alt = re.sub(r"github\.com/([^/]+)/([^/]+)/main/",
                     r"github.com/\1/\2/raw/main/", alt, count=1)
        if alt != primary:
            urls.append(alt)
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
    "expiry_days": 7,       # 免费秒点有效期（天）
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


def simulate_small(ac, p, start_day=1, rest_days=None):
    """模拟一个小号。返回 (copies, daily_events, author_income, end_balance, login_used)
    start_day: 方案起始的真实日历日（今天几号），所有 day 均为日历日。
    rest_days: 周日集合，这些天零收入（不点赞、不登录、不复制），但秒点照常过期。
    """
    rest_days = rest_days or set()
    like_gain = p["like_gain"] * p["like_per_day"]
    login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
    login_granted = 0  # 方案窗口内实际领到的登录奖励次数
    queue = [(start_day, float(ac["balance"]))] if ac["balance"] > 0 else []
    copies = []       # (day, work_index_1based)
    events = []       # 每日事件 list of (day, action, amount, balance)
    ptr = 0
    work_count = max(1, p["work_count"])
    price = p["work_price"]

    for d in range(start_day, start_day + p["plan_days"]):
        queue = _expire(queue, d, p["expiry_days"])
        if d in rest_days:
            # 周日：不点赞、不领登录奖、不复制；只记录零收入
            events.append((d, "周日休息(无收入)", 0, None))
        else:
            gain = like_gain
            if login_left > 0:
                gain += p["login_bonus"]
                login_left -= 1
                login_granted += 1
                events.append((d, "登录领奖+点赞", gain, None))
            else:
                events.append((d, "点赞", gain, None))
            if gain > 0:
                queue.append((d, float(gain)))

            made = 0
            while price > 0 and _total(queue) >= price:
                queue = _consume(queue, price)
                wi = ptr % work_count
                copies.append((d, wi + 1))
                events.append((d, "复制→作品%d" % (wi + 1), -price, None))
                ptr += 1
                made += 1
                if made > 1000:  # 保险
                    break

        bal = _total(queue)
        # 回填当天所有事件的余额（遇到上一天已填的即停止）
        for i in range(len(events) - 1, -1, -1):
            if events[i][3] is None:
                events[i] = (events[i][0], events[i][1], events[i][2], bal)
            else:
                break

    author_income = len(copies) * price * (1 - p["copy_fee_pct"] / 100.0)
    return copies, events, author_income, _total(queue), login_granted


def simulate_big(ac, p, income_by_day, start_day=1, rest_days=None, pack_on_start=False):
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
    queue = [(start_day, float(ac["balance"]))] if ac["balance"] > 0 else []
    packs = []
    events = []
    packable_days = []
    backend = p["backend_projects"] * p["backend_per_day"]
    pack_cost = p["pack_cost"]

    for d in range(start_day, start_day + p["plan_days"]):
        queue = _expire(queue, d, p["expiry_days"])
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
        if day_gain > 0:
            queue.append((d, float(day_gain)))
        if inc > 0:
            queue.append((d, float(inc)))
        if is_rest:
            events.append((d, "周日休息(无收款)", 0, None))
        else:
            events.append((d, "收款+活跃", day_gain + inc, None))

        # 后端扣费：周日照常扣
        queue = _consume(queue, backend)
        events.append((d, "后端扣费", -backend, None))

        # 打包：只按用户勾选在起始日执行一次；之后余额够仅提示、不自动扣
        if pack_on_start and is_first and not is_rest and pack_cost > 0 and _total(queue) >= pack_cost:
            queue = _consume(queue, pack_cost)
            packs.append(d)
            events.append((d, "打包(按你勾选)", -pack_cost, None))
        elif pack_cost > 0 and _total(queue) >= pack_cost:
            packable_days.append(d)

        bal = _total(queue)
        for i in range(len(events) - 1, -1, -1):
            if events[i][3] is None:
                events[i] = (events[i][0], events[i][1], events[i][2], bal)
            else:
                break

    return packs, events, _total(queue), login_granted, packable_days


def build_plan(accounts, p, ctx=None, pack_on_start=False):
    """生成 (文本方案, csv行列表)。accounts: list of dict(role,note,balance,login_claimed)
    ctx: month_context() 返回的日期上下文；不传则按今天实时获取。
    pack_on_start: 用户勾选「起始日打包」→ 起始日打包一次并扣点。
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
    small_results = []
    income_by_day = {}
    for ac in smalls:
        copies, ev, author_income, endbal, login_used = simulate_small(ac, p, start_day, rest_days)
        small_results.append((ac, copies, ev, author_income, endbal, login_used))
        per = p["work_price"] * (1 - p["copy_fee_pct"] / 100.0)
        for d, wi in copies:
            income_by_day[d] = income_by_day.get(d, 0.0) + per

    packs, bev, bend, big_login_used, packable_days = simulate_big(
        big, p, income_by_day, start_day, rest_days, pack_on_start)

    # ---- 文本方案 ----
    L = []
    L.append("=" * 58)
    L.append("%s 方案（%d月%d日 %s 起，至%d月末共 %d 天）" %
             (APP_NAME, ctx["month"], ctx["start_day"], ctx["weekday"],
              ctx["month"], ctx["plan_days"]))
    if ctx.get("today_done"):
        L.append("※ 你已勾选「今天(%d月%d日)全部操作过」，本方案从明天开始排。" %
                 (ctx["today_month"], ctx["today"]))
    L.append("=" * 58)
    per = p["work_price"] * (1 - p["copy_fee_pct"] / 100.0)
    L.append("规则：作品定价 %d 点，手续费 %d%% → 大号每得 %g 点/次" %
             (p["work_price"], p["copy_fee_pct"], per))
    L.append("      点赞 +%d/天（%d次×%d点）；登录前 %d 天 +%d/天；有效期 %d 天" %
             (p["like_gain"] * p["like_per_day"], p["like_per_day"], p["like_gain"],
              p["login_days_cap"], p["login_bonus"], p["expiry_days"]))
    L.append("      后端 %d 项×%d = %d/天；打包 %d 点/次" %
             (p["backend_projects"], p["backend_per_day"],
              p["backend_projects"] * p["backend_per_day"], p["pack_cost"]))
    if rest_days:
        rest_txt = "、".join("%d日" % d for d in sorted(rest_days))
        L.append("      ★ 周日（%s）为休息日：全天无点赞/登录/复制/打包收入，后端照常扣费。" % rest_txt)
    L.append("")

    # ===== 每日逐号行动表（每个号每天做什么，一眼看清）=====
    day_label = "明天" if ctx.get("today_done") else "今天"
    L.append("【%s（%d月%d日 %s）各号该做什么】" %
             (day_label, ctx["month"], ctx["start_day"], ctx["weekday"]))
    notes = [ac["note"] for ac in smalls] + [big["note"]]
    if start_day in rest_days:
        L.append("  ★ 起始日是周日：所有号都不登录、不点赞、不复制、不打包，全部静默；")
        L.append("    只有大号后端照扣 %d 点。等周一再恢复操作。" %
                 (p["backend_projects"] * p["backend_per_day"]))
    else:
        for ac, copies, ev, ai, eb, lu in small_results:
            login_left = max(0, p["login_days_cap"] - int(ac.get("login_claimed", 0)))
            today_copies = [wi for d, wi in copies if d == start_day]
            login_txt = "登录领奖+点赞" if login_left > 0 else "点赞(已领满登录)"
            if today_copies:
                cp = "，复制：" + "、".join("作品%d" % w for w in today_copies)
            else:
                cp = "（余额不足，先攒点）"
            L.append("  · %s：%s%d 次%s" %
                     (ac["note"], login_txt, p["like_per_day"], cp))
        big_login_left = max(0, p["login_days_cap"] - int(big.get("login_claimed", 0)))
        big_login_txt = "登录领奖+点赞" if big_login_left > 0 else "点赞(已领满登录)"
        pack_today = "；【按你勾选】今天打包一次 -%d 点" % p["pack_cost"] if (
            pack_on_start and packs) else "；今天不打包"
        L.append("  · %s：%s%d 次（后端今天照扣 %d 点）%s" %
                 (big["note"], big_login_txt, p["like_per_day"],
                  p["backend_projects"] * p["backend_per_day"], pack_today))
    L.append("")

    # ===== 逐日行动预览：每天每个号做什么（直接取模拟事件，准确）=====
    L.append("【逐日行动预览】每个号每天具体做什么，照着做即可")
    # 把每个号的 events 归到 天→备注→[动作]
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
    for ac, copies, ev, ai, eb, lu in small_results:
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
    for d in range(start_day, start_day + p["plan_days"]):
        week = WEEK_CN[datetime.date(ctx["year"], ctx["month"], d).weekday()]
        tag = "  ★周日休息日" if d in rest_days else ""
        L.append("  ── %d日(%s)%s" % (d, week, tag))
        for note in notes:
            acts = actions_by_day.get(d, {}).get(note, [])
            L.append("      %s：%s" % (note, "、".join(acts) if acts else "静默"))
    L.append("")

    # 小号排期
    for ac, copies, ev, ai, eb, lu in small_results:
        L.append("-" * 58)
        L.append("【%s】当前 %g 点｜本月已领登录 %d 天（方案内再领 %d 天）" %
                 (ac["note"], ac["balance"], int(ac.get("login_claimed", 0)), lu))
        L.append("  复制 %d 次 → 为大号贡献 %g 点；月末余额约 %g 点" %
                 (len(copies), ai, eb))
        if copies:
            txt = "  "
            for i, (d, wi) in enumerate(copies):
                txt += "%d日→作品%d  " % (d, wi)
                if (i + 1) % 5 == 0:
                    L.append(txt)
                    txt = "  "
            if txt.strip():
                L.append(txt)
            L.append("  （作品按 1→2→3→4 轮换，同一账号不会连续复制同一个作品）")
        else:
            L.append("  当前余额不足定价 %d，需先攒点。" % p["work_price"])
        L.append("")

    # 大号排期
    L.append("-" * 58)
    total_ai = sum(r[3] for r in small_results)
    total_copies = sum(len(r[1]) for r in small_results)
    L.append("【%s】当前 %g 点｜本月已领登录 %d 天（方案内再领 %d 天）" %
             (big["note"], big["balance"], int(big.get("login_claimed", 0)), big_login_used))
    L.append("  小号合计复制 %d 次 → 转账收入 %g 点" % (total_copies, total_ai))
    L.append("  后端每天 -%d，%d 天共 -%d" %
             (p["backend_projects"] * p["backend_per_day"], p["plan_days"],
              p["backend_projects"] * p["backend_per_day"] * p["plan_days"]))
    if packs:
        L.append("  打包：按你的勾选，在 %s 打包 1 次，扣 %d 点。" %
                 ("、".join("%d日" % d for d in packs), p["pack_cost"]))
    else:
        L.append("  本次未安排打包（平时不打包，余额自然累积/过期）。")
    if packable_days:
        # 只提示第一段连续可打包区间的起始日
        L.append("  可打包时机：%d日 起余额达到 %d 点，届时你可自行决定要不要打包（软件不自动扣）。" %
                 (packable_days[0], p["pack_cost"]))
    else:
        L.append("  规划期内余额未再达到打包线 %d。" % p["pack_cost"])
    L.append("  月末预计余额约 %g 点" % bend)
    L.append("")
    L.append("=" * 58)
    L.append("【汇总】小号复制 %d 次｜大号打包 %d 次｜大号月末余额约 %g 点" %
             (total_copies, len(packs), bend))
    if p["work_count"] < 2:
        L.append("提示：作品数量建议≥2，否则同一账号可能连续复制同一作品。")
    L.append("说明：方案按真实日历日排布（%d月%d日～%d日）；当前余额按起始日可用计入并从起始日起算有效期；" %
             (ctx["month"], ctx["start_day"], ctx["days_in_month"]))
    L.append("      余额请填「调整好作品之后」的实际数（调整消耗的秒点已含在内，软件不再另扣）。")
    L.append("      打包平时不自动做；要打包就在界面勾选，生成时只扣那一次。改了余额记得重新生成。")

    # ---- CSV 明细 ----
    rows = [["日期(号)", "账号", "备注", "动作", "变动(点)", "操作后余额"]]
    for ac, copies, ev, ai, eb, lu in small_results:
        for d, action, amt, bal in ev:
            rows.append([d, "小号", ac["note"], action, round(amt, 2), round(bal, 2)])
    for d, action, amt, bal in bev:
        rows.append([d, "大号", big["note"], action, round(amt, 2), round(bal, 2)])

    return "\n".join(L), rows


# ---------- GUI ----------
class AccountRow:
    def __init__(self, parent, role, idx, note="", balance=0, login_claimed=0, on_delete=None):
        self.role = role
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self.role_var = tk.StringVar(value="大号" if role == "big" else "小号%d" % idx)
        self.note_var = tk.StringVar(value=note)
        self.bal_var = tk.StringVar(value=str(balance))
        self.login_var = tk.StringVar(value=str(login_claimed))

        ttk.Label(self.frame, textvariable=self.role_var, width=8,
                  font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, padx=6, pady=6)
        ttk.Label(self.frame, text="备注").grid(row=0, column=1, padx=3)
        ttk.Entry(self.frame, textvariable=self.note_var, width=12).grid(row=0, column=2, padx=3)
        ttk.Label(self.frame, text="当前秒点").grid(row=0, column=3, padx=3)
        ttk.Entry(self.frame, textvariable=self.bal_var, width=9).grid(row=0, column=4, padx=3)
        # 大号和小号都需要「本月已领登录天数」（两者都涉及登录奖励）
        ttk.Label(self.frame, text="本月已领登录(天)").grid(row=0, column=5, padx=3)
        ttk.Spinbox(self.frame, from_=0, to=7, textvariable=self.login_var,
                    width=4).grid(row=0, column=6, padx=3)
        if role == "small":
            ttk.Button(self.frame, text="删除", width=6,
                       command=on_delete).grid(row=0, column=7, padx=6)

    def to_dict(self):
        bal = float(self.bal_var.get() or 0)
        lc = int(self.login_var.get() or 0)
        return {"role": self.role, "note": self.note_var.get().strip() or self.role_var.get(),
                "balance": bal, "login_claimed": lc}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("%s v%s" % (APP_NAME, VERSION))
        self.geometry("960x760")
        self.minsize(880, 680)
        self.configure(padx=10, pady=8)
        self.rows = []
        self.small_counter = 0
        self.last_rows = None
        self.today_done_var = tk.BooleanVar(value=False)  # 今天是否已全部操作过
        self.pack_today_var = tk.BooleanVar(value=False)  # 起始日是否打包
        self.ctx = month_context(today_done=False)
        self._build_style()
        self._build_ui()
        self._auto_load()   # 启动自动恢复上次配置（无则用默认）
        # 关闭窗口时自动保存，用户无需手动点
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 每次启动后台静默检查更新（不打断使用）
        self.after(300, lambda: self._check_update(manual=False))

    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Card.TFrame", relief="groove", borderwidth=1)
        st.configure("TLabel", font=("Microsoft YaHei", 10))
        st.configure("TButton", font=("Microsoft YaHei", 10))
        st.configure("TEntry", font=("Microsoft YaHei", 10))
        st.configure("Title.TLabel", font=("Microsoft YaHei", 15, "bold"))

    def _build_ui(self):
        # ===== 顶部更新抬头（常驻）：版本号 + 检查更新 + 状态 + 恢复默认地址 =====
        top = tk.Frame(self, bg="#2c3e50")
        top.pack(fill="x", pady=(0, 4))
        self.ver_label = tk.Label(top, text="v" + APP_VERSION, bg="#2c3e50", fg="#cfe3ff",
                                  font=("Microsoft YaHei", 10, "bold"), cursor="hand2")
        self.ver_label.pack(side="left", padx=(8, 6))
        self.ver_label.bind("<Button-1>", lambda e: self._check_update(manual=True))
        self.update_btn = ttk.Button(top, text="检查更新",
                                     command=lambda: self._check_update(manual=True))
        self.update_btn.pack(side="left", padx=(0, 8))
        self.update_status = tk.Label(top, text="", bg="#2c3e50", fg="#aab7c4",
                                      font=("Microsoft YaHei", 9))
        self.update_status.pack(side="left", padx=(0, 8))
        self.restore_src_btn = ttk.Button(top, text="恢复默认更新地址",
                                          command=self._restore_default_source)
        if load_update_config().get("source"):
            self.restore_src_btn.pack(side="left", padx=(0, 6))

        # ===== 非模态更新横幅（默认隐藏，发现新版时弹出）=====
        b = tk.Frame(self, bg="#fff3cd", relief="solid", borderwidth=1)
        b.pack(fill="x")
        b.pack_forget()
        self.banner = b
        inner = tk.Frame(b, bg="#fff3cd")
        inner.pack(fill="x", padx=10, pady=6)
        self.banner_text = tk.Label(inner, text="", bg="#fff3cd", fg="#7a5b00",
                                    font=("Microsoft YaHei", 10, "bold"))
        self.banner_text.pack(side="left")
        self.banner_notes = tk.Label(inner, text="", bg="#fff3cd", fg="#8a6d00",
                                     font=("Microsoft YaHei", 9))
        self.banner_notes.pack(side="left", padx=8)
        self.banner_stage = tk.Label(inner, text="", bg="#fff3cd", fg="#8a6d00",
                                     font=("Microsoft YaHei", 9))
        self.banner_stage.pack(side="left", padx=8)
        self.banner_pb = ttk.Progressbar(inner, orient="horizontal",
                                         mode="determinate", maximum=1)
        bf = tk.Frame(inner, bg="#fff3cd")
        bf.pack(side="right")
        self.btn_now = ttk.Button(bf, text="立即更新", command=self._start_download)
        self.btn_now.pack(side="left", padx=3)
        self.btn_skip = ttk.Button(bf, text="跳过此版本", command=self._on_skip)
        self.btn_skip.pack(side="left", padx=3)
        self.btn_later = ttk.Button(bf, text="稍后再说", command=self._on_later)
        self.btn_later.pack(side="left", padx=3)

        ttk.Label(self, text="秒点规划器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="填入各账号当前秒点 → 一键生成点赞 / 复制 / 打包方案",
                  foreground="#666").pack(anchor="w", pady=(0, 4))
        # 日期栏（可刷新）
        self.date_var = tk.StringVar()
        self.rest_var = tk.StringVar()
        ttk.Label(self, textvariable=self.date_var, foreground="#0a6",
                  font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=(0, 2))
        ttk.Label(self, textvariable=self.rest_var, foreground="#c60",
                  font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(0, 6))

        # 起始状态开关区
        sw = ttk.LabelFrame(self, text=" 执行状态（生成方案前先确认） ", padding=6)
        sw.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(sw, text="今天各号已全部操作过（勾选→方案从明天排起）",
                        variable=self.today_done_var,
                        command=self._on_switch_changed).pack(anchor="w")
        ttk.Checkbutton(sw, text="起始日要打包作品（勾选→方案第一天打包一次并扣点；平时不打包）",
                        variable=self.pack_today_var,
                        command=self._on_switch_changed).pack(anchor="w")

        # 账号区
        acc_box = ttk.LabelFrame(self, text=" 账号（可增删小号、改备注与当前秒点） ", padding=8)
        acc_box.pack(fill="x")
        self.acc_list = ttk.Frame(acc_box)
        self.acc_list.pack(fill="x")
        btn_bar = ttk.Frame(acc_box)
        btn_bar.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_bar, text="＋ 添加小号", command=self.add_small).pack(side="left")
        ttk.Button(btn_bar, text="重置为 1大3小", command=self._load_defaults).pack(side="left", padx=6)

        # 参数区
        par_box = ttk.LabelFrame(self, text=" 规则参数（按秒哒实际规则，可自行调整） ", padding=8)
        par_box.pack(fill="x", pady=(10, 0))
        self.param_vars = {}
        for i, (key, label) in enumerate(PARAM_LABELS):
            r, c = divmod(i, 4)
            cell = ttk.Frame(par_box)
            cell.grid(row=r, column=c, padx=8, pady=4, sticky="w")
            ttk.Label(cell, text=label, width=16).pack(side="left")
            v = tk.StringVar()
            ent = ttk.Entry(cell, textvariable=v, width=7)
            if key == "plan_days":
                ent.configure(state="readonly")  # 规划天数自动取当月，禁止手改
            ent.pack(side="left")
            self.param_vars[key] = v

        # 操作按钮
        op = ttk.Frame(self)
        op.pack(fill="x", pady=10)
        ttk.Button(op, text="生成方案", command=self.on_generate).pack(side="left")
        ttk.Button(op, text="导出明细CSV", command=self.on_export).pack(side="left", padx=6)
        ttk.Label(op, text="（配置每次改动后自动保存，下次打开自动恢复，无需手动存取）",
                  foreground="#888").pack(side="left", padx=6)

        # 输出区
        out_box = ttk.LabelFrame(self, text=" 方案输出 ", padding=6)
        out_box.pack(fill="both", expand=True)
        self.out = scrolledtext.ScrolledText(out_box, wrap="word", font=("Microsoft YaHei", 10))
        self.out.pack(fill="both", expand=True)

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

    def _on_switch_changed(self):
        self._refresh_date_bar()
        self._auto_save()

    # ---- 账号行管理 ----
    def add_row(self, role, idx, note, balance, login_claimed):
        row = AccountRow(self.acc_list, role, idx, note, balance, login_claimed,
                         on_delete=lambda r=None: None)
        row.frame.pack(fill="x", pady=2)
        if role == "small":
            row.frame.configure  # noqa
            # 重新绑定删除命令，带上自身引用
            for w in row.frame.winfo_children():
                pass
        self.rows.append(row)
        return row

    def add_small(self):
        self.small_counter += 1
        row = self.add_row("small", self.small_counter, "小号%d" % self.small_counter, 0, 0)
        # 找到删除按钮并绑定
        btn = row.frame.grid_slaves(row=0, column=7)
        if btn:
            btn[0].configure(command=lambda rr=row: self.delete_row(rr))

    def delete_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            row.frame.destroy()

    def _load_defaults(self):
        for r in list(self.rows):
            r.frame.destroy()
        self.rows = []
        self.small_counter = 0
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
    def on_generate(self):
        try:
            accounts, p = self.collect()
            text, rows = build_plan(accounts, p, self.ctx,
                                    pack_on_start=self.pack_today_var.get())
            self.last_rows = rows
            self.out.delete("1.0", "end")
            self.out.insert("1.0", text)
            self._auto_save()  # 生成时顺手保存，改动即时落盘
        except Exception as e:
            messagebox.showerror("无法生成方案", str(e))

    def on_export(self):
        if not self.last_rows:
            messagebox.showinfo("提示", "请先点击「生成方案」。")
            return
        path = filedialog.asksaveasfilename(
            title="导出明细CSV", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")], initialfile="秒点方案明细.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(self.last_rows)
            messagebox.showinfo("成功", "已导出：%s" % path)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def config_path(self):
        return os.path.join(_app_dir(), "秒点规划器配置.json")

    # ---- 配置：开机自动恢复 + 自动保存（无需手动点）----
    def _auto_save(self):
        """静默保存当前账号与参数，不弹窗、不打断。"""
        try:
            accounts, p = self.collect()
            data = {"version": VERSION, "accounts": accounts, "params": p,
                    "today_done": self.today_done_var.get(),
                    "pack_today": self.pack_today_var.get()}
            with open(self.config_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 自动保存失败不打扰用户

    def _apply_data(self, data):
        """把配置数据灌回界面（供自动恢复与手动重置共用）。"""
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
        self._refresh_date_bar()

    def _auto_load(self):
        """启动时静默读取上次配置；没有或损坏则用默认值。"""
        path = self.config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("accounts"):
                    self._apply_data(data)
                    return
            except Exception:
                pass
        self._load_defaults()

    # ===== 内置版本更新：检查 / 横幅 / 下载 / 安装 / 重启 =====
    def _check_update(self, manual=False):
        cfg = load_update_config()
        source = cfg.get("source") or UPDATE_MANIFEST_PRIMARY
        urls = _mirror_urls(source)
        self.update_status.config(text="检查更新中…", fg="#aab7c4")
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
            self.update_status.config(text="检查失败（网络/地址）", fg="#aab7c4")
            if manual:
                messagebox.showwarning("检查更新", "暂时无法连接更新服务器，请稍后再试。")
            return
        latest = manifest.get("version", "0")
        self.update_status.config(text="线上 v%s · %s" % (latest, self._short_source(hit_source)),
                                  fg="#aab7c4")
        if cmp_version(latest, APP_VERSION) > 0:
            self.update_status.config(fg="#ffd27f")   # 有新版本 → 橙色提示
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
        self.banner.pack(fill="x")
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
        win.geometry("360x150")
        win.transient(self)
        tk.Label(win, text="新版本已安装完成！",
                 font=("Microsoft YaHei", 12, "bold")).pack(pady=(16, 6))
        tk.Label(win, text="是否立即重启以使用新版本？",
                 font=("Microsoft YaHei", 10)).pack(pady=4)
        bf = tk.Frame(win)
        bf.pack(pady=10)
        ttk.Button(bf, text="立即重启",
                   command=lambda: (win.destroy(), restart_app(self))).pack(side="left", padx=8)
        ttk.Button(bf, text="稍后重启", command=win.destroy).pack(side="left", padx=8)

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
        self.update_status.config(text="已恢复默认更新地址", fg="#aab7c4")

    def _on_close(self):
        self._auto_save()
        self.destroy()


def main():
    cleanup_mei()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
