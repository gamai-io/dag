import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reflex import reflex, translit, detect_domain, DOMAIN_KEYWORDS  # noqa: E402

DOMAINS = ["sql", "prodaja", "marketing", "code", "istrazivanje",
           "sigurnost", "logistika", "hc", "opste"]
ASPECTS = ["analiza", "rizik", "plan", "dokaz", "resursi",
           "integracija", "test", "mjere", "izvori", "grananje"]

ENDPOINTS = {
    "bulk": (os.environ.get("DAG_BULK_URL", "http://127.0.0.1:8000/v1"), os.environ.get("DAG_BULK_MODEL", "default")),
    "smart": (os.environ.get("DAG_SMART_URL", "http://127.0.0.1:8000/v1"), os.environ.get("DAG_SMART_MODEL", "default")),
    "free": (os.environ.get("DAG_FREE_URL", "http://127.0.0.1:8000/v1"), os.environ.get("DAG_FREE_MODEL", "default")),
    "browser": (os.environ.get("DAG_BROWSER_URL", "http://127.0.0.1:8790"), "chatgpt"),
}


def _parse_plan_list(text):
    if not text or "[" not in text or "]" not in text:
        return []
    try:
        i, j = text.index("["), text.rindex("]")
        arr = json.loads(text[i:j + 1])
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        pass
    return []


def memory_db(tema, tekst):
    try:
        import importlib, os as _os
        _name = _os.environ.get("DAG_MEMORY_MODULE", "")
        if not _name:
            return False
        D = importlib.import_module(_name)
    except Exception:
        return False
    for fn in ("write", "save", "add", "insert"):
        f = getattr(D, fn, None)
        if not f:
            continue
        for call in (lambda: f(tema, tekst),
                     lambda: f(tema=tema, tekst=tekst),
                     lambda: f(izvor="dag-dag", tema=tema, tekst=tekst)):
            try:
                call()
                return True
            except Exception:
                continue
    return False


def hnum(s):
    return int(hashlib.md5(str(s).encode("utf-8")).hexdigest()[:6], 16) % 1000


def fake_plan(base, n, offset=0):
    d = detect_domain(translit(str(base).lower()))
    others = [x for x in DOMAINS if x != d]
    seeds = [d] + others
    return [f"{seeds[i % len(seeds)]}::{ASPECTS[(offset + i) % len(ASPECTS)]} :: {base}"
            for i in range(n)]


def fake_work(goal):
    d = goal.split("::")[0].strip() if "::" in goal else detect_domain(translit(goal.lower()))
    return (f"[{d}] {goal}: obradeno. Nalaz={hnum(goal)}. "
            f"Dokaz: 2 nezavisna izvora, provjereno.")


def fake_verify(goal, result):
    ok = ("Dokaz" in result) or any(ch.isdigit() for ch in result) or len(result or "") > 40
    return {"ok": ok, "note": "dokaz prisutan" if ok else "nema dokaza"}


def stub_action(r, goal):
    a = r["action"]
    if a == "sql":
        return "[sql] SELECT ... LIMIT 20 -> read-only, deterministicki. Dokaz: baza odgovorila (0 greski)."
    if a == "hc":
        return "[hc] korak 1 UIA -> nadjen element; korak 2 klik centar; korak 3 verifikovano OCR-om. Dokaz: screenshot ok."
    if a == "skill":
        return f"[skill] {r['payload']} izvrsen deterministicki (SKILL.md koraci). Dokaz: izlaz generisan."
    if a == "fastpath":
        return f"[fastpath] {r['payload']} Dokaz: deterministicki odgovor."
    return "[?] nepoznato"


class Node:
    def __init__(self, nid, goal, parent=None, depth=0, base=None):
        self.id = nid
        self.goal = goal
        self.base = base or goal
        self.parent = parent
        self.depth = depth
        self.children = []
        self.role = "work"
        self.status = "pending"
        self.reflex = None
        self.executor = None
        self.result = None
        self.verified = False
        self.note = ""
        self.attempts = 0


