"""Radar takibi: her gun 4 olcuden bir puan, puana gore "saglam profil" ve "zayif halka" listeleri,
listelere yeni eklenen hisselerin takip defterine yazilmasi ve 5 / 20 islem gunu sonra
ongorunun dogrulanip dogrulanmadiginin olculmesi.

Olculer (5 yillik BIST verisinde sonraki 20 gunle tutarli iliski gosterenler):
  beta60      endekse duyarlilik (dusuk olan daha iyi)
  kapanisYeri son 20 gunde kapanisin gun ici araliktaki yeri (yuksege yakin olan daha iyi)
  zirve       52 haftalik zirveye yakinlik (yakin olan daha iyi)
  mom         12 aylik getiri, son ay haric (yuksek olan daha iyi)

Ongoru tanimlari:
  saglam: hisse, esit agirlikli BIST 100 ortalamasini gecer
  zayif : hisse, esit agirlikli BIST 100 ortalamasinin gerisinde kalir
  bant  : 5. gun kapanisi, beklenen oynaklik bandinin (+/- 1.28 x gunluk oynaklik x kok 5) icinde kalir

Yalnizca depodaki fiyat serilerini kullanir; ag erisimi yoktur. Gecmise donuk kayitlar
yalnizca o gune kadar olan veriyle uretilir. Bu bir olcum aracidir; yatirim tavsiyesi degildir.
"""
import json, math, os, sys
from datetime import datetime, timezone, timedelta

SURUM = "1"
TR = timezone(timedelta(hours=3))
UFUK = (5, 20)
DILIM = 0.2              # en iyi / en kotu %20
BEKLEME = 20            # ayni hisse ayni listeye 20 islem gunu dolmadan yeniden yazilmaz
GERIYE_GUN = 60          # ilk kurulumda gecmise donuk doldurulan islem gunu
DEFTER_SINIR = 3000
BANT_Z = 1.2816          # %80 iki yonlu bant
OLCULER = [
    ("beta60", -1, "Endekse duyarlılık", "Son 60 günde hissenin endeks hareketine ne kadar abartılı tepki verdiği. 1 = endeksle aynı, 1,5 = endeksin bir buçuk katı. Düşük olan daha iyi puan alır."),
    ("kapanisYeri", 1, "Kapanış yeri", "Son 20 günde kapanışın gün içi en düşük–en yüksek aralığında nerede olduğu. 1 = hep zirvede kapanmış, 0 = hep dipte. Yüksek olan daha iyi puan alır."),
    ("zirve", 1, "52 hafta zirvesine yakınlık", "Fiyatın son bir yılın en yükseğine oranı. 1 = zirvede, 0,7 = zirvenin %30 altında. Yüksek olan daha iyi puan alır."),
    ("mom", 1, "12 aylık güç", "Son 12 ayın getirisi (son ay hariç). Yüksek olan daha iyi puan alır."),
]


# ------------------------------------------------------------------ veri

def duzelt(s):
    c = s["closes"]; n = 0
    for i in range(len(c) - 2, -1, -1):
        if not c[i] or c[i] <= 0 or not c[i + 1]:
            continue
        r = c[i + 1] / c[i]
        if r < 0.8 or r > 1.25:
            for k in ("closes", "opens", "highs", "lows"):
                a = s[k]
                for j in range(i + 1):
                    if a[j] is not None:
                        a[j] = a[j] * r
            n += 1
    return n


def birlestir(u, s):
    d0 = s["dates"][0]
    ud = u.get("dates") or []
    if not ud or ud[0] >= d0:
        return s
    j = next((k for k, d in enumerate(ud) if d >= d0), len(ud))
    oran = 1.0
    if j < len(ud) and ud[j] == d0 and u["closes"][j]:
        oran = s["closes"][0] / u["closes"][j]
    out = {"ticker": s.get("ticker"), "asof": s.get("asof")}
    for k in ("dates", "closes", "opens", "highs", "lows", "volumes"):
        onc = (u.get(k) or [])[:j]
        if k in ("closes", "opens", "highs", "lows"):
            onc = [x * oran if x is not None else None for x in onc]
        out[k] = onc + list(s.get(k) or [])
    return out


