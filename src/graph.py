"""Evidence-based GraphRAG ontology for the Day 19 bonus.

Compared with src/hint_graph.py, this models source-scoped CaseReport,
Participation and DrugFinding nodes, as well as numeric Threshold and Penalty
nodes. A report is not assumed to be a globally identified real-world case.
The four original public KG contracts remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .hint_graph import (
    SUBSTANCES, Neo4jGraph as DriverGraph, find_substances, link_entity,
    load_markdown_docs, normalize_crime, parse_law_article,
)
from .legal_evidence import (
    article_leads, entity_key, mass_in_grams, named_mass_thresholds,
    normalized_text, penalty_band, without_related_teasers, explicit_responsibility_totals,
)
from .models import Document
from .store import EmbeddingStore


NEWS_PROMPT = """Trích xuất bằng chứng từ PHẦN CHÍNH của bài báo về ma túy.
Mỗi bài là MỘT báo cáo có nguồn, không phải mã vụ án toàn cục. Không trích teaser,
bài liên quan hay ví dụ không thuộc vụ chính. Bài hội nghị/tuyên truyền không có vụ cụ thể: case=null.
Chỉ dùng thông tin có trong bài. Không suy diễn tội của một người từ tội của người khác.
Giữ nguyên tên đầy đủ, khối lượng, biệt danh. Không đổi 'thuốc lắc' thành MDMA nếu bài chưa xác nhận.
Phân biệt bắt giữ, truy tố, xét xử, đã tuyên án; không tự gọi nghi phạm là người đã bị tuyên án.
Đối với chất: ghi tổng lượng quy trách nhiệm nếu bài nói rõ; không cộng lại tổng với từng lần thu giữ.
Mỗi findings là một bằng chứng chất; subject là người chịu trách nhiệm nếu nêu rõ, nếu không để rỗng.
evidence phải là trích đoạn NGUYÊN VĂN liên tục trong bài chứng minh tên chất và lượng, không dùng dấu ...
Không suy ra khối lượng gam từ số viên/chỉ. amount giữ 'hơn', 'gần', 'khoảng' và đơn vị của nguồn.
Trả JSON:
{{"case": {{"name":"tên ngắn", "summary":"tóm tắt vụ chính", "date":"ngày hoặc rỗng",
"location":"tỉnh/thành phố hoặc rỗng", "charges":["tội từ danh sách hoặc bỏ trống"],
"people":[{{"name":"họ tên", "aliases":[], "role":"vai trò", "stage":"giai đoạn tố tụng hoặc rỗng",
"charges":["tội riêng của người từ danh sách"], "sentence":"mức án hoặc rỗng",
"evidence":"trích nguyên văn chứng minh vai trò/tội/án"}}],
"findings":[{{"name":"tên chất", "amount":"lượng hoặc rỗng", "subject":"tên người hoặc rỗng",
"evidence":"trích nguyên văn chất/lượng"}}]}}}}
DANH SÁCH TỘI: {crimes}
DANH SÁCH CHẤT: {substances}
Nguồn: {doc_id}
Tiêu đề: {title}
Nội dung chính:
{content}"""


def extract_report(doc: Document, content: str, crimes: list[str], llm_fn: Callable) -> dict | None:
    raw = llm_fn(NEWS_PROMPT.format(
        crimes="; ".join(crimes), substances=", ".join(SUBSTANCES),
        doc_id=doc.id, title=doc.metadata.get("title", ""), content=content[:12000]), json_mode=True)
    try:
        case = json.loads(raw)["case"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{doc.id}: JSON phải có case object hoặc null") from error
    if case is None:
        return None
    if not isinstance(case, dict):
        raise ValueError(f"{doc.id}: case không phải object")
    body = entity_key(content)
    case["charges"] = sorted({c for c in (link_entity(x, crimes) for x in case.get("charges", [])) if c})
    people = []
    for person in case.get("people", []):
        name = normalized_text(person.get("name") or "")
        if not name or entity_key(name) not in body:
            continue
        person["name"] = name
        person["id"] = entity_key(name)
        person["participation_id"] = f"{doc.id}/person/{person['id']}"
        person["aliases"] = sorted({normalized_text(alias) for alias in person.get("aliases", []) if alias})
        person["charges"] = sorted({c for c in (link_entity(x, crimes) for x in person.get("charges", [])) if c})
        for key in ("role", "stage", "sentence", "evidence"):
            person[key] = person.get(key) or ""
        person["verified"] = bool(person["evidence"] and entity_key(person["evidence"]) in body)
        people.append(person)
    case["people"] = people
    case["charges"] = sorted(set(case["charges"]) | {c for person in people for c in person["charges"]})
    findings = []
    for finding in case.get("findings", []):
        name = normalized_text(finding.get("name") or "")
        if not name or entity_key(name) not in body:
            continue
        name = link_entity(name, SUBSTANCES, normalize=entity_key) or name.casefold()
        amount = normalized_text(finding.get("amount") or "")
        evidence = finding.get("evidence") or ""
        verified = bool(evidence and entity_key(evidence) in body)
        grams, qualifier = mass_in_grams(amount)
        # A numeric threshold is usable only if the quoted evidence also contains that mass.
        quoted_masses = []
        if verified:
            from .legal_evidence import MASS
            for match in MASS.finditer(evidence):
                quoted_masses.append(mass_in_grams(match[0])[0])
        quantity_verified = verified and grams is not None and grams in quoted_masses
        finding_id = hashlib.sha256(entity_key(name + "|" + amount + "|" + (finding.get("subject") or "") + "|" + evidence).encode()).hexdigest()[:20]
        findings.append({"id": f"{doc.id}/finding/{finding_id}", "name": name, "amount": amount,
                         "mass_g": grams if quantity_verified else None, "qualifier": qualifier,
                         "subject": normalized_text(finding.get("subject") or ""),
                         "evidence": evidence, "verified": verified, "quantity_verified": quantity_verified})
    for finding in findings:
        finding['evidence_type'] = 'llm_quote'
    for total in explicit_responsibility_totals(content, people, SUBSTANCES):
        digest = hashlib.sha256(entity_key(total['name']+'|'+total['subject']+'|'+total['evidence']).encode()).hexdigest()[:20]
        total['id'] = f'{doc.id}/responsibility-total/{digest}'
        findings.append(total)
    case["findings"] = findings
    for key in ("name", "summary", "date", "location"):
        case[key] = case.get(key) or ""
    return case


class Neo4jGraph(DriverGraph):
    """Keep the driver/reset/stats contract; write and traverse the new ontology."""

    def constraints(self) -> None:
        keys = {"SourceDocument": "id", "Article": "id", "Clause": "id", "Crime": "name",
                "CaseReport": "id", "Substance": "name", "Person": "id", "Location": "name",
                "Participation": "id", "DrugFinding": "id", "Penalty": "id", "Threshold": "id"}
        for label, key in keys.items():
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def source(self, doc: Document, removed: int = 0) -> None:
        self.run("""
            MERGE (d:SourceDocument {id:$id})
            SET d.doc_id=$id, d.title=$title, d.kb=$kb, d.url=$url, d.version=$version,
                d.excluded_teasers=$removed
            """, id=doc.id, title=doc.metadata.get("title", ""), kb=doc.metadata.get("kb", ""),
            url=doc.metadata.get("source_url", ""), version=doc.metadata.get("document_version", ""), removed=removed)

    def law(self, article: dict) -> None:
        self.run("""
            MATCH (d:SourceDocument {id:$doc_id})
            MERGE (a:Article {id:$id}) SET a.title=$title, a.law=$law, a.doc_id=$doc_id
            MERGE (d)-[:DESCRIBES]->(a)
            FOREACH (name IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name:name}) MERGE (a)-[:DEFINES]->(c))
            WITH a UNWIND $clauses AS row
            MERGE (cl:Clause {id:row.id})
            SET cl.doc_id=$doc_id, cl.number=row.number, cl.text=row.text, cl.penalty=row.penalty
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (name IN row.substances |
                MERGE (s:Substance {name:name}) MERGE (cl)-[:MENTIONS]->(s))
            """, **article)
        penalties = []
        for clause in article["clauses"]:
            penalty = penalty_band(clause, article["doc_id"])
            if penalty:
                penalties.append(penalty)
                self.run("""
                    MATCH (cl:Clause {id:$clause_id})
                    MERGE (p:Penalty {id:$id})
                    SET p.doc_id=$doc_id, p.text=$text, p.min_years=$min_years, p.max_years=$max_years,
                        p.life=$life, p.death=$death, p.severity=$severity
                    MERGE (cl)-[:HAS_PENALTY]->(p)
                    """, clause_id=clause["id"], **penalty)
            for threshold in named_mass_thresholds(clause, article["doc_id"], find_substances):
                self.run("""
                    MATCH (cl:Clause {id:$clause_id})
                    MERGE (t:Threshold {id:$id})
                    SET t.doc_id=$doc_id, t.point=$point, t.min_g=$min_g, t.max_g=$max_g,
                        t.lower_inclusive=$lower_inclusive, t.upper_inclusive=$upper_inclusive, t.text=$text
                    MERGE (cl)-[:HAS_THRESHOLD]->(t)
                    FOREACH (name IN $substances |
                        MERGE (s:Substance {name:name}) MERGE (t)-[:FOR_SUBSTANCE]->(s))
                    """, clause_id=clause["id"], **threshold)
        if penalties:
            most_severe = max(penalties, key=lambda p: (p["severity"], p["max_years"] or 0))
            self.run("MATCH (a:Article {id:$article}), (p:Penalty {id:$penalty}) MERGE (a)-[:MAX_PENALTY]->(p)",
                     article=article["id"], penalty=most_severe["id"])

    def report(self, case: dict, doc: Document) -> None:
        self.run("""
            MATCH (d:SourceDocument {id:$doc_id})
            MERGE (k:CaseReport {id:$doc_id})
            SET k.doc_id=$doc_id, k.name=$name, k.summary=$summary, k.date=$date
            MERGE (d)-[:DESCRIBES]->(k)
            FOREACH (name IN CASE WHEN $location='' THEN [] ELSE [$location] END |
                MERGE (l:Location {name:name}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (name IN $charges |
                MERGE (c:Crime {name:name}) MERGE (k)-[:ALLEGES]->(c))
            FOREACH (row IN $people |
                MERGE (p:Person {id:row.id}) ON CREATE SET p.doc_id=$doc_id
                SET p.name=row.name,
                    p.aliases=reduce(names=coalesce(p.aliases, []), name IN row.aliases |
                        CASE WHEN name IN names THEN names ELSE names+[name] END)
                MERGE (i:Participation {id:row.participation_id})
                SET i.doc_id=$doc_id, i.role=row.role, i.stage=row.stage, i.sentence=row.sentence,
                    i.evidence=row.evidence, i.verified=row.verified
                MERGE (p)-[:HAS_PARTICIPATION]->(i) MERGE (i)-[:IN_REPORT]->(k)
                FOREACH (name IN row.charges |
                    MERGE (c:Crime {name:name}) MERGE (i)-[:ACCUSED_OF]->(c)))
            FOREACH (row IN $findings |
                MERGE (f:DrugFinding {id:row.id})
                SET f.doc_id=$doc_id, f.amount=row.amount, f.mass_g=row.mass_g,
                    f.qualifier=row.qualifier, f.subject=row.subject, f.evidence=row.evidence,
                    f.verified=row.verified, f.quantity_verified=row.quantity_verified,
                    f.evidence_type=row.evidence_type
                MERGE (k)-[:HAS_FINDING]->(f)
                MERGE (s:Substance {name:row.name}) MERGE (f)-[:OF_SUBSTANCE]->(s))
            """, doc_id=doc.id, **case)

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        if max_facts <= 0:
            return []
        question = normalized_text(question)
        q = question.casefold()
        substances = find_substances(question)
        aggregation = bool(substances and re.search(r"những vụ|các vụ|vụ việc nào|vụ nào", q))
        maximum = bool(re.search(r"tối đa|cao nhất|nặng nhất", q))
        basic = "cơ bản" in q
        # seed_facts remains the ontology-independent contract helper; omit bulky nodes from edges.
        seed_ids, _ = self.seed_facts(question, doc_ids, skip_labels=("Clause", "Threshold", "Penalty"), limit=0)
        reports = self.run("""
            MATCH (k:CaseReport)
            WHERE ($aggregation AND EXISTS {
                MATCH (k)-[:HAS_FINDING]->(:DrugFinding)-[:OF_SUBSTANCE]->(s:Substance)
                WHERE s.name IN $substances
            }) OR (NOT $aggregation AND (
                k.doc_id IN $docs OR elementId(k) IN $seeds OR EXISTS {
                    MATCH (p:Person)-[:HAS_PARTICIPATION]->(:Participation)-[:IN_REPORT]->(k)
                    WHERE elementId(p) IN $seeds
                }))
            MATCH (d:SourceDocument)-[:DESCRIBES]->(k)
            RETURN k.id AS id, k.name AS name, k.summary AS summary, d.title AS title, d.url AS url
            ORDER BY id LIMIT $limit
            """, docs=doc_ids, seeds=seed_ids, aggregation=aggregation, substances=substances, limit=max_facts)
        report_ids = [row["id"] for row in reports]
        participations = self.run("""
            MATCH (p:Person)-[:HAS_PARTICIPATION]->(i:Participation)-[:IN_REPORT]->(k:CaseReport)
            WHERE k.id IN $ids
            OPTIONAL MATCH (i)-[:ACCUSED_OF]->(c:Crime)
            RETURN k.id AS report, p.name AS person, p.aliases AS aliases,
                   i.role AS role, i.stage AS stage, i.sentence AS sentence,
                   i.evidence AS evidence, i.verified AS verified, collect(c.name) AS charges
            ORDER BY report, person
            """, ids=report_ids)
        findings = self.run("""
            MATCH (k:CaseReport)-[:HAS_FINDING]->(f:DrugFinding)-[:OF_SUBSTANCE]->(s:Substance)
            WHERE k.id IN $ids AND (NOT $aggregation OR s.name IN $substances)
            RETURN k.id AS report, s.name AS substance, f.amount AS amount, f.mass_g AS mass_g,
                   f.subject AS subject, f.evidence AS evidence, f.verified AS verified,
                   f.quantity_verified AS quantity_verified, f.qualifier AS qualifier
            ORDER BY report, substance, subject
            """, ids=report_ids, aggregation=aggregation, substances=substances)
        facts = []
        if aggregation:
            facts.append(f"Danh sách đầy đủ theo graph: {len(reports)} báo cáo nguồn liên quan {', '.join(substances)}. "
                         "Bao phủ mọi báo cáo; chỉ gộp thành cùng vụ khi ngữ cảnh chứng minh, giữ nguồn.")
        for row in reports:
            actors = [p["person"] for p in participations if p["report"] == row["id"]]
            actor_states = [f"{p['person']}: giai đoạn {p['stage'] or 'chưa nêu'}, "
                            f"án riêng {p['sentence'] or 'chưa nêu'}"
                            for p in participations if p["report"] == row["id"]]
            drug_evidence = [f"{f['substance']}: {f['amount'] or 'nguồn không nêu lượng'}; "
                             f"chủ thể: {f['subject'] or 'chưa quy cho người cụ thể'}; trích: {f['evidence']}"
                             for f in findings if f["report"] == row["id"]]
            facts.append(f"Báo cáo [{row['id']}] '{row['title']} ({row['url']}): {row['summary']} "
                         f"Người: {', '.join(actors)}. Trạng thái riêng: {'; '.join(actor_states)}. "
                         f"Bằng chứng chất: {' | '.join(drug_evidence)}")
        if aggregation:
            return list(dict.fromkeys(facts))[:max_facts]
        named = [p for p in participations if entity_key(p["person"]) in q
                 or any(entity_key(alias) in q for alias in p["aliases"] or [])]
        important = named or participations
        for row in important:
            facts.append(f"[{row['report']}] {row['person']}: vai trò {row['role']}; giai đoạn {row['stage'] or 'không nêu'}; "
                         f"tội riêng {', '.join(row['charges']) or 'chưa xác định trong corpus'}; "
                         f"án {row['sentence'] or 'nguồn chưa nêu án'}. Bằng chứng: {row['evidence']}")
        crimes = sorted({c for row in important for c in row["charges"]})
        if not crimes:
            crimes = [r["crime"] for r in self.run("""
                MATCH (k:CaseReport)-[:ALLEGES]->(c:Crime) WHERE k.id IN $ids
                RETURN DISTINCT c.name AS crime ORDER BY crime
                """, ids=report_ids)]
        numbers = re.findall(r"[Đđ]iều\s+(\d+)", question)
        articles = self.run("""
            MATCH (a:Article)
            WHERE a.doc_id IN $docs OR any(number IN $numbers WHERE a.id STARTS WITH 'Điều '+number+' ')
               OR EXISTS { MATCH (a)-[:DEFINES]->(c:Crime) WHERE c.name IN $crimes }
            RETURN a.id AS id ORDER BY id
            """, docs=doc_ids, numbers=numbers, crimes=crimes)
        article_ids = [row["id"] for row in articles]
        matches = self.run("""
            MATCH (k:CaseReport)-[:HAS_FINDING]->(f:DrugFinding)-[:OF_SUBSTANCE]->(s:Substance)
                  <-[:FOR_SUBSTANCE]-(t:Threshold)<-[:HAS_THRESHOLD]-(cl:Clause)<-[:HAS_CLAUSE]-(a:Article)
            WHERE k.id IN $reports AND a.id IN $articles AND f.quantity_verified=true
              AND f.qualifier IN ['eq', 'gt'] AND f.mass_g >= t.min_g
              AND (t.max_g IS NULL OR f.mass_g < t.max_g)
              AND (size($people)=0 OR f.subject='' OR f.subject IN $people)
            RETURN DISTINCT cl.id AS clause, a.id AS article, cl.number AS number, t.point AS point,
                   f.mass_g AS grams, f.amount AS amount, f.qualifier AS qualifier,
                   f.subject AS subject, s.name AS substance,
                   t.min_g AS lower, t.max_g AS upper, t.text AS threshold_text, f.evidence AS evidence
            ORDER BY article, number DESC
            """, reports=report_ids, articles=article_ids, people=[p["person"] for p in named])
        # A strict lower bound only proves a band with no upper bound. It cannot prove an upper limit.
        matches = [m for m in matches if m["upper"] is None or m["qualifier"] == "eq"]
        if not basic:
            for row in matches:
                facts.append(f"So ngưỡng trực tiếp [{row['article']} khoản {row['number']} điểm {row['point']}]: "
                             f"{row['substance']} {row['amount']} = giá trị/ngưỡng {row['grams']:g} gam; "
                             f"người {row['subject'] or 'chưa rõ'}. Quy tắc: {row['threshold_text']}. "
                             "Đây là đối chiếu ngưỡng từ corpus, không phải phán quyết thực tế.")
        if maximum:
            highest = self.run("""
                MATCH (a:Article)-[:MAX_PENALTY]->(p:Penalty)<-[:HAS_PENALTY]-(cl:Clause)
                WHERE a.id IN $articles
                RETURN a.id AS article, cl.id AS clause, cl.number AS number,
                       p.text AS text, p.life AS life, p.death AS death
                ORDER BY article
                """, articles=article_ids)
            for row in highest:
                facts.append(f"Khung cao nhất [{row['article']} khoản {row['number']}]: {row['text']}; "
                             "khác khung cơ bản, chưa khẳng định bị can đã chịu mức này.")
        else:
            highest = []
        selected = [r["clause"] for r in matches] if not basic else []
        selected += [r["clause"] for r in highest]
        clauses = self.run("""
            MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE a.id IN $articles AND (cl.number=1 OR a.law <> 'BLHS' OR cl.id IN $selected)
            RETURN a.id AS article, a.title AS title, cl.number AS number, cl.text AS text
            ORDER BY article, number
            """, articles=article_ids, selected=selected)
        facts += [f"[{r['article']} - {r['title']}] khoản {r['number']}: {r['text']}" for r in clauses]
        return list(dict.fromkeys(facts))[:max_facts]


def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document], llm_fn: Callable[..., str]) -> None:
    graph.constraints()
    articles = [parse_law_article(doc) for doc in law_docs]
    crimes = sorted({article["crime"] for article in articles if article["crime"]})
    for doc, article in zip(law_docs, articles):
        graph.source(doc)
        graph.law(article)
    leads = article_leads(news_docs)
    for doc in news_docs:
        content, removed = without_related_teasers(doc, leads)
        graph.source(doc, removed)
        case = extract_report(doc, content, crimes, llm_fn)
        if case is not None:
            graph.report(case, doc)


GRAPH_PROMPT = """Trả lời chỉ dựa trên đoạn văn và bằng chứng graph có nguồn bên dưới.
Nêu Điều, khoản khi có. Phân biệt khung cơ bản và khung cao nhất; không coi án chưa tuyên là đã tuyên.
Đối chiếu chủ thể riêng: tội/án của một người không tự áp dụng cho mọi người trong báo cáo.
Khối lượng 'hơn' là cận dưới, 'gần/khoảng' là ước lượng; không đổi số viên thành gam.
Đối chiếu Threshold chỉ là suy luận theo corpus, không kết luận bản án thực tế nếu nguồn chưa xác nhận.
Câu tổng hợp phải bao phủ toàn bộ danh sách báo cáo trong graph, gộp bài cùng vụ khi có căn cứ và giữ nguồn.
Câu chỉ hỏi các vụ: nêu tên vụ/chủ thể/chất/nguồn, không thêm mức án hay khối lượng nếu không cần.
Số báo cáo nguồn không phải số vụ duy nhất. Không gọi mỗi báo cáo là một vụ khác biệt nếu chưa đủ bằng chứng.
Nếu không đủ thông tin, nêu phần thiếu. Không dùng kiến thức ngoài ngữ cảnh.

Dữ kiện graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""


class GraphRAGAgent:
    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store, self.graph, self.llm_fn = store, graph, llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)
        doc_ids = list(dict.fromkeys(chunk["metadata"]["doc_id"] for chunk in chunks))
        facts = self.graph.context(question, doc_ids)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {fact}" for fact in facts),
            chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, 1)), question=question)
        return self.llm_fn(prompt)
