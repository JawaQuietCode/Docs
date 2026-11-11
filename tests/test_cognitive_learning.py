import json
import tempfile
import unittest
from pathlib import Path

from agent import cognitive
from agent.intelligence import rank_tools
from memory.store import Store
from tools import registry

class CognitiveLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'tenant.sqlite3'
        self.store=Store(str(self.db))
    def tearDown(self): self.store.conn.close(); self.tmp.cleanup()

    def test_nlu_browser_stats_and_adaptive_explanation(self):
        parsed=cognitive.understand('Buka dashboard dan jelaskan langkah-langkahnya secara singkat')
        self.assertIn('browser',parsed['intents']); self.assertTrue(parsed['multi_step']); self.assertEqual(parsed['explanation_depth'],'brief')
        self.assertEqual(cognitive.composition_plan(parsed)[0].split(':')[0],'browser')
        self.assertEqual(cognitive.statistics_summary([1,2,3])['n'],3)
        self.assertEqual(cognitive.statistics_summary([float('nan')])['ok'],False)

    def test_human_feedback_updates_tool_order_and_is_tenant_persisted(self):
        tools=[{'name':'tool_a'},{'name':'tool_b'}]
        before=rank_tools(tools,'ordinary task',self.store)
        for _ in range(12):self.store.record_learning_event('tool:conversation','tool_b',1,source='human',chat_id='tenant-x')
        after=rank_tools(tools,'ordinary task',self.store)
        self.assertEqual([x['name'] for x in before],['tool_a','tool_b'])
        self.assertEqual([x['name'] for x in after][0],'tool_b')
        self.assertEqual(self.store.learning_stats('tool:conversation')[0]['human_n'],12)

    def test_skill_reward_changes_only_relevant_external_skill_order(self):
        from skills.loader import _match
        skills=[{'name':'skill_a','triggers':['report'],'priority':0}, {'name':'skill_b','triggers':['report'],'priority':0}]
        matched=_match(skills,'write a report',2,with_reason=True,reward_scores={'skill_b':1.5})
        self.assertEqual(matched[0][0]['name'],'skill_b')
        self.assertIn('learned preference',matched[0][1])

    def test_rlaif_heuristic_and_statistical_caveat(self):
        reward,evidence=cognitive.rlaif_score('research latest sources','Verified source https://example.org supports the finding; uncertainty remains.')
        self.assertGreater(reward,0); self.assertEqual(evidence['rubric'],'heuristic-v1')
        score,proof=cognitive.rlaif_score('summarize','')
        self.assertEqual(score,-1); self.assertTrue(proof['empty'])

    def test_reasoning_mode_detection_and_calibrated_guidance(self):
        parsed=cognitive.understand("Bandingkan sebab-akibat dan buat counterfactual, cari kontra-argumen, gunakan beberapa perspektif, dan susun timeline riset hipotesis")
        for mode in ("causal", "counterfactual", "adversarial", "multi_perspective", "temporal", "scientific"):
            self.assertIn(mode, parsed["reasoning_modes"])
        guidance=cognitive.adaptive_guidance("Jelaskan hubungan sebab-akibat, buat counterfactual, devil's advocate dan timeline eksperimen")
        self.assertIn("correlation does not establish causation", guidance)
        self.assertIn("counterfactual", guidance.lower())
        self.assertIn("rival", guidance.lower())
        self.assertIn("validity interval", guidance.lower())

    def test_abstract_pattern_and_analogy_are_model_guided_not_proven(self):
        parsed=cognitive.understand("Cari pola abstrak dan analogi lintas domain")
        self.assertIn("abstract_pattern", parsed["reasoning_modes"])
        self.assertIn("analogy", parsed["reasoning_modes"])
        guidance=cognitive.adaptive_guidance("Temukan pola abstrak dan analogi")
        self.assertIn("mapping", guidance.lower())
        self.assertIn("not proof", guidance.lower())

    def test_system_prompt_contains_live_task_adaptation(self):
        text=cognitive.adaptive_guidance('Bandingkan tren dan hitung rata-rata statistik')
        self.assertIn('statistics',text); self.assertIn('Statistical reasoning',text); self.assertIn('comparison_table',text)

    def test_native_feedback_and_reviewable_prompt_skill_proposals(self):
        from tools.builtin import learning
        cfg={'context':{'memory_db_path':str(self.db),'tenant':'tenant-a','chat_id':'chat-1','tool_allow':['agent_learning']}}
        feedback=learning.handler({'action':'feedback','query':'riset sumber terbaru','intent':'research','target':'agent_learning','rating':5},cfg)
        self.assertTrue(feedback['ok'])
        self.assertEqual(self.store.learning_stats('tool:research')[0]['human_n'],1)
        candidates=[{'name':'generic_a'},{'name':'agent_learning'}]
        self.assertEqual(rank_tools(candidates,'riset sumber terbaru',self.store)[0]['name'],'agent_learning')
        for _ in range(2):learning.handler({'action':'feedback','query':'jelaskan','intent':'explain','target':'deep','rating':5},cfg)
        self.assertEqual(cognitive.explanation_profile('jelaskan',self.store)['depth'],'deep')
        prompt=learning.handler({'action':'optimize_prompt','query':'jelaskan statistik secara ringkas','target':'statistics'},cfg)
        from skills import loader
        external=loader.all_skills()['external']
        self.assertTrue(external)
        selected=external[0]; skill_name=selected['name']; query=selected['triggers'][0] if selected['triggers'] else skill_name
        skill=learning.handler({'action':'refine_skill','skill_name':skill_name,'proposal':'Require primary-source cross-checks','rationale':'Observed missing provenance'},cfg)
        self.assertEqual(prompt['status'],'candidate_saved'); self.assertEqual(skill['status'],'proposed_not_applied')
        denied=learning.handler({'action':'approve_proposal','proposal_id':skill['proposal_id']},cfg)
        self.assertFalse(denied['ok'])
        approved_cfg={'context':{**cfg['context'],'is_primary':True,'owner_confirmed':True,'owner_request_text':f"setuju proposal {skill['proposal_id']}"}}
        approved=learning.handler({'action':'approve_proposal','proposal_id':skill['proposal_id']},approved_cfg)
        self.assertTrue(approved['ok'])
        from agent.context import skills_block
        rendered=skills_block(query,self.store)
        self.assertIn('OWNER-APPROVED RUNTIME SKILL REFINEMENTS',rendered)
        self.assertEqual(len(self.store.learning_proposals()),2)

    def test_native_learning_tool_e2e_through_agent_loop(self):
        from tools.builtin import learning
        from agent.loop import run_turn
        class FakeLLM:
            def __init__(self):self.calls=0;self.tool_payload=''
            def chat(self,messages,tools=None,timeout=None,**kwargs):
                self.calls+=1
                if self.calls==1:
                    return {'content':'','tool_calls':[{'id':'call-1','type':'function','function':{'name':'agent_learning','arguments':json.dumps({'action':'statistics','values':[10,20,30]})}}]}
                self.tool_payload=' '.join(str(m.get('content') or '') for m in messages if m.get('role')=='tool')
                return {'content':'Ringkasan selesai: n=3, rata-rata 20.','tool_calls':[]}
        llm=FakeLLM()
        result=run_turn(llm,[{'role':'user','content':'Hitung statistik'}],[registry.get('agent_learning') or {'name':'agent_learning','schema':learning.SCHEMA,'handler':learning.handler}],tool_config={'context':{'memory_db_path':str(self.db),'tenant':'e2e','chat_id':'c1','tool_allow':['agent_learning']}},max_iterations=3,timeout_seconds=8)
        self.assertIn('rata-rata 20',result['raw'])
        self.assertIn('"mean": 20.0',llm.tool_payload)
        self.assertEqual(self.store.learning_stats(),[])
        # Verify the JSON tool result came back through the loop, not a direct function call.
        self.assertEqual(result['replies'][0]['type'],'text')

if __name__=='__main__':unittest.main()
