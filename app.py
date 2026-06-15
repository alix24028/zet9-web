import math, os, sys, glob
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# --- Lazy ephem import ---
_ephem = None
def get_ephem():
    global _ephem
    if _ephem is None:
        import ephem
        _ephem = ephem
    return _ephem

# --- Data path ---
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if not os.path.exists(DATA_DIR):
    DATA_DIR = r"C:\ZET 9\Wrk"

# --- Constants ---
ZODIAC_SIGNS_AR = [
    ("الحمل", "Aries"), ("الثور", "Taurus"), ("الجوزاء", "Gemini"),
    ("السرطان", "Cancer"), ("الأسد", "Leo"), ("العذراء", "Virgo"),
    ("الميزان", "Libra"), ("العقرب", "Scorpio"), ("القوس", "Sagittarius"),
    ("الجدي", "Capricorn"), ("الدلو", "Aquarius"), ("الحوت", "Pisces"),
]
MANSIONS_28 = [
    "الشرطان", "البطين", "الثريا", "الدبران",
    "الهقعة", "الهقعة", "الذراع", "النثرة",
    "الطرف", "الجبهة", "الزبرة", "الصرفة",
    "العواء", "السماك", "الغفر", "الزبانا",
    "الإكليل", "القلب", "الشولة", "النعائم",
    "البلدة", "سعد الذابح", "سعد بلع", "سعد السعود",
    "سعد الأخبية", "الفرغ المقدم", "الفرغ المؤخر", "الرشاء",
]
MANSION_DEG = 360.0 / 28.0
LOCAL_TZ_OFFSET = 3
PLANET_NAMES = {
    "Sun": "الشمس", "Mercury": "عطارد", "Venus": "الزهرة",
    "Mars": "المريخ", "Jupiter": "المشتري", "Saturn": "زحل",
    "Uranus": "أورانوس", "Neptune": "نبتون", "Pluto": "بلوتو",
}

# --- Data loading ---
ZET_MANSION_CACHE = None

def parse_nakshatra_file(filepath, timezone_offset=3):
    data = {}
    if not os.path.exists(filepath):
        return data
    with open(filepath, 'r', encoding='cp1256', errors='replace') as f:
        for line in f:
            line = line.strip()
            if '>' not in line:
                continue
            parts = line.split('>')
            if len(parts) < 2:
                continue
            rest = parts[1].strip()
            sp = rest.find(' ')
            if sp < 0:
                continue
            num_str = rest[:sp].strip()
            if not num_str.isdigit():
                continue
            num = int(num_str)
            dp = parts[0].strip()
            dp = dp.replace("   ", " ").replace("  ", " ").strip()
            for fmt in ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                         "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]:
                try:
                    dt = datetime.strptime(dp, fmt)
                    break
                except ValueError:
                    continue
            else:
                continue
            dt -= timedelta(hours=timezone_offset)
            data[dt] = num
    return data

def load_zet_moon_data():
    mansions_by_year = {}
    pattern = os.path.join(DATA_DIR, "Nakshatras*.txt")
    for fpath in glob.glob(pattern):
        try:
            year_data = parse_nakshatra_file(fpath)
            for k, v in year_data.items():
                mansions_by_year[k.year] = mansions_by_year.get(k.year, {})
                mansions_by_year[k.year][k] = v
        except Exception:
            pass
    return mansions_by_year

def get_mansion_from_zet(dt):
    global ZET_MANSION_CACHE
    if ZET_MANSION_CACHE is None:
        ZET_MANSION_CACHE = load_zet_moon_data()
    year_data = ZET_MANSION_CACHE.get(dt.year)
    if not year_data:
        return None
    entries = sorted(year_data.items())
    if dt < entries[0][0]:
        return None
    result = entries[0][1]
    for edt, num in entries:
        if edt <= dt:
            result = num
        else:
            break
    return result

