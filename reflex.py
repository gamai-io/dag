import re
import unicodedata

_MAP = {"č": "c", "ć": "c", "ž": "z", "š": "s", "đ": "d",
        "Č": "c", "Ć": "c", "Ž": "z", "Š": "s", "Đ": "d"}


def translit(s):
    out = "".join(_MAP.get(ch, ch) for ch in str(s))
    return unicodedata.normalize("NFKD", out).encode("ascii", "ignore").decode()


RISK_T1 = [
    r"rm\s+-rf\s+/(?!\w)", r"rm\s+-rf\s+~", r"rm\s+-rf\s+\*",
    r"format\s+[a-z]:", r"mkfs", r"dd\s+if=.*of=/dev/",
    r"drop\s+table", r"drop\s+database", r"truncate\s+table",
    r"\bshutdown\b", r"\breboot\b", r"obrisi sve", r"izbrisi bazu", r"izbrisi disk",
]
RISK_T2 = [
    r"obrisi fajl", r"obrisi folder", r"obrisi log", r"delete file",
    r"\brm\b", r"\bdrop\b", r"root pristup bez", r"ugasi server",
    r"killall", r"pkill\s+-9", r"chmod\s+777", r"chown\s+-r",
]

SQL_STRONG = re.compile(r"\b(select|upit|konto|sifmat|matbroj|jib|sql)\b|baza podataka")
SQL_COUNT = re.compile(r"\bkoliko\b.{0,30}\b(kupac|kupci|artikal|artikli|zalih|racun|faktur|narudzb)")
NUM = re.compile(r"\b\d{4,}\b")

SKILL_MAP = [
    (r"ponuda|ponudu|ponudi|predracun", "quote-workflow"),
    (r"\bpdv\b", "vat-check"),
    (r"\bseo\b|\baeo\b", "seo"),
    (r"kupac|kupca|kupci|kupce", "customer-search"),
    (r"zaliha|zalihe|stanje|artikal|artikli", "stock-check"),
    (r"postis|posiljk|posiljka", "logistics"),
    (r"whatsapp", "messaging"),
    (r"wordpress|cms", "cms"),
    (r"racun|faktur|otpremnic", "accounting"),
    (r"self.?heal|watchdog|servis.*pao|pao.*servis|pale servise|digni servis|"
     r"restart.*servis|servis.*(restart|down)|health.*servis", "self-healing"),
    (r"tunel|tunnel|reverse tunel|keepalive|auto.?reconnect", "tunnel-stability"),
    (r"nebula|multi.?channel|multi.?kanal|kanali po masini|prsten nadzora|"
     r"fallback kanal", "multichannel"),
    (r"tehnicar|tehnicar|serviser|skeniraj flotu|flota.*(zdrav|pad|pao)|"
     r"pokvareno|nesto ne radi", "self-healing"),
]

HC_VERB = re.compile(r"\b(otvori|pokreni|klikni|klik|ukucaj|napisi|upisi|pritisni|"
                     r"zatvori|aktiviraj|prebaci)\b")
HC_HOST = re.compile(r"\b(na|u|preko|na racunaru|na masini|na ekranu)\b")
HC_PROG = re.compile(r"\b(notepad|word|excel|paint|browser|edge|chrome|winamp|vlc|"
                     r"spotify|explorer|cmd|powershell|calculator|calc|firefox)\b")

FAST_PATTERNS = [
    (r"^(zdravo|cao|hello|hi|hej)\b", "Pozdrav."),
    (r"koje su baze|koje baze|lista baza", "Databases: read-only access available."),
    (r"koji si model|ko si ti", "I am a DAG planner."),
]

DOMAIN_KEYWORDS = {
    "sql": ["kupac", "konto", "sifra", "zaliha", "cijena", "baza", "upit", "artikal", "stanje"],
    "hc": ["otvori", "klikni", "ukucaj", "ekran", "prozor", "pokreni", "notepad"],
    "prodaja": ["ponuda", "prodaj", "faktura", "marza", "cijenovnik", "narudzba"],
    "marketing": ["seo", "aeo", "kampanja", "reklama", "ads", "blog", "objava", "tekst"],
    "code": ["kod", "funkcija", "python", "skripta", "bug", "api", "algoritam"],
    "istrazivanje": ["istrazi", "analiziraj", "uporedi", "dokazi", "izvor", "trend", "provjeri"],
    "sigurnost": ["sigurnost", "leak", "rizik", "privatnost", "sken", "port", "harden"],
    "logistika": ["dostava", "posiljka", "transport", "carina", "postis", "kurir"],
    "opste": [],
}


def risk_score(t):
    score = 0.0
    for p in RISK_T1:
        if re.search(p, t):
            score = max(score, 0.9)
    for p in RISK_T2:
        if re.search(p, t):
            score = max(score, 0.7)
    return score


def _is_sql(t):
    return bool(SQL_STRONG.search(t) or SQL_COUNT.search(t) or NUM.search(t))


def _is_hc(t):
    if HC_PROG.search(t):
        return True
    return bool(HC_VERB.search(t) and HC_HOST.search(t))


def _skill(t):
    for pat, name in SKILL_MAP:
        if re.search(pat, t):
            return name
    return None


def detect_domain(t):
    best, bestn = "opste", 0
    for d, kws in DOMAIN_KEYWORDS.items():
        n = sum(1 for k in kws if k in t)
        if n > bestn:
            best, bestn = d, n
    return best


def _r(action, domain, confidence, reason, payload=None):
    return {"action": action, "domain": domain, "confidence": confidence,
            "reason": reason, "payload": payload}


def reflex(text, ctx=None):
    t = translit(str(text).lower())
    rs = risk_score(t)
    if rs >= 0.9:
        return _r("block", "risk", rs, "T1 katastrofalno",
                  payload="Ne mogu izvrsiti opasnu/razornu akciju.")
    if rs >= 0.7:
        return _r("block", "risk", rs, "T2 visoko rizicno",
                  payload="Ne mogu izvrsiti bez izricitog odobrenja.")
    # MEDIA_ROUTE_V1 (27.09.2026): medijski/kreativni zadaci -> media/browser (ne SQL!)
    if re.search(r'\b(plakat|poster|grafika|slika|video|spot|reklama|kampanj|logo|banner|story|objava|pjesma|muzika|audio|crtez|dizajn)\b', t):
        return _r("media", "marketing", 0.85, "kreativni/medijski zadatak")
    if _is_sql(t):
        return _r("sql", "sql", 0.95, "SQL/konto/sifra detektovan")
    sk = _skill(t)
    if sk:
        return _r("skill", "skill", 0.90, sk, payload=sk)
    if _is_hc(t):
        return _r("hc", "hc", 0.90, "HC komanda detektovana")
    for pat, msg in FAST_PATTERNS:
        if re.search(pat, t):
            return _r("fastpath", "opste", 0.99, "fastpath", payload=msg)
    return _r("ai", detect_domain(t), 0.50, "nema deterministicke rute")