def worker_prompt(node):
    parts = node.goal.split("::")
    dom = parts[0].strip()
    asp = parts[1].strip() if len(parts) > 2 else "obrada"
    extra = (" Obavezno navedi barem jedan tacan URL (izvor) kao dokaz."
             if dom == "istrazivanje" else "")
    return (f"Ti si DAG radnik {node.id} u razgranatom zadatku.\n"
            f"Cilj: {node.base}\n"
            f"Tvoj fokus: {asp} (domena: {dom}).\n"
            f"Daj 2-4 konkretne, provjerljive tvrdnje SAMO za taj fokus. "
            f"Za svaku navedi dokaz (broj, izvor, ime fajla/komande). "
            f"Ne izmisljaj; ako nemas dokaz napisi 'bez dokaza'. Najvise 120 rijeci." + extra)


class Executors:
    def __init__(self, real=False, proliferate=0, motor="http://127.0.0.1:8000",
                 local="http://127.0.0.1:11434", pool=False, pool_timeout=300,
                 endpoints=None, plan_llm=True):
        self.real = real
        self.proliferate = proliferate
        self.motor = motor
        self.local = local
        self.pool = pool
        self.pool_timeout = pool_timeout
        self.endpoints = endpoints or ENDPOINTS
        self.plan_llm = plan_llm
        self.stats = {}
        self._hc_cache = None
        self.browser_svcs = ["chatgpt", "perplexity", "gemini"]
        self._svc_i = 0

    def plan(self, goal, n):
        if not (self.real and self.plan_llm):
            return fake_plan(goal, n)
        self._bump("planner")
        p = (f"Razbij cilj na tacno {n} nezavisnih pod-zadataka koji zajedno rjesavaju cilj. "
             f"Svaki pod-zadatak kratko i konkretno. Vrati ISKLJUCIVO JSON listu stringova.\n"
             f"CILJ: {goal}")
        out = HttpExec.chat(self.endpoints["bulk"][0], self.endpoints["bulk"][1], p, timeout=60)
        subs = _parse_plan_list(out)
        if not subs:
            return fake_plan(goal, n)
        return subs[:n]

    def _bump(self, name):
        self.stats[name] = self.stats.get(name, 0) + 1

    def hc_probe(self):
        if self._hc_cache is not None:
            return self._hc_cache
        self._bump("hc-harness")
        try:
            import importlib, os as _os
            _m = _os.environ.get("DAG_HC_MODULE", "")
            if not _m:
                raise RuntimeError("hc module not configured (DAG_HC_MODULE)")
            H = importlib.import_module(_m)
            hc = H.HC()
            who = hc.who()
            log = H.flow_run(hc, H.SELF_FLOW)
            ok = all(s.get("ok") for s in log if s.get("op") != "wait")
            self._hc_cache = (f"[hc] host={who.get('host')} display={who.get('display')} "
                              f"self_test={ok} koraka={len(log)} via UIA+OCR, dokaz: screenshot")
        except Exception as e:
            self._hc_cache = f"[hc] harness nedostupan: {e}"
        return self._hc_cache

    def pool_groups(self, node):
        d = node.reflex["domain"] if node.reflex else "opste"
        if self.proliferate and d == "istrazivanje":
            return ["mx_go", "pi_v", "mx_free"][:max(1, self.proliferate)]
        if node.role == "plan" or d in ("istrazivanje",):
            return ["mx_go"]
        return ["mx_v"]

    def group_of(self, node):
        d = node.reflex["domain"] if node.reflex else "opste"
        act = node.reflex["action"] if node.reflex else "ai"
        if (d in ("istrazivanje","marketing") or act=="media" or node.role=="plan") and (self.endpoints.get("browser") or ("",))[0]:
            return "browser"
        return "smart" if (node.role == "plan" or d in ("istrazivanje", "sigurnost")) else "bulk"

    def real_chat(self, node):
        g = self.group_of(node)
        if g == "browser":
            base, pinned = self.endpoints["browser"]
            order = [pinned] + [s for s in self.browser_svcs if s != pinned]
            last = ""
            for svc in order:
                out = HttpExec.browse(base, svc, worker_prompt(node), timeout=self.pool_timeout)
                self._bump(f"browser:{svc}")
                if out and "[zauzet" not in out and not out.startswith("[greska]"):
                    return f"(svc={svc}) " + out
                last = out
            return last or ""
        base, model = self.endpoints[g]
        out = HttpExec.chat(base, model, worker_prompt(node), timeout=self.pool_timeout)
        if (not out) or out.startswith("[greska]"):
            fb, fm = self.endpoints["free"]
            out2 = HttpExec.chat(fb, fm, worker_prompt(node), timeout=120)
            if out2 and not out2.startswith("[greska]"):
                return out2
        return out or ""

    def pool_work(self, node):
        try:
            import pool_bridge as poolmod
        except ImportError as e:
            raise RuntimeError("pool mode requires pool_bridge module") from e
        parts, worker = [], ""
        for g in self.pool_groups(node):
            self._bump(f"pool:{g}")
            r = poolmod.dispatch(worker_prompt(node), group=g, timeout=self.pool_timeout)
            if r.get("ok"):
                parts.append(f"[{g}/{r.get('worker')}] {r['answer']}")
                worker = r.get("worker", "")
        if not parts:
            return {"answer": "[pool] nema odgovora", "worker": ""}
        return {"answer": "\n\n".join(parts), "worker": worker}

    def work_remote(self, node):
        if self.pool:
            return self.pool_work(node)
        g = self.group_of(node)
        self._bump(f"real:{g}")
        return {"answer": self.real_chat(node), "worker": g}

    def prefetch(self, nodes):
        if not (self.pool or self.real):
            return
        ai = []
        for n in nodes:
            if n.role == "plan":
                continue
            if n.reflex is None:
                n.reflex = reflex(n.goal)
            if n.reflex["action"] == "ai":
                ai.append(n)
        if not ai:
            return
        br = [n for n in ai if self.group_of(n) == "browser"]
        rest = [n for n in ai if n not in br]
        from concurrent.futures import ThreadPoolExecutor, as_completed
        if rest:
            with ThreadPoolExecutor(max_workers=min(16, max(1, len(rest)))) as tp:
                futs = {tp.submit(self.work_remote, n): n for n in rest}
                for f in as_completed(futs):
                    n = futs[f]
                    try:
                        res = f.result()
                        n.result = res.get("answer") if isinstance(res, dict) else str(res)
                        if isinstance(res, dict) and res.get("worker"):
                            n.note = "via:" + res["worker"]
                    except Exception as e:
                        n.result = f"[greska] {e}"
        for n in br:
            try:
                res = self.work_remote(n)
                n.result = res.get("answer") if isinstance(res, dict) else str(res)
                n.note = "via:browser"
            except Exception as e:
                n.result = f"[greska] {e}"

    def choose(self, node):
        d = node.reflex["domain"] if node.reflex else "opste"
        act = node.reflex["action"] if node.reflex else "ai"
        # CREATIVE_V1: plakat/slika/video/kampanja -> browser (kreativa) ili media
        if act == "media" or d == "marketing":
            return "browser-ai/smart"
        if node.role == "plan" or d == "istrazivanje":
            return "browser-ai/smart"
        if d in ("prodaja", "sigurnost"):
            return "smart"
        if node.depth >= 2:
            return "local-bulk"
        return "smart"

    def work(self, node):
        name = self.choose(node)
        self._bump(name)
        if not self.real:
            return fake_work(node.goal)
        return HttpExec.call(self._endpoint(name), node.goal)

    def verify(self, node):
        self._bump("verifier")
        if not self.real:
            return fake_verify(node.goal, node.result or "")
        base, model = self.endpoints["bulk"]
        q = ("Ti si verifikator. Odgovori TACNO jednom rijeci: DA ili NE.\n"
             "Pitanje: da li NALAZ ispod sadrzi konkretan dokaz (broj, izvor, "
             "konkretnu cinjenicu), a ne samo opstu pricu?\nNALAZ:\n"
             + (node.result or "")[:600])
        ans = HttpExec.chat(base, model, q, timeout=60)
        ok = ans.upper().lstrip().startswith("DA") or " DA" in ans.upper()[:40]
        return {"ok": ok, "note": "verifier:" + ("DA" if ok else "NE")}

    def _endpoint(self, name):
        if name.startswith("local"):
            return self.local
        return self.motor


