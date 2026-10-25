#!/usr/bin/env python3
"""MarkFlow Draft: cut a raw talking-head recording by its transcript (and script, if any).

Input:  SRT of the raw recording (or a word-level JSON), optionally the script text (.txt/.md/.docx).
Output: <name>.xml  - Premiere Pro sequence (File > Import), FCP7 XML
        <name>.edl  - the same cut as CMX3600 EDL (fallback)
        <name>.srt  - captions of the kept speech, on the new timeline
        <name>_report.txt - what was cut and why

Standard library only. No AI: text matching by rules.
"""
import argparse
import difflib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from xml.sax.saxutils import escape

VERSION = "0.1.1"
PING_URL = os.environ.get("MARKFLOW_PING_URL", "https://artemsytnik.com/api/ping")
FILLERS = {"э", "ээ", "эээ", "а", "аа", "ааа", "м", "мм", "ммм", "ну", "вот", "так", "эм", "хм", "ой"}


# ---------- reading ----------

def read_text(path):
    if path.lower().endswith(".docx"):
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
        return "\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)) for p in paras)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Не читается как текст: {path}")


def parse_tc(s):
    h, m, rest = s.strip().replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def read_srt(path):
    """Cues as (start, end, text)."""
    cues = []
    for block in re.split(r"\n\s*\n", read_text(path).replace("\r", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, l in enumerate(lines):
            if "-->" in l:
                a, b = l.split("-->")
                text = " ".join(lines[i + 1:]).strip()
                text = re.sub(r"<[^>]+>", "", text)
                if text:
                    cues.append((parse_tc(a), parse_tc(b.split()[0]), text))
                break
    cues.sort()
    return cues


def norm(w):
    w = w.lower().replace("ё", "е")
    return re.sub(r"[^\w]", "", w)


def stem(w):
    return norm(w)[:5]


def words_from_cues(cues):
    """Split cues into words; times inside a cue are spread by word length."""
    words = []
    for ci, (a, b, text) in enumerate(cues):
        toks = [t for t in text.split() if norm(t)]
        if not toks:
            continue
        total = sum(len(t) + 1 for t in toks)
        t = a
        for tok in toks:
            d = (b - a) * (len(tok) + 1) / total
            words.append({"w": tok, "s": t, "e": t + d, "cue": ci})
            t += d
    return words


def read_words_json(path):
    """[{"word"|"w"|"text", "start"|"s", "end"|"e"}] or {"words": [[w, s, e], ...]}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("words", data) if isinstance(data, dict) else data
    words = []
    for it in items:
        if isinstance(it, (list, tuple)):
            w, s, e = it[0], it[1], it[2]
        else:
            w = it.get("word", it.get("w", it.get("text", "")))
            s, e = it.get("start", it.get("s")), it.get("end", it.get("e"))
        if norm(str(w)):
            words.append({"w": str(w), "s": float(s), "e": float(e), "cue": None})
    words.sort(key=lambda x: x["s"])
    return words


def script_phrases(text):
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", " ", text)       # markdown links
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("\\", "")
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    phrases = []
    for p in parts:
        toks = [stem(t) for t in p.split() if norm(t)]
        if len(toks) >= 3:
            phrases.append({"text": p.strip(), "st": toks})
        elif toks and phrases:
            phrases[-1]["st"] += toks
            phrases[-1]["text"] += " " + p.strip()
    return phrases


# ---------- matching ----------

def ratio(a, b):
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def candidates(ph, T, index):
    """Windows of the transcript that look like this phrase: (start, end, score)."""
    p = ph["st"]
    L = len(p)
    starts = set()
    for s in p[:3]:
        starts.update(index.get(s, ()))
    found = []
    for i in sorted(starts):
        best = None
        for w in {max(2, round(L * 0.8)), L, round(L * 1.25)}:
            seg = T[i:i + w]
            if not seg:
                continue
            r = ratio(p, seg)
            if best is None or r > best[2]:
                best = (i, i + len(seg), r)
        if best and best[2] >= 0.5:
            found.append(best)
    found.sort(key=lambda c: -c[2])
    kept = []
    for c in found:
        if all(min(c[1], k[1]) - max(c[0], k[0]) < 0.5 * (c[1] - c[0]) for k in kept):
            kept.append(c)
    pset = set(p)
    out = []
    for i, j, r in kept:
        while j > i + 1 and T[j - 1] not in pset:
            j -= 1
        while i < j - 1 and T[i] not in pset:
            i += 1
        # restart inside the window: the last place where the phrase begins again wins
        head = p[:3]
        for k in range(j - len(head), i, -1):
            if T[k:k + len(head)] == head:
                i = k
                break
        out.append((i, j, r))
    return out


def choose(phrases, T):
    """One take per phrase, in order, preferring the last good take (DP)."""
    index = {}
    for k, s in enumerate(T):
        index.setdefault(s, []).append(k)
    states = []  # (end, total, prev_state, phrase_idx, cand)
    for pi, ph in enumerate(phrases):
        cs = candidates(ph, T, index)
        if not cs:
            continue
        top = max(c[2] for c in cs)
        good = [c for c in cs if c[2] >= top - 0.12]
        latest = max(good, key=lambda c: c[0])
        new = []
        for c in cs:
            gain = c[2] + (0.15 if c is latest else 0.0)
            best_prev, best_total = None, 0.0
            for si, st in enumerate(states):
                if st[0] <= c[0] + 1 and st[1] > best_total:
                    best_prev, best_total = si, st[1]
            new.append((c[1], best_total + gain, best_prev, pi, c))
        states.extend(new)
    if not states:
        return []
    si = max(range(len(states)), key=lambda k: states[k][1])
    chain = []
    while si is not None:
        st = states[si]
        chain.append((st[3], st[4]))
        si = st[2]
    return chain[::-1]


def keep_by_script(words, phrases):
    T = [stem(w["w"]) for w in words]
    chain = choose(phrases, T)
    keep = [None] * len(words)
    heads = set(tuple(ph["st"][:2]) for ph in phrases)
    for pi, (i, j, r) in chain:
        for k in range(i, j):
            keep[k] = "script"
    # a phrase often ends in words the script writes differently (numbers, names): take up to
    # 4 more words if the speech runs on without a pause and does not start a phrase again
    for pi, (i, j, r) in chain:
        k = j
        while (k < len(words) and k - j < 4 and keep[k] is None
               and words[k]["s"] - words[k - 1]["e"] < 0.25 and tuple(T[k:k + 2]) not in heads
               and T[k] not in T[k + 1:k + 16]):  # a word said again soon belongs to a retake
            keep[k] = "script"
            k += 1
    # speech that is not a take of any script line: keep it, it may be an improvisation
    known = set(s for ph in phrases for s in ph["st"])
    k = 0
    while k < len(words):
        if keep[k] is None:
            m = k
            while m < len(words) and keep[m] is None:
                m += 1
            run = [T[x] for x in range(k, m) if T[x] not in FILLERS]
            if len(run) >= 6 and sum(1 for s in run if s not in known) / len(run) > 0.5:
                for x in range(k, m):
                    keep[x] = "improv"
            k = m
        else:
            k += 1
    return keep, len(chain)


def utterances(words, cues):
    """Groups of words: SRT cues, or runs split by pauses."""
    groups, cur = [], []
    for k, w in enumerate(words):
        if cur:
            prev = words[cur[-1]]
            split = (w["cue"] != prev["cue"]) if cues else (w["s"] - prev["e"] > 0.6)
            if split:
                groups.append(cur)
                cur = []
        cur.append(k)
    if cur:
        groups.append(cur)
    return groups


def keep_without_script(words, cues):
    """No script: a run of words that starts again a bit later is a failed take; keep the last one."""
    T = [stem(w["w"]) for w in words]
    keep = ["speech"] * len(words)
    n, look, head = len(T), 60, 3
    j = 1
    while j < n - head:
        restart = None
        if not all(s in FILLERS for s in T[j:j + head]):
            for i in range(max(0, j - look), j):
                if keep[i] and T[i:i + head] == T[j:j + head]:
                    span = j - i
                    if ratio(T[i:j], T[j:j + span]) >= 0.6:
                        restart = i
                        break
        if restart is not None:
            for k in range(restart, j):
                keep[k] = None
        j += 1
    for g in utterances(words, cues):
        if all(T[k] in FILLERS for k in g):
            for k in g:
                keep[k] = None
    return keep, None


# ---------- ranges ----------

def kept_ranges(words, keep, cues, pad_in, pad_out, max_pause):
    ranges = []
    for k, w in enumerate(words):
        if not keep[k]:
            continue
        s, e = w["s"], w["e"]
        if cues and w["cue"] is not None:
            c = w["cue"]
            first = k == 0 or words[k - 1]["cue"] != c
            last = k == len(words) - 1 or words[k + 1]["cue"] != c
            if first:
                s = cues[c][0]
            if last:
                e = cues[c][1]
        if ranges and keep[k - 1] and s - ranges[-1][1] <= max_pause:
            ranges[-1][1] = max(ranges[-1][1], e)
            ranges[-1][3].append(k)
        else:
            ranges.append([s, e, keep[k], [k]])
    out = []
    for s, e, why, ks in ranges:
        s, e = max(0.0, s - pad_in), e + pad_out
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
            out[-1][3] += ks
        else:
            out.append([s, e, why, ks])
    return out


# ---------- writing ----------

def fr(t, fps):
    return int(round(t * fps))


def tc(frames, fps):
    f = frames % fps
    s = frames // fps
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}:{f:02d}"


def srt_tc(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def path_url(media):
    p = media.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p):
        return "file://localhost/" + urllib.parse.quote(p, safe="/:")
    if p.startswith("/"):
        return "file://localhost" + urllib.parse.quote(p, safe="/")
    return urllib.parse.quote(p, safe="/")


def write_xml(path, ranges, media, fps, src_dur, width, height, markers, src_w=None, src_h=None):
    name = os.path.basename(media.replace("\\", "/"))
    rate = f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"
    srcf = fr(src_dur, fps)
    clips, pos = [], 0
    for s, e, why, ks in ranges:
        a, b = fr(s, fps), fr(e, fps)
        if b <= a:
            continue
        clips.append((pos, pos + b - a, a, b))
        pos += b - a
    file_full = (
        f'<file id="file-1"><name>{escape(name)}</name><pathurl>{escape(path_url(media))}</pathurl>{rate}'
        f"<duration>{srcf}</duration><timecode>{rate}<string>00:00:00:00</string><frame>0</frame>"
        f"<displayformat>NDF</displayformat></timecode><media><video><samplecharacteristics>{rate}"
        f"<width>{src_w or width}</width><height>{src_h or height}</height><pixelaspectratio>square</pixelaspectratio>"
        f"</samplecharacteristics></video><audio><samplecharacteristics><depth>16</depth>"
        f"<samplerate>48000</samplerate></samplecharacteristics><channelcount>2</channelcount></audio></media></file>"
    )

    fit = ""
    if src_w and src_h and (src_w, src_h) != (width, height):
        # a 4K source in a 1080 sequence: scale to fit, as «Scale to frame size» would
        scale = round(min(width / src_w, height / src_h) * 100, 3)
        fit = (
            "<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>"
            "<effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>scale</parameterid>"
            f"<name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale}</value></parameter>"
            "</effect></filter>"
        )

    def item(kind, n, ch, c, first):
        st, en, a, b = c
        fid = file_full if first else '<file id="file-1"/>'
        links = (
            f"<link><linkclipref>clipitem-v{n}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{n}</clipindex></link>"
            f"<link><linkclipref>clipitem-a{n}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{n}</clipindex><groupindex>1</groupindex></link>"
            f"<link><linkclipref>clipitem-b{n}</linkclipref><mediatype>audio</mediatype><trackindex>2</trackindex><clipindex>{n}</clipindex><groupindex>1</groupindex></link>"
        )
        src = "" if kind == "v" else f"<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex></sourcetrack>"
        return (
            f'<clipitem id="clipitem-{kind}{n}"><name>{escape(name)}</name><enabled>TRUE</enabled>'
            f"<duration>{srcf}</duration>{rate}<start>{st}</start><end>{en}</end><in>{a}</in><out>{b}</out>"
            f"{fid}{src}{fit if kind == 'v' else ''}{links}</clipitem>"
        )

    v = "".join(item("v", n + 1, 0, c, n == 0) for n, c in enumerate(clips))
    a1 = "".join(item("a", n + 1, 1, c, False) for n, c in enumerate(clips))
    a2 = "".join(item("b", n + 1, 2, c, False) for n, c in enumerate(clips))
    mk = "".join(
        f"<marker><name>{escape(m[1])}</name><comment>{escape(m[2])}</comment><in>{m[0]}</in><out>-1</out></marker>"
        for m in markers
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="sequence-1"><name>MarkFlow черновик</name><duration>{pos}</duration>{rate}'
        f"<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>"
        f"<media><video><format><samplecharacteristics>{rate}<width>{width}</width><height>{height}</height>"
        f"<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{v}</track></video>"
        f"<audio><numOutputChannels>2</numOutputChannels><format><samplecharacteristics><depth>16</depth>"
        f"<samplerate>48000</samplerate></samplecharacteristics></format>"
        f"<track>{a1}<outputchannelindex>1</outputchannelindex></track>"
        f"<track>{a2}<outputchannelindex>2</outputchannelindex></track></audio></media>{mk}</sequence></xmeml>\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    return clips


def write_edl(path, clips, media, fps):
    name = os.path.basename(media.replace("\\", "/"))
    lines = ["TITLE: MarkFlow draft", "FCM: NON-DROP FRAME", ""]
    for n, (st, en, a, b) in enumerate(clips, 1):
        lines.append(f"{n:03d}  AX       AA/V  C        {tc(a, fps)} {tc(b, fps)} {tc(st, fps)} {tc(en, fps)}")
        lines.append(f"* FROM CLIP NAME: {name}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_outputs(base, words, keep, ranges, clips, cues, fps, removed_report, phrases_total, phrases_found):
    # captions on the new timeline: kept words regrouped
    out_srt, n = [], 0
    offset = []
    for (s, e, why, ks), (st, en, a, b) in zip([r for r in ranges if fr(r[1], fps) > fr(r[0], fps)], clips):
        shift = st / fps - a / fps
        group, gs = [], None
        for k in ks:
            w = words[k]
            if gs is None:
                gs = w["s"]
            group.append(w)
            if len(group) >= 8 or k == ks[-1] or (cues and w["cue"] != words[min(k + 1, len(words) - 1)]["cue"]):
                n += 1
                t0 = max(gs + shift, st / fps)
                t1 = min(group[-1]["e"] + shift, en / fps)
                out_srt.append(f"{n}\n{srt_tc(t0)} --> {srt_tc(max(t1, t0 + 0.3))}\n{' '.join(x['w'] for x in group)}\n")
                group, gs = [], None
    with open(base + ".srt", "w", encoding="utf-8") as f:
        f.write("\n".join(out_srt))
    with open(base + "_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(removed_report))


def removed_list(words, keep, fps):
    rep, k = [], 0
    while k < len(words):
        if keep[k]:
            k += 1
            continue
        m = k
        while m < len(words) and not keep[m]:
            m += 1
        text = " ".join(w["w"] for w in words[k:m])
        why = "паразиты/шум" if all(stem(w["w"]) in FILLERS for w in words[k:m]) else "дубль или оговорка"
        rep.append(f"{tc(fr(words[k]['s'], fps), fps)}  [{why}]  {text}")
        k = m
    return rep


def newer(a, b):
    """True if version a is newer than b ("0.2.0" > "0.1.9")."""
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except (AttributeError, ValueError):
        return False


def ping(payload):
    """Send the anonymous run numbers; the answer says which skill version is the latest."""
    if os.environ.get("MARKFLOW_NO_PING"):
        return None
    try:
        req = urllib.request.Request(
            PING_URL, data=json.dumps(payload).encode(), method="POST",
            # Cloudflare refuses the default Python-urllib agent (error 1010)
            headers={"content-type": "application/json", "user-agent": f"MarkFlowDraft/{VERSION}"},
        )
        latest = json.loads(urllib.request.urlopen(req, timeout=3).read() or b"{}").get("latest")
        return latest if newer(latest, VERSION) else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="MarkFlow Draft: черновик Premiere из расшифровки и сценария")
    ap.add_argument("--srt", help="SRT исходной записи (из Premiere: Text > Captions > Export SRT)")
    ap.add_argument("--words", help="или JSON со словами и таймингами")
    ap.add_argument("--script", help="сценарий: .txt, .md или .docx (только текст ведущего)")
    ap.add_argument("--media", required=True, help="имя или полный путь исходного видео")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--source-width", type=int, help="ширина исходника, если не равна секвенции (например 3840)")
    ap.add_argument("--source-height", type=int)
    ap.add_argument("--duration", type=float, help="длина исходника в секундах (если известна)")
    ap.add_argument("--pause", type=float, default=0.5, help="паузы длиннее этого режутся, с")
    ap.add_argument("--pad-in", type=float, default=0.10)
    ap.add_argument("--pad-out", type=float, default=0.20)
    ap.add_argument("--out", default="markflow_draft", help="имя выходных файлов без расширения")
    ap.add_argument("--no-ping", action="store_true", help="не отправлять анонимную статистику запуска")
    a = ap.parse_args()

    if not a.srt and not a.words:
        raise SystemExit("Нужен --srt или --words")
    cues = read_srt(a.srt) if a.srt else None
    words = words_from_cues(cues) if cues else read_words_json(a.words)
    if not words:
        raise SystemExit("В расшифровке нет слов")

    if a.script:
        phrases = script_phrases(read_text(a.script))
        if not phrases:
            raise SystemExit("В сценарии не нашлось фраз")
        keep, found = keep_by_script(words, phrases)
        mode = "script"
    else:
        phrases, found = [], None
        keep, _ = keep_without_script(words, cues)
        mode = "no_script"

    ranges = kept_ranges(words, keep, cues, a.pad_in, a.pad_out, a.pause)
    src_dur = a.duration or (max(w["e"] for w in words) + 5)
    markers, pos = [], 0
    for s, e, why, ks in ranges:
        if why == "improv":
            markers.append((pos, "Не по сценарию", " ".join(words[k]["w"] for k in ks)[:200]))
        pos += max(0, fr(e, a.fps) - fr(s, a.fps))
    clips = write_xml(a.out + ".xml", ranges, a.media, a.fps, src_dur, a.width, a.height, markers, a.source_width, a.source_height)
    write_edl(a.out + ".edl", clips, a.media, a.fps)
    report = removed_list(words, keep, a.fps)
    write_outputs(a.out, words, keep, ranges, clips, cues, a.fps, report, len(phrases), found)

    raw = max(w["e"] for w in words) - min(w["s"] for w in words)
    kept = sum(c[1] - c[0] for c in clips) / a.fps
    summary = {
        "mode": mode,
        "source_min": round(raw / 60, 1),
        "draft_min": round(kept / 60, 1),
        "clips": len(clips),
        "removed_pieces": len(report),
        "improv_kept": len(markers),
    }
    if phrases:
        summary["script_phrases"] = len(phrases)
        summary["phrases_found"] = found
    if not a.no_ping:
        latest = ping({"v": VERSION, **{k: summary[k] for k in ("mode", "source_min", "draft_min", "clips")}})
        if latest:
            summary["update"] = f"Вышла версия {latest}: скачайте заново на artemsytnik.com/markflow"
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
