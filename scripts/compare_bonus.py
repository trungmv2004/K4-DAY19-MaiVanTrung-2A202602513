"""Generate the bonus comparison and report from actual benchmark/audit files.

Never changes benchmark answers or scores. Run after benchmark, audit and capture.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_benchmark(path: Path) -> dict:
    text = path.read_text(encoding='utf-8')
    header = text.splitlines()[0]
    nodes, rels = map(int, re.search(r'KG: (\d+) nodes / (\d+) rels', header).groups())
    tables = text.split('== Indexing (one-off)', 1)[1].split('== Per question', 1)[0].strip()
    indexing, querying = tables.split('== Querying (mean per question)')
    indices = {}
    queries = {}
    for line in indexing.splitlines():
        fields = line.split()
        if fields and fields[0] in ('flat', 'graph'):
            indices[fields[0]] = dict(zip(('calls','input','output','usd','seconds'), map(float,fields[1:])))
    for line in querying.splitlines():
        fields = line.split()
        if fields and fields[0] in ('flat', 'graph'):
            queries[fields[0]] = dict(zip(('recall','judge','input','output','usd','seconds'),map(float,fields[1:])))
    questions = {}
    pattern = r'^--- (Q\d+) \[([^\]]+)\] (flat|graph) recall=([\d.]+) judge=(\d+) ([\d.]+)s\n(.*?)(?=^--- |\Z)'
    for match in re.finditer(pattern, text, re.M | re.S):
        q, kind, pipeline, recall, judge, seconds, answer = match.groups()
        questions[(q,pipeline)] = {'type':kind,'recall':float(recall),'judge':int(judge),'seconds':float(seconds),'answer':answer.strip()}
    assert len(questions)==12, 'Need all 12 judged answers'
    return {'header':header,'text':text,'tables':'== Indexing (one-off)\n'+tables,
            'nodes':nodes,'rels':rels,'indexing':indices,'querying':queries,'questions':questions}


def f(value: float, decimals=2) -> str:
    return f'{value:.{decimals}f}'.replace('.', ',')


def quoted(answer: str) -> str:
    return '\n'.join(('> '+line.rstrip()) if line.strip() else '>' for line in answer.splitlines())


def main() -> None:
    before = read_benchmark(ROOT/'ket_qua_benchmark_kg.hint.txt')
    after = read_benchmark(ROOT/'ket_qua_benchmark_kg.txt')
    old_audit = json.loads((ROOT/'report/hint/audit_kg.json').read_text(encoding='utf-8'))
    audit = json.loads((ROOT/'report/audit_kg.json').read_text(encoding='utf-8'))
    assert audit['stats']=={'nodes':after['nodes'],'relationships':after['rels']}
    assert len(audit['node_counts']['rows'])==12
    assert len(audit['relationship_counts']['rows'])==15
    conditions = lambda header: header.split(' | KG:')[0]
    assert conditions(before['header'])==conditions(after['header']), 'Models/corpus/retrieval settings differ'
    hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
              for name in ('ket_qua_benchmark_kg.hint.txt','ket_qua_benchmark_kg.txt','src/hint_graph.py','src/graph.py','src/legal_evidence.py')}
    assert hashes['ket_qua_benchmark_kg.hint.txt']=='3929325c471017bae202d20f8f3f3386aca6de1c2e054f7c1502f796f33afd1d'
    assert hashes['src/hint_graph.py']=='631e81acc54dd219ce5b51ca14d7a63b9f27182fb662af30df8f75a71b7e01dc'
    comparisons = [{'id':q,'hint':before['questions'][(q,'graph')],'bonus':after['questions'][(q,'graph')]}
                   for q in ('Q1','Q2','Q3','Q4','Q5','Q6')]
    metadata = {'date':'2026-10-05','baseline_preserved_byte_for_byte':True,'sha256':hashes,
                'hint_header':before['header'],'bonus_header':after['header'],
                'indexing':{'hint':before['indexing']['graph'],'bonus':after['indexing']['graph']},
                'querying':{'hint':before['querying']['graph'],'bonus':after['querying']['graph']},
                'questions':comparisons,
                'comparison_scope':'Complete pipelines, including ontology/extraction/teaser preprocessing/retrieval/prompt; one run per final pipeline.'}
    (ROOT/'report/bonus_comparison.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    fi, gi = after['indexing']['flat'], after['indexing']['graph']
    fq, gq = after['querying']['flat'], after['querying']['graph']
    bi, bq = before['indexing']['graph'], before['querying']['graph']
    ratio_rows = '\n'.join(f'| {label} | {f(left,precision)} | {f(right,precision)} | {f(right/left)}× |'
                          for label,left,right,precision in [
                              ('Indexing USD',fi['usd'],gi['usd'],5),('Indexing giây',fi['seconds'],gi['seconds'],1),
                              ('Mỗi câu USD',fq['usd'],gq['usd'],5),('Mỗi câu giây',fq['seconds'],gq['seconds'],2),
                              ('Mỗi câu input token',fq['input'],gq['input'],0)])
    why = {'Q1':'Định nghĩa nằm trong một đoạn luật; KG bổ sung căn cứ.',
           'Q2':'Tên và án nằm trong một bài; Participation giữ án riêng theo người.',
           'Q3':'Nối sentence trong tin với Điều 251 khoản 1.',
           'Q4':'MAX_PENALTY lấy được khoản 4 Điều 255 dù không nhắc chất.',
           'Q5':'DrugFinding và Threshold đối chiếu lượng có nguồn với ngưỡng của Điều 250.',
           'Q6':'Graph tăng keyword recall; judge hòa và đáp án vẫn tách người cùng nguồn thành nhiều vụ.'}
    qrows=[]
    bonus_rows=[]
    for q in ('Q1','Q2','Q3','Q4','Q5','Q6'):
        flat, graph = after['questions'][(q,'flat')],after['questions'][(q,'graph')]
        winner='Graph' if (graph['judge'],graph['recall'])>(flat['judge'],flat['recall']) else 'Hòa chất lượng' if (graph['judge'],graph['recall'])==(flat['judge'],flat['recall']) else 'Flat'
        qrows.append(f"| {q} | {flat['type']} | {f(flat['recall'])} / {flat['judge']} | {f(graph['recall'])} / {graph['judge']} | {winner} | {why[q]} |")
        old=before['questions'][(q,'graph')]
        bonus_rows.append(f"| {q} | {f(old['recall'])} / {old['judge']} | {f(graph['recall'])} / {graph['judge']} |")
    delta_index=gi['usd']-bi['usd']
    saving=bq['usd']-gq['usd']
    breakeven=(f'Khoảng **{math.ceil(delta_index/saving)} câu** để tiết kiệm tiền hỏi bù indexing tăng thêm: '
               f'{f(delta_index,5)} / {f(saving,5)}; chỉ tính USD đã làm tròn, cùng kiểu tải câu hỏi.'
               if delta_index>0 and saving>0 else 'Không có điểm hòa vốn dương theo các số đã đo này.')
    node_counts=', '.join(f"{r['label']}={r['n']}" for r in audit['node_counts']['rows'])
    rel_counts=', '.join(f"{r['rel']}={r['n']}" for r in audit['relationship_counts']['rows'])
    check=(ROOT/'report/validation_bonus_check.txt').read_text(encoding='utf-8-sig').strip()
    tests=(ROOT/'report/validation_bonus_tests.txt').read_text(encoding='utf-8-sig').strip()
    old_huy=json.dumps(old_audit['huy_provenance']['rows'],ensure_ascii=False,indent=2)
    new_huy=json.dumps(audit['huy_provenance']['rows'],ensure_ascii=False,indent=2)
    thresholds=json.dumps(audit['huy_numeric_rule']['rows'],ensure_ascii=False,indent=2)
    maximum=json.dumps(audit['maximum_255']['rows'],ensure_ascii=False,indent=2)
    unverified=json.dumps(audit['unverified_quantities']['rows'][:3],ensure_ascii=False,indent=2)
    full_scores=sum(after['questions'][(q,'graph')]['judge']==2 for q in ('Q1','Q2','Q3','Q4','Q5','Q6'))
    text=f'''# Báo cáo Day 19 — Flat RAG vs GraphRAG, ontology bonus

**Họ tên:** Mai Văn Trung  **MSSV:** 2A202602513  **Ngày:** 05/10/2026

Corpus: 18 điều luật, 20 bài báo, 176 chunk; top_k=3, chunk_size=800. Chat và embedding đều qua OpenRouter với `openai/gpt-4o-mini` và `openai/text-embedding-3-small`. Giữ nguyên benchmark và 48 test gốc. Thiết kế mới: [ONTOLOGY.md](ONTOLOGY.md), **12 label, 15 loại cạnh**; graph cuối **{after['nodes']} node / {after['rels']} cạnh**. Các file benchmark đều sinh từ code với --judge, không sửa đáp án hay điểm.

Kết quả cuối: [ket_qua_benchmark_kg.txt](../ket_qua_benchmark_kg.txt). Baseline gợi ý: [ket_qua_benchmark_kg.hint.txt](../ket_qua_benchmark_kg.hint.txt). Bằng chứng trước/sau: [audit cũ](hint/audit_kg.json), [audit mới](audit_kg.json), [so sánh có hash](bonus_comparison.json).

## 1. Chi phí

Hai bảng chép nguyên từ benchmark cuối:

```text
{after['tables']}
```

| Chỉ số | Flat | Graph bonus | Graph / Flat |
| --- | --- | --- | --- |
{ratio_rows}

Graph indexing bao gồm cùng vector index của Flat, cộng dựng KG: thêm {int(gi['calls']-fi['calls'])} lần chat, {int(gi['input']-fi['input'])} input token, {int(gi['output']-fi['output'])} output token, khoảng **{f(gi['usd']-fi['usd'],5)} USD / {f(gi['seconds']-fi['seconds'],1)} giây**. Luật và ngưỡng trích bằng regex; chat dùng cho tin. Mỗi câu Graph thêm khoảng {int(gq['input']-fq['input'])} input token vì dữ kiện có nguồn và luật, đổi lại cung cấp đường nối mà Flat thiếu.

Giá ước tính trong src/llm.py đối chiếu ngày 05/10/2026: [OpenRouter GPT-4o-mini](https://openrouter.ai/openai/gpt-4o-mini) 0,15/0,60 USD mỗi triệu input/output token; [embedding](https://openrouter.ai/openai/text-embedding-3-small) 0,02 USD/triệu token. USD dựa trên token, không phải số dư tài khoản; chưa tính cache, phí nạp credit, phần cứng và Docker. LLM judge nằm ngoài chi phí pipeline; các lần check, thử nghiệm và lượt dừng không nằm trong bảng kết quả cuối. Benchmark tái sử dụng cùng vector index, không cộng hai indexing độc lập khi tính tiền thực chạy.

So với Flat, Graph vẫn tăng cả indexing và tiền hỏi, nên không có hòa vốn về USD thuần túy. Chi phí tăng thêm cho N câu xấp xỉ `{gi['usd']-fi['usd']:.5f} + N × {gq['usd']-fq['usd']:.5f} USD`; lợi ích cần đánh giá bằng giá trị câu trả lời đủ căn cứ.

## 2. Từng câu hỏi

| Câu | Loại | Flat recall / judge | Graph bonus recall / judge | Thắng | Vì sao |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(qrows)}

Graph bonus có **{full_scores}/6 câu judge=2**. Recall và judge là hai phép đo khác nhau: keyword recall không kiểm mức án/tội đúng; judge dựa trên gold và có thể chấm sai. Điểm không phải kiểm chứng độc lập về pháp luật hiện hành. Q1–Q2 nằm gọn một đoạn nên Flat đủ; Q3–Q5 cần nối tin với luật; Q6 cần bao phủ nhiều nguồn và phân biệt số báo cáo với số vụ.

## 3. Phân tích lỗi và bằng chứng sửa

### E2 — Khung cao nhất bị bỏ do lọc khoản theo chất (đã sửa)

**Hiện tượng:** Q4 ontology gợi ý trả tối đa 7 năm, dù Điều 255 khoản 4 có 20 năm hoặc chung thân.

**Bằng chứng trước:** nguyên văn Q4 Graph trong file .hint.txt:

{quoted(before['questions'][('Q4','graph')]['answer'])}

**Nguyên nhân:** lấy khoản 1 và khoản MENTIONS chất; khoản 4 Điều 255 không nhắc chất. LLM biến khung cơ bản thành tối đa. Ontology cũ không có quan hệ biểu diễn khung cao nhất.

**Sửa cụ thể:** thêm Penalty với life/death/số năm/severity, HAS_PENALTY và Article MAX_PENALTY, giữ Clause áp dụng. src/graph.py.context dùng đường này cho câu hỏi tối đa, không cần chất xuất hiện trong khoản. src/legal_evidence.py xếp khung từ chính văn bản corpus. Đánh đổi: tăng node/quan hệ và công dựng graph; tránh kéo hết khoản cho mọi câu.

Truy vấn sau:

```cypher
{audit['maximum_255']['cypher']};
```

```json
{maximum}
```

**Bằng chứng sau:** nguyên văn Q4 Graph trong benchmark cuối:

{quoted(after['questions'][('Q4','graph')]['answer'])}

### E3 — Teaser tạo một vụ Cái Quang Huy trong bài Lê Minh Thành (đã giảm)

**Hiện tượng:** hai Case dùng tên khác nhau mô tả Huy, một cái sinh từ đoạn bài liên quan ở cuối nguồn Lê Minh Thành. Đáp án Q6 cũ lặp vụ Huy.

**Bằng chứng trước:** Cypher và kết quả lưu trong audit gợi ý:

```cypher
{old_audit['huy_provenance']['cypher']};
```

```json
{old_huy}
```

**Nguyên nhân:** nguồn chưa phân biệt teaser với thân bài và khóa Case là tên LLM tự đặt. MERGE theo tên không giải quyết hai tên cùng một vụ, có thể ghi đè nguồn.

**Sửa cụ thể:** SourceDocument và CaseReport theo doc_id tách báo cáo khỏi mã vụ thật; Participation giữ tội/án theo nguồn. Quy tắc trong src/legal_evidence.py loại đoạn cuối khớp nguyên văn lead của bài khác trong cùng corpus; không dùng tên người hoặc gold cố định. src/graph.py trích vụ chính. Đánh đổi: chưa tự merge mọi báo cáo cùng vụ; teaser ngoài corpus/đổi chữ vẫn có thể còn. Giữ nhiều báo cáo có nguồn là chủ đích, không gọi số node là số vụ duy nhất.

Truy vấn sau và kết quả:

```cypher
{audit['huy_provenance']['cypher']};
```

```json
{new_huy}
```

### E6 — Lượng chưa đủ bằng chứng số hoặc đơn vị chưa hỗ trợ (còn giới hạn)

**Hiện tượng:** một số DrugFinding giữ amount nguyên văn nhưng không có mass_g dùng để so ngưỡng. Đây có thể là thiếu đơn vị hợp lệ như “5 viên”, hoặc quote LLM không khớp nguồn.

**Bằng chứng:** truy vấn sau; hiển thị tối đa 3 hàng, toàn bộ ở audit_kg.json:

```cypher
{audit['unverified_quantities']['cypher']};
```

```json
{unverified}
```

**Nguyên nhân:** corpus chưa có khối lượng cho số viên/chỉ; quote được LLM trích có thể đổi câu chữ hoặc không chứng minh lượng. Kiểm quote/mass không kiểm entailment toàn bộ chủ thể. Xấp xỉ “gần/khoảng” cũng không đủ kết luận ngưỡng pháp lý, kể cả khi mass_g biểu diễn được giá trị ước lượng.

**Đề xuất:** giữ giá trị chưa biết, không điền số giả; bổ sung extraction schema/kiểm chủ thể và trích dẫn, kiểm lại nguồn khi LLM quote sai; chỉ đổi đơn vị có căn cứ. Regex tổng lượng đã bổ sung mẫu trách nhiệm trong nguồn, không tự cộng lượng thu giữ và tổng. Đánh đổi: thêm logic/kiểm toán, có thể tăng token nếu phải trích lại; chưa giải quyết đơn vị vật phẩm hay mọi cấu trúc văn bản.

### E4 — Recall/judge chưa chứng minh độ đúng

**Bằng chứng còn lại ở Q6 Graph bonus (recall=1,00, judge=1):** câu trả lời gọi “4 vụ việc”, nhưng mục Cái Quang Huy và mục Nguyễn Hữu Đức cùng dẫn `news-100260917203001265`. Câu trả lời không dẫn nguồn `news-100260924105118645` dù graph có báo cáo này trong mdma_cases; không thể kết luận đã liệt kê đúng mọi vụ duy nhất. Nguyên văn đáp án cuối:

{quoted(after['questions'][('Q6','graph')]['answer'])}

Q4 gợi ý recall=0,67 dù sai tối đa; Q6 Flat recall=0 nhưng judge=1 vì tên rút gọn. Điểm judge có thể khác mức bao phủ nguồn thực tế, nhất là gold Q6 gộp vụ viện trong khi nhiều bài cùng nói về viện/Sầm Sơn. Nguyên nhân là so chuỗi và LLM judge dựa trên gold. Đề xuất chấm theo trường/nguồn/tập vụ có định danh và rà soát gold, đổi lại tốn công gán nhãn. Giữ nguyên benchmark gốc theo quy định.

## 4. Kết luận

Graph bonus đạt recall trung bình **{f(gq['recall'])}**, judge **{f(gq['judge'])}**, so với Flat **{f(fq['recall'])}/{f(fq['judge'])}**; chi phí hỏi **{f(gq['usd']/fq['usd'])}×**, input token **{f(gq['input']/fq['input'])}×**. Nên dùng KG khi cần nối người/tội trong tin với điều/khoản/ngưỡng hoặc tổng hợp nhiều nguồn. Flat đủ cho Q1–Q2 single-hop. Phần so sánh bonus dưới cho thấy tác động thực của thiết kế.

KG vẫn cần kiểm dữ liệu: CaseReport không là mã vụ thật, tên người có thể trùng, quote/subject có thể trích sai, ngưỡng chỉ áp dụng phạm vi hỗ trợ. Một lần chạy và sáu câu chưa chứng minh độ ổn định hay hiệu quả ở corpus lớn. Mọi suy luận luật chỉ dựa trên phiên bản corpus lab.

## 5. Tự kiểm

```text
$ .venv/Scripts/python.exe -m pytest tests/ -q
{tests}

$ .venv/Scripts/python.exe -u bench_kg.py --check
{check}
```

58 test gồm **48 test gốc không sửa** và **10 test bonus**; check dựng luật + một bài, khác graph đầy đủ của benchmark. Check chạy trước benchmark; không chạy lại sau để tránh thay graph đầy đủ bằng graph nhỏ.

Graph cuối: {node_counts}; tổng {after['nodes']}. Quan hệ: {rel_counts}; tổng {after['rels']}. Có {audit['bridge_count']['rows'][0]['paths']} đường CaseReport → Crime ← Article. Các label/cạnh khớp ONTOLOGY.md.

Ba ảnh chụp nguyên cửa sổ Chrome trên graph bonus, không dùng ảnh mẫu, không cắt/chỉnh ảnh. Chạy :clear trước từng truy vấn; thu sidebar/zoom trình duyệt để thấy đủ 12 label. Chọn **Trần Thanh Tuấn**, tội trên Participation riêng, khác người mẫu Lê Minh Thành.

![Đếm 12 label](img/kg_count.png)

![Cầu nối nguồn tin và luật](img/kg_cross_kb.png)

![Trần Thanh Tuấn và căn cứ tội riêng](img/kg_my_case.png)

## 6. Bonus +15 — so sánh ontology trước/sau

| Chỉ số GraphRAG | Gợi ý | Bonus |
| --- | --- | --- |
| Node / cạnh | {before['nodes']} / {before['rels']} | {after['nodes']} / {after['rels']} |
| Indexing USD | {f(bi['usd'],5)} | {f(gi['usd'],5)} |
| Indexing giây | {f(bi['seconds'],1)} | {f(gi['seconds'],1)} |
| Recall trung bình | {f(bq['recall'])} | {f(gq['recall'])} |
| Judge trung bình | {f(bq['judge'])} | {f(gq['judge'])} |
| Input token/câu | {f(bq['input'],0)} | {f(gq['input'],0)} |
| USD/câu | {f(bq['usd'],5)} | {f(gq['usd'],5)} |
| Giây/câu | {f(bq['seconds'])} | {f(gq['seconds'])} |

| Câu | Gợi ý recall / judge | Bonus recall / judge |
| --- | --- | --- |
{chr(10).join(bonus_rows)}

Khác biệt có chủ đích và vấn đề giải quyết ghi đủ ở ONTOLOGY.md mục 7: nguồn/báo cáo, Participation theo giai đoạn/chủ thể, DrugFinding + Threshold, Penalty + MAX_PENALTY. Q4 là competency question pipeline gợi ý trả sai và pipeline mới có đường lấy đúng khung tối đa. Q5 bổ sung đối chiếu lượng có trích nguồn bằng Cypher, không chỉ nhờ LLM đọc số.

```cypher
{audit['huy_numeric_rule']['cypher']};
```

```json
{thresholds}
```

Điểm hòa vốn giữa **Graph bonus và Graph gợi ý**: {breakeven} Không phải hòa vốn với Flat. Các phép tính dùng bảng đã làm tròn.

Baseline .hint.txt và src/hint_graph.py được giữ byte-for-byte, có SHA-256 trong bonus_comparison.json. Cùng corpus/model/provider/top_k/chunk_size; đây là so sánh hai pipeline hoàn chỉnh, có cả thay đổi extraction, teaser, truy vấn và prompt. Không kết luận mọi mức tăng do riêng cấu trúc ontology; không có nhiều lần đo để loại nhiễu LLM/mạng.

Tái lập (mỗi benchmark xóa/dựng graph):

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:LAB_SOLUTION_PACKAGE='src_hint'
.venv/Scripts/python.exe bench_kg.py --judge --out ket_qua_benchmark_kg.hint.rerun.txt
Remove-Item Env:LAB_SOLUTION_PACKAGE
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe bench_kg.py --check
.venv/Scripts/python.exe bench_kg.py --judge
.venv/Scripts/python.exe scripts/audit_kg.py
.venv/Scripts/python.exe scripts/capture_neo4j.py
.venv/Scripts/python.exe scripts/compare_bonus.py
```

Muốn report tái lập khớp lần mới, cập nhật log test/check; không sửa tay file benchmark. Chụp ảnh cần Windows/Chrome và requirements-browser.txt.

## Vấn đề gặp phải và giới hạn

- Lần thử đầu của bonus phát hiện LLM bỏ tổng lượng quy trách nhiệm; đã bổ sung regex lấy tổng có chủ thể duy nhất và ranh giới câu, kiểm bằng test. Bảng chính chỉ phản ánh code cuối, không trộn lượt thử.
- Một số CaseReport có thể thiếu tội hoặc thông tin vì bài không nêu tội trong corpus, ngoài chương luật hoặc extraction sai; xem broken_bridges. Không tự tạo căn cứ pháp lý không có trong nguồn.
- API key/venv được gitignore; không đưa key vào code/báo cáo. Bài làm được commit cục bộ; **chưa push, chưa nộp link**. Neo4j vẫn chạy để xem graph; có thể tắt khi không dùng.
'''
    (ROOT/'report/REPORT_KG.md').write_text(text,encoding='utf-8')
    print(f'Generated report/REPORT_KG.md and report/bonus_comparison.json: {after["nodes"]} nodes / {after["rels"]} rels')
    print(f'Graph recall/judge: hint {bq["recall"]}/{bq["judge"]} -> bonus {gq["recall"]}/{gq["judge"]}')


if __name__=='__main__':
    main()