def ek_liste(kok):
    p = os.path.join(kok, "data/ek_liste.txt"); out = []
    if os.path.exists(p):
        for satir in open(p, encoding="utf-8"):
            k = satir.split("#")[0].strip().upper()
            if 3 <= len(k) <= 6 and k.isalnum() and k not in out:
                out.append(k)
    return out


def seans_tamamla(S, kok):
    """Gunluk seri geride kaldiysa seans ici veriden (data/intraday.json) eksik kapanislari ekler:
    onceki kapanis (oncekiKapanis) ve seans bittiyse (son bar 18:00 ya da sonrasi) gunun kapanisi."""
    p = os.path.join(kok, "data/intraday.json")
    if not os.path.exists(p):
        return None
    try:
        D = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    gun = D.get("asof"); rows = {r.get("ticker"): r for r in D.get("rows") or []}
    if not gun or not rows:
        return None
    try:
        g = datetime.strptime(gun, "%Y-%m-%d")
    except Exception:
        return None
    bitti = all((r.get("zaman") or "00:00") >= "18:00" for r in rows.values() if r.get("son"))
    eklenen = {"onceki": 0, "bugun": 0}
    onceki_gun = None
    for t, s in S.items():
        r = rows.get(t)
        if not r or not r.get("son"):
            continue
        son_tarih = s["dates"][-1]
        if son_tarih >= gun:
            continue
        # bir onceki islem gunu: hafta sonunu atla
        og = g - timedelta(days=1)
        while og.weekday() >= 5:
            og -= timedelta(days=1)
        ogs = og.strftime("%Y-%m-%d")
        if son_tarih < ogs and r.get("oncekiKapanis"):
            pc = float(r["oncekiKapanis"])
            for k, v in (("dates", ogs), ("closes", pc), ("opens", pc), ("highs", pc), ("lows", pc), ("volumes", 0)):
                s[k].append(v)
            eklenen["onceki"] += 1; onceki_gun = ogs
        if bitti:
            c = float(r["son"])
            for k, v in (("dates", gun), ("closes", c), ("opens", float(r.get("acilis") or c)),
                         ("highs", float(r.get("yuksek") or c)), ("lows", float(r.get("dusuk") or c)),
                         ("volumes", float(r.get("hacim") or 0))):
                s[k].append(v)
            eklenen["bugun"] += 1
    if not eklenen["onceki"] and not eklenen["bugun"]:
        return None
    return {"gun": gun if eklenen["bugun"] else None, "oncekiGun": onceki_gun, "hisse": max(eklenen.values()),
            "not": "Günlük kapanış verisi gecikti; eksik gün(ler) seans içi veriden (yaklaşık 15 dk gecikmeli) tamamlandı. Resmî kapanış gelince yerine o kullanılır."}


def yukle(kok):
    uni = json.load(open(os.path.join(kok, "data/universe.json"), encoding="utf-8"))
    kume = list(uni.get("bist100", []))
    S = {}
    for t in kume + [x for x in ek_liste(kok) if x not in kume]:
        p = os.path.join(kok, "data/series", t + ".json")
        if not os.path.exists(p):
            continue
        s = json.load(open(p, encoding="utf-8"))
        c = s.get("closes") or []
        if len(c) < 60 or not s.get("highs") or not s.get("lows"):
            continue
        s.setdefault("opens", list(c)); s.setdefault("volumes", [0] * len(c))
        pu = os.path.join(kok, "data/uzun", t + ".json")
        if os.path.exists(pu):
            try:
                s = birlestir(json.load(open(pu, encoding="utf-8")), s)
            except Exception:
                pass
        for k in ("closes", "opens", "highs", "lows", "volumes"):
            s[k] = list(s[k])
        s["dates"] = list(s["dates"])
        S[t] = s
    tam = seans_tamamla(S, kok)
    for s in S.values():
        duzelt(s)
    return set(kume), S, tam


