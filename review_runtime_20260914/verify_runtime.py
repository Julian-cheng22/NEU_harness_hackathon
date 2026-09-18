"""Review-only driver; imports existing mechanisms without modifying them."""
import os
os.environ['HARNESS_LLM'] = 'local'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys, json, time, hashlib, re, random, argparse
from pathlib import Path
from dataclasses import asdict
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from harness.db import DbConfig, connect, run_query, assert_read_only, _apply_row_cap
from harness import agent, joins, schema_card
from harness.llm import LocalLLM, LLMResponse, ToolCall
from eval.grade import matches
from eval.run import load_questions, gold_rows, run_arm, summarize
CFG = DbConfig()

def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')

def sql(query):
    with connect(CFG) as conn, conn.cursor() as cur:
        cur.execute(query)
        return list(cur.fetchall())

# Frozen before any inference. These are new same-database questions, not a
# sealed benchmark or a cross-database generalization claim.
FRESH = [
 {'id':'R01','defect_ids':['D5'],'question':"What is the average numeric event_value for 'export.csv', excluding missing or non-numeric values? Return just the average.", 'gold_sql':"SELECT AVG(CAST(event_value AS DECIMAL(18,4))) AS avg_value FROM usage_events WHERE feature_key='export.csv' AND event_value REGEXP '^[0-9]+(\\\\.[0-9]+)?$'"},
 {'id':'R02','defect_ids':['D10','D4'],'question':"What is the total paid invoice amount in US dollars? Include invoices marked paid even if their payment timestamp is missing. Return just the total.", 'gold_sql':"SELECT SUM(CASE WHEN currency_minor=1 THEN amount/100 WHEN currency_minor=0 THEN amount WHEN issued_at<'2025-07-01' THEN amount/100 ELSE amount END) FROM invoices WHERE status='paid'"},
 {'id':'R03','defect_ids':['D7'],'question':"How many usage events occurred on 2026-07-01 in UTC? Return just the count.", 'gold_sql':"SELECT COUNT(*) FROM usage_events WHERE (CASE WHEN source='batch' THEN event_ts+INTERVAL 4 HOUR ELSE event_ts END)>='2026-07-01' AND (CASE WHEN source='batch' THEN event_ts+INTERVAL 4 HOUR ELSE event_ts END)<'2026-07-02'"},
 {'id':'R04','defect_ids':['D3'],'question':"What is the current list price of the API Add-on (SKU-ADD-API)? Return just the price.", 'gold_sql':"SELECT list_price_usd FROM product_catalog WHERE sku='SKU-ADD-API' AND effective_to IS NULL"},
 {'id':'R05','defect_ids':['D10'],'question':"What is the total value of lost deals, net of tax, in US dollars? Return just the total.", 'gold_sql':"SELECT SUM(amount/1.08) FROM deals WHERE status='lost'"},
 {'id':'R06','defect_ids':['D8'],'question':"How many priority P1 support tickets refer to a non-null customer key that has no matching customer record? Return just the count.", 'gold_sql':"SELECT COUNT(*) FROM support_tickets t LEFT JOIN customers c ON c.customer_id=t.cust_id WHERE t.priority='P1' AND t.cust_id IS NOT NULL AND c.customer_id IS NULL"},
 {'id':'R07','defect_ids':[],'question':"How many employees are on the Engineering team? Return just the count.", 'gold_sql':"SELECT COUNT(*) FROM employees WHERE team='Engineering'"},
 {'id':'R08','defect_ids':[],'question':"How many support tickets are marked priority P2? Return just the count.", 'gold_sql':"SELECT COUNT(*) FROM support_tickets WHERE priority='P2'"},
]

class Scripted:
    def __init__(self, responses): self.responses=iter(responses)
    def chat(self, *args, **kwargs):
        response=next(self.responses)
        if isinstance(response, Exception): raise response
        return response

