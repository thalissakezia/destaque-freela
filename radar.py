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
from datetime import datetime, timedelta, timezone
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
            "publicado": data_publicacao(r.get("postedDate")),
            "cliente": r.get("authorName", ""),
        })
    return vagas


# ---------- Filtros ----------
IA = r"(\bia\b|\bias\b|inteligencia artificial|chat ?gpt|\bgpt\b|\bai\b)"
PROIBE_IA = [
    r"sem (o )?(uso|ajuda|auxilio|utilizacao) d[eao]s? " + IA,
    r"sem " + IA,
    r"(nao|nunca) (pode|podem|deve|devera|sera|e|sao|aceito|aceitamos|aceitarei|quero|permito|permitimos|utilizar|usar)[^.;]{0,40}" + IA,
    r"proibid[oa]s?[^.;]{0,40}" + IA,
    IA + r"[^.;]{0,30}(proibid|nao (e |sera )?(permitid|aceit))",
    r"nada de " + IA,
    r"no ai\b|without ai|ai[- ]generated (content )?(is )?not",
    r"(feito|escrito|redigido) (por|de forma) (um )?(humano|pessoa|manual)",
    r"redacao (propria|humana)",
]


def proibe_ia(vaga):
    texto = sem_acento(vaga["titulo"] + " " + vaga["descricao"])
    return any(re.search(p, texto) for p in PROIBE_IA)