# ------------------------------------------------------------------ panel ve olculer

class Panel:
    def __init__(self, S, kume):
        self.tk = sorted(S)
        self.kume = kume
        self.tarih = sorted({d for s in S.values() for d in s["dates"]})
        ti = {d: i for i, d in enumerate(self.tarih)}
        N = len(self.tarih)
        self.ti = ti
        self.C = {}; self.H = {}; self.L = {}
        for t, s in S.items():
            c = [None] * N; h = [None] * N; l = [None] * N
            for k, d in enumerate(s["dates"]):
                i = ti[d]
                c[i] = s["closes"][k]; h[i] = s["highs"][k]; l[i] = s["lows"][k]
            self.C[t] = c; self.H[t] = h; self.L[t] = l
        # gunluk getiriler
        self.R = {}
        for t in self.tk:
            c = self.C[t]; r = [None] * N
            for i in range(1, N):
                if c[i] and c[i - 1]:
                    r[i] = c[i] / c[i - 1] - 1
            self.R[t] = r
        # esit agirlikli BIST 100 gunluk getirisi ve seviyesi
        self.ew = [0.0] * N; self.E = [1.0] * N; self.ewn = [0] * N
        for i in range(1, N):
            v = [self.R[t][i] for t in self.tk if t in kume and self.R[t][i] is not None]
            self.ew[i] = sum(v) / len(v) if v else 0.0
            self.ewn[i] = len(v)
            self.E[i] = self.E[i - 1] * (1 + self.ew[i])
        self._olc()

    def _olc(self):
        N = len(self.tarih)
        self.O = {}
        ew = self.ew
        for t in self.tk:
            c, h, l, r = self.C[t], self.H[t], self.L[t], self.R[t]
            beta = [None] * N; ky = [None] * N; zr = [None] * N; mom = [None] * N; vol = [None] * N
            # kayan pencereler
            sx = sy = sxx = sxy = 0.0; n = 0; q = []
            kq = []; ks = 0.0
            vq = []; vs = vss = 0.0
            for i in range(N):
                # beta60
                if r[i] is not None:
                    x, y = ew[i], r[i]
                    q.append((x, y)); sx += x; sy += y; sxx += x * x; sxy += x * y; n += 1
                    if len(q) > 60:
                        x0, y0 = q.pop(0); sx -= x0; sy -= y0; sxx -= x0 * x0; sxy -= x0 * y0; n -= 1
                    if n >= 50:
                        vx = sxx / n - (sx / n) ** 2
                        if vx > 1e-12:
                            beta[i] = (sxy / n - sx / n * sy / n) / vx
                    # oynaklik20
                    vq.append(y); vs += y; vss += y * y
                    if len(vq) > 20:
                        y0 = vq.pop(0); vs -= y0; vss -= y0 * y0
                    if len(vq) >= 15:
                        m = vs / len(vq)
                        vol[i] = math.sqrt(max(vss / len(vq) - m * m, 0.0))
                # kapanis yeri 20
                if c[i] is not None and h[i] is not None and l[i] is not None:
                    a = (c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0.5
                    kq.append(a); ks += a
                    if len(kq) > 20:
                        ks -= kq.pop(0)
                    if len(kq) >= 15:
                        ky[i] = ks / len(kq)
                # 52 hafta zirvesine yakinlik ve 12-1 momentum
                if c[i] is not None and i >= 200:
                    w = [x for x in c[max(0, i - 251):i + 1] if x is not None]
                    if len(w) >= 200:
                        zr[i] = c[i] / max(w)
                    if i >= 252 and c[i - 21] and c[i - 252]:
                        mom[i] = c[i - 21] / c[i - 252] - 1
            self.O[t] = {"beta60": beta, "kapanisYeri": ky, "zirve": zr, "mom": mom, "vol": vol}

    def puanlar(self, i):
        """i gunu icin {hisse: puan(0-100)}; en az 3 olcu ve en az 40 hisse gerekir."""
        sira = {}
        for ad, isaret, _, _ in OLCULER:
            v = [(self.O[t][ad][i] * isaret, t) for t in self.tk
                 if self.C[t][i] is not None and self.O[t][ad][i] is not None]
            if len(v) < 40:
                continue
            v.sort()
            n = len(v); k = 0
            while k < n:
                j = k
                while j + 1 < n and v[j + 1][0] == v[k][0]:
                    j += 1
                rr = ((k + j) / 2) / (n - 1)
                for m in range(k, j + 1):
                    sira.setdefault(v[m][1], []).append(rr)
                k = j + 1
        p = {t: 100 * sum(a) / len(a) for t, a in sira.items() if len(a) >= 3}
        return p if len(p) >= 40 else {}

    def listeler(self, i):
        p = self.puanlar(i)
        if not p:
            return p, [], []
        s = sorted(p, key=lambda t: (-p[t], t))
        k = max(1, int(round(len(s) * DILIM)))
        return p, s[:k], s[-k:][::-1]

    def getiri(self, t, i, j):
        a, b = self.C[t][i], self.C[t][j]
        if not a or not b:
            return None
        return b / a - 1

    def ew_getiri(self, i, j):
        return self.E[j] / self.E[i] - 1


# ------------------------------------------------------------------ metin yardimcilari

def yz(x, d=1, isaret=True):
    """yuzde: +%1,2 / −%0,8"""
    if x is None:
        return "—"
    v = round(abs(x) * 100, d)
    s = ("%." + str(d) + "f") % v
    s = s.replace(".", ",")
    if not isaret:
        return "%" + s
    if v == 0:
        return "%" + s
    return ("+" if x > 0 else "−") + "%" + s


def pz(x, d=1, isaret=True):
    """puan farki: +1,2 puan (isaret=False: 1,2 puan)"""
    if x is None:
        return "—"
    v = round(abs(x) * 100, d)
    s = (("%." + str(d) + "f") % v).replace(".", ",")
    if v == 0:
        return "0 puan"
    if not isaret:
        return s + " puan"
    return ("+" if x > 0 else "−") + s + " puan"


LISTE_AD = {"saglam": "Sağlam profil", "zayif": "Zayıf halka"}
ONGORU = {
    "saglam": "Endeksin (eşit ağırlıklı BIST 100) önünde gitmesi beklenir.",
    "zayif": "Endeksin (eşit ağırlıklı BIST 100) gerisinde kalması beklenir.",
}


def yorum_yaz(k, h, taban):
    g = k["g%d" % h]
    ad = "Bir hafta" if h == 5 else "20 iş günü"
    tb = (taban.get(k["liste"]) or {}).get("g%d" % h) or {}
    oran = tb.get("dogru")
    if g["dogru"]:
        bas = "Öngörü doğrulandı."
    else:
        bas = "Öngörü doğrulanmadı."
    if k["liste"] == "zayif":
        orta = "%s sonunda %s %s değişti, endeks %s; hisse endeksin %s %s kaldı." % (
            ad, k["kod"], yz(g["getiri"]), yz(g["ew"]), pz(g["fark"], isaret=False), "gerisinde" if g["fark"] < 0 else "önünde")
    else:
        orta = "%s sonunda %s %s değişti, endeks %s; hisse endeksin %s %s gitti." % (
            ad, k["kod"], yz(g["getiri"]), yz(g["ew"]), pz(g["fark"], isaret=False), "önünde" if g["fark"] > 0 else "gerisinde")
    son = ""
    if oran is not None:
        if g["dogru"]:
            son = " Geçmiş 5 yılda bu listede öngörü 10 kaydın yaklaşık %d tanesinde doğrulanmıştı." % round(oran / 10)
        else:
            son = " Bu sürpriz değil: geçmiş 5 yılda bu listede 10 kaydın yaklaşık %d tanesinde öngörü doğrulanmamıştı." % round((100 - oran) / 10)
    if h == 5 and g.get("bantIci") is not None:
        son += " Fiyat hareketi beklenen ±\u2060%%%s oynaklık bandının %s." % (
            (("%.1f" % k["bant5"]).replace(".", ",")), "içinde kaldı" if g["bantIci"] else "dışına taştı")
    if g.get("puanSimdi") is not None:
        son += " Puanı %d → %d." % (round(k["puan"]), round(g["puanSimdi"]))
    return bas + " " + orta + son


# ------------------------------------------------------------------ defter

def kayit_yap(P, t, liste, i, p, kaynak):
    o = P.O[t]
    vol = o["vol"][i]
    return {
        "id": "%s:%s:%s" % (P.tarih[i], liste, t),
        "kod": t, "liste": liste, "tarih": P.tarih[i], "kaynak": kaynak,
        "fiyat": round(P.C[t][i], 4), "puan": round(p[t], 1),
        "olcu": {a: (None if o[a][i] is None else round(o[a][i], 3)) for a, _, _, _ in OLCULER},
        "oyn": None if vol is None else round(vol * 100, 2),
        "bant5": None if vol is None else round(BANT_Z * vol * math.sqrt(5) * 100, 1),
        "ongoru": ONGORU[liste],
    }


def degerlendir(P, k, taban, puan_cache):
    i = P.ti.get(k["tarih"])
    if i is None:
        return False
    deg = False
    N = len(P.tarih)
    for h in UFUK:
        a = "g%d" % h
        if k.get(a) or i + h >= N:
            continue
        g = P.getiri(k["kod"], i, i + h)
        if g is None:
            continue
        e = P.ew_getiri(i, i + h)
        fark = g - e
        dogru = fark > 0 if k["liste"] == "saglam" else fark < 0
        ps = puan_cache(i + h).get(k["kod"])
        r = {"tarih": P.tarih[i + h], "getiri": round(g, 4), "ew": round(e, 4), "fark": round(fark, 4),
             "dogru": dogru, "puanSimdi": None if ps is None else round(ps, 1)}
        if h == 5 and k.get("bant5") is not None:
            r["bantIci"] = abs(g) * 100 <= k["bant5"]
        k[a] = r
        k[a]["yorum"] = yorum_yaz(k, h, taban)
        deg = True
    return deg


def ara_durum(P, k, puan_son):
    i = P.ti.get(k["tarih"]); N = len(P.tarih)
    if i is None:
        return None
    j = N - 1
    g = P.getiri(k["kod"], i, j)
    if g is None:
        return None
    e = P.ew_getiri(i, j)
    return {"gun": j - i, "getiri": round(g, 4), "ew": round(e, 4), "fark": round(g - e, 4),
            "simdilikDogru": (g - e > 0) if k["liste"] == "saglam" else (g - e < 0),
            "puanSimdi": None if puan_son.get(k["kod"]) is None else round(puan_son[k["kod"]], 1)}


# ------------------------------------------------------------------ gecmis taban oranlari

def taban_oranlari(P, bas=260):
    """5 yillik gecmiste: her gun listeye YENI eklenen hisseler icin ongorulerin dogrulanma orani."""
    N = len(P.tarih)
    st = {l: {"g5": [0, 0, 0.0], "g20": [0, 0, 0.0]} for l in ("saglam", "zayif")}
    bant = [0, 0]
    rs = {"g5": [0, 0, 0], "g20": [0, 0, 0]}   # rastgele hisse: n, endeksi gecen, geride kalan
    yil = {}
    onceki = {"saglam": set(), "zayif": set()}
    sonk = {}                                  # (hisse, liste) -> son kayit gunu (20 gun bekleme)
    for i in range(bas, N - 5):
        p, sg, zy = P.listeler(i)
        if not p:
            onceki = {"saglam": set(), "zayif": set()}
            continue
        if i % 5 == 0:
            for t in p:
                for h in UFUK:
                    if i + h < N:
                        g = P.getiri(t, i, i + h)
                        if g is not None:
                            f = g - P.ew_getiri(i, i + h); r_ = rs["g%d" % h]
                            r_[0] += 1; r_[1] += f > 0; r_[2] += f < 0
        for liste, L in (("saglam", sg), ("zayif", zy)):
            yeni = [t for t in L if t not in onceki[liste] and i - sonk.get((t, liste), -99) >= BEKLEME]
            for t in yeni:
                sonk[(t, liste)] = i
                for h in UFUK:
                    if i + h >= N:
                        continue
                    g = P.getiri(t, i, i + h)
                    if g is None:
                        continue
                    f = g - P.ew_getiri(i, i + h)
                    d = f > 0 if liste == "saglam" else f < 0
                    s = st[liste]["g%d" % h]
                    s[0] += 1; s[1] += d; s[2] += f
                    if h == 20:
                        y = P.tarih[i][:4]
                        yy = yil.setdefault(y, {"saglam": [0, 0], "zayif": [0, 0]})
                        yy[liste][0] += 1; yy[liste][1] += d
                    if h == 5:
                        v = P.O[t]["vol"][i]
                        if v is not None:
                            bant[0] += 1; bant[1] += abs(g) <= BANT_Z * v * math.sqrt(5)
            onceki[liste] = set(L)
    out = {}
    for l in st:
        out[l] = {}
        for h in st[l]:
            n, d, f = st[l][h]
            out[l][h] = {"n": n, "dogru": round(100 * d / n) if n else None, "ortFark": round(f / n, 4) if n else None}
    out["rastgele"] = {h: {"n": v[0], "onde": round(100 * v[1] / v[0]) if v[0] else None,
                           "geride": round(100 * v[2] / v[0]) if v[0] else None} for h, v in rs.items()}
    out["bant"] = {"n": bant[0], "ic": round(100 * bant[1] / bant[0]) if bant[0] else None}
    out["yillar"] = {y: {l: (round(100 * v[l][1] / v[l][0]) if v[l][0] else None) for l in v} for y, v in sorted(yil.items())}
    out["donem"] = "%s – %s" % (P.tarih[bas], P.tarih[N - 1])
    return out


# ------------------------------------------------------------------ karne

def karne(kayitlar, kaynak=None):
    out = {}
    for l in ("saglam", "zayif"):
        out[l] = {}
        for h in UFUK:
            v = [k["g%d" % h] for k in kayitlar if k["liste"] == l and k.get("g%d" % h)
                 and (kaynak is None or k["kaynak"] == kaynak)]
            n = len(v)
            out[l]["g%d" % h] = {"n": n, "dogru": round(100 * sum(x["dogru"] for x in v) / n) if n else None,
                                 "ortFark": round(sum(x["fark"] for x in v) / n, 4) if n else None}
    b = [k["g5"]["bantIci"] for k in kayitlar if k.get("g5") and k["g5"].get("bantIci") is not None
         and (kaynak is None or k["kaynak"] == kaynak)]
    out["bant"] = {"n": len(b), "ic": round(100 * sum(b) / len(b)) if b else None}
    return out


def karne_yorum(kr, taban, baslik):
    sat = []
    for l in ("zayif", "saglam"):
        for h in UFUK:
            a = kr[l]["g%d" % h]; t = (taban.get(l) or {}).get("g%d" % h) or {}
            if not a["n"]:
                continue
            ad = "bir haftalık" if h == 5 else "20 günlük"
            rg = ((taban.get("rastgele") or {}).get("g%d" % h) or {}).get("onde" if l == "saglam" else "geride")
            s = "%s, %s: %d kaydın %d tanesinde öngörü doğrulandı (%%%d). 5 yıllık beklenti %%%s%s." % (
                LISTE_AD[l], ad, a["n"], round(a["n"] * a["dogru"] / 100), a["dogru"],
                "—" if t.get("dogru") is None else t["dogru"],
                "" if rg is None else ", rastgele seçilmiş bir hisse için %%%d" % rg)
            if a.get("ortFark") is not None and t.get("ortFark") is not None:
                s += " Ortalama fark %s (5 yıllık %s)." % (pz(a["ortFark"]), pz(t["ortFark"]))
            if a["n"] < 20:
                s += " Örnek henüz az; sonuç şansla kolayca değişebilir."
            elif t.get("dogru") is not None:
                fark = a["dogru"] - t["dogru"]
                if fark >= 8:
                    s += " Beklentinin üzerinde."
                elif fark <= -8:
                    s += " Beklentinin altında; bu dönemde ölçüler zayıf çalışıyor."
                else:
                    s += " Beklentiyle uyumlu."
            sat.append(s)
    if kr["bant"]["n"]:
        sat.append("Oynaklık bandı: %d kayıttan %d tanesinde (%%%d) hafta sonu fiyatı beklenen bandın içinde kaldı. 5 yıllık beklenti %%%s." % (
            kr["bant"]["n"], round(kr["bant"]["n"] * kr["bant"]["ic"] / 100), kr["bant"]["ic"], taban["bant"]["ic"]))
    return {"baslik": baslik, "satirlar": sat}


# ------------------------------------------------------------------ ana akis

def calistir(kok):
    kume, S, tam = yukle(kok)
    P = Panel(S, kume)
    N = len(P.tarih); son = N - 1
    _pc = {}

    def puan_cache(i):
        if i not in _pc:
            _pc[i] = P.puanlar(i)
        return _pc[i]

    taban = taban_oranlari(P)
    dy = os.path.join(kok, "data/radar_defter.json")
    D = None
    if os.path.exists(dy):
        try:
            D = json.load(open(dy, encoding="utf-8"))
        except Exception:
            D = None
    if not D or D.get("surum") != SURUM:
        D = {"surum": SURUM, "canliBaslangic": P.tarih[son], "kayitlar": []}
        # gecmise donuk doldurma: son GERIYE_GUN gun (bugun haric)
        onceki = None; sonk = {}
        for i in range(max(260, son - GERIYE_GUN - 1), son):
            p, sg, zy = P.listeler(i)
            _pc[i] = p
            if onceki is not None and p:
                for liste, L in (("saglam", sg), ("zayif", zy)):
                    for t in L:
                        if t not in onceki[liste] and i - sonk.get((t, liste), -99) >= BEKLEME:
                            D["kayitlar"].append(kayit_yap(P, t, liste, i, p, "geriye"))
                            sonk[(t, liste)] = i
            onceki = {"saglam": set(sg), "zayif": set(zy)} if p else None
    K = D["kayitlar"]
    var = {k["id"] for k in K}
    # bugun
    p, sg, zy = P.listeler(son); _pc[son] = p
    pi = son - 1
    p0, sg0, zy0 = P.listeler(pi); _pc[pi] = p0
    bugun_eklenen = []
    aktif = {(k["kod"], k["liste"]) for k in K
             if P.ti.get(k["tarih"]) is not None and son - P.ti[k["tarih"]] < BEKLEME}
    if p:
        for liste, L, L0 in (("saglam", sg, sg0), ("zayif", zy, zy0)):
            for t in L:
                if t in L0 or (t, liste) in aktif:
                    continue
                k = kayit_yap(P, t, liste, son, p, "canli")
                if k["id"] in var:
                    continue
                K.append(k); var.add(k["id"]); bugun_eklenen.append(k["id"])
    for k in K:
        degerlendir(P, k, taban, puan_cache)
    # bir onceki surumde seans verisiyle yazilmis kayitlar: tarih artik yoksa birak
    if len(K) > DEFTER_SINIR:
        K[:] = K[-DEFTER_SINIR:]

    # takip (20 gunu dolmamis) ve degerlendirilenler
    takip = []
    for k in K:
        if k.get("g20"):
            continue
        a = ara_durum(P, k, p)
        if a is None:
            continue
        x = dict(k); x["ara"] = a
        takip.append(x)
    takip.sort(key=lambda k: (k["tarih"], k["liste"], -k["puan"]), reverse=True)
    haftalik = [k for k in K if k.get("g5")]
    haftalik.sort(key=lambda k: (k["g5"]["tarih"], k["tarih"]), reverse=True)
    biten = [k for k in K if k.get("g20")]
    biten.sort(key=lambda k: k["g20"]["tarih"], reverse=True)

    def satir(t):
        o = P.O[t]; v = o["vol"][son]
        return {"kod": t, "puan": round(p[t], 1),
                "olcu": {a: (None if o[a][son] is None else round(o[a][son], 3)) for a, _, _, _ in OLCULER},
                "oyn": None if v is None else round(v * 100, 2),
                "bant5": None if v is None else round(BANT_Z * v * math.sqrt(5) * 100, 1),
                "bant20": None if v is None else round(BANT_Z * v * math.sqrt(20) * 100, 1),
                "yeni": t not in (sg0 if t in sg else zy0)}

    br = [P.C[t][son] > (sum(P.C[t][son - 49:son + 1]) / 50) for t in P.tk
          if t in kume and P.C[t][son] and all(P.C[t][son - 49:son + 1])]
    kr_c = karne(K, "canli"); kr_g = karne(K, "geriye"); kr_t = karne(K)
    out = {
        "surum": SURUM, "asof": P.tarih[son],
        "uretildi": datetime.now(TR).strftime("%Y-%m-%d %H:%M"),
        "seansTamamlama": tam, "kapsam": len(p),
        "piyasa": {"ewSon5": round(P.ew_getiri(son - 5, son), 4), "ewSon20": round(P.ew_getiri(son - 20, son), 4),
                   "genislik": round(100 * sum(br) / len(br)) if br else None},
        "olculer": [{"ad": a, "isaret": s, "baslik": b, "aciklama": c} for a, s, b, c in OLCULER],
        "ongoru": ONGORU,
        "listeler": {"saglam": [satir(t) for t in sg], "zayif": [satir(t) for t in zy]},
        "puanlar": {t: round(v, 1) for t, v in sorted(p.items())},
        "bugunEklenen": bugun_eklenen,
        "takip": takip[:150],
        "haftalik": haftalik[:60],
        "biten": biten[:40],
        "karne": {"canli": kr_c, "geriye": kr_g, "toplam": kr_t},
        "karneYorum": [karne_yorum(kr_c, taban, "Canlı kayıtlar (%s sonrası)" % D["canliBaslangic"]),
                       karne_yorum(kr_g, taban, "Geriye dönük kayıtlar (son %d gün, o günün verisiyle)" % GERIYE_GUN)],
        "taban": taban,
        "canliBaslangic": D["canliBaslangic"],
        "uyari": "Bu liste geçmiş fiyat davranışına dayalı bir ölçümdür; yatırım tavsiyesi değildir. Kararlar ve sonuçları size aittir.",
    }
    return out, D


def main():
    kok = sys.argv[1] if len(sys.argv) > 1 else "."
    out, D = calistir(kok)
    json.dump(out, open(os.path.join(kok, "data/radar.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(D, open(os.path.join(kok, "data/radar_defter.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("radar:", out["asof"], "kapsam", out["kapsam"], "| seans tamamlama:", out["seansTamamlama"] and out["seansTamamlama"]["hisse"])
    print("saglam:", [x["kod"] for x in out["listeler"]["saglam"]])
    print("zayif :", [x["kod"] for x in out["listeler"]["zayif"]])
    print("bugun eklenen:", out["bugunEklenen"])
    print("takipte:", len(out["takip"]), "| haftalik sonuc:", len(out["haftalik"]), "| biten:", len(out["biten"]))
    print("taban:", json.dumps(out["taban"], ensure_ascii=False))
    for kr in out["karneYorum"]:
        print("#", kr["baslik"])
        for s in kr["satirlar"]:
            print("  ", s)


if __name__ == "__main__":
    main()
