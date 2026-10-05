"""Read-only graph evidence for the report; run after bench_kg.py --judge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.graph import Neo4jGraph

QUERIES = {
    "node_counts": "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC",
    "relationship_counts": "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC",
    "bridge_count": "MATCH (:CaseReport)-[:ALLEGES]->(:Crime)<-[:DEFINES]-(:Article) RETURN count(*) AS paths",
    "broken_bridges": "MATCH (k:CaseReport) WHERE NOT (k)-[:ALLEGES]->() RETURN k.name AS name, k.doc_id AS doc_id ORDER BY doc_id",
    "substances": "MATCH (s:Substance) RETURN s.name AS name ORDER BY toLower(s.name)",
    "blank_charges": "MATCH (p:Person)-[:HAS_PARTICIPATION]->(i:Participation)-[:IN_REPORT]->(k:CaseReport) WHERE NOT (i)-[:ACCUSED_OF]->() RETURN p.name AS person, i.role AS role, k.name AS case_name, k.doc_id AS doc_id ORDER BY doc_id, person",
    "mdma_cases": "MATCH (k:CaseReport)-[:HAS_FINDING]->(f:DrugFinding)-[:OF_SUBSTANCE]->(:Substance {name:'MDMA'}) RETURN k.name AS case_name, k.doc_id AS doc_id, f.amount AS amount, f.mass_g AS mass_g, f.qualifier AS qualifier, f.subject AS subject, f.evidence AS evidence, f.verified AS verified ORDER BY doc_id, subject, amount",
    "hoang_nato": "MATCH (p:Person)-[:HAS_PARTICIPATION]->(i:Participation)-[:IN_REPORT]->(k:CaseReport) WHERE p.name='Dương Minh Tuấn' OR 'Hoàng Nato' IN coalesce(p.aliases, []) OPTIONAL MATCH (i)-[:ACCUSED_OF]->(c:Crime)<-[:DEFINES]-(a:Article) OPTIONAL MATCH (a)-[:MAX_PENALTY]->(pen:Penalty) RETURN p.name AS person, i.role AS role, i.stage AS stage, k.doc_id AS doc_id, c.name AS charge, a.id AS article, pen.text AS maximum ORDER BY doc_id",
    "article_255": "MATCH (:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(cl:Clause) RETURN cl.number AS number, cl.penalty AS penalty, cl.text AS text ORDER BY number",
    "huy_provenance": "MATCH (p:Person {name:'Cái Quang Huy'})-[:HAS_PARTICIPATION]->(i:Participation)-[:IN_REPORT]->(k:CaseReport) OPTIONAL MATCH (i)-[:ACCUSED_OF]->(c:Crime) RETURN p.name AS person, k.name AS case_name, k.doc_id AS doc_id, collect(c.name) AS charges, i.stage AS stage, i.sentence AS sentence ORDER BY doc_id",
    "my_case": "MATCH (p:Person {name:'Trần Thanh Tuấn'})-[:HAS_PARTICIPATION]->(i:Participation)-[:ACCUSED_OF]->(c:Crime)<-[:DEFINES]-(a:Article) MATCH (i)-[:IN_REPORT]->(k:CaseReport) RETURN p.name AS person, i.sentence AS sentence, k.name AS case_name, c.name AS crime, a.id AS article",
    "maximum_255": "MATCH (a:Article {id:'Điều 255 BLHS'})-[:MAX_PENALTY]->(p:Penalty)<-[:HAS_PENALTY]-(cl:Clause) RETURN a.id AS article, cl.number AS number, p.text AS penalty, p.life AS life, p.death AS death",
    "mdma_thresholds_250": "MATCH (:Article {id:'Điều 250 BLHS'})-[:HAS_CLAUSE]->(cl:Clause)-[:HAS_THRESHOLD]->(t:Threshold)-[:FOR_SUBSTANCE]->(:Substance {name:'MDMA'}) RETURN cl.number AS number, t.point AS point, t.min_g AS min_g, t.max_g AS max_g, t.text AS text ORDER BY number",
    "huy_numeric_rule": "MATCH (k:CaseReport)-[:HAS_FINDING]->(f:DrugFinding)-[:OF_SUBSTANCE]->(s:Substance {name:'MDMA'})<-[:FOR_SUBSTANCE]-(t:Threshold)<-[:HAS_THRESHOLD]-(cl:Clause)<-[:HAS_CLAUSE]-(a:Article {id:'Điều 250 BLHS'}) WHERE f.subject='Cái Quang Huy' AND f.quantity_verified=true AND f.qualifier IN ['eq','gt'] AND f.mass_g>=t.min_g AND (t.max_g IS NULL OR (f.qualifier='eq' AND f.mass_g<t.max_g)) RETURN k.doc_id AS doc_id, f.amount AS raw_amount, f.mass_g AS mass_g, f.qualifier AS qualifier, cl.number AS number, t.point AS point, t.min_g AS min_g, t.max_g AS max_g",
    "teaser_sources": "MATCH (d:SourceDocument) WHERE d.excluded_teasers>0 RETURN d.id AS doc_id, d.excluded_teasers AS excluded ORDER BY doc_id",
    "unverified_quantities": "MATCH (f:DrugFinding) WHERE f.amount <> '' AND NOT f.quantity_verified RETURN f.doc_id AS doc_id, f.amount AS amount, f.qualifier AS qualifier, f.verified AS quote_verified, f.evidence AS evidence ORDER BY doc_id, amount",
}


def main() -> None:
    load_dotenv(ROOT / '.env')
    graph = Neo4jGraph(os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
                       os.getenv('NEO4J_USER', 'neo4j'), os.getenv('NEO4J_PASSWORD', 'password123'))
    try:
        evidence = {key: {'cypher': cypher, 'rows': graph.run(cypher)} for key, cypher in QUERIES.items()}
        q4 = "Giang hồ 'Hoàng Nato' bị bắt về hành vi gì, và hành vi đó có thể bị phạt tù tối đa bao nhiêu theo Bộ luật Hình sự?"
        evidence['q4_context'] = graph.context(q4, ['news-100260925144412498'])
        evidence['q6_context_without_vector'] = graph.context('Những vụ việc nào trong tin tức có liên quan đến ma túy MDMA?', [])
        evidence['q5_context'] = graph.context('Cái Quang Huy bị truy tố về tội gì với loại ma túy nào? Với khối lượng MDMA trong vụ này, khoản nào của điều luật tương ứng được áp dụng và khung hình phạt là gì?', ['news-100260917203001265'])
        evidence['stats'] = graph.stats()
        out = ROOT / 'report' / 'audit_kg.json'
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Saved {out.relative_to(ROOT)}: {evidence["stats"]}')
        for name in ('broken_bridges', 'blank_charges', 'mdma_cases', 'hoang_nato', 'huy_provenance', 'my_case'):
            print(name, json.dumps(evidence[name]['rows'], ensure_ascii=False))
    finally:
        graph.close()


if __name__ == '__main__':
    main()