def get_all_zet_mansions_for_period(start, end):
    global ZET_MANSION_CACHE
    if ZET_MANSION_CACHE is None:
        ZET_MANSION_CACHE = load_zet_moon_data()
    results = []
    for yr in range(start.year, end.year + 1):
        year_data = ZET_MANSION_CACHE.get(yr)
        if not year_data:
            continue
        for edt, num in sorted(year_data.items()):
            if start <= edt <= end:
                results.append((edt, num))
    return sorted(results)

def get_mansion_bounds(dt):
    global ZET_MANSION_CACHE
    if ZET_MANSION_CACHE is None:
        ZET_MANSION_CACHE = load_zet_moon_data()
    year_data = ZET_MANSION_CACHE.get(dt.year)
    if not year_data:
        return None, None
    entries = sorted(year_data.items())
    entry = None
    for i, (edt, num) in enumerate(entries):
        if edt <= dt:
            entry = (edt, num)
        else:
            next_edt = edt
            return entry, next_edt
    return entry, None

# --- Astronomy helpers ---
def get_sidereal_position(tropical_longitude):
    ayanamsa = 21.0 + (datetime.now().year - 2000) * 0.013
    return normalize_angle(tropical_longitude - ayanamsa)

def normalize_angle(deg):
    return deg % 360.0

def get_zodiac_sign(longitude):
    deg = normalize_angle(longitude)
    sign_index = int(deg / 30)
    name = ZODIAC_SIGNS_AR[sign_index][0]
    degree_in_sign = deg - (sign_index * 30)
    return name, sign_index, degree_in_sign

def get_mansion_number(longitude):
    deg = normalize_angle(longitude)
    idx = int(deg / MANSION_DEG)
    if idx >= 28:
        idx = 27
    return idx + 1

def check_planet_retrograde(obs, date, planet_class):
    obs.date = date
    p = planet_class(obs)
    lon1 = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
    obs.date = date + timedelta(hours=24)
    p2 = planet_class(obs)
    lon2 = normalize_angle(get_ephem().Ecliptic(p2).lon * 180 / math.pi)
    diff = lon2 - lon1
    if diff > 180:
        diff -= 360
    elif diff < -180:
        diff += 360
    return diff < -0.001, get_sidereal_position(lon1)

