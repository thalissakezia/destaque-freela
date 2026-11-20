"""Busca projetos no 99Freelas e na Workana, dá uma nota para cada um
de acordo com as minhas habilidades, calcula estatísticas do mercado
e avisa no celular (ntfy ou Telegram) quando aparece projeto bom.

Gera:
  docs/vagas.json    lista de projetos com nota (usada pela página)
  docs/mercado.json  estatísticas (habilidades mais pedidas, orçamentos)
  dados/vistos.json  projetos já avisados no Telegram
"""
import html
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PASTA = Path(__file__).parent
DOCS = PASTA / "docs"
DADOS = PASTA / "dados"
HEADERS = {"User-Agent": "Mozilla/5.0 (radar pessoal de projetos)", "Accept-Language": "pt-BR"}
PAUSA = 3  # segundos entre pedidos, para não sobrecarregar os sites


def sem_acento(t):
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def baixar(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(PAUSA)
    return r.text


# ---------- 99Freelas ----------
def ler_99freelas(pagina_html):
    sopa = BeautifulSoup(pagina_html, "html.parser")
    vagas = []
    for li in sopa.select("li.result-item"):
        link = li.select_one("h1.title a")
        if not link:
            continue
        info = li.select_one("p.information")
        texto_info = " ".join(info.get_text(" ").split()) if info else ""
        partes = [p.strip() for p in texto_info.split("|")]
        desc = li.select_one("div.description")
        data = li.select_one("b.datetime")
        propostas = re.search(r"Propostas:\s*(\d+)", texto_info)
        cliente = li.select_one("p.client a")
        vagas.append({
            "id": "99f-" + li.get("data-id", link["href"]),
            "fonte": "99Freelas",
            "titulo": link.get_text(strip=True),
            "link": "https://www.99freelas.com.br" + link["href"].split("?")[0],
            "categoria": partes[0] if partes else "",
            "nivel": partes[1] if len(partes) > 1 else "",
            "descricao": " ".join(desc.get_text(" ").replace("Expandir", "").replace("Esconder", "").replace("… ", "").split()) if desc else "",
            "habilidades": [],
            "orcamento": "",
            "propostas": int(propostas.group(1)) if propostas else None,
            "publicado": datetime.fromtimestamp(int(data["cp-datetime"]) / 1000, timezone.utc).isoformat() if data and data.get("cp-datetime") else None,
            "cliente": cliente.get_text(strip=True) if cliente else "",
        })
    return vagas


# ---------- Workana ----------
def ler_workana(pagina_html):
    m = re.search(r":results-initials='([^']*)'", pagina_html)
    if not m:
        return []
    dados = json.loads(html.unescape(m.group(1)))
    vagas = []
    for r in dados.get("results", []):
        titulo = BeautifulSoup(r.get("title", ""), "html.parser").get_text(strip=True)
        propostas = re.search(r"(\d+)", r.get("totalBids") or "")
        vagas.append({
            "id": "wk-" + r["slug"],
            "fonte": "Workana",
            "titulo": titulo,
            "link": "https://www.workana.com/job/" + r["slug"],
            "categoria": "",
            "nivel": "",
            "descricao": BeautifulSoup(r.get("description", ""), "html.parser").get_text(" ", strip=True),
            "habilidades": [s.get("anchorText", "") for s in r.get("skills") or []],
            "orcamento": r.get("budget") or "",
            "propostas": int(propostas.group(1)) if propostas else None,
            "publicado": r.get("postedDate"),
            "cliente": r.get("authorName", ""),
        })
    return vagas


# ---------- Nota ----------
def dar_nota(vaga, cfg):
    texto = sem_acento(" ".join([vaga["titulo"], vaga["descricao"], " ".join(vaga["habilidades"])]))
    titulo = sem_acento(vaga["titulo"])
    pontos, achou = 0, []
    for hab, peso in cfg["habilidades"].items():
        h = sem_acento(hab)
        if h in texto:
            pontos += peso * (2 if h in titulo else 1)
            achou.append(hab)
    ruins = [e for e in cfg["evitar"] if sem_acento(e) in texto]
    pontos -= 15 * len(ruins)
    nota = max(0, min(100, pontos * 1.5))
    # menos concorrência vale um pouco mais
    p = vaga.get("propostas")
    if nota and p is not None:
        nota += 10 if p < 10 else 5 if p < 30 else 0 if p < 80 else -10
    vaga["nota"] = max(0, min(100, round(nota)))
    vaga["combina"] = achou
    vaga["evitar"] = ruins
    return vaga


# ---------- Estatísticas ----------
def numeros_orcamento(txt):
    nums = [int(n.replace(".", "")) for n in re.findall(r"\d[\d.]*", txt or "")]
    if not nums:
        return None
    moeda = "USD" if "USD" in txt else "BRL"
    return moeda, sum(nums) / len(nums)


def estatisticas(vagas, cfg):
    termos = Counter()
    for v in vagas:
        texto = sem_acento(" ".join([v["titulo"], v["descricao"], " ".join(v["habilidades"])]))
        for hab in cfg["habilidades"]:
            if sem_acento(hab) in texto:
                termos[hab] += 1
    tags = Counter(s for v in vagas for s in v["habilidades"])
    orc = {"BRL": [], "USD": []}
    for v in vagas:
        n = numeros_orcamento(v["orcamento"])
        if n:
            orc[n[0]].append(n[1])
    props = [v["propostas"] for v in vagas if v["propostas"] is not None]
    def resumo(lista):
        if not lista:
            return None
        s = sorted(lista)
        return {"qtd": len(s), "minimo": round(s[0]), "mediana": round(s[len(s) // 2]), "maximo": round(s[-1])}
    return {
        "atualizado": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "total": len(vagas),
        "por_fonte": dict(Counter(v["fonte"] for v in vagas)),
        "minhas_habilidades_pedidas": [{"termo": t, "vagas": n} for t, n in termos.most_common() if n],
        "tags_mais_pedidas": [{"termo": t, "vagas": n} for t, n in tags.most_common(15)],
        "orcamento_medio": {k: resumo(v) for k, v in orc.items()},
        "propostas_por_projeto": resumo(props),
        "boas_vagas": sum(1 for v in vagas if v["nota"] >= cfg["nota_minima_alerta"]),
    }


# ---------- Telegram ----------
def avisar(vagas, cfg):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    topico = os.getenv("NTFY_TOPICO")
    arq = DADOS / "vistos.json"
    vistos = set(json.loads(arq.read_text())) if arq.exists() else set()
    novas = [v for v in vagas if v["nota"] >= cfg["nota_minima_alerta"] and v["id"] not in vistos]
    if (token and chat) or topico:
        for v in novas[:10]:
            corpo = (f"{v['fonte']} | Propostas: {v['propostas'] if v['propostas'] is not None else '?'}"
                     f"{' | ' + v['orcamento'] if v['orcamento'] else ''}\n"
                     f"Combina: {', '.join(v['combina'])}")
            try:
                if token and chat:
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  data={"chat_id": chat, "text": f"Nota {v['nota']} | {v['titulo']}\n{corpo}\n{v['link']}"},
                                  timeout=20)
                if topico:
                    requests.post("https://ntfy.sh/", timeout=20, json={
                        "topic": topico, "title": f"Nota {v['nota']}: {v['titulo']}",
                        "message": corpo, "click": v["link"], "tags": ["briefcase"]})
            except requests.RequestException as e:
                print("Aviso falhou:", e)
    else:
        # sem aviso configurado, não marca nada como visto
        return 0
    vistos.update(v["id"] for v in novas)
    DADOS.mkdir(exist_ok=True)
    arq.write_text(json.dumps(sorted(vistos)[-2000:], ensure_ascii=False))
    return len(novas)


def coletar(cfg):
    todas = {}
    for termo in cfg["buscas"]:
        q = requests.utils.quote(termo)
        if "99freelas" in cfg["fontes"]:
            try:
                for v in ler_99freelas(baixar(f"https://www.99freelas.com.br/projects?q={q}")):
                    todas[v["id"]] = v
            except requests.RequestException as e:
                print("99Freelas falhou em", termo, e)
        if "workana" in cfg["fontes"]:
            try:
                for v in ler_workana(baixar(f"https://www.workana.com/pt/jobs?language=pt&query={q}")):
                    todas[v["id"]] = v
            except requests.RequestException as e:
                print("Workana falhou em", termo, e)
    return list(todas.values())


def salvar(vagas, cfg):
    vagas = sorted((dar_nota(v, cfg) for v in vagas), key=lambda v: -v["nota"])
    DOCS.mkdir(exist_ok=True)
    (DOCS / "vagas.json").write_text(json.dumps(vagas, ensure_ascii=False, indent=1))
    (DOCS / "mercado.json").write_text(json.dumps(estatisticas(vagas, cfg), ensure_ascii=False, indent=1))
    return vagas


def main():
    cfg = json.loads((PASTA / "config.json").read_text(encoding="utf-8"))
    vagas = salvar(coletar(cfg), cfg)
    novas = avisar(vagas, cfg)
    print(f"{len(vagas)} projetos, {novas} novos acima da nota {cfg['nota_minima_alerta']}")
    if not vagas:
        sys.exit("Nenhum projeto encontrado. O layout dos sites pode ter mudado.")


if __name__ == "__main__":
    main()