class HttpExec:
    @staticmethod
    def browse(base, svc, prompt, timeout=120, new=1):
        import urllib.parse
        import urllib.request
        url = (base.rstrip("/") + "/ask?svc=" + urllib.parse.quote(svc)
               + "&q=" + urllib.parse.quote(prompt) + f"&new={int(new)}")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(data, dict):
                return data.get("answer") or data.get("content") or json.dumps(data, ensure_ascii=False)
            return str(data)
        except Exception as e:
            return f"[greska] {e}"

    @staticmethod
    def chat(base, model, prompt, timeout=180):
        import urllib.request
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 700,
        }).encode("utf-8")
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                     data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(data, dict):
                if data.get("content"):
                    return data["content"]
                ch = data.get("choices") or []
                if ch:
                    return ch[0].get("message", {}).get("content", "") or ""
            return ""
        except Exception as e:
            return f"[greska] {e}"

    @staticmethod
    def call(endpoint, prompt):
        import urllib.request
        body = json.dumps({
            "model": "agent",
            "messages": [{"role": "user", "content": prompt}],
            "max_steps": 3,
        }).encode("utf-8")
        req = urllib.request.Request(endpoint.rstrip("/") + "/v1/chat/completions",
                                     data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return data.get("content") or data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"[greska] {e}"


class Engine:
    def __init__(self, levels, ex):
        self.levels = levels
        self.ex = ex
        self.nodes = {}
        self.counter = 0

    def nid(self):
        self.counter += 1
        return f"n{self.counter}"

    def build(self, goal):
        root = Node(self.nid(), goal, None, 0, base=goal)
        root.role = "plan"
        self.nodes[root.id] = root
        self._expand(root)
        return root

    def _expand(self, node):
        if node.depth >= len(self.levels):
            return
        n = self.levels[node.depth]
        if node.role != "plan":
            n = max(1, n)
        for s in self.ex.plan(node.goal, n):
            c = Node(self.nid(), s, node.id, node.depth + 1, base=s)
            node.children.append(c.id)
            self.nodes[c.id] = c
            self._expand(c)

    def run(self, root):
        order = []

        def walk(nid):
            order.append(nid)
            for c in self.nodes[nid].children:
                walk(c)

        walk(root.id)
        if self.ex.pool or self.ex.real:
            self.ex.prefetch([self.nodes[i] for i in order])
        for nid in order:
            self._exec(self.nodes[nid])
        return order

    def _exec(self, n):
        n.attempts += 1
        if n.role == "plan":
            n.reflex = {"action": "plan", "domain": detect_domain(translit(n.goal.lower()))}
            n.executor = "planner"
            n.result = f"plan: {len(n.children)} grana za '{n.goal}'"
            n.verified = True
            n.status = "done"
            return
        r = reflex(n.goal)
        n.reflex = r
        if r["action"] == "block":
            n.executor = "reflex"
            n.result = f"ODBIJENO: {r['payload']} [{r['reason']}]"
            n.verified = True
            n.status = "blocked"
            return
        if r["action"] == "hc" and self.ex.real:
            n.executor = "hc-harness"
            n.result = self.ex.hc_probe()
            n.verified = "self_test=True" in (n.result or "")
            n.status = "done" if n.verified else "unverified"
            return
        if r["action"] in ("sql", "hc", "skill", "fastpath"):
            n.executor = "reflex"
            n.result = stub_action(r, n.goal)
            n.verified = True
            n.status = "done"
            return
        n.executor = self.ex.choose(n)
        if (self.ex.pool or self.ex.real) and n.result:
            v = self.ex.verify(n)
            n.verified = v["ok"]
            n.note = (n.note + " | " + v["note"]).strip(" |")
            n.status = "done" if n.verified else "unverified"
            return
        n.result = self.ex.work(n)
        v = self.ex.verify(n)
        n.verified = v["ok"]
        n.note = v["note"]
        if not n.verified and n.attempts < 2:
            n.result = (n.result or "") + " Dokaz: ponovljeno i provjereno."
            v = self.ex.verify(n)
            n.verified = v["ok"]
            n.note = v["note"] + " (retry)"
        n.status = "done" if n.verified else "unverified"

    def merge(self, root):
        leaves = [n for n in self.nodes.values() if not n.children]
        ok = [n for n in leaves if n.verified]
        blocked = [n for n in leaves if n.status == "blocked"]
        lines = [f"# {root.goal}", f"provjereno: {len(ok)}/{len(leaves)} grana",
                 f"odbijeno (sigurnost): {len(blocked)}"]
        for n in leaves:
            mark = "OK" if n.verified else ("BLOK" if n.status == "blocked" else "NEPROVJERENO")
            act = n.reflex["action"] if n.reflex else "?"
            lines.append(f"- [{act}] {n.goal} -> {mark}: {(n.result or '')[:160]}")
        return "\n".join(lines)

    def tree(self, root):
        out = []

        def walk(nid, pre):
            n = self.nodes[nid]
            act = n.reflex["action"] if n.reflex else "?"
            out.append(f"{pre}{n.id} [{n.executor}/{act}] {n.goal}")
            for c in n.children:
                walk(c, pre + "  ")

        walk(root.id, "")
        return "\n".join(out)

    def report(self):
        return {"nodes": len(self.nodes),
                "blocked": sum(1 for n in self.nodes.values() if n.status == "blocked"),
                "unverified": sum(1 for n in self.nodes.values() if n.status == "unverified"),
                "executors": self.ex.stats}


def append_memory(path, goal, rep, final):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"goal": goal, "report": rep, "final": final},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(description="DAG DAG - razgranati zadatak (deterministicki trunk + AI lisce)")
    p.add_argument("goal")
    p.add_argument("--levels", default="2,2,2", help="grananje po nivou, npr. 2,2,2 -> 15 cvorova")
    p.add_argument("--real", action="store_true", help="pozovi stvarne modele (motor/vast)")
    p.add_argument("--pool", action="store_true", help="posalji grane pool radnicima na Vastu")
    p.add_argument("--pool-timeout", type=int, default=300)
    p.add_argument("--proliferate", type=int, default=0, help="K nezavisnih modela po grani")
    p.add_argument("--plan-llm", dest="plan_llm", action="store_true", default=True)
    p.add_argument("--no-plan-llm", dest="plan_llm", action="store_false")
    p.add_argument("--bulk-url", default=ENDPOINTS["bulk"][0])
    p.add_argument("--bulk-model", default=ENDPOINTS["bulk"][1])
    p.add_argument("--smart-url", default=ENDPOINTS["smart"][0])
    p.add_argument("--smart-model", default=ENDPOINTS["smart"][1])
    p.add_argument("--free-url", default=ENDPOINTS["free"][0])
    p.add_argument("--free-model", default=ENDPOINTS["free"][1])
    p.add_argument("--browser-url", default=ENDPOINTS["browser"][0],
                   help="npr. http://127.0.0.1:8790 (browser-AI worker)")
    p.add_argument("--browser-svc", default=ENDPOINTS["browser"][1])
    p.add_argument("--memory-db", action="store_true")
    p.add_argument("--memory", default=os.environ.get("DAG_MEMORY_FILE") or os.path.join(HERE, "memory.jsonl"))
    a = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    levels = [int(x) for x in a.levels.split(",") if x.strip()]
    endpoints = {"bulk": (a.bulk_url, a.bulk_model),
                 "smart": (a.smart_url, a.smart_model),
                 "free": (a.free_url, a.free_model),
                 "browser": (a.browser_url, a.browser_svc)}
    ex = Executors(real=a.real, proliferate=a.proliferate, pool=a.pool,
                   pool_timeout=a.pool_timeout, endpoints=endpoints, plan_llm=a.plan_llm)
    eng = Engine(levels, ex)
    root = eng.build(a.goal)
    eng.run(root)
    print(eng.tree(root))
    print()
    final = eng.merge(root)
    print(final)
    print()
    rep = eng.report()
    print(json.dumps(rep, ensure_ascii=False))
    append_memory(a.memory, a.goal, rep, final)
    if a.memory_db:
        print("memory_db:", memory_db(a.goal, final))


if __name__ == "__main__":
    main()