def find_voc_period(obs, date):
    obs.date = date
    moon = get_ephem().Moon(obs)
    ml = normalize_angle(get_ephem().Ecliptic(moon).lon * 180 / math.pi)
    current_sign = int(ml / 30)
    planet_names = ["Sun", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"]
    bodies = [
        get_ephem().Sun(obs), get_ephem().Mercury(obs), get_ephem().Venus(obs),
        get_ephem().Mars(obs), get_ephem().Jupiter(obs), get_ephem().Saturn(obs),
    ]
    init_lons = {}
    for i, b in enumerate(bodies):
        init_lons[planet_names[i]] = normalize_angle(get_ephem().Ecliptic(b).lon * 180 / math.pi)
    orb = 2.0
    asp_deg = [0, 60, 90, 120, 180]

    def has_aspect(moon_lon):
        for name in planet_names:
            diff = abs(moon_lon - init_lons[name])
            if diff > 180: diff = 360 - diff
            for a in asp_deg:
                if abs(diff - a) <= orb:
                    return True, name, a
        return False, None, None

    voc_end = None
    for h in range(1, 192):
        cur = date + timedelta(hours=h)
        obs.date = cur
        m2 = get_ephem().Moon(obs)
        m2l = normalize_angle(get_ephem().Ecliptic(m2).lon * 180 / math.pi)
        if int(m2l / 30) != current_sign:
            voc_end = cur
            break
        ok, nm, asp = has_aspect(m2l)
        if ok:
            voc_end = cur
            break

    voc_start = None
    for h in range(1, 96):
        cur = date - timedelta(hours=h)
        obs.date = cur
        m2 = get_ephem().Moon(obs)
        m2l = normalize_angle(get_ephem().Ecliptic(m2).lon * 180 / math.pi)
        if int(m2l / 30) != current_sign:
            voc_start = cur
            break
        ok, nm, asp = has_aspect(m2l)
        if ok:
            voc_start = cur
            break

    cur_ok, cur_nm, cur_asp = has_aspect(ml)
    is_voc = not cur_ok
    aspects = []
    if cur_ok:
        aspects = [{"planet": cur_nm, "aspect": cur_asp}]

    return {
        "is_voc": is_voc,
        "aspects": aspects,
        "voc_start": voc_start.strftime("%Y-%m-%d %H:%M") if voc_start else None,
        "voc_end": voc_end.strftime("%Y-%m-%d %H:%M") if voc_end else None,
    }

def format_pos(lon):
    d = int(lon)
    m = abs(int((lon - d) * 60))
    s = abs((lon - d) * 60 - m) * 60
    return f"{d}°{m:02d}'{s:04.1f}\""

def format_dt_ar(dt):
    return f"{dt.year}-{dt.month:02d}-{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"

def planet_motion_diff(obs, d1, d2, planet_class):
    obs.date = d1
    p = planet_class(obs)
    lon1 = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
    obs.date = d2
    p = planet_class(obs)
    lon2 = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
    diff = lon2 - lon1
    if diff > 180: diff -= 360
    elif diff < -180: diff += 360
    return diff

def find_station_time(obs, transition_day, planet_class, retro_to_direct=True):
    if retro_to_direct:
        start_dt = datetime(transition_day.year, transition_day.month, transition_day.day) - timedelta(hours=18)
    else:
        start_dt = datetime(transition_day.year, transition_day.month, transition_day.day) - timedelta(hours=6)
    for minute in range(0, 48 * 60):
        dt1 = start_dt + timedelta(minutes=minute)
        dt2 = dt1 + timedelta(minutes=5)
        diff = planet_motion_diff(obs, dt1, dt2, planet_class)
        is_retro = diff < -0.001
        if retro_to_direct:
            if not is_retro:
                return dt1
        else:
            if is_retro:
                return dt1
    return transition_day

def find_retrograde_periods(obs, start, end, planet_class):
    obs.date = start
    p = planet_class(obs)
    lon1 = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
    was_retro = False
    retro_starts = []
    retro_ends = []
    current_start = None
    d = start
    while d <= end:
        obs.date = d
        p = planet_class(obs)
        lon = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
        diff = lon - lon1
        if diff > 180:
            diff -= 360
        elif diff < -180:
            diff += 360
        is_retro = diff < -0.001
        if is_retro and not was_retro:
            current_start = d
        elif not is_retro and was_retro and current_start:
            retro_starts.append(current_start)
            retro_ends.append(d)
            current_start = None
        lon1 = lon
        was_retro = is_retro
        d += timedelta(days=1)
    if was_retro and current_start:
        retro_starts.append(current_start)
        retro_ends.append(end)
    return retro_starts, retro_ends

def find_retrograde_end(obs, dt, planet_class):
    d = dt
    obs.date = d
    p = planet_class(obs)
    lon1 = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
    was_retro = True
    d += timedelta(days=1)
    for _ in range(400):
        obs.date = d
        p = planet_class(obs)
        lon = normalize_angle(get_ephem().Ecliptic(p).lon * 180 / math.pi)
        diff = lon - lon1
        if diff > 180: diff -= 360
        elif diff < -180: diff += 360
        is_retro = diff < -0.001
        if not is_retro and was_retro:
            return find_station_time(obs, d, planet_class, retro_to_direct=True)
        lon1 = lon
        was_retro = is_retro
        d += timedelta(days=1)
    return None

def calc_day(local_dt, obs):
    dt = local_dt - timedelta(hours=LOCAL_TZ_OFFSET)
    obs.date = dt
    moon = get_ephem().Moon(obs)
    moon_tropical = normalize_angle(get_ephem().Ecliptic(moon).lon * 180 / math.pi)
    moon_sidereal = get_sidereal_position(moon_tropical)
    venus_retro, venus_lon = check_planet_retrograde(obs, dt, get_ephem().Venus)
    merc_retro, merc_lon = check_planet_retrograde(obs, dt, get_ephem().Mercury)
    venus_sid = get_sidereal_position(venus_lon)
    merc_sid = get_sidereal_position(merc_lon)
    voc = find_voc_period(obs, dt)
    mn_sign, msi, mds = get_zodiac_sign(moon_tropical)
    mn_sign_s, msi_s, mds_s = get_zodiac_sign(moon_sidereal)
    zet_mansion = get_mansion_from_zet(local_dt)
    calc_mansion = get_mansion_number(moon_sidereal)
    v_sign, vi, vd = get_zodiac_sign(venus_sid)
    m_sign, mi, md = get_zodiac_sign(merc_sid)

    lines = []
    lines.append("=" * 58)
    lines.append(f"  التاريخ: {local_dt.strftime('%Y-%m-%d')}   الوقت: {local_dt.strftime('%H:%M')}   ت ع +3")
    lines.append("=" * 58)
    lines.append("")
    lines.append(">>> القمر (Moon)")
    lines.append(f"  الطول استوائي: {format_pos(moon_tropical)}")
    lines.append(f"  الطول فلكي:    {format_pos(moon_sidereal)}")
    lines.append(f"  البرج استوائي: {mn_sign}  {mds:.2f}°")
    lines.append(f"  البرج فلكي:    {mn_sign_s}  {mds_s:.2f}°")
    if zet_mansion:
        lines.append(f"  المنزلة (ZET):  {zet_mansion} - {MANSIONS_28[zet_mansion-1]}")
        lines.append(f"  المنزلة (حساب): {calc_mansion} - {MANSIONS_28[calc_mansion-1]}")
        entry, nxt = get_mansion_bounds(local_dt)
        if entry:
            lines.append(f"  دخول: {format_dt_ar(entry[0])}")
        if nxt:
            lines.append(f"  خروج: {format_dt_ar(nxt)}")
    else:
        lines.append(f"  المنزلة: {calc_mansion} - {MANSIONS_28[calc_mansion-1]}")
    lines.append("")
    lines.append(">>> الزهرة (Venus)")
    lines.append(f"  الطول فلكي: {format_pos(venus_sid)}")
    lines.append(f"  البرج فلكي: {v_sign}  {vd:.2f}°")
    if venus_retro:
        vr_end = find_retrograde_end(obs, dt, get_ephem().Venus)
        lines.append(f"  ** الزهرة في تراجع **")
        if vr_end:
            lines.append(f"  العودة للمباشر: {(vr_end + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append(f"  الزهرة مستقيمة")
    lines.append("")
    lines.append(">>> عطارد (Mercury)")
    lines.append(f"  الطول فلكي: {format_pos(merc_sid)}")
    lines.append(f"  البرج فلكي: {m_sign}  {md:.2f}°")
    if merc_retro:
        mr_end = find_retrograde_end(obs, dt, get_ephem().Mercury)
        lines.append(f"  ** عطارد في تراجع **")
        if mr_end:
            lines.append(f"  العودة للمباشر: {(mr_end + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append(f"  عطارد مستقيم")
    lines.append("")
    lines.append(">>> القمر خالي المسار (Void of Course)")
    if voc["is_voc"]:
        lines.append(f"  ** القمر خالي المسار **")
        if voc["voc_start"]:
            lines.append(f"  بداية الخلو: {voc['voc_start']}")
        if voc["voc_end"]:
            lines.append(f"  نهاية الخلو: {voc['voc_end']}")
    else:
        lines.append("  القمر غير خالي المسار")
        for asp in voc["aspects"]:
            lines.append(f"    - {asp['planet']}: {asp['aspect']}°")
    lines.append("")
    lines.append("-" * 58)
    issues = []
    if venus_retro:
        issues.append("الزهرة في تراجع - لا تبدأ بمشاريع مهمة")
    else:
        issues.append("الزهرة مستقيمة ✓")
    if merc_retro:
        issues.append("عطارد في تراجع - لا تبدأ عقود أو تواصل")
    else:
        issues.append("عطارد مستقيم ✓")
    if voc["is_voc"]:
        issues.append("القمر خالي المسار - تجنب البدء بمشاريع جديدة")
    else:
        issues.append("القمر غير خالي المسار ✓")
    lines.extend(issues)
    lines.append("-" * 58)
    return "\n".join(lines)

def calc_month(dt, obs):
    year = dt.year
    month = dt.month
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        end = datetime(year, month + 1, 1) - timedelta(seconds=1)
    month_name = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
                  "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"][month - 1]
    lines = []
    lines.append("=" * 58)
    lines.append(f"  التقرير الشهري: {month_name} {year}")
    lines.append("=" * 58)
    lines.append("")
    lines.append(">>> منازل القمر (Mansions)")
    zet_mansions = get_all_zet_mansions_for_period(start, end)
    if zet_mansions:
        lines.append("  (بيانات ZET الأصلية)")
        for i, (edt, num) in enumerate(zet_mansions):
            if i + 1 < len(zet_mansions):
                next_edt = zet_mansions[i+1][0]
                lines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → {format_dt_ar(next_edt)}")
            else:
                lines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → ...")
    else:
        lines.append("  (لا توجد بيانات ZET لهذا الشهر)")
    lines.append("")
    lines.append(">>> الكواكب الراجعة (Retrogrades)")
    for pname, pclass, arname in [("Venus", get_ephem().Venus, "الزهرة"),
                                    ("Mercury", get_ephem().Mercury, "عطارد")]:
        starts, ends = find_retrograde_periods(obs, start, end, pclass)
        if starts:
            lines.append(f"  {arname} في تراجع:")
            for s, e in zip(starts, ends):
                st_time = find_station_time(obs, s, pclass, retro_to_direct=False)
                rt_time = find_station_time(obs, e, pclass, retro_to_direct=True)
                lines.append(f"    بداية: {(st_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"    عودة:  {(rt_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"    مدة:   {(e-s).days} يوم")
        else:
            lines.append(f"  {arname} مستقيم طوال الشهر")
    lines.append("")
    lines.append("-" * 58)
    lines.append("انتهى التقرير الشهري")
    lines.append("-" * 58)
    return "\n".join(lines)

def calc_year(dt, obs):
    year = dt.year
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31, 23, 59)
    lines = []
    lines.append("=" * 58)
    lines.append(f"  التقرير السنوي: {year}")
    lines.append("=" * 58)
    lines.append("")
    lines.append(">>> منازل القمر (Mansions)")
    zet_mansions = get_all_zet_mansions_for_period(start, end)
    if zet_mansions:
        rlines = []
        for i, (edt, num) in enumerate(zet_mansions):
            if i + 1 < len(zet_mansions):
                next_edt = zet_mansions[i+1][0]
                rlines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → {format_dt_ar(next_edt)}")
            else:
                rlines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → ...")
        lines.append(f"  (بيانات ZET - {len(rlines)} حدث)")
        lines.extend(rlines[:40])
        if len(rlines) > 40:
            lines.append(f"  ... و{len(rlines)-40} حدث آخر")
    else:
        lines.append("  (بيانات ZET غير متوفرة لهذه السنة)")
    lines.append("")
    lines.append(">>> الكواكب الراجعة (Retrogrades)")
    for pname, pclass, arname in [("Venus", get_ephem().Venus, "الزهرة"),
                                    ("Mercury", get_ephem().Mercury, "عطارد")]:
        starts, ends = find_retrograde_periods(obs, start, end, pclass)
        total_days = sum((e - s).days for s, e in zip(starts, ends))
        if starts:
            lines.append(f"  {arname} - إجمالي التراجع {total_days} يوم")
            for s, e in zip(starts, ends):
                st_time = find_station_time(obs, s, pclass, retro_to_direct=False)
                rt_time = find_station_time(obs, e, pclass, retro_to_direct=True)
                lines.append(f"    ┌ بداية التراجع: {(st_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"    └ العودة للمباشر: {(rt_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"     المدة: {(e-s).days} يوم")
        else:
            lines.append(f"  {arname} مستقيم طوال السنة")
    lines.append("")
    lines.append("-" * 58)
    lines.append("انتهى التقرير السنوي")
    lines.append("-" * 58)
    return "\n".join(lines)

def calc_custom(start_d, end_d, obs):
    lines = []
    lines.append("=" * 58)
    lines.append(f"  تقرير مخصص: {start_d.strftime('%Y-%m-%d')} → {end_d.strftime('%Y-%m-%d')}")
    lines.append("=" * 58)
    lines.append("")
    lines.append(">>> منازل القمر (Mansions)")
    zet_mansions = get_all_zet_mansions_for_period(start_d, end_d)
    if zet_mansions:
        rlines = []
        for i, (edt, num) in enumerate(zet_mansions):
            if i + 1 < len(zet_mansions):
                next_edt = zet_mansions[i+1][0]
                rlines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → {format_dt_ar(next_edt)}")
            else:
                rlines.append(f"  {num} {MANSIONS_28[num-1]}: {format_dt_ar(edt)} → ...")
        lines.append(f"  (بيانات ZET - {len(rlines)} حدث)")
        lines.extend(rlines[:60])
        if len(rlines) > 60:
            lines.append(f"  ... و{len(rlines)-60} حدث آخر")
    else:
        lines.append("  (بيانات ZET غير متوفرة لهذه الفترة)")
    lines.append("")
    lines.append(">>> الكواكب الراجعة (Retrogrades)")
    utc_start = start_d - timedelta(hours=LOCAL_TZ_OFFSET)
    utc_end = end_d + timedelta(hours=23, minutes=59) - timedelta(hours=LOCAL_TZ_OFFSET)
    for pname, pclass, arname in [("Venus", get_ephem().Venus, "الزهرة"),
                                    ("Mercury", get_ephem().Mercury, "عطارد")]:
        starts, ends = find_retrograde_periods(obs, utc_start, utc_end, pclass)
        total_days = sum((e - s).days for s, e in zip(starts, ends))
        if starts:
            lines.append(f"  {arname} - إجمالي التراجع {total_days} يوم")
            for s, e in zip(starts, ends):
                st_time = find_station_time(obs, s, pclass, retro_to_direct=False)
                rt_time = find_station_time(obs, e, pclass, retro_to_direct=True)
                lines.append(f"    ┌ بداية التراجع: {(st_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"    └ العودة للمباشر: {(rt_time + timedelta(hours=LOCAL_TZ_OFFSET)).strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"     المدة: {(e-s).days} يوم")
        else:
            lines.append(f"  {arname} مستقيم طوال الفترة")
    lines.append("")
    lines.append("-" * 58)
    lines.append("انتهى التقرير المخصص")
    lines.append("-" * 58)
    return "\n".join(lines)

def calc(date_str, time_str, mode="يومي", end_date_str=None):
    for fmt in ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"]:
        try:
            d = datetime.strptime(f"{date_str} {time_str}", fmt)
            break
        except ValueError:
            continue
    else:
        return "خطأ: صيغة التاريخ غير صحيحة\nاستخدم YYYY-MM-DD HH:MM"
    obs = get_ephem().Observer()
    obs.lat = "25.3667"
    obs.lon = "49.5667"
    obs.elevation = 0
    dt = d - timedelta(hours=LOCAL_TZ_OFFSET)
    if mode == "يومي":
        return calc_day(d, obs)
    elif mode == "شهري":
        return calc_month(dt, obs)
    elif mode == "سنوي":
        return calc_year(dt, obs)
    elif mode == "مخصص":
        if not end_date_str:
            return "خطأ: الرجاء إدخال تاريخ النهاية"
        try:
            end_d = datetime.strptime(end_date_str, "%Y-%m-%d")
        except ValueError:
            return "خطأ: صيغة تاريخ النهاية غير صحيحة\nاستخدم YYYY-MM-DD"
        if end_d < d:
            return "خطأ: تاريخ النهاية يجب أن يكون بعد تاريخ البداية"
        return calc_custom(d, end_d, obs)
    return "خطأ: وضع غير معروف"

# --- Flask Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/calculate", methods=["POST"])
def calculate():
    data = request.get_json()
    try:
        result = calc(
            data.get("date", ""),
            data.get("time", "12:00"),
            data.get("mode", "يومي"),
            data.get("end_date", ""),
        )
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