def data_publicacao(txt, agora=None):
    """Converte 'há 3 dias', 'Ontem', 'há 2 horas' em data ISO."""
    if not txt or re.match(r"\d{4}-", txt):
        return txt
    agora = agora or datetime.now(timezone.utc)
    t = sem_acento(txt)
    if "ontem" in t:
        return (agora - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(minuto|hora|dia|semana|mes)", t)
    if not m:
        return agora.isoformat()
    n, un = int(m.group(1)), m.group(2)
    dias = {"minuto": 1 / 1440, "hora": 1 / 24, "dia": 1, "semana": 7, "mes": 30}[un]
    return (agora - timedelta(days=n * dias)).isoformat()


def ordem(vaga):
    """Mais recentes primeiro; dentro do mesmo período, menos propostas primeiro."""
    try:
        dias = (datetime.now(timezone.utc) - datetime.fromisoformat(vaga["publicado"])).total_seconds() / 86400
    except (TypeError, ValueError):
        dias = 99
    faixa = 0 if dias <= 1 else 1 if dias <= 3 else 2 if dias <= 7 else 3
    p = vaga["propostas"] if vaga["propostas"] is not None else 999
    return (faixa, p, dias)


# ---------- Nota ----------
def tem(termo, texto):
    t = sem_acento(termo).strip()
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", texto) is not None


def nao_consigo(vaga, cfg):
    texto = sem_acento(vaga["titulo"] + " " + vaga["descricao"])
    return [t for t in cfg.get("nao_consigo", []) if tem(t, texto)]


def dar_nota(vaga, cfg):
    texto = sem_acento(" ".join([vaga["titulo"], vaga["descricao"], " ".join(vaga["habilidades"])]))
    titulo = sem_acento(vaga["titulo"])
    pontos, achou = 0, []
    for hab, peso in cfg["habilidades"].items():
        if tem(hab, texto):
            pontos += peso * (2 if tem(hab, titulo) else 1)
            achou.append(hab)
    # área de trabalho que dá para fazer (a do título vale mais)
    area, bonus = "", 0
    for nome, a in cfg.get("areas", {}).items():
        no_titulo = any(tem(p, titulo) for p in a["palavras"])
        no_texto = no_titulo or any(tem(p, texto) for p in a["palavras"])
        valor = a["peso"] if no_titulo else a["peso"] // 2 if no_texto else 0
        if valor > bonus:
            area, bonus = nome, valor
    ruins = [e for e in cfg["evitar"] if tem(e, texto)]
    nota = min(70, pontos * 1.2) + bonus - 20 * len(ruins)
    p = vaga.get("propostas")
    if nota > 0 and p is not None:
        nota += 10 if p < 10 else 5 if p < 30 else 0 if p < 80 else -10
    vaga["nota"] = max(0, min(100, round(nota)))
    vaga["area"] = area
    vaga["combina"] = achou
    vaga["evitar"] = ruins
    return vaga


# ---------- Proposta pronta ----------
PASSOS = {
    "Dados e BI": ("um dashboard", [
        "Entender quais números você precisa acompanhar e de onde vêm os dados.",
        "Tratar e organizar os dados para que fiquem confiáveis e fáceis de atualizar.",
        "Montar o dashboard com os indicadores principais e filtros simples, e te mostrar como usar."]),
    "Planilhas": ("uma planilha", [
        "Entender como você trabalha hoje e o que a planilha precisa resolver.",
        "Montar a estrutura com fórmulas e validações, para evitar erro de digitação.",
        "Entregar pronta, com uma aba de instruções e um teste junto com você."]),
    "Automação e código": ("a automação", [
        "Mapear o processo atual e o resultado que você espera.",
        "Desenvolver a solução com o código organizado e documentado.",
        "Testar com os seus dados reais e entregar com instruções de uso."]),
    "Sites": ("um site", [
        "Entender o seu público e o que o site precisa fazer (vender, captar contato, apresentar).",
        "Montar o layout responsivo, que funciona bem no celular, com os seus textos e cores.",
        "Publicar, testar e te mostrar como fazer pequenas alterações."]),
    "Textos": ("um texto", [
        "Entender o público, o tom de voz e o objetivo do texto.",
        "Escrever a primeira versão com estrutura clara e boa leitura.",
        "Revisar com você e ajustar até ficar do jeito que você quer."]),
    "Tradução": ("uma tradução", [
        "Ler o material inteiro e entender o contexto e o público.",
        "Traduzir mantendo o sentido e o tom, e não palavra por palavra.",
        "Revisar a ortografia e a formatação antes de entregar."]),
    "Apresentações e documentos": ("um material", [
        "Entender para quem é o material e qual mensagem precisa passar.",
        "Organizar o conteúdo em uma sequência clara e direta.",
        "Diagramar com visual limpo e entregar no formato que você precisar."]),
}
INTRO = {
    "Dados e BI": "que mostre os números com clareza e seja fácil de atualizar.",
    "Planilhas": "que funcione de forma prática, sem depender de ajustes manuais toda hora.",
    "Automação e código": "que tire o trabalho repetitivo das mãos da sua equipe.",
    "Sites": "bonito, rápido e que funcione bem no celular.",
    "Textos": "claro, bem escrito e no tom certo para o seu público.",
    "Tradução": "fiel ao original e natural para quem vai ler.",
    "Apresentações e documentos": "organizado e com visual profissional.",
}
PERGUNTA = {
    "Dados e BI": "Para te passar valor e prazo certinhos: você já tem os dados prontos ou eles ainda estão espalhados em vários arquivos?",
    "Planilhas": "Para te passar valor e prazo certinhos: você usa Excel ou Google Sheets, e mais alguém vai mexer na planilha?",
    "Sites": "Para te passar valor e prazo certinhos: você já tem domínio e os textos e imagens, ou preciso criar também?",
    "Textos": "Para te passar valor e prazo certinhos: você tem algum exemplo de texto que gosta, para eu acertar o tom?",
    "Tradução": "Para te passar valor e prazo certinhos: quantas palavras ou páginas tem o material, e em que formato ele está?",
}
NOMES = {"power bi": "Power BI", "sql": "SQL", "python": "Python", "excel": "Excel", "kpi": "KPIs",
         "etl": "ETL", "vba": "VBA", "google sheets": "Google Sheets", "looker": "Looker", "pandas": "Pandas",
         "dashboard": "dashboards", "planilha": "planilhas", "relatório": "relatórios"}


def montar_proposta(vaga, cfg):
    eu = cfg.get("eu", {})
    entrega, passos = PASSOS.get(vaga.get("area"), ("a solução", PASSOS["Automação e código"][1]))
    itens = [NOMES.get(h, h) for h in vaga["combina"][:3]]
    ferramentas = (itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]) if itens else ""
    linhas = [
        f"Olá! Li o seu projeto \"{vaga['titulo']}\" e entendi que você precisa de {entrega} "
        + INTRO.get(vaga.get("area"), "que resolva isso de forma prática e bem feita."),
        "",
        "Como eu faria:",
        *[f"{i}. {p}" for i, p in enumerate(passos, 1)],
        "",
        (f"Trabalho com {ferramentas}. " if ferramentas else "")
        + (f"Tenho {eu['diferencial']}." if eu.get("diferencial") else "")
        + (f" Alguns projetos meus estão em {eu['portfolio']}." if eu.get("portfolio") else ""),
        "",
        PERGUNTA.get(vaga.get("area"), "Para te passar valor e prazo certinhos: qual é o resultado mais importante para você nesse projeto?"),
        "",
        eu.get("assinatura", ""),
    ]
    return "\n".join(linhas).strip()


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
    repo = os.getenv("GITHUB_REPOSITORY", "")
    pagina = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if "/" in repo else ""
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
                        "message": corpo + ("\nProposta pronta na página." if v.get("proposta") else ""),
                        "click": pagina + "#" + v["id"] if pagina else v["link"], "tags": ["briefcase"],
                        "actions": [{"action": "view", "label": "Abrir no site", "url": v["link"]}]})
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
    vagas = [dar_nota(v, cfg) for v in vagas if not proibe_ia(v) and not nao_consigo(v, cfg)]
    vagas.sort(key=ordem)
    for v in vagas:
        if v["nota"] >= cfg["nota_minima_alerta"]:
            v["proposta"] = montar_proposta(v, cfg)
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
