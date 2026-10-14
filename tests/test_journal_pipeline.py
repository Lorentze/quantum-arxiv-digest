import copy
import unittest
from qradar.analysis import AnalysisError
from qradar.journal_pipeline import merge_journal_collection
from qradar.pipeline import empty_state, import_analyses, make_update
from test_pipeline import PROFILE, NOW, paper
from test_hosted import item


def journal(doi='10.1038/s41567-026-00001-0', title='SPAM robust randomized benchmarking'):
    return {'id':'doi:'+doi, 'version':1, 'title':title, 'authors':['Researcher'],
            'abstract':'A rigorous quantum protocol.', 'url':'https://www.nature.com/articles/'+doi.split('/')[1],
            'published':'2026-10-08T00:00:00Z', 'updated':'2026-10-08T00:00:00Z', 'categories':[],
            'sources':['nature_physics'], 'publication':{'journal_id':'nature_physics','journal':'Nature Physics',
            'doi':doi,'url':'https://www.nature.com/articles/'+doi.split('/')[1],
            'published':'2026-10-08T00:00:00Z','date_basis':'published-online'}}


def collected(state, papers):
    return merge_journal_collection(state, PROFILE, {'papers':papers,
        'source_status':{'nature_physics':{'status':'ok','checked_at':NOW.isoformat()}}}, now=NOW)


class JournalPipelineTests(unittest.TestCase):
    def setUp(self):
        self.state=empty_state(PROFILE)

    def test_journal_collection_requires_analysis(self):
        result=collected(self.state,[journal()])
        self.assertEqual(len(result['pending']),1)
        self.assertEqual(result['papers'],[])
        result2=collected(result,[journal()])
        self.assertEqual(len(result2['pending']),1)

    def test_same_title_merges_preprint_and_queues_review_without_removing_report(self):
        p=paper('2610.00001')
        p['analysis']=item()['analysis']
        self.state['papers']=[p]
        result=collected(self.state,[journal()])
        self.assertEqual(len(result['papers']),1)
        self.assertEqual(result['pending'],[])
        self.assertEqual(result['papers'][0]['sources'],['arxiv','nature_physics'])
        self.assertEqual(result['publication_reviews_pending'][0]['id'],p['id'])
        self.assertNotIn('publication',p)

    def test_published_analysis_needs_significance_and_bound_source(self):
        result=collected(self.state,[journal()])
        p=result['pending'][0]
        entry=item(p['id'])
        entry['analysis']['source_url']=p['url']
        with self.assertRaises(AnalysisError):
            import_analyses(result,PROFILE,{'analyses':[entry]},now=NOW)
        entry['analysis']['publication_significance']=['基于公开摘要推断：该工作解决了可扩展的认证问题。']
        published=import_analyses(result,PROFILE,{'analyses':[entry]},now=NOW)
        self.assertEqual(len(published['papers']),1)
        bad=copy.deepcopy(entry)
        bad['analysis']['source_url']='https://www.nature.com/articles/unrelated'
        with self.assertRaises(AnalysisError):
            import_analyses(result,PROFILE,{'analyses':[bad]},now=NOW)

    def test_publication_review_is_transactional_and_retains_original_analysis(self):
        p=paper('2610.00001'); p['analysis']=item()['analysis']; self.state['papers']=[p]
        state=collected(self.state,[journal()])
        p=state['papers'][0]
        review={'id':p['id'],'doi':p['publication']['doi'], 'publication_significance':['分析判断：具有实际门认证意义。'],
                'evidence_level':'abstract','source_url':p['publication']['url'],
                'analyzed_at':NOW.isoformat(),'model':'ChatGPT hosted'}
        result=import_analyses(state,PROFILE,{'publication_reviews':[review]},now=NOW)
        self.assertEqual(result['papers'][0]['analysis']['summary'],p['analysis']['summary'])
        self.assertEqual(result['publication_reviews_pending'],[])
        before=copy.deepcopy(state)
        bad={**review,'doi':'10.1038/other'}
        with self.assertRaises(AnalysisError):
            import_analyses(state,PROFILE,{'publication_reviews':[review,bad]},now=NOW)
        self.assertEqual(state,before)

    def test_source_failure_does_not_fake_success_or_clear_backlog(self):
        self.state['pending']=[paper('2610.00002')]
        result=merge_journal_collection(self.state,PROFILE,{'papers':[],
            'source_status':{'science':{'status':'failed','error':'403'}}},now=NOW)
        self.assertNotIn('last_journal_collection_run',result)
        self.assertEqual(result['pending'],self.state['pending'])

    def test_arxiv_after_journal_deduplicates(self):
        from unittest.mock import patch
        self.state['pending']=[journal()]
        with patch('qradar.pipeline.fetch_recent',return_value=[paper('2610.00001')]):
            result=make_update(self.state,PROFILE,now=NOW,analyze_candidates=False)
        self.assertEqual(len(result['pending']),1)
        self.assertEqual(result['pending'][0]['arxiv_id'],'2610.00001')
        self.assertEqual(result['pending'][0]['sources'],['nature_physics','arxiv'])

if __name__=='__main__':
    unittest.main()

class IdentityAndMetadataTests(unittest.TestCase):
    def test_disjoint_authors_do_not_merge_by_same_title(self):
        from qradar.journal_pipeline import same_paper
        a=paper('2610.00001'); b=journal(); b['authors']=['Someone Else']
        self.assertFalse(same_paper(a,b))
        a['doi']=b['publication']['doi']
        self.assertTrue(same_paper(a,b))

    def test_improved_abstract_is_rescreened(self):
        blank=journal(title='A new way to study physical systems')
        blank['abstract']=''
        state=collected(empty_state(PROFILE),[blank])
        self.assertEqual(state['pending'],[])
        full=copy.deepcopy(blank); full['abstract']='We study SPAM robust randomized benchmarking.'
        result=collected(state,[full])
        self.assertEqual(len(result['pending']),1)

    def test_exact_recorded_crossref_source_is_valid_only_for_this_journal(self):
        from qradar.journal_pipeline import valid_source_url
        p=journal(); p['abstract_source_url']='https://api.crossref.org/journals/1745-2481/works?rows=1000'
        self.assertTrue(valid_source_url(p,p['abstract_source_url']))
        self.assertFalse(valid_source_url(p,'https://api.crossref.org/journals/2041-1723/works?rows=1000'))

class PublicationStatusTests(unittest.TestCase):
    def test_accepted_only_is_deferred_then_can_be_collected_when_published(self):
        j=journal(); j['publication']['stage']='accepted_manuscript'
        state=collected(empty_state(PROFILE),[j])
        self.assertEqual(state['pending'],[])
        self.assertEqual(state['screened'][j['id']]['status'],'awaiting_publication')
        j['publication']['stage']='journal_article'
        result=collected(state,[j])
        self.assertEqual(len(result['pending']),1)

    def test_reference_pdf_is_doi_bound(self):
        from qradar.journal_pipeline import valid_source_url
        j=journal()
        self.assertTrue(valid_source_url(j,j['url']+'_reference.pdf'))
        self.assertFalse(valid_source_url(j,'https://www.nature.com/articles/wrong_reference.pdf'))