def audit():
    save('fresh_questions_frozen.json', FRESH)
    result={}
    result['environment']={'provider':'local','model':LocalLLM().model,'thinking':LocalLLM().enable_thinking,'db':sql('SELECT VERSION() AS version, DATABASE() AS db'),'grants':sql('SHOW GRANTS')}
    qs=load_questions(); gold={q['id']:sql(q['gold_sql']) for q in qs}
    result['gold_execution']={'total':len(gold),'nonempty':sum(bool(v) for v in gold.values())}
    result['replay']={}
    for arm in ['baseline','harness']:
        records=json.loads((ROOT/'eval'/'out'/f'{arm}-local.json').read_text())['records']
        replay=[]
        for r in records:
            out=run_query(r['final_sql'],CFG)
            actual=bool(out['rows']) and matches(gold[r['id']],out['rows'])
            replay.append({'id':r['id'],'saved_correct':r['correct'],'replayed_correct':actual,'gold':gold[r['id']],'candidate':out['rows'],'error':out['error']})
        result['replay'][arm]={'correct':sum(r['replayed_correct'] for r in replay),'changed':[r['id'] for r in replay if r['saved_correct']!=r['replayed_correct']],'records':replay}
    result['q05_semantic_check']=sql("SELECT SUM(status='won') AS won_deals,SUM(status='lost') AS lost_deals FROM deals")
    result['d6_counts']=sql("SELECT COUNT(*) AS rows_n,COUNT(DISTINCT company_name) AS raw_distinct,COUNT(DISTINCT LOWER(REGEXP_REPLACE(company_name,'[^a-zA-Z0-9]',''))) AS gold_distinct FROM customers")
    result['d6_injected_pairs']=sql("SELECT d.customer_id AS duplicate_id,d.company_name AS variant,c.customer_id AS original_id,c.company_name AS original_name,LOWER(REGEXP_REPLACE(d.company_name,'[^a-zA-Z0-9]',''))=LOWER(REGEXP_REPLACE(c.company_name,'[^a-zA-Z0-9]','')) AS gold_merges FROM customers d JOIN customers c ON c.customer_id<=1120 AND d.customer_id>1120 AND (d.company_name=CONCAT(c.company_name,', Inc.') OR d.company_name=CONCAT(c.company_name,' Inc') OR d.company_name=CONCAT(c.company_name,' Incorporated') OR d.company_name=CONCAT(c.company_name,' LLC') OR d.company_name=CONCAT(c.company_name,' Ltd.')) ORDER BY d.customer_id,c.customer_id")
    result['grader_edges']={
      'different_large_counts_accepted':matches([{'n':100000000}],[{'n':100000001}]),
      'different_cent_totals_accepted':matches([{'n':10354399.06}],[{'n':10354399.46}]),
      'inconsistent_column_swaps_accepted':matches([{'a':1,'b':2},{'a':3,'b':4}],[{'a':2,'b':1},{'a':3,'b':4}]),
      'wrong_explicit_order_accepted':matches([{'industry':'Energy'},{'industry':'Fintech'}],[{'industry':'Fintech'},{'industry':'Energy'}]),
    }
    result['row_caps']=[]
    for label,query in [('default','SELECT event_id FROM usage_events'),('explicit_large_limit','SELECT event_id FROM usage_events LIMIT 250'),('union','SELECT customer_id AS id FROM customers UNION ALL SELECT customer_id AS id FROM customers')]:
        out=run_query(query,CFG)
        result['row_caps'].append({'case':label,**{k:out[k] for k in ['ok','row_count','truncated','sql','error']}})
    box=agent.make_toolbox(CFG)
    result['incomplete_agent']=[]
    query_call=LLMResponse(tool_calls=[ToolCall('r1','execute_sql',{'sql':'SELECT COUNT(*) AS n FROM employees'})])
    cases=[('provider_failure',[query_call,RuntimeError('review-induced local failure')],3),('step_limit',[query_call],1),('final_wrong_sql',[query_call,LLMResponse(text='```sql\nSELECT 999 AS n\n```')],3)]
    for name,responses,limit in cases:
        res=agent.run_harness(Scripted(responses),'How many employees?',CFG,max_steps=limit,toolbox=box)
        result['incomplete_agent'].append({'case':name,**asdict(res),'grader_would_pass':bool(res.rows) and matches([{'n':40}],res.rows)})
    result['ambiguous_joins']=[asdict(j) for j in box.joins if j.is_ambiguous]
    result['schema_join_block']=box.card.split('[Foreign keys]')[-1]
    # A separate, direct calculation on raw records verifies the new scalar
    # answers without invoking the model or the production result comparator.
    from decimal import Decimal
    from datetime import datetime,timedelta
    events=sql('SELECT * FROM usage_events'); invoices=sql('SELECT * FROM invoices'); deals=sql('SELECT * FROM deals')
    numeric=re.compile(r'^[0-9]+(\.[0-9]+)?$')
    vals=[Decimal(e['event_value']) for e in events if e['feature_key']=='export.csv' and e['event_value'] is not None and numeric.fullmatch(e['event_value'])]
    paid=Decimal(0)
    for inv in invoices:
        if inv['status']!='paid':continue
        cents=inv['currency_minor']==1 or (inv['currency_minor'] is None and inv['issued_at']<datetime(2025,7,1))
        paid+=inv['amount']/100 if cents else inv['amount']
    ids={r['customer_id'] for r in sql('SELECT customer_id FROM customers')}
    expected={
      'R01':sum(vals)/len(vals),'R02':paid,
      'R03':sum(datetime(2026,7,1)<=e['event_ts']+(timedelta(hours=4) if e['source']=='batch' else timedelta())<datetime(2026,7,2) for e in events),
      'R04':next(r['list_price_usd'] for r in sql('SELECT * FROM product_catalog') if r['sku']=='SKU-ADD-API' and r['effective_to'] is None),
      'R05':sum((r['amount']/Decimal('1.08') for r in deals if r['status']=='lost'),Decimal(0)),
      'R06':sum(r['priority']=='P1' and r['cust_id'] is not None and r['cust_id'] not in ids for r in sql('SELECT * FROM support_tickets')),
      'R07':sum(r['team']=='Engineering' for r in sql('SELECT * FROM employees')),
      'R08':sum(r['priority']=='P2' for r in sql('SELECT * FROM support_tickets')),
    }
    result['fresh_independent_oracles']={}
    for q in FRESH:
        actual=next(iter(sql(q['gold_sql'])[0].values()))
        difference=abs(Decimal(str(actual))-Decimal(str(expected[q['id']])))
        result['fresh_independent_oracles'][q['id']]={'raw_records_calculation':expected[q['id']],'sql_oracle':actual,'absolute_difference':difference}
        assert difference<Decimal('0.0001'), (q['id'],difference)
    save('audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['replay','d6_injected_pairs','schema_join_block','ambiguous_joins']},default=str),flush=True)
    print('REPLAY', {k:(v['correct'],v['changed']) for k,v in result['replay'].items()},flush=True)

def infer():
    # Use frozen on-disk questions; no edits after seeing model responses.
    fresh=json.loads((OUT/'fresh_questions_frozen.json').read_text())
    client=LocalLLM()
    ok,message=client.health(); print(message,flush=True)
    assert ok
    for name,questions in [('original',load_questions()),('fresh',fresh)]:
        for arm in ['baseline','harness']:
            started=time.time()
            records=run_arm(arm,questions,client,CFG,CFG)
            payload={'arm':arm,'provider':'local','model':client.model,'enable_thinking':client.enable_thinking,'started_unix':started,'elapsed_s':time.time()-started,'question_set':name,'summary':summarize(records),'records':records}
            save(f'{name}-{arm}-local.json',payload)
            print('COMPLETE',name,arm,payload['summary'],flush=True)

if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='audit':audit()
    elif mode=='infer':infer()
