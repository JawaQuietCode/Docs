import tempfile, unittest
from pathlib import Path
from memory.intelligence import IntelligentMemory

class IntelligentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=IntelligentMemory(Path(self.tmp.name)/'memory.db')
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def test_typed_memory_and_retrieval(self):
        a=self.store.add('episodic','Rapat pemasaran membahas peluncuran produk baru',source='user',metadata={'project':'launch'})
        self.assertTrue(a['ok'])
        hits=self.store.retrieve('peluncuran produk')
        self.assertEqual(hits[0]['id'],a['id']); self.assertEqual(hits[0]['kind'],'episodic')
    def test_working_memory_expiry(self):
        a=self.store.add('working','catatan sementara untuk sprint',ttl_seconds=1)
        self.store.db.execute('UPDATE items SET expires_at=1 WHERE id=?',(a['id'],)); self.store.db.commit()
        self.assertEqual(self.store.search('sprint'),[])
    def test_association_and_provenance(self):
        a=self.store.add('semantic','WhatsApp memakai kartu rich untuk markdown',source='docs')
        b=self.store.add('procedural','Gunakan markdown layout untuk merender tabel',source='observed')
        self.assertTrue(self.store.link(a['id'],b['id'],'implemented_by')['ok'])
        p=self.store.provenance(a['id']); self.assertEqual(p['source'],'docs')
        self.assertEqual(p['relations'][0]['item']['id'],b['id'])
    def test_persistent_mission_lifecycle(self):
        created=self.store.mission('create',title='Upgrade onboarding',goal='Ship the improved welcome flow')
        self.assertTrue(created['ok'])
        self.assertTrue(self.store.mission('update',mission_id=created['id'],status='paused')['ok'])
        listed=self.store.mission('list')['missions']
        self.assertEqual(listed[0]['status'],'paused')
    def test_reflection_and_compression(self):
        self.store.add('episodic','Pengujian browser Chromium berhasil pada startup')
        self.store.add('episodic','Startup Chromium browser smoke test berhasil')
        self.assertGreaterEqual(self.store.reflect()['count'],2)
        result=self.store.compress(kind='episodic')
        self.assertTrue(result['ok']); self.assertGreaterEqual(result['compressed'],2)

if __name__=='__main__': unittest.main()
